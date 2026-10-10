import copy
import struct

import pytest

from renderer_parser import Section
from static_scene import read_static_descriptors, resolve_static_scene


def fixture():
    data = bytearray(512)
    # Resource offsets are deliberately separate: LEVL links a vertex pool
    # and GPU stream rather than borrowing indices from an adjacent asset.
    sections = [Section(0, "rdms", 0, 64, 0), Section(1, "pbrc", 64, 64, 0),
                Section(2, "levl", 160, 352, 0)]
    base, descriptor = 160, 228
    struct.pack_into("<I", data, base + 28, 2)  # cells
    struct.pack_into("<Ii", data, base + 44, 1, 20)  # count, table +68
    struct.pack_into("<I6fI", data, descriptor, 1, 101., 203., 7., 102., 204., 7., 5)
    struct.pack_into("<Ii", data, descriptor + 32, 1, 320 - (descriptor + 36))
    struct.pack_into("<II", data, descriptor + 40, 0, 1)
    struct.pack_into("<Ii", data, descriptor + 48, 1, 324 - (descriptor + 52))
    struct.pack_into("<Ii", data, descriptor + 64, 2, 348 - (descriptor + 68))
    struct.pack_into("<H", data, 320, 1)
    struct.pack_into("<6f", data, 324, 101., 203., 7., 102., 204., 7.)
    struct.pack_into("<II4sf", data, 348, 0, 1, b"pbrc", 25.)
    struct.pack_into("<II4sf", data, 364, 0, 1, b"pbrc", 100.)
    # One triangle packet; vertex 0 is deliberately unreferenced.
    struct.pack_into("<i", data, 68, 4)
    packets = [(0x17fc, [5]), (0x1808, [3, 1, 2]), (0x17fc, [0])]
    offset = 72
    for method, values in packets:
        struct.pack_into("<I", data, offset, method | len(values) << 18)
        struct.pack_into(f"<{len(values)}I", data, offset + 4, *values)
        offset += 4 * (len(values) + 1)
    pool = {"resourceIndex": 0, "sourceOffset": 0, "origin": [101., 203., 7.],
            "positions": [900., 900., 900., 101., 203., 7., 102., 203., 7., 101., 204., 7.],
            "colors": [0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 1.],
            "uvs": [9., 9., 0., 0., 1., 0., 0., 1.],
            "uv2": [99., 99., 10., 20., 30., 40., 50., 60.], "uvSetCount": 2,
            "vertexAlphas": [0., .25, .5, 1.],
            "textureStages": [{"stage": 0, "textureId": "surf-0009"}],
            "material": {"opacity": .75}, "materialValid": True}
    return data, sections, pool


def test_static_geometry_uses_identity_world_coordinates_and_explicit_gpu_links():
    data, sections, pool = fixture()
    before = bytes(data)
    original = copy.deepcopy(pool)
    result = resolve_static_scene(data, [pool], sections)
    assert bytes(data) == before and pool == original
    assert result["stats"]["staticPlacementFailures"] == {}
    assert result["stats"]["staticDescriptorCount"] == 1
    assert result["stats"]["staticBindingCount"] == 2
    assert result["stats"]["staticLodAlternatives"] == 1
    mesh, = result["meshes"]
    assert mesh["positions"] == [101., 203., 7., 102., 203., 7., 101., 204., 7.]
    assert mesh["indices"] == [2, 0, 1]
    assert mesh["uv2"] == [10., 20., 30., 40., 50., 60.]
    assert mesh["vertexAlphas"] == [.25, .5, 1.]
    assert mesh["parts"] == [{"start": 0, "count": 3, "partIndex": 0, "textureId": "surf-0009"}]
    assert mesh["position"] == [101.5, 203.5, 7.]
    assert mesh["cellIndices"] == [1]
    assert mesh["lodMaxDistance"] == 25.
    assert mesh["coordinateSpace"] == "world" and mesh["worldPositionVerified"]
    assert mesh["staticGeometry"] and mesh["sceneRole"] == "static"
    assert not mesh["editable"] and "nodeIndex" not in mesh and "editBinding" not in mesh


@pytest.mark.parametrize("offset,fmt,value", [
    (208, "i", 1000),  # descriptor table pointer past resource
    (292, "I", 0xFFFFFFFF),  # binding count past resource
    (320, "H", 2),  # invalid cell ID
    (256, "I", 7),  # unsupported descriptor type
    (356, "4s", b"body"),  # tag disagrees with its primitive resource
    (352, "I", 0),  # primitive points at rdms instead of pbrc
    (260, "I", 0xFFFFFFFF),  # cell count extent
    (284, "I", 1),  # malformed nonempty lighting array pointer
])
def test_malformed_static_tables_never_invent_resource_bindings(offset, fmt, value):
    data, sections, pool = fixture()
    struct.pack_into("<" + fmt, data, offset, value)
    with pytest.raises(ValueError):
        read_static_descriptors(data, sections)


def test_static_world_bounds_reject_an_unproven_scale_or_translation():
    data, sections, pool = fixture()
    pool["positions"][3] += 100
    result = resolve_static_scene(data, [pool], sections)
    assert not result["meshes"]
    assert result["stats"]["staticPlacementFailures"] == {"géométrie statique hors de ses bornes monde": 1}


def test_source_lod_order_is_preserved_and_invalid_order_is_reported():
    data, sections, pool = fixture()
    struct.pack_into("<f", data, 360, 200.)
    result = resolve_static_scene(data, [pool], sections)
    assert not result["meshes"]
    assert result["stats"]["staticPlacementFailures"] == {"distances statiques LOD non ordonnées": 1}


def test_asset_archives_and_empty_static_tables_are_not_dereferenced():
    data, sections, pool = fixture()
    assert read_static_descriptors(data, sections[:2])["descriptors"] == []
    struct.pack_into("<Ii", data, 204, 0, -99999)
    assert read_static_descriptors(data, sections)["descriptors"] == []


def test_lighting_assignments_preserve_verified_arrays_without_moving_geometry():
    data, sections, pool = fixture()
    descriptor = 228
    struct.pack_into("<Ii", data, descriptor + 40, 1, 380 - (descriptor + 44))
    struct.pack_into("<I", data, 380, 2)
    struct.pack_into("<Ii", data, descriptor + 56, 1, 384 - (descriptor + 60))
    struct.pack_into("<IIi", data, 384, 4, 2, 404 - 392)
    struct.pack_into("<2h", data, 404, -1, 7)
    table = read_static_descriptors(data, sections)
    entry, = table["descriptors"]
    assert entry["lightReferences"] == [2]
    assert entry["partLightReferences"] == [{"encodedReference": 4, "indices": [-1, 7], "sourceOffset": 384}]
    result = resolve_static_scene(data, [pool], sections)
    assert result["meshes"][0]["positions"] == pool["positions"][3:]
    assert "lumières du moteur Xbox" in result["warnings"][-1]
