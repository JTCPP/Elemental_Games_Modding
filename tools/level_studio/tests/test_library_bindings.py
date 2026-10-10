import math
import os
from pathlib import Path
import struct

import pytest

from library_bindings import read_library_platform_bindings
from renderer_parser import Section, read_platform_bindings, read_sections, decode_pushbuffer
from scene_graph import read_graph


def library_fixture(descriptor_count=1):
    data = bytearray(1200)
    record, target = 64, 256
    struct.pack_into("<Ii", data, record + 28, descriptor_count, target - record - 32)
    cursor = target + descriptor_count * 72
    for index in range(descriptor_count):
        descriptor = target + index * 72
        struct.pack_into("<I", data, descriptor + 28, 5)
        # An empty array's serialized value 1 is not dereferenceable.
        struct.pack_into("<IiII", data, descriptor + 32, 0, 1, 0, 1)
        struct.pack_into("<Ii", data, descriptor + 48, 1, cursor - descriptor - 52)
        struct.pack_into("<6f", data, cursor, -1, -2, -3, 1, 2, 3)
        cursor += 24
        struct.pack_into("<Ii", data, descriptor + 56, 0, -999999)
        struct.pack_into("<Ii", data, descriptor + 64, 1, cursor - descriptor - 68)
        struct.pack_into("<II4sf", data, cursor, 1, 2, b"pbrc", 100 + index)
        cursor += 16
    sections = [Section(0, "node", 0, 1000, 8), Section(1, "rdms", 1000, 64, 16),
                Section(2, "pbrc", 1064, 64, 8)]
    return data, sections, record, target


def test_empty_first_array_uses_explicit_pair_and_bounds_table():
    data, sections, record, _ = library_fixture()
    with pytest.raises(ValueError, match="limite des descripteurs"):
        read_platform_bindings(data, sections[0], record, sections)
    assert read_library_platform_bindings(data, sections[0], record, sections) == [{
        "meshResource": 1, "primitiveResource": 2, "primitiveTag": "pbrc", "maxDistance": 100.,
        "bindingOffset": 352, "descriptorIndex": 0,
    }]


def test_all_descriptors_keep_their_explicit_bindings():
    data, sections, record, _ = library_fixture(2)
    references = read_library_platform_bindings(data, sections[0], record, sections)
    assert [reference["descriptorIndex"] for reference in references] == [0, 1]
    assert [reference["bindingOffset"] for reference in references] == [424, 464]


def test_empty_platform_does_not_dereference_following_resource():
    data, sections, record, _ = library_fixture()
    struct.pack_into("<Ii", data, record + 28, 0, -999999)
    assert read_library_platform_bindings(data, sections[0], record, sections) == []


@pytest.mark.parametrize("field,value,error", [
    (28, 4, "descripteur"), (40, 1, "paramètres"), (56, 1, "auxiliaire"),
    (48, 20001, "bornes"), (52, -999999, "bornes"), (64, 20001, "liaisons"),
    (68, -999999, "liaisons"),
])
def test_unknown_or_out_of_bounds_tables_are_rejected(field, value, error):
    data, sections, record, target = library_fixture()
    struct.pack_into("<i" if value < 0 else "<I", data, target + field, value)
    with pytest.raises(ValueError, match=error):
        read_library_platform_bindings(data, sections[0], record, sections)


def test_header_boundary_cannot_be_inferred_from_neighbouring_pairs():
    data, sections, record, target = library_fixture()
    # Valid-looking resource pairs elsewhere cannot compensate for a wrong
    # descriptor boundary. Every pointer must be verified, not searched for.
    struct.pack_into("<i", data, target + 52, 24 + 4)
    with pytest.raises(ValueError):
        read_library_platform_bindings(data, sections[0], record, sections)


@pytest.mark.parametrize("mesh,primitive,tag,distance", [
    (2, 2, b"pbrc", 100), (1, 1, b"pbrc", 100), (1, 2, b"pbrw", 100),
    (999, 2, b"pbrc", 100), (1, 2, b"pbrc", math.inf),
])
def test_resource_tags_and_distance_are_validated(mesh, primitive, tag, distance):
    data, sections, record, _ = library_fixture()
    struct.pack_into("<II4sf", data, 352, mesh, primitive, tag, distance)
    with pytest.raises(ValueError, match="ressources"):
        read_library_platform_bindings(data, sections[0], record, sections)


def test_overlapping_auxiliary_and_pair_arrays_are_rejected():
    data, sections, record, target = library_fixture()
    struct.pack_into("<i", data, target + 68, 328 - target - 68)
    with pytest.raises(ValueError):
        read_library_platform_bindings(data, sections[0], record, sections)


def test_populated_first_array_preserves_existing_level_reader():
    data, sections, record, target = library_fixture()
    # 72-byte header, 4 bytes of populated first-array storage, bounds, pair.
    struct.pack_into("<Ii", data, target + 32, 1, 328 - target - 36)
    struct.pack_into("<Ii", data, target + 48, 1, 332 - target - 52)
    struct.pack_into("<6f", data, 332, -1, -2, -3, 1, 2, 3)
    struct.pack_into("<Ii", data, target + 64, 1, 356 - target - 68)
    struct.pack_into("<II4sf", data, 356, 1, 2, b"pbrc", 100)
    assert read_library_platform_bindings(data, sections[0], record, sections) == read_platform_bindings(data, sections[0], record, sections)


@pytest.mark.parametrize("archive,expected_nonempty,expected_empty", [
    ("fx.xbr", 788, 11), ("characters.xbr", 289, 24),
])
def test_original_library_all_platforms_have_explicit_valid_gpu_links(archive, expected_nonempty, expected_empty):
    from editor_backend import DEFAULT_SOURCE
    source = Path(os.environ.get("AZURIK_GAME_DUMP", str(DEFAULT_SOURCE)))
    path = source / "gamedata" / archive
    if not path.is_file():
        pytest.skip("Original EU archive unavailable; synthetic corruption cases still run.")
    data = path.read_bytes()
    sections = read_sections(data)
    counts = {"nonempty": 0, "empty": 0}
    seen = set()
    for section in (value for value in sections if value.tag == "node"):
        if struct.unpack_from("<I", data, section.offset)[0] == 0:
            continue
        try:
            _, nodes = read_graph(data, sections, node_index=section.index)
        except ValueError as exc:
            # Two fx graphs contain pre-existing unsupported parent cycles;
            # their placement is not fabricated by this bindings reader.
            assert archive == "fx.xbr" and "Cycle détecté" in str(exc)
            continue
        for node in nodes:
            if node["type"] != "platform":
                continue
            references = read_library_platform_bindings(data, section, node["recordOffset"], sections)
            counts["nonempty" if references else "empty"] += 1
            for reference in references:
                pair = (reference["meshResource"], reference["primitiveResource"])
                if pair in seen:
                    continue
                seen.add(pair)
                mesh = sections[pair[0]]
                vertex_count = struct.unpack_from("<I", data, mesh.offset + 24)[0]
                triangles = decode_pushbuffer(data, sections[pair[1]], vertex_count)
                assert triangles and len(triangles) % 3 == 0
                assert max(triangles) < vertex_count
    assert counts == {"nonempty": expected_nonempty, "empty": expected_empty}
