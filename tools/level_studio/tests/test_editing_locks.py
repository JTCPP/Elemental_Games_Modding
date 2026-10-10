"""Locks protect shared placements, descendants, previews and history."""
import copy
import json
import math
from pathlib import Path

import pytest

from editor_backend import StudioBackend, PREVIEW_WARNING
from scene_graph import identity, local_matrix, multiply, translation, transform_point
from test_editor_backend import FixtureBackend, fixture_data
from test_transform_backend import GraphBackend, transform_fixture, find


class PreviewBackend(FixtureBackend):
    def _parse_scene(self, data, level):
        scene = super()._parse_scene(data, level)
        mesh = copy.deepcopy(scene["meshes"][0])
        mesh.update(id="mesh-static", name="Static terrain", editable=False,
                    worldMatrix=identity(), sourceNormals=[0, 0, 1] * 3,
                    normals=[0, 0, 1] * 3, worldPositionVerified=True, coordinateSpace="world")
        mesh.pop("nodeIndex")
        mesh.pop("editBinding")
        scene["meshes"].append(mesh)
        return scene


@pytest.fixture
def preview(tmp_path):
    source = tmp_path / "source/gamedata"
    source.mkdir(parents=True)
    (source / "town.xbr").write_bytes(fixture_data())
    return PreviewBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")


def reopen(backend):
    return type(backend)(backend.source_dir, backend.project_dir, backend.exports_dir, texture_dir=backend.texture_dir)


def item(backend, identifier="mesh-static"):
    scene = backend.get_scene("town")
    return next(row for row in scene["objects"] + scene["meshes"] if row["id"] == identifier)


def test_preview_only_mesh_move_trs_undo_redo_and_persistence(preview):
    source = preview._source_path("town").read_bytes()
    original = item(preview)
    assert not original["editable"]
    assert original["previewEditable"] and original["previewOnly"] and not original["exportable"]
    assert original["localRotation"] == [0, 0, 0] and original["localScale"] == [1, 1, 1]
    target, rotation, scale = [55.5, 65.5, 71], [0.2, -0.4, 0.7], [2, 3, -1]
    moved = preview.transform("town", "mesh-static", position=target, rotation=rotation, scale=scale)
    assert moved["pendingCount"] == 0 and moved["previewCount"] == 1
    assert moved["warning"] == PREVIEW_WARNING
    transformed = item(preview)
    assert transformed["position"] == pytest.approx(target)
    expected = multiply(local_matrix(target, rotation, scale), translation([-v for v in original["position"]]))
    assert transformed["worldMatrix"] == pytest.approx(expected)
    assert transformed["positions"][:3] == pytest.approx(transform_point(expected, original["positions"][:3]))
    restored = reopen(preview)
    assert item(restored)["positions"] == transformed["positions"]
    assert restored.undo("town")["changed"]
    assert item(restored)["positions"] == original["positions"]
    assert restored.preview_count() == 0
    assert restored.redo("town")["changed"]
    assert item(restored)["positions"] == transformed["positions"]
    assert preview._source_path("town").read_bytes() == source
    summary = restored.project_summary()
    assert summary["previewCount"] == 1 and summary["levels"]["town"]["previewCount"] == 1


def test_preview_trs_starts_relative_to_baked_source_and_reset_uses_identity(preview):
    _, source_scene = preview._load("town")
    mesh = next(row for row in source_scene["meshes"] if row["id"] == "mesh-static")
    mesh.update(localRotation=[0.2, 0.7, -0.4], localScale=[2, 3, 4],
                originalLocalRotation=[0.2, 0.7, -0.4], originalLocalScale=[2, 3, 4])
    original = item(preview)
    assert original["localRotation"] == [0, 0, 0] and original["localScale"] == [1, 1, 1]
    assert original["originalLocalRotation"] == [0, 0, 0] and original["originalLocalScale"] == [1, 1, 1]
    preview.transform("town", "mesh-static", rotation=[0, 0, 0.5], scale=[2, 2, 2])
    transformed = item(preview)
    assert transformed["localRotation"] == [0, 0, 0.5]
    preview.transform("town", "mesh-static", rotation=transformed["originalLocalRotation"],
                      scale=transformed["originalLocalScale"], position=original["position"])
    assert preview.preview_count() == 0 and item(preview)["positions"] == original["positions"]


def test_preview_only_export_reports_overrides_without_fabricated_xbr_patches(preview):
    preview.move("town", "mesh-static", [55.5, 65.5, 71])
    result = preview.export()
    folder = Path(result["directory"])
    assert result["fileCount"] == 0 and result["editCount"] == 0 and result["previewCount"] == 1
    assert not list((folder / "gamedata").iterdir())
    assert json.loads((folder / "mod.json").read_text("utf-8"))["xbr_edits"] == []
    report = json.loads((folder / "scene-overrides.json").read_text("utf-8"))
    assert not report["gameExportable"]
    assert report["levels"]["town"]["overrides"]["mesh-static"]["translation"] == [5, 5, 1]
    assert PREVIEW_WARNING in result["warnings"]
    # Real writable and preview-only edits can coexist in one export.
    preview.move("town", "entity-00000064", [11, 21, 31])
    mixed = preview.export()
    assert mixed["fileCount"] == 1 and mixed["editCount"] == 1 and mixed["previewCount"] == 1


def test_preview_lock_rejects_move_transform_reset_and_history_without_changing_project(preview):
    original = item(preview)["position"]
    preview.move("town", "mesh-static", [55.5, 65.5, 71])
    preview.set_lock("town", True, "mesh-static")
    saved = preview.project_path.read_bytes()
    assert item(reopen(preview))["locked"]
    for mutation in (lambda: preview.move("town", "mesh-static", original),
                     lambda: preview.transform("town", "mesh-static", rotation=[0, 0, 1]),
                     lambda: preview.undo("town")):
        with pytest.raises(ValueError, match="verrouillé"):
            mutation()
        assert preview.project_path.read_bytes() == saved
    preview.set_lock("town", False, "mesh-static")
    assert preview.undo("town")["changed"]
    preview.set_lock("town", True, "mesh-static")
    with pytest.raises(ValueError, match="verrouillé"):
        preview.redo("town")


def test_lock_all_persists_and_unlock_all_restores_editability(preview):
    result = preview.set_lock("town", True, all_items=True)
    all_items = result["scene"]["meshes"] + result["scene"]["objects"]
    assert result["count"] == len(all_items) and all(row["locked"] for row in all_items)
    assert item(reopen(preview), "entity-00000064")["locked"]
    result = preview.set_lock("town", False, all_items=True)
    assert not any(row["locked"] for row in result["scene"]["meshes"] + result["scene"]["objects"])
    preview.move("town", "entity-00000064", [11, 21, 31])


def test_locks_reject_non_boolean_and_unknown_items(preview):
    for bad in (None, "true", 1, []):
        with pytest.raises(ValueError):
            preview.set_lock("town", bad, "mesh-static")
    with pytest.raises(ValueError):
        preview.set_lock("town", True, "unknown")
    assert not preview.project_path.exists()


def test_locked_material_part_protects_shared_node_parent_and_history(tmp_path):
    source = tmp_path / "source/gamedata"
    source.mkdir(parents=True)
    data, _ = transform_fixture()
    (source / "training_room.xbr").write_bytes(data)
    backend = GraphBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    scene = backend.get_scene("training_room")
    parent, child = find(scene, "ruby"), find(scene, "emerald")
    backend.move("training_room", parent["id"], [12, 24, 36])
    material = scene["meshes"][0]
    result = backend.set_lock("training_room", True, material["id"])
    shared = [row for row in result["scene"]["meshes"] + result["scene"]["objects"]
              if row.get("editBinding", {}).get("transformNode") == material["editBinding"]["transformNode"]]
    assert all(row["locked"] for row in shared)
    saved = backend.project_path.read_bytes()
    for mutation in (lambda: backend.move("training_room", child["id"], [11, 22, 33]),
                     lambda: backend.transform("training_room", parent["id"], scale=[2, 2, 2]),
                     lambda: backend.undo("training_room")):
        with pytest.raises(ValueError, match="verrouillé"):
            mutation()
        assert backend.project_path.read_bytes() == saved
    backend.set_lock("training_room", False, child["id"])
    assert backend.undo("training_room")["changed"]
    assert backend._source_path("training_room").read_bytes() == data


def test_tampered_preview_offsets_and_invalid_trs_are_rejected(preview):
    preview.move("town", "mesh-static", [55.5, 65.5, 71])
    saved = preview.project_path.read_bytes()
    for request in ({"scale": [0, 1, 1]}, {"rotation": [math.nan, 0, 0]}, {"position": [True, 0, 0]}):
        with pytest.raises(ValueError):
            preview.transform("town", "mesh-static", **request)
        assert preview.project_path.read_bytes() == saved
    project = json.loads(saved)
    project["levels"]["town"]["previewEdits"]["mesh-static"]["offset"] = 100
    preview.project_path.write_text(json.dumps(project), "utf-8")
    with pytest.raises(ValueError):
        reopen(preview).get_scene("town")
    with pytest.raises(ValueError):
        reopen(preview).export()


def test_reading_old_project_does_not_write_upgrade_to_disk(preview):
    preview.move("town", "entity-00000064", [11, 21, 31])
    project = json.loads(preview.project_path.read_text("utf-8"))
    del project["levels"]["town"]["locks"]
    del project["levels"]["town"]["previewEdits"]
    preview.project_path.write_text(json.dumps(project), "utf-8")
    before = preview.project_path.read_bytes()
    reopened = reopen(preview)
    reopened.get_scene("town")
    assert reopened.project_path.read_bytes() == before
