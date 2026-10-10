"""Exercise source graph TRS bytes, pivots, hierarchy and V1 persistence."""
import copy
import json
import math
import struct
from pathlib import Path

import pytest

from editor_backend import StudioBackend
from renderer_parser import read_sections
from scene_graph import local_matrix, multiply, resolve_scene, transform_point
from test_scene_graph import graph_fixture


def transform_fixture(zero_child_scale=False):
    graph, _, pool = graph_fixture()
    # An additional real generator selects the root transform. Its child
    # generator and two material parts select the second transform.
    struct.pack_into("<I", graph, 0, 5)
    struct.pack_into("<i", graph, 48, 512 - 48)
    kind = b"critterGenerator"
    graph[950:950 + len(kind)] = kind
    struct.pack_into("<IiI", graph, 512, len(kind), 950 - 516, 0)
    struct.pack_into("<i", graph, 524, 776 - 524)
    struct.pack_into("<i", graph, 536, 0)
    struct.pack_into("<Ii", graph, 540, 4, 980 - 544)
    graph[980:984] = b"ruby"
    for record in (128, 256):
        for offset, pivot in ((44, [0.5, -0.25, 0.75]), (56, [0.1, 0.2, 0.3]),
                              (68, [-0.4, 0.6, 0.2]), (80, [0.3, -0.1, 0.2])):
            struct.pack_into("<3f", graph, record + offset, *pivot)
    struct.pack_into("<3f", graph, 644 + 16, 0.2, -0.3, 0.4)
    struct.pack_into("<3f", graph, 644 + 28, 0 if zero_child_scale else 1.5, 0.75, 2)
    # Wrap the relative graph in an actual v4 archive. Relative pointers
    # survive the prefix; the TOC has the verified cumulative-end convention.
    data = bytearray(256) + graph
    data[:4] = b"xobx"
    struct.pack_into("<I", data, 4, 4)
    struct.pack_into("<II", data, 12, 4, 256)
    for i, (size, tag, end) in enumerate(((1500, b"node", 1500), (64, b"rdms", 1600),
                                         (64, b"pbrc", 1664), (64, b"pbrc", 1728))):
        struct.pack_into("<I4sII", data, 64 + 16 * i, size, tag, 8, end)
    pool["sourceOffset"] = 1756
    return bytes(data), pool


class GraphBackend(StudioBackend):
    def _parse_scene(self, data, level):
        _, pool = transform_fixture()
        scene = resolve_scene(data, [pool], read_sections(data))
        scene["collisions"] = {"positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "indices": [0, 1, 2]}
        return scene


@pytest.fixture
def studio(tmp_path):
    source = tmp_path / "source" / "gamedata"
    source.mkdir(parents=True)
    original, _ = transform_fixture()
    (source / "training_room.xbr").write_bytes(original)
    backend = GraphBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    return backend, original


def find(scene, name):
    return next(item for item in scene["objects"] if item["name"] == name)


def compare_scene(preview, reread):
    for a, b in zip(preview["meshes"], reread["meshes"]):
        assert a["positions"] == pytest.approx(b["positions"], abs=1e-5)
        assert a["position"] == pytest.approx(b["position"], abs=1e-5)
        assert a["origin"] == pytest.approx(b["origin"], abs=1e-5)
    for a, b in zip(preview["objects"], reread["objects"]):
        assert a["position"] == pytest.approx(b["position"], abs=1e-5)
        assert a["placementMatrix"] == pytest.approx(b["placementMatrix"], abs=1e-5)


def test_rotation_scale_and_world_target_match_export_redecoded_under_parent(studio):
    backend, original = studio
    start = backend.get_scene("training_room")
    child = find(start, "emerald")
    assert child["localRotation"] == pytest.approx([0.2, -0.3, 0.4])
    assert child["editBinding"]["rotationOffset"] == 916
    assert child["editBinding"]["scaleOffset"] == 928
    assert start["capabilities"]["meshRotation"] and start["capabilities"]["entityScale"]
    target = [11, 25, 37]
    result = backend.transform("training_room", child["id"], rotation=[0.5, -0.7, 1.2], scale=[2, 3, -1], position=target)
    scene = result["scene"]
    assert result["changed"] and result["canUndo"]
    assert find(scene, "emerald")["position"] == pytest.approx(target, abs=1e-5)
    assert scene["collisions"] == start["collisions"]
    assert find(scene, "emerald")["originalLocalRotation"] == child["originalLocalRotation"]
    # Source pivots survive recomposition; the local matrix uses their exact
    # decoded float32 values, rather than a translation around mesh centre.
    node = scene["nodes"][1]
    expected = local_matrix(node["position"], node["rotation"], node["scale"], node["rotatePivot"],
                            node["rotatePivotTranslation"], node["scalePivot"], node["scalePivotTranslation"])
    assert node["localMatrix"] == pytest.approx(expected)
    exported = backend.export()
    payload = (Path(exported["directory"]) / "gamedata" / "training_room.xbr").read_bytes()
    assert struct.unpack_from("<3f", payload, 916) == pytest.approx([0.5, -0.7, 1.2])
    assert struct.unpack_from("<3f", payload, 928) == (2, 3, -1)
    allowed = set(range(904, 940))
    assert all(a == b for i, (a, b) in enumerate(zip(original, payload)) if i not in allowed)
    assert backend._source_path("training_room").read_bytes() == original
    reread = GraphBackend(Path(exported["directory"]), backend.project_dir.parent / "reread-project",
                         backend.exports_dir.parent / "reread-exports", texture_dir=backend.texture_dir).get_scene("training_room")
    compare_scene(scene, reread)


def test_parent_transform_descendants_then_child_world_move_and_persisted_undo(studio):
    backend, original = studio
    initial = backend.get_scene("training_room")
    root, child = find(initial, "ruby"), find(initial, "emerald")
    parent_result = backend.transform("training_room", root["id"], rotation=[0.1, 0.6, -0.4], scale=[3, 0.5, 2])
    after_parent = parent_result["scene"]
    assert after_parent["meshes"][0]["positions"] != initial["meshes"][0]["positions"]
    assert find(after_parent, "emerald")["localRotation"] == child["localRotation"]
    backend.move("training_room", child["id"], [18, 19, 20])
    final = backend.get_scene("training_room")
    assert find(final, "emerald")["position"] == pytest.approx([18, 19, 20], abs=1e-5)
    assert backend.pending_count() == 2
    result = backend.export()
    reread = GraphBackend(Path(result["directory"]), backend.project_dir.parent / "reread-project",
                         backend.exports_dir.parent / "reread-exports", texture_dir=backend.texture_dir).get_scene("training_room")
    compare_scene(final, reread)
    reopened = GraphBackend(backend.source_dir, backend.project_dir, backend.exports_dir, texture_dir=backend.texture_dir)
    reopened.undo("training_room")
    compare_scene(reopened.get_scene("training_room"), after_parent)
    reopened.undo("training_room")
    compare_scene(reopened.get_scene("training_room"), initial)
    assert reopened.pending_count() == 0
    reopened.redo("training_room")
    reopened.redo("training_room")
    compare_scene(reopened.get_scene("training_room"), final)
    assert backend._source_path("training_room").read_bytes() == original


def test_mesh_rotation_preserves_node_pivot_and_all_linked_parts(studio):
    backend, _ = studio
    initial = backend.get_scene("training_room")
    selected = initial["meshes"][0]
    result = backend.transform("training_room", selected["id"], rotation=[0, 0, math.pi / 2], scale=[2, 1, 1])
    scene = result["scene"]
    assert backend.pending_count() == 1
    assert scene["meshes"][0]["positions"] == scene["meshes"][1]["positions"]
    assert scene["nodes"][1]["position"] == initial["nodes"][1]["position"]
    assert scene["nodes"][1]["rotatePivot"] == initial["nodes"][1]["rotatePivot"]
    # Bounds are recomputed after rotation; a rotated AABB centre is not
    # generally the AABB centre of the rotated triangle.
    vertices = scene["meshes"][0]["positions"]
    assert scene["meshes"][0]["position"] == pytest.approx([(min(vertices[a::3]) + max(vertices[a::3])) / 2 for a in range(3)])
    assert not backend.transform("training_room", selected["id"], rotation=scene["meshes"][0]["localRotation"], scale=scene["meshes"][0]["localScale"])["changed"]
    assert len(backend._state("training_room")["undo"]) == 1


@pytest.mark.parametrize("field,value", [("rotationOffset", 500), ("scaleOffset", 500),
                                        ("originalBytes", {"position": "00" * 12, "rotation": "00" * 12, "scale": "00" * 12})])
def test_tampered_transform_offset_and_original_bytes_reject_export(studio, field, value):
    backend, _ = studio
    item = find(backend.get_scene("training_room"), "emerald")
    backend.transform("training_room", item["id"], rotation=[1, 2, 3], scale=[2, 2, 2])
    project = json.loads(backend.project_path.read_text("utf-8"))
    project["levels"]["training_room"]["edits"][item["id"]][field] = value
    backend.project_path.write_text(json.dumps(project), "utf-8")
    reopened = GraphBackend(backend.source_dir, backend.project_dir, backend.exports_dir, texture_dir=backend.texture_dir)
    with pytest.raises(ValueError):
        reopened.export()
    assert not backend.exports_dir.exists()


def test_invalid_transform_is_atomic_and_history_is_unchanged(studio):
    backend, _ = studio
    item = find(backend.get_scene("training_room"), "emerald")
    backend.transform("training_room", item["id"], rotation=[0.2, 0.3, 0.4])
    before = copy.deepcopy(backend._project)
    for invalid in ([0, 1, 1], [float("nan"), 1, 1], [True, 1, 1], [1001, 1, 1]):
        with pytest.raises(ValueError):
            backend.transform("training_room", item["id"], rotation=[1, 2, 3], scale=invalid)
        assert backend._project == before
    with pytest.raises(ValueError):
        backend.transform("training_room", item["id"], rotation=[float("inf"), 0, 0])
    assert backend._project == before


def test_corrupt_history_snapshot_rejects_without_changing_current_edits(studio):
    backend, _ = studio
    item = find(backend.get_scene("training_room"), "emerald")
    backend.transform("training_room", item["id"], rotation=[1, 2, 3])
    backend.transform("training_room", item["id"], scale=[2, 2, 2])
    state = backend._state("training_room")
    state["undo"][-1]["beforeEdits"][item["id"]]["rotationOffset"] = 500
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        backend.undo("training_room")
    assert state == before


def test_v1_translation_project_and_legacy_history_migrate_without_losing_edits(studio):
    backend, _ = studio
    item = find(backend.get_scene("training_room"), "emerald")
    before = item["position"][:]
    target = [before[0] + 1, before[1] + 2, before[2] + 3]
    backend.move("training_room", item["id"], target)
    project = json.loads(backend.project_path.read_text("utf-8"))
    state = project["levels"]["training_room"]
    edit = state["edits"][item["id"]]
    for key in ("transformVersion", "localRotation", "localScale", "rotationOffset", "scaleOffset", "originalBytes"):
        edit.pop(key)
    for row in state["undo"]:
        row.pop("beforeEdits")
        row.pop("afterEdits")
    backend.project_path.write_text(json.dumps(project), "utf-8")
    reopened = GraphBackend(backend.source_dir, backend.project_dir, backend.exports_dir, texture_dir=backend.texture_dir)
    assert find(reopened.get_scene("training_room"), "emerald")["position"] == pytest.approx(target, abs=1e-5)
    reopened.undo("training_room")
    assert reopened.pending_count() == 0
    reopened.redo("training_room")
    assert find(reopened.get_scene("training_room"), "emerald")["position"] == pytest.approx(target, abs=1e-5)
    reopened.transform("training_room", item["id"], rotation=[0, 0, 0.5])
    assert find(reopened.get_scene("training_room"), "emerald")["position"] != pytest.approx(target, abs=1e-5)  # Offset pivot rotates.
    assert reopened._project["version"] == 1
    assert reopened._state("training_room")["edits"][item["id"]]["transformVersion"] == 2


def test_zero_scale_source_descendant_still_follows_editable_ancestor(studio):
    backend, _ = studio
    data, _ = transform_fixture(zero_child_scale=True)
    backend._source_path("training_room").write_bytes(data)
    scene = backend.get_scene("training_room")
    assert all(not m["editable"] for m in scene["meshes"])
    assert all("_localPositions" not in m for m in scene["meshes"])
    result = backend.transform("training_room", find(scene, "ruby")["id"], rotation=[0.5, 1, 0.7])
    assert result["scene"]["meshes"][0]["positions"] != scene["meshes"][0]["positions"]
    export = backend.export()
    reread = GraphBackend(Path(export["directory"]), backend.project_dir.parent / "reread-project",
                         backend.exports_dir.parent / "reread-exports", texture_dir=backend.texture_dir).get_scene("training_room")
    compare_scene(result["scene"], reread)
