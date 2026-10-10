import copy
import struct

import pytest

from environment_scene import build_environment, read_environment_header
from renderer_parser import Section


def fixture_environment():
    data = bytearray(4096)
    sections = [Section(0, "node", 256, 1408, 8), Section(1, "levl", 2048, 1024, 8),
                Section(2, "rdms", 3328, 64, 8), Section(3, "pbrc", 3456, 64, 128)]
    base, cells = 2048, 2112
    struct.pack_into("<3f", data, base + 12, 120, 230, 340)
    struct.pack_into("<HIi", data, base + 26, 1, 2, cells - base - 32)
    text = cells + 144
    for index, name in enumerate(("worldCell", "Sky_namespace:skyCellShape")):
        record = cells + index * 72
        struct.pack_into("<Ii", data, record, len(name), text - record - 4)
        data[text:text + len(name) + 1] = name.encode() + b"\0"
        text += len(name) + 1
    def platform(record, descriptor, cell):
        struct.pack_into("<Ii", data, record + 28, 1, descriptor - record - 32)
        struct.pack_into("<IIi", data, descriptor + 28, 5, 1, 36)
        struct.pack_into("<H", data, descriptor + 72, cell)
        struct.pack_into("<Ii", data, descriptor + 64, 1, 8)
        struct.pack_into("<II4sf", data, descriptor + 76, 2, 3, b"pbrc", 1000)
    platform(288, 512, 1)
    platform(392, 640, 1)
    platform(440, 768, 0)
    nodes = [{"index": 1, "type": "transform", "name": "Sky_namespace:dayGroup", "parent": -1},
             {"index": 2, "type": "platform", "name": "arbitraryShape", "parent": 1, "recordOffset": 288},
             {"index": 3, "type": "transform", "name": "Sky_namespace:nightGroup", "parent": -1},
             {"index": 4, "type": "platform", "name": "StarfieldShape", "parent": 3, "recordOffset": 392},
             {"index": 6, "type": "platform", "name": "Sky_namespace:day_but_world_cell", "parent": -1, "recordOffset": 440}]
    meshes = [{"id": "placed-day", "nodeIndex": 2, "resourceIndex": 2, "primitiveResource": 3},
              {"id": "placed-night", "nodeIndex": 4, "resourceIndex": 2, "primitiveResource": 3},
              {"id": "placed-world", "nodeIndex": 6, "resourceIndex": 2, "primitiveResource": 3},
              {"id": "unplaced-pool", "resourceIndex": 2, "primitiveResource": 3}]
    return data, sections, nodes, meshes


def test_sky_anchor_and_cell_membership_are_source_driven_not_name_or_resource_guesses():
    data, sections, nodes, meshes = fixture_environment()
    original = bytes(data), copy.deepcopy(nodes), copy.deepcopy(meshes)
    result = build_environment(bytes(data), sections, nodes, meshes)
    sky = result["sky"]
    assert sky["anchor"] == pytest.approx([1.2, 2.3, 3.4])
    assert sky["skyIndex"] == 1
    assert sky["cellName"] == "Sky_namespace:skyCellShape"
    assert sky["sourceVerified"] is True
    assert sky["classificationVerified"] is True
    assert sky["meshIds"] == ["placed-day", "placed-night"]
    assert sky["defaultVariant"] == "day"
    variants = {variant["id"]: variant for variant in sky["variants"]}
    assert variants["day"]["meshIds"] == ["placed-day"]
    assert variants["night"]["meshIds"] == ["placed-night"]
    assert variants["all"]["meshIds"] == ["placed-day", "placed-night"]
    assert sky["animationStateVerified"] is False
    assert result["warnings"] == []
    assert original == (bytes(data), nodes, meshes)


def test_absent_sky_sentinel_does_not_infer_from_sky_node_names():
    data, sections, nodes, meshes = fixture_environment()
    struct.pack_into("<H", data, 2048 + 26, 0xFFFF)
    sky = build_environment(bytes(data), sections, nodes, meshes)["sky"]
    assert sky["available"] is False
    assert sky["sourceVerified"] is True
    assert sky["meshIds"] == []


@pytest.mark.parametrize("damage", ["nan", "wrong_sky_index", "escaped_cell_table", "escaped_name", "wrong_name_length"])
def test_invalid_levl_header_is_rejected_and_nonfatal_to_scene(damage):
    data, sections, nodes, meshes = fixture_environment()
    if damage == "nan":
        struct.pack_into("<f", data, 2048 + 12, float("nan"))
    elif damage == "wrong_sky_index":
        struct.pack_into("<H", data, 2048 + 26, 100)
    elif damage == "escaped_cell_table":
        struct.pack_into("<i", data, 2048 + 32, 4096)
    elif damage == "escaped_name":
        struct.pack_into("<i", data, 2112 + 4, 4096)
    elif damage == "wrong_name_length":
        struct.pack_into("<I", data, 2112, 1)
    with pytest.raises(ValueError):
        read_environment_header(bytes(data), sections)
    result = build_environment(bytes(data), sections, nodes, meshes)
    assert result["sky"]["available"] is False
    assert result["sky"]["sourceVerified"] is False
    assert result["warnings"]


def test_invalid_platform_cell_is_omitted_with_warning_without_false_sky_classification():
    data, sections, nodes, meshes = fixture_environment()
    struct.pack_into("<H", data, 512 + 72, 999)
    result = build_environment(bytes(data), sections, nodes, meshes)
    assert result["sky"]["meshIds"] == ["placed-night"]
    assert result["sky"]["classificationVerified"] is False
    assert result["warnings"]


def test_common_source_cell_mesh_is_included_in_both_explicit_previews():
    data, sections, nodes, meshes = fixture_environment()
    nodes[1]["parent"] = -1
    sky = build_environment(bytes(data), sections, nodes, meshes)["sky"]
    variants = {variant["id"]: variant for variant in sky["variants"]}
    assert variants["night"]["meshIds"] == ["placed-night", "placed-day"]
    assert "day" not in variants
    assert sky["defaultVariant"] == "all"


def fixture_static_environment():
    data, sections, nodes, meshes = fixture_environment()
    base, table, cells, pairs = 2048, 2368, 2512, 2520
    struct.pack_into("<Ii", data, base + 44, 2, table - base - 48)
    for index, cell in enumerate((1, 0)):
        descriptor = table + index * 72
        struct.pack_into("<I", data, descriptor + 28, 5)
        struct.pack_into("<Ii", data, descriptor + 32, 1, cells + index * 2 - descriptor - 36)
        struct.pack_into("<H", data, cells + index * 2, cell)
        binding = pairs + index * 16
        struct.pack_into("<Ii", data, descriptor + 64, 1, binding - descriptor - 68)
        struct.pack_into("<II4sf", data, binding, 2, 3, b"pbrc", 1000)
        meshes.append({"id": f"static-{index}", "sceneRole": "static",
                       "staticDescriptorIndex": index, "staticDescriptorOffset": descriptor,
                       "levlResource": 1, "cellIndices": [cell], "bindingOffset": binding,
                       "resourceIndex": 2, "primitiveResource": 3, "sourceOffset": 3328})
    return data, sections, nodes, meshes


def test_static_sky_is_verified_against_levl_and_included_as_common_in_both_phases():
    data, sections, nodes, meshes = fixture_static_environment()
    original = bytes(data), copy.deepcopy(meshes)
    result = build_environment(bytes(data), sections, nodes, meshes)
    sky = result["sky"]
    assert sky["meshIds"] == ["placed-day", "placed-night", "static-0"]
    assert sky["staticDescriptorIndices"] == [0]
    assert sky["classificationVerified"] is True
    variants = {row["id"]: row for row in sky["variants"]}
    assert variants["day"]["meshIds"] == ["placed-day", "static-0"]
    assert variants["night"]["meshIds"] == ["placed-night", "static-0"]
    assert original == (bytes(data), meshes)


@pytest.mark.parametrize("damage", ["cell_metadata", "binding", "mesh_origin", "descriptor", "levl"])
def test_forged_static_metadata_never_creates_false_sky_even_with_a_valid_node_key(damage):
    data, sections, nodes, meshes = fixture_static_environment()
    target = meshes[-1]  # It belongs to the world cell, despite a plausible NODE key.
    target["nodeIndex"] = 2
    if damage == "cell_metadata":
        target["cellIndices"] = [1]
    elif damage == "binding":
        target["bindingOffset"] += 1
    elif damage == "mesh_origin":
        target["sourceOffset"] += 4
    elif damage == "descriptor":
        target["staticDescriptorOffset"] += 72
    elif damage == "levl":
        target["levlResource"] = 999
    result = build_environment(bytes(data), sections, nodes, meshes)
    assert "static-1" not in result["sky"]["meshIds"]
    assert result["sky"]["meshIds"] == ["placed-day", "placed-night", "static-0"]
    assert result["sky"]["classificationVerified"] is False
    assert result["warnings"]


def test_corrupt_static_source_table_is_nonfatal_and_preserves_verified_node_sky():
    data, sections, nodes, meshes = fixture_static_environment()
    struct.pack_into("<H", data, 2512, 999)
    result = build_environment(bytes(data), sections, nodes, meshes)
    assert result["sky"]["meshIds"] == ["placed-day", "placed-night"]
    assert result["sky"]["sourceVerified"] is True
    assert result["sky"]["classificationVerified"] is False
    assert result["warnings"]
