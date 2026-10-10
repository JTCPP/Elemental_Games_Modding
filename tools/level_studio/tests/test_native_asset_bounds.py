"""Native vertex edits retain every original LEVL culling volume and LOD."""
import copy
import struct

import pytest

from asset_editing import _validate_static_bounds, mesh_plan
from renderer_parser import Section, read_sections
from test_static_scene import fixture


def archive_fixture():
    """Wrap the existing static descriptor/GPU fixture with a real RDMS pool."""
    source, _, pool = fixture()
    # Use FLOAT3 because the fixture's deliberately unused outlier exceeds
    # packed SHORT3 ranges. The selected static triangle retains its source
    # world coordinates and origin exactly.
    rdms = bytearray(64 + 4 * 44)
    struct.pack_into("<8I3f5I", rdms, 0, 5, 0, 0, 1, 0, 0, 4, 36,
                     *pool["origin"], 0x80040112, 0, len(rdms) - 52, 0, 0)
    # Match the renderer's known 44-byte FLOAT3 declaration.
    from renderer_parser import _DECLARATIONS
    declaration = next(key for key, stride in _DECLARATIONS.items() if stride == 44)
    struct.pack_into("<I", rdms, 44, declaration)
    for vertex in range(4):
        offset = 64 + vertex * 44
        values = [pool["positions"][vertex * 3 + axis] - pool["origin"][axis] for axis in range(3)]
        struct.pack_into("<3f3f", rdms, offset, *values, 0, 0, 1)
        rdms[offset + 24:offset + 28] = bytes((255, 255, 255, 255))
        struct.pack_into("<4f", rdms, offset + 28, 0, 0, 0, 0)
    pbr = bytes(source[64:128])
    levl = bytes(source[160:512])
    # Relative LEVL and GPU pointers remain valid after moving the entire
    # resources; only their archive offsets and cumulative TOC ends change.
    prefix = bytearray(256)
    prefix[:4] = b"xobx"
    struct.pack_into("<I", prefix, 4, 4)
    struct.pack_into("<II", prefix, 12, 3, 256)
    end = 0
    for index, (tag, blob) in enumerate(((b"rdms", rdms), (b"pbrc", pbr), (b"levl", levl))):
        end += len(blob)
        struct.pack_into("<I4sII", prefix, 64 + index * 16, len(blob), tag, 8, end)
    data = bytes(prefix) + bytes(rdms) + pbr + levl
    model = {"positions": pool["positions"][3:], "indices": [2, 0, 1], "coordinateSpace": "asset"}
    return data, read_sections(data), model


def test_native_resize_outside_levl_bounds_is_rejected_and_source_untouched():
    data, sections, model = archive_fixture()
    before = bytes(data)
    model["positions"][3] += 0.5
    with pytest.raises(ValueError, match="bornes statiques LEVL"):
        mesh_plan(data, sections[0], 1, model)
    assert data == before


def test_native_vertex_change_inside_original_culling_volume_is_allowed():
    data, sections, model = archive_fixture()
    model["positions"][3] -= 0.25
    patches = mesh_plan(data, sections[0], 1, model)
    assert patches
    assert all(sections[0].offset + 64 <= offset < offset + len(payload) <= sections[0].offset + sections[0].size
               for offset, payload in patches)
    # No LEVL culling records can enter the writable plan.
    assert all(offset + len(payload) <= sections[2].offset for offset, payload in patches)


def test_out_of_bounds_static_replacement_remains_portable_preview(tmp_path):
    from editor_backend import StudioBackend
    from renderer_parser import parse_level
    from static_scene import resolve_static_scene
    class StaticBackend(StudioBackend):
        def _parse_scene(self, payload, level):
            parsed = parse_level(payload, level, self.texture_dir, assemble_scene=False)
            resolved = resolve_static_scene(payload, parsed["meshes"], read_sections(payload))
            parsed["meshes"] = resolved["meshes"]
            parsed["nodes"] = []
            return parsed
    data, _, model = archive_fixture()
    source = tmp_path / "source/gamedata"
    source.mkdir(parents=True)
    (source / "town.xbr").write_bytes(data)
    backend = StaticBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    mesh = next(m for m in backend.get_scene("town")["meshes"] if m.get("staticGeometry"))
    model["positions"][3] += 0.5
    result = backend.replace_model("town", mesh["id"], "Larger static triangle", model)
    assert not result["gameExportable"]
    assert "bornes statiques LEVL" in result["warning"]
    assert backend.pending_count() == 0 and backend.preview_count() == 1
    assert any(m["id"] == mesh["id"] and m.get("modelReplaced") for m in result["scene"]["meshes"])
    exported = backend.export()
    assert exported["fileCount"] == 0 and exported["editCount"] == 0
    assert (source / "town.xbr").read_bytes() == data


def test_native_shared_pool_checks_other_static_descriptors():
    data, sections, pool = fixture()
    # Add a second descriptor referencing the same pool. Its tighter bounds
    # accept the source, but not a candidate x-coordinate shifted past 101.
    descriptor = bytearray(data[228:300])
    struct.pack_into("<I", data, 204, 2)
    data[300:372] = descriptor
    # Relocate arrays after both 72-byte headers.
    for offset in (228, 300):
        struct.pack_into("<Ii", data, offset + 32, 0, 1)
        struct.pack_into("<Ii", data, offset + 48, 0, 1)
        struct.pack_into("<Ii", data, offset + 64, 1, 400 - (offset + 68))
    struct.pack_into("<II4sf", data, 400, 0, 1, b"pbrc", 25)
    struct.pack_into("<6f", data, 232, 100, 203, 7, 103, 204, 7)
    struct.pack_into("<6f", data, 304, 101, 203, 7, 102, 204, 7)
    candidate = pool["positions"][:]
    candidate[3] = 100.5
    with pytest.raises(ValueError, match="bornes statiques LEVL"):
        _validate_static_bounds(data, sections, 0, candidate)


def test_native_plan_checks_unshown_lod_alternative_using_the_same_pool():
    data, sections, pool = fixture()
    # The highest-detail primitive selects vertices 1..3. A second LOD now
    # selects the deliberately unused outlier vertex 0 from the same pool.
    alternate = bytearray(data[64:128])
    struct.pack_into("<3I", alternate, 20, 0, 1, 2)
    data.extend(alternate)
    sections.append(Section(3, "pbrc", 512, 64, 0))
    struct.pack_into("<I", data, 368, 3)
    with pytest.raises(ValueError, match="bornes statiques LEVL"):
        _validate_static_bounds(data, sections, 0, pool["positions"])


def test_static_tolerance_matches_one_quantization_step_and_relative_float_slack():
    data, sections, pool = fixture()
    epsilon = 1 / 256 + 204 * 1e-6
    allowed = copy.deepcopy(pool["positions"])
    allowed[3] -= epsilon * 0.99
    _validate_static_bounds(data, sections, 0, allowed)
    allowed[3] -= epsilon * 0.02
    with pytest.raises(ValueError, match="bornes statiques LEVL"):
        _validate_static_bounds(data, sections, 0, allowed)
