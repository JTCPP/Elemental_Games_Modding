import math
import struct
from pathlib import Path

import pytest

from editor_backend import StudioBackend
from renderer_parser import Section, read_sections
from scene_graph import read_graph, resolve_scene, inverse_affine, transform_point


def lod_fixture():
    data = bytearray(3000)
    records = (128, 256, 480, 608, 736, 832)
    kinds = ("transform", "lod", "transform", "transform", "platform", "platform")
    parents = (-1, 0, 1, 1, 2, 3)
    params = (1200, 1244, 1340, 1384, 1428, 1432)
    struct.pack_into("<Ii", data, 0, len(records), 28)
    for index, (record, kind, parent, parameter) in enumerate(zip(records, kinds, parents, params)):
        field = 32 + index * 4
        struct.pack_into("<i", data, field, record - field)
        name = 1500 + index * 30
        data[name:name + len(kind)] = kind.encode()
        count = 21 if kind == "lod" else 10 if kind == "transform" else 1
        struct.pack_into("<IiIi", data, record, len(kind), name - record - 4, count, parameter - record - 12)
        struct.pack_into("<i", data, record + 24, parent)
        if kind in ("transform", "lod"):
            values = [1, 10, 20, 30, 0, 0, 0, 1, 1, 1] if index == 0 else [1, 3, 4, 5, 0, 0, math.pi / 2, 2, 2, 2] if index == 1 else [0, 1, 0, 0, 0, 0, 0, 1, 1, 1] if index == 2 else [1, 0, 1, 0, 0, 0, 0, 1, 1, 1]
            struct.pack_into("<10f", data, parameter, *values)
            struct.pack_into("<4f", data, record + 92, 0, 0, 0, 1)
        else:
            struct.pack_into("<f", data, parameter, 1)
            struct.pack_into("<Ii", data, record + 28, 1, 1700 - record - 32)
    struct.pack_into("<3f", data, records[1] + 68, 1, 0, 0)
    struct.pack_into("<9f", data, records[1] + 120, 60, *([0] * 8))
    struct.pack_into("<Ii", data, records[1] + 16, 2, 2000 - records[1] - 20)
    for index, target in enumerate((2, 3)):
        struct.pack_into("<fifIII", data, 2000 + index * 24, 1, 1, 1, 11 + index, 0, target)
    struct.pack_into("<I", data, 1700 + 28, 5)
    struct.pack_into("<Ii", data, 1700 + 32, 1, 1772 - 1700 - 36)
    struct.pack_into("<Ii", data, 1700 + 64, 1, 1800 - 1700 - 68)
    struct.pack_into("<II4sf", data, 1800, 1, 2, b"pbrc", 100)
    struct.pack_into("<Ii", data, 2900, 1, 28)
    stream = [(1 << 18) | 0x17fc, 6, (1 << 18) | 0x1800, 1 << 16,
              (1 << 18) | 0x1808, 2, (1 << 18) | 0x17fc, 0]
    struct.pack_into("<8I", data, 2932, *stream)
    sections = [Section(0, "node", 0, 2800, 8), Section(1, "rdms", 0, 64, 16), Section(2, "pbrc", 2900, 64, 8)]
    pool = {"resourceIndex": 1, "sourceOffset": 100, "origin": [0, 0, 0],
            "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "colors": [1, 1, 1] * 3,
            "uvs": [0, 0, 1, 0, 0, 1], "textureStages": []}
    return data, sections, pool


def test_lod_inherits_serialized_trs_pivots_and_descendant_edit_binding():
    data, sections, pool = lod_fixture()
    scene = resolve_scene(data, [pool], sections)
    close = next(mesh for mesh in scene["meshes"] if mesh["nodeIndex"] == 4)
    assert close["positions"][:3] == pytest.approx([14, 25, 35], abs=1e-5)
    assert close["positions"][3:6] == pytest.approx([14, 27, 35], abs=1e-5)
    lod = scene["nodes"][1]
    assert lod["rotatePivot"] == [1, 0, 0]
    assert lod["position"] == [3, 4, 5]
    assert close["editBinding"]["transformNode"] == 2
    assert close["editBinding"]["parentWorldMatrix"] == lod["worldMatrix"]
    assert transform_point(close["editBinding"]["parentWorldInverse"], [14, 25, 35]) == pytest.approx([1, 0, 0], abs=1e-5)


def test_near_preview_uses_actual_links_and_preserves_authored_visibility_and_bytes():
    data, sections, pool = lod_fixture()
    original = bytes(data)
    scene = resolve_scene(data, [pool], sections)
    close, far = scene["meshes"]
    assert close["visible"] and not close["authoredVisible"]
    assert not far["visible"] and far["authoredVisible"]
    assert scene["nodes"][2]["visibility"] == 0
    assert scene["nodes"][3]["visibility"] == 1
    assert scene["nodes"][1]["lodThresholds"] == [60] + [0] * 8
    assert close["lodSelections"][0]["branchNode"] == 2
    assert scene["stats"]["verifiedLodCount"] == 1
    assert scene["stats"]["lodRecoveredMeshCount"] == 1
    assert scene["stats"]["editorVisibleMeshCount"] == 1
    assert bytes(data) == original


def test_unknown_curved_lod_link_does_not_activate_any_branch():
    data, sections, pool = lod_fixture()
    struct.pack_into("<i", data, 2004, 64)
    scene = resolve_scene(data, [pool], sections)
    close, far = scene["meshes"]
    assert not close["visible"] and far["visible"]
    assert not scene["nodes"][1]["lodBindingsVerified"]
    assert "courbe" in scene["nodes"][1]["lodBindingWarning"]
    assert scene["stats"]["lodRecoveredMeshCount"] == 0


def test_lod_must_have_complete_inherited_parameters_and_threshold_record():
    data, sections, _ = lod_fixture()
    struct.pack_into("<I", data, 256 + 8, 10)
    with pytest.raises(ValueError, match="paramètres"):
        read_graph(data, sections)


class LodBackend(StudioBackend):
    def _parse_scene(self, data, level):
        _, _, pool = lod_fixture()
        pool["sourceOffset"] = 3056
        return resolve_scene(data, [pool], read_sections(data))


@pytest.mark.parametrize("inherit_mode,rotation_order", [(0, 0), (1, 0), (2, 0), (0, 3), (1, 3), (2, 3)])
def test_existing_ids_persist_and_trs_export_uses_corrected_lod_parent(tmp_path, inherit_mode, rotation_order):
    graph, _, _ = lod_fixture()
    struct.pack_into("<I", graph, 480 + 40, inherit_mode)
    struct.pack_into("<I", graph, 480 + 108, rotation_order)
    struct.pack_into("<3f", graph, 1244 + 16, 0.2, -0.3, 0.4)
    original = bytearray(256) + graph
    original[:4] = b"xobx"
    struct.pack_into("<I", original, 4, 4)
    struct.pack_into("<II", original, 12, 3, 256)
    for index, (size, tag, end) in enumerate(((2800, b"node", 2800), (64, b"rdms", 2900), (64, b"pbrc", 2964))):
        struct.pack_into("<I4sII", original, 64 + 16 * index, size, tag, 8, end)
    original = bytes(original)
    source = tmp_path / "source" / "gamedata"
    source.mkdir(parents=True)
    path = source / "town.xbr"
    path.write_bytes(original)
    backend = LodBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    before = backend.get_scene("town")
    close = next(mesh for mesh in before["meshes"] if mesh["nodeIndex"] == 4)
    ids = [mesh["id"] for mesh in before["meshes"]]
    target = [30, 40, 50]
    backend.transform("town", close["id"], position=target, rotation=[0.2, -0.3, 0.4], scale=[1.5, 2, 0.5])
    preview = backend.get_scene("town")
    reopened = LodBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    restored = reopened.get_scene("town")
    assert [mesh["id"] for mesh in restored["meshes"]] == ids
    assert restored["meshes"][0]["positions"] == pytest.approx(preview["meshes"][0]["positions"], abs=1e-5)
    assert restored["meshes"][0]["position"] == pytest.approx(target, abs=1e-5)
    exported = reopened.export()
    payload = (Path(exported["path"]) / "gamedata" / "town.xbr").read_bytes()
    reread = reopened._parse_scene(payload, "town")
    assert reread["meshes"][0]["positions"] == pytest.approx(preview["meshes"][0]["positions"], abs=1e-5)
    assert reread["meshes"][1]["positions"] == before["meshes"][1]["positions"]
    binding = close["editBinding"]
    channels = (binding["editOffset"], binding["rotationOffset"], binding["scaleOffset"])
    assert all(a == b for offset, (a, b) in enumerate(zip(original, payload)) if not any(start <= offset < start + 12 for start in channels))
    assert path.read_bytes() == original


def test_camera_oriented_effect_is_visible_but_not_editable_as_a_static_center():
    graph, sections, pool = lod_fixture()
    struct.pack_into("<I", graph, 480 + 40, 3)
    scene = resolve_scene(graph, [pool], sections)
    close = scene["meshes"][0]
    assert close["visible"] and close["cameraDependent"]
    assert not close["editable"]
    assert scene["nodes"][2]["worldPositionVerified"]
