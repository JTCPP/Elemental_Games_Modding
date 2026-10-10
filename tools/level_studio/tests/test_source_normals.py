"""Source-normal decoding, vertex correspondence and affine edit behavior."""
import copy
import math
from pathlib import Path
import struct

import pytest

from asset_library import AssetLibrary
from renderer_parser import Section, decode_cmp_normal, decode_mesh, read_sections
from scene_graph import local_matrix, normal_attributes, resolve_scene, transform_normals, transform_vector
from static_scene import resolve_static_scene
from test_scene_graph import graph_fixture
from test_static_scene import fixture as static_fixture
from test_transform_backend import GraphBackend, find, transform_fixture


def packed(x, y, z):
    return (x & 0x7FF) | ((y & 0x7FF) << 11) | ((z & 0x3FF) << 22)


@pytest.mark.parametrize("xyz,expected", [
    ((1023, 0, 0), [1., 0., 0.]),
    ((0, -1023, 511), [0., -1., 1.]),
    ((-1024, -1024, -512), [-1024 / 1023, -1024 / 1023, -512 / 511]),
    ((511, -256, 127), [511 / 1023, -256 / 1023, 127 / 511]),
])
def test_cmp_signed_fields_match_xemu_buffer_formula(xyz, expected):
    assert decode_cmp_normal(packed(*xyz)) == expected


@pytest.mark.parametrize("declaration,stride", [(0x80001111, 16), (0x80002111, 18),
                                               (0x80004111, 22), (0x80040111, 30)])
def test_packed_normal_word_is_at_byte_six_for_every_supported_declaration(declaration, stride):
    size = (64 + 3 * stride + 3) & ~3
    data = bytearray(size)
    struct.pack_into("<8I3f5I", data, 0, 5, 0, 0, 1, 0, 0, 3, 36,
                     0, 0, 0, declaration, 0, size - 52, 0, 0)
    words = [packed(1023, 0, 0), packed(0, -1023, 511), packed(-1024, 0, -512)]
    for i, word in enumerate(words):
        struct.pack_into("<3hI", data, 64 + i * stride, i * 256, 0, 0, word)
        data[64 + i * stride + 10:64 + i * stride + 14] = bytes((10, 20, 30, 40))
    before = bytes(data)
    result = decode_mesh(data, Section(0, "rdms", 0, size, 16), {}, {})
    assert bytes(data) == before
    assert result["sourceNormals"] == [value for word in words for value in decode_cmp_normal(word)]
    assert result["normals"] == result["sourceNormals"]
    assert result["packedNormals"] == list(b"".join(struct.pack("<I", word) for word in words))
    assert result["normalFormat"] == "CMP-S11-S11-S10" and result["normalSpace"] == "local"
    assert result["colors"] == pytest.approx([30 / 255, 20 / 255, 10 / 255] * 3)


def test_float44_normal_keeps_raw_float3_at_byte_twelve():
    size = 64 + 3 * 44
    data = bytearray(size)
    struct.pack_into("<8I3f5I", data, 0, 5, 0, 0, 1, 0, 0, 3, 36,
                     0, 0, 0, 0x252, 0, size - 52, 0, 0)
    values = [.25, -.5, .75, 2., 0., 0., 0., 0., 0.]
    for i in range(3):
        struct.pack_into("<3f", data, 64 + i * 44 + 12, *values[i * 3:i * 3 + 3])
    mesh = decode_mesh(data, Section(0, "rdms", 0, size, 16), {}, {})
    assert mesh["sourceNormals"] == values and mesh["normals"] == values
    assert mesh["packedNormals"] == [] and mesh["normalFormat"] == "float3"
    struct.pack_into("<f", data, 64 + 12, math.nan)
    with pytest.raises(ValueError, match="sommet"):
        decode_mesh(data, Section(0, "rdms", 0, size, 16), {}, {})


def test_inverse_transpose_preserves_orthogonality_and_ignores_translation():
    matrix = local_matrix([10, 20, 30], [0, 0, math.pi / 2], [2, 4, 1])
    normals, verified = transform_normals(matrix, [1, 1, 0])
    assert verified and normals == pytest.approx([-.25, .5, 0], abs=1e-12)
    tangent = transform_vector(matrix, [1, -1, 0])
    assert sum(n * t for n, t in zip(normals, tangent)) == pytest.approx(0, abs=1e-12)
    moved = local_matrix([-90, 5, 400], [0, 0, math.pi / 2], [2, 4, 1])
    assert transform_normals(moved, [1, 1, 0]) == (normals, True)
    # No automatic normalization or winding compensation is introduced.
    reflected = local_matrix([0, 0, 0], [0, 0, 0], [-2, 3, 4])
    assert transform_normals(reflected, [2, 0, 0]) == ([-1, 0, 0], True)


def normal_pool(pool):
    pool["sourceNormals"] = [1., -1., .5] * 3 + [99., 98., 97.]
    pool["normals"] = pool["sourceNormals"][:]
    pool["packedNormals"] = list(range(16))
    pool["normalFormat"] = "CMP-S11-S11-S10"
    return pool


def test_graph_and_library_remap_source_normals_under_nonuniform_parent():
    data, sections, pool = graph_fixture()
    normal_pool(pool)
    struct.pack_into("<3f", data, 644 + 28, 2, 3, 4)
    original = copy.deepcopy(pool)
    result = resolve_scene(data, [pool], sections)
    for mesh in result["meshes"]:
        assert mesh["sourceNormals"] == pool["sourceNormals"][:9]
        assert mesh["packedNormals"] == list(range(12))
        assert mesh["normalTransformVerified"] and mesh["normalSpace"] == "world"
        tangent = transform_vector(mesh["worldMatrix"], [1, 1, 0])
        assert sum(n * t for n, t in zip(mesh["normals"][:3], tangent)) == pytest.approx(0, abs=1e-6)
    assert pool == original


def test_static_remap_keeps_identity_normals_and_drops_unreferenced_vertex():
    data, sections, pool = static_fixture()
    normal_pool(pool)
    mesh, = resolve_static_scene(data, [pool], sections)["meshes"]
    assert mesh["sourceNormals"] == pool["sourceNormals"][3:]
    assert mesh["normals"] == mesh["sourceNormals"]
    assert mesh["packedNormals"] == list(range(4, 16))
    assert mesh["normalSpace"] == "world" and mesh["normalTransformVerified"]


def test_library_preview_uses_same_source_correspondence_and_singular_fallback(tmp_path, monkeypatch):
    data, sections, pool = graph_fixture()
    normal_pool(pool)
    pool["name"] = "Source"
    monkeypatch.setattr("asset_library.decode_mesh", lambda *_: pool)
    library = AssetLibrary(tmp_path, tmp_path / "textures")
    value = {"data": data, "sections": sections, "shaders": {}, "references": {}, "pools": {}}
    refs = [{"meshResource": 1, "primitiveResource": 2}]
    matrix = local_matrix([10, 20, 30], [.4, -.3, .6], [2, 3, 4])
    mesh, = library._static_parts(value, refs, matrix)
    assert mesh["sourceNormals"] == pool["sourceNormals"][:9]
    assert mesh["normals"] == pytest.approx(transform_normals(matrix, mesh["sourceNormals"])[0])
    singular = local_matrix([0, 0, 0], [.4, -.3, .6], [0, 3, 4])
    attrs = normal_attributes(pool, [0, 2], singular)
    assert attrs["normals"] == attrs["sourceNormals"] == pool["sourceNormals"][:6]
    assert attrs["normalSpace"] == "local-fallback" and not attrs["normalTransformVerified"]
    assert attrs["packedNormals"] == list(range(4)) + list(range(8, 12))


class NormalBackend(GraphBackend):
    def _parse_scene(self, data, level):
        _, pool = transform_fixture()
        normal_pool(pool)
        return resolve_scene(data, [pool], read_sections(data))


@pytest.mark.parametrize("zero_scale", [False, True])
def test_backend_normals_recompose_with_descendants_export_undo_and_source_preservation(tmp_path, zero_scale):
    source = tmp_path / "source" / "gamedata"
    source.mkdir(parents=True)
    original, _ = transform_fixture(zero_child_scale=zero_scale)
    (source / "training_room.xbr").write_bytes(original)
    backend = NormalBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    first = backend.get_scene("training_room")
    parent = find(first, "ruby")
    changed = backend.transform("training_room", parent["id"], rotation=[.4, -.2, .7], scale=[3, .5, -2])["scene"]
    for mesh in changed["meshes"]:
        assert mesh["sourceNormals"] == first["meshes"][0]["sourceNormals"]
        if zero_scale:
            assert not mesh["normalTransformVerified"] and mesh["normalSpace"] == "local-fallback"
            assert mesh["normals"] == mesh["sourceNormals"]
        else:
            tangent = transform_vector(mesh["worldMatrix"], [1, 1, 0])
            assert sum(n * t for n, t in zip(mesh["normals"][:3], tangent)) == pytest.approx(0, abs=1e-5)
            assert mesh["normals"] != first["meshes"][0]["normals"]
    exported = backend.export()
    reread = NormalBackend(Path(exported["directory"]), tmp_path / "reread-project", tmp_path / "reread-exports", texture_dir=tmp_path / "textures").get_scene("training_room")
    assert reread["meshes"][0]["normals"] == pytest.approx(changed["meshes"][0]["normals"], abs=1e-5)
    backend.undo("training_room")
    assert backend.get_scene("training_room")["meshes"][0]["normals"] == first["meshes"][0]["normals"]
    backend.redo("training_room")
    assert backend.get_scene("training_room")["meshes"][0]["normals"] == changed["meshes"][0]["normals"]
    assert (source / "training_room.xbr").read_bytes() == original
