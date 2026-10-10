import struct

import pytest

from renderer_parser import Section, read_platform_bindings
from scene_graph import read_graph, resolve_scene


def graph_fixture():
    data = bytearray(1800)
    records = [128, 256, 384, 448]
    struct.pack_into("<Ii", data, 0, 4, 28)
    for index, record in enumerate(records):
        struct.pack_into("<i", data, 32 + index * 4, record - (32 + index * 4))
    for index, (record, kind) in enumerate(zip(records, ("transform", "transform", "platform", "critterGenerator"))):
        name = 800 + index * 24
        data[name:name + len(kind)] = kind.encode()
        struct.pack_into("<IiI", data, record, len(kind), name - record - 4, 10 if index < 2 else 0)
        params = 600 + index * 44
        struct.pack_into("<i", data, record + 12, params - record - 12)
        struct.pack_into("<i", data, record + 24, -1 if index == 0 else 0 if index == 1 else 1)
        if index < 2:
            values = [1, 10, 20, 30, 0, 0, 1.5707963267948966, 2, 2, 2] if index == 0 else [1, 1, 0, 0, 0, 0, 0, 1, 1, 1]
            struct.pack_into("<10f", data, params, *values)
            struct.pack_into("<4f", data, record + 92, 0, 0, 0, 1)
    struct.pack_into("<i", data, 384 + 32, 1024 - 384 - 32)
    struct.pack_into("<I", data, 384 + 28, 2)
    struct.pack_into("<Ii", data, 448 + 28, 7, 900 - 448 - 32)
    data[900:907] = b"emerald"
    # Two separate material headers, each with its own explicit pair array.
    for index in range(2):
        descriptor = 1024 + index * 72
        struct.pack_into("<I", data, descriptor + 28, 5)
        struct.pack_into("<Ii", data, descriptor + 32, 1, 1168 + index * 4 - descriptor - 36)
        count = 2 if index == 0 else 1
        pointer = 1200 + index * 32
        struct.pack_into("<Ii", data, descriptor + 64, count, pointer - descriptor - 68)
        struct.pack_into("<II4sf", data, pointer, 1, 2, b"pbrc", 100)
        if count == 2:
            struct.pack_into("<II4sf", data, pointer + 16, 1, 3, b"pbrc", 500)
    for base in (1600, 1664):
        struct.pack_into("<Ii", data, base, 1, 28)
        stream = [(1 << 18) | 0x17fc, 6, (1 << 18) | 0x1800, 1 << 16,
                  (1 << 18) | 0x1808, 2, (1 << 18) | 0x17fc, 0]
        struct.pack_into("<8I", data, base + 32, *stream)
    sections = [Section(0, "node", 0, 1500, 8), Section(1, "rdms", 0, 64, 16),
                Section(2, "pbrc", 1600, 64, 8), Section(3, "pbrc", 1664, 64, 8)]
    pool = {"resourceIndex": 1, "sourceOffset": 100, "origin": [0, 0, 0],
            "positions": [0, 0, 0, 1, 0, 0, 0, 1, 0, 9999, 9999, 9999],
            "colors": [1, 1, 1] * 4, "uvs": [0, 0, 1, 0, 0, 1, 9, 9], "textureStages": []}
    return data, sections, pool


def test_world_placements_preserve_all_materials_and_one_detail_variant():
    data, sections, pool = graph_fixture()
    bindings = read_platform_bindings(data, sections[0], 384, sections)
    assert [b["descriptorIndex"] for b in bindings] == [0, 0, 1]
    scene = resolve_scene(data, [pool], sections)
    assert len(scene["meshes"]) == 2
    for mesh in scene["meshes"]:
        assert mesh["primitiveResource"] == 2  # The 500-distance alternative is excluded.
        assert len(mesh["positions"]) == 9  # An unused vertex cannot distort bounds.
        assert mesh["positions"][:3] == pytest.approx([10, 22, 30], abs=1e-5)
        assert mesh["positions"][3:6] == pytest.approx([10, 24, 30], abs=1e-5)
        assert mesh["worldPositionVerified"] and mesh["editable"]
        assert mesh["editBinding"]["editOffset"] == 648
    assert scene["objects"][0]["position"] == pytest.approx([10, 22, 30], abs=1e-5)


def test_logical_self_owner_is_not_a_spatial_cycle():
    data, sections, _ = graph_fixture()
    kind = b"gameState"
    data[872:872 + len(kind)] = kind
    struct.pack_into("<I", data, 448, len(kind))
    struct.pack_into("<i", data, 448 + 24, 3)
    _, nodes = read_graph(data, sections)
    assert nodes[3]["parent"] == -1
    assert nodes[3]["declaredParent"] == 3
    assert nodes[3]["worldPositionVerified"] is False


def test_empty_platform_array_does_not_instance_the_next_models_data():
    data, sections, pool = graph_fixture()
    struct.pack_into("<I", data, 384 + 28, 0)
    assert read_platform_bindings(data, sections[0], 384, sections) == []
    scene = resolve_scene(data, [pool], sections)
    assert scene["meshes"] == []
    assert len(scene["objects"]) == 1


def test_parent_visibility_is_inherited_without_discarding_editable_records():
    data, sections, pool = graph_fixture()
    struct.pack_into("<f", data, 600, 0)
    scene = resolve_scene(data, [pool], sections)
    assert len(scene["meshes"]) == 2
    assert all(not m["visible"] and m["editable"] for m in scene["meshes"])
    assert not scene["objects"][0]["visible"]


def test_spatial_cycle_and_out_of_section_tables_are_rejected():
    data, sections, _ = graph_fixture()
    struct.pack_into("<i", data, 256 + 24, 1)
    with pytest.raises(ValueError, match="Cycle"):
        read_graph(data, sections)
    data, sections, _ = graph_fixture()
    struct.pack_into("<i", data, 4, 100000)
    with pytest.raises(ValueError, match="Pointeur"):
        read_graph(data, sections)


def test_named_graph_selection_does_not_replace_default_level_graph():
    data, sections, _ = graph_fixture()
    # A second serialized NODE has different placements and identical relative pointers.
    data.extend(bytes(data[:1500]))
    struct.pack_into("<f", data, 1800 + 604, 100)
    second = Section(4, "node", 1800, 1500, 8)
    selected, nodes = read_graph(data, sections + [second], node_index=0)
    assert selected.index == 0 and nodes[0]["position"][0] == 10
    selected, nodes = read_graph(data, sections + [second])
    assert selected.index == 4 and nodes[0]["position"][0] == 100
    with pytest.raises(ValueError, match="demandé"):
        read_graph(data, sections, node_index=999)


def test_second_uv_channel_follows_referenced_vertices():
    data, sections, pool = graph_fixture()
    pool["uv2"] = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 9, 9]
    scene = resolve_scene(data, [pool], sections)
    for mesh in scene["meshes"]:
        assert mesh["uvSetCount"] == 2
        assert mesh["uv2"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
