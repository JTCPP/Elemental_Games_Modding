import base64
import copy
import io
import json
import math
import struct
from pathlib import Path

import pytest
from PIL import Image

from asset_editing import geometry, mesh_plan, texture_plan
from editor_backend import StudioBackend
from renderer_parser import Section, decode_texture, parse_level, read_sections
from test_renderer_parser import fixture_level


def model():
    return {"positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "indices": [0, 1, 2], "uvs": [0, 0, 1, 0, 0, 1]}


def image_data(size=(4, 4), color=(200, 70, 10, 255)):
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


class AssetBackend(StudioBackend):
    def _parse_scene(self, data, level):
        return parse_level(data, level, self.texture_dir, assemble_scene=False)


@pytest.fixture
def studio(tmp_path):
    source = tmp_path / "source/gamedata"
    source.mkdir(parents=True)
    data = bytearray(fixture_level())
    # Original fixture has a base image only: mark it accurately as non-mipped.
    struct.pack_into("<I", data, 0x1000, 0x10)
    (source / "town.xbr").write_bytes(data)
    return AssetBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")


def reopen(backend):
    return type(backend)(backend.source_dir, backend.project_dir, backend.exports_dir, texture_dir=backend.texture_dir)


def row(backend, identifier):
    return next(m for m in backend.get_scene("town")["meshes"] if m["id"] == identifier)


def test_import_duplicate_move_history_reset_reopen_and_portable_export(studio):
    original = studio._source_path("town").read_bytes()
    texture = studio.import_texture("town", "My texture", image_data())["id"]
    geometry = model() | {"textureId": texture}
    imported = studio.import_model("town", "My model", geometry, [10, 20, 30])["id"]
    scene = studio.get_scene("town")
    assert len(scene["meshes"]) == 2 and len(scene["textures"]) == 2
    assert row(studio, imported)["previewEditable"] and not row(studio, imported)["exportable"]
    start = row(studio, imported)["position"]
    duplicate = studio.duplicate("town", imported)["id"]
    assert row(studio, duplicate)["position"] == [start[0] + 2, *start[1:]]
    studio.move("town", duplicate, [30, 40, 50])
    assert row(studio, duplicate)["position"] == [30, 40, 50]
    studio.undo("town")
    assert row(studio, duplicate)["position"] == [start[0] + 2, *start[1:]]
    studio.redo("town")
    exported = studio.export()
    folder = Path(exported["directory"])
    assert exported["fileCount"] == 0 and not list((folder / "gamedata").iterdir())
    assert len(list((folder / "assets").iterdir())) == 3
    manifest = json.loads((folder / "scene-overrides.json").read_text("utf-8"))
    assert set(manifest["levels"]["town"]["assets"]["models"]) == {imported, duplicate}
    reopened = reopen(studio)
    assert reopened._project["version"] == 2
    assert row(reopened, duplicate)["position"] == [30, 40, 50]
    reopened.set_lock("town", True, imported)
    reopened.reset_level("town")
    assert len(reopened.get_scene("town")["meshes"]) == 1
    assert reopened.preview_count() == reopened.pending_count() == 0
    reopened.undo("town")
    assert row(reopened, imported)["locked"]
    assert row(reopened, duplicate)["position"] == [30, 40, 50]
    reopened.redo("town")
    assert len(reopened.get_scene("town")["meshes"]) == 1
    assert studio._source_path("town").read_bytes() == original


def test_duplicate_is_frozen_current_world_geometry_delete_is_undoable(studio):
    source_id = studio.get_scene("town")["meshes"][0]["id"]
    duplicate = studio.duplicate("town", source_id, [0, 0, 4])["id"]
    original = row(studio, source_id)
    assert row(studio, duplicate)["positions"] == [v + (4 if i % 3 == 2 else 0) for i, v in enumerate(original["positions"])]
    studio.delete_asset("town", duplicate)
    assert not any(m["id"] == duplicate for m in studio.get_scene("town")["meshes"])
    studio.undo("town")
    assert row(studio, duplicate)["positions"][2] == 34
    studio.redo("town")
    assert len(studio.get_scene("town")["meshes"]) == 1
    with pytest.raises(ValueError, match="Seuls"):
        studio.delete_asset("town", source_id)


def test_native_texture_replaces_only_original_pixel_ranges_roundtrips_and_undo(studio):
    original = studio._source_path("town").read_bytes()
    result = studio.replace_texture("town", "surf-0000", "Orange", image_data())
    assert result["gameExportable"] and studio.pending_count() == 1
    updated = studio.get_scene("town")["textures"][0]
    assert updated["gameExportable"] and updated["replaced"]
    result = studio.export()
    payload = (Path(result["directory"]) / "gamedata/town.xbr").read_bytes()
    assert payload[:0x1080] == original[:0x1080] and payload[0x10c0:] == original[0x10c0:]
    image, fmt, kind = decode_texture(payload, read_sections(payload)[0])
    assert image.getpixel((0, 0)) == (200, 70, 10, 255)
    assert studio._source_path("town").read_bytes() == original
    reopened = reopen(studio)
    assert reopened.pending_count() == 1
    reopened.undo("town")
    assert reopened.pending_count() == 0
    reopened.redo("town")
    assert reopened.pending_count() == 1


def test_size_mismatch_texture_is_portable_preview_not_fabricated_game_patch(studio):
    result = studio.replace_texture("town", "surf-0000", "Larger", image_data((8, 8)))
    assert not result["gameExportable"] and studio.pending_count() == 0
    assert studio.get_scene("town")["textures"][0]["width"] == 8
    result = studio.export()
    assert result["fileCount"] == 0 and result["editCount"] == 0
    assert list((Path(result["directory"]) / "assets").iterdir())


def test_native_model_same_topology_bytes_preserved_and_redecoded(studio):
    original = studio._source_path("town").read_bytes()
    mesh = studio.get_scene("town")["meshes"][0]
    replacement = {"positions": mesh["positions"][:], "indices": mesh["indices"][:], "uvs": mesh["uvs"][:], "normals": [0, 0, 1] * 3, "coordinateSpace": "asset"}
    replacement["positions"][3] += 1
    result = studio.replace_model("town", mesh["id"], "Edited original", replacement)
    assert result["gameExportable"] and studio.pending_count() == 1
    assert row(studio, mesh["id"])["positions"][3] == 12
    exported = studio.export()
    payload = (Path(exported["directory"]) / "gamedata/town.xbr").read_bytes()
    assert payload[:0x11f0] == original[:0x11f0]  # resource header unchanged
    assert payload[0x1226:] == original[0x1226:]  # index tables unchanged
    assert len(payload) == len(original)
    studio.undo("town")
    assert row(studio, mesh["id"])["positions"][3] == 11
    studio.redo("town")
    assert row(studio, mesh["id"])["positions"][3] == 12
    studio.set_lock("town", True, mesh["id"])
    with pytest.raises(ValueError, match="verrouill"):
        studio.undo("town")


def test_changed_topology_model_is_preview_and_can_be_transformed(studio):
    identifier = studio.get_scene("town")["meshes"][0]["id"]
    result = studio.replace_model("town", identifier, "Triangle", model())
    assert not result["gameExportable"] and studio.pending_count() == 0
    assert len(row(studio, identifier)["indices"]) == 3
    position = row(studio, identifier)["position"]
    studio.move("town", identifier, [position[0] + 2, *position[1:]])
    assert row(studio, identifier)["position"][0] == position[0] + 2
    studio.undo("town")
    assert row(studio, identifier)["position"] == position
    studio.undo("town")
    assert len(row(studio, identifier)["indices"]) == 6


def test_preview_on_shared_instance_preserves_other_native_edit_through_history(studio, monkeypatch):
    original_parser = studio._parse_scene
    alias_id = "mesh-alias"
    def parse_with_alias(data, level):
        scene = original_parser(data, level)
        alias = copy.deepcopy(scene["meshes"][0])
        alias["id"] = alias_id
        alias["name"] = "Shared pool instance"
        scene["meshes"].append(alias)
        return scene
    monkeypatch.setattr(studio, "_parse_scene", parse_with_alias)
    original_source = studio._source_path("town").read_bytes()
    first = studio.get_scene("town")["meshes"][0]
    first_id = first["id"]
    replacement = {"positions": first["positions"][:], "indices": first["indices"][:],
                   "uvs": first["uvs"][:], "coordinateSpace": "asset"}
    replacement["positions"][3] += 1
    assert studio.replace_model("town", first_id, "Native first instance", replacement)["gameExportable"]
    native_key = f"mesh-{first['resourceIndex']}"
    native_edit = copy.deepcopy(studio._state("town")["assetEdits"][native_key])

    preview = studio.replace_model("town", alias_id, "Preview second instance", model())
    assert not preview["gameExportable"]
    assert studio._state("town")["assetEdits"][native_key] == native_edit
    assert studio.pending_count("town") == 1 and row(studio, first_id)["positions"][3] == 12
    assert len(row(studio, alias_id)["indices"]) == 3

    studio.undo("town")
    assert studio._state("town")["assetEdits"][native_key] == native_edit
    assert row(studio, first_id)["positions"][3] == row(studio, alias_id)["positions"][3] == 12
    assert len(row(studio, alias_id)["indices"]) == 6
    studio.redo("town")
    assert studio._state("town")["assetEdits"][native_key] == native_edit
    assert row(studio, first_id)["positions"][3] == 12 and len(row(studio, alias_id)["indices"]) == 3

    # A preview on the target which owns the native replacement removes that
    # replacement, while leaving the independent preview on the other instance.
    own_preview = studio.replace_model("town", first_id, "Preview first instance", model())
    assert not own_preview["gameExportable"] and native_key not in studio._state("town")["assetEdits"]
    assert studio.pending_count("town") == 0
    assert set(studio._state("town")["modelOverrides"]) == {first_id, alias_id}
    studio.undo("town")
    assert studio._state("town")["assetEdits"][native_key] == native_edit
    assert row(studio, first_id)["positions"][3] == 12 and len(row(studio, alias_id)["indices"]) == 3
    studio.redo("town")
    assert native_key not in studio._state("town")["assetEdits"]
    assert studio.pending_count("town") == 0
    assert studio._source_path("town").read_bytes() == original_source


def test_native_model_requires_explicit_coordinates(studio):
    mesh = studio.get_scene("town")["meshes"][0]
    result = studio.replace_model("town", mesh["id"], "Ambiguous", {
        "positions": mesh["positions"], "indices": mesh["indices"]})
    assert not result["gameExportable"]
    assert "explicite" in result["warning"]
    assert studio.pending_count() == 0


def test_world_model_replacement_converts_placement_before_native_write(studio, monkeypatch):
    from scene_graph import local_matrix, transform_point, transform_normals
    matrix = local_matrix([100, -20, 5], [0, 0, math.pi / 2], [2, 2, 2])
    parse = studio._parse_scene

    def placed(data, level):
        scene = parse(data, level)
        mesh = scene["meshes"][0]
        mesh["positions"] = [v for i in range(0, len(mesh["positions"]), 3)
                             for v in transform_point(matrix, mesh["positions"][i:i + 3])]
        mesh["worldMatrix"] = matrix
        mesh["coordinateSpace"] = "world"
        return scene

    monkeypatch.setattr(studio, "_parse_scene", placed)
    mesh = studio.get_scene("town")["meshes"][0]
    world_positions = mesh["positions"][:]
    # One local +X unit becomes two world +Y units with this placement.
    world_positions[4] += 2
    world_normals, _ = transform_normals(matrix, [1, 0, 0] * 3)
    result = studio.replace_model("town", mesh["id"], "World geometry", {
        "positions": world_positions, "indices": mesh["indices"],
        "normals": world_normals, "coordinateSpace": "world"})
    assert result["gameExportable"]
    assert row(studio, mesh["id"])["positions"] == pytest.approx(world_positions)
    exported = studio.export()
    payload = (Path(exported["directory"]) / "gamedata/town.xbr").read_bytes()
    local = parse_level(payload, "fixture", studio.texture_dir, assemble_scene=False)["meshes"][0]
    assert local["positions"][3] == 12
    assert local["normals"] == pytest.approx([1, 0, 0] * 3)


def test_native_mesh_rejects_vertex_stream_outside_resource(studio):
    data = bytearray(studio._source_path("town").read_bytes())
    section = read_sections(data)[2]
    struct.pack_into("<I", data, section.offset + 24, 8)
    with pytest.raises(ValueError, match="dépasse"):
        mesh_plan(data, section, None, model())


def test_asset_undo_save_failure_restores_memory_and_history(studio, monkeypatch):
    imported = studio.import_model("town", "Keep me", model())["id"]
    before = copy.deepcopy(studio._state("town"))
    def failed_save():
        raise OSError("Disk full")
    monkeypatch.setattr(studio, "save", failed_save)
    with pytest.raises(OSError, match="Disk full"):
        studio.undo("town")
    assert studio._state("town") == before
    assert row(studio, imported)["name"] == "Keep me"


def test_imported_model_rotation_also_rotates_world_normals(studio):
    imported = studio.import_model("town", "Lit model", model() | {"normals": [1, 0, 0] * 3})["id"]
    studio.transform("town", imported, rotation=[0, 0, math.pi / 2])
    assert row(studio, imported)["normals"] == pytest.approx([0, 1, 0] * 3, abs=1e-7)
    studio.undo("town")
    assert row(studio, imported)["normals"] == pytest.approx([1, 0, 0] * 3)


def test_import_validation_hash_tampering_unknown_refs_and_nonfinite(studio):
    with pytest.raises(ValueError):
        studio.import_model("town", "Bad", model() | {"positions": [float("nan")] * 9})
    with pytest.raises(ValueError):
        studio.import_model("town", "Bad", model() | {"textureId": "absent"})
    with pytest.raises(ValueError):
        studio.import_texture("town", "Bad", "data:image/png;base64,not base64")
    identifier = studio.import_model("town", "Good", model())["id"]
    asset = studio._state("town")["models"][identifier]["asset"]
    (studio.project_dir / "imports" / asset).write_bytes(b"{}")
    studio._invalidate_asset_scene("town")
    with pytest.raises(ValueError, match="changé"):
        studio.get_scene("town")
    with pytest.raises(ValueError):
        studio._read_asset("../../dump.xbr")


@pytest.mark.parametrize("format_flag,fmt,alpha", [(0x20, "DXT1", 255), (0x40, "DXT3", 136)])
def test_dxt_replacement_roundtrips_full_chain_without_touching_header(format_flag, fmt, alpha):
    # 8x8, 4x4, 2x2, 1x1 compressed blocks, standard native mip layout.
    unit = 8 if fmt == "DXT1" else 16
    size = 128 + (4 + 1 + 1 + 1) * unit
    data = bytearray(size)
    struct.pack_into("<I", data, 0, 0x10000000 | format_flag)
    struct.pack_into("<2f", data, 28, 8, 8)
    struct.pack_into("<i", data, 40, 88)
    section = Section(0, "surf", 0, size, 128)
    image = Image.new("RGBA", (8, 8), (255, 0, 0, alpha))
    patches = texture_plan(bytes(data), section, image)
    assert len(patches) == 4
    payload = bytearray(data)
    for offset, replacement in patches:
        payload[offset:offset + len(replacement)] = replacement
    decoded, decoded_format, _ = decode_texture(bytes(payload), section)
    assert decoded_format == fmt
    assert decoded.getpixel((0, 0)) == (255, 0, 0, alpha)
    assert payload[:128] == data[:128]
