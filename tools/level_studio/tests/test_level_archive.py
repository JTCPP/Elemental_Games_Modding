"""Native archive/index transformations; fixtures contain no retail assets."""
import struct

import pytest

from level_archive import (alias_level, clone_level, named_resources,
                           read_index, update_index)


def make_archive(resources, names, relocations=()):
    """Small v4 archive with the same compact footer consumed by the engine."""
    payload_start = 4096
    payload = b"".join(blob for tag, blob in resources)
    aux_start = (payload_start + len(payload) + 4095) & ~4095
    tags = list(dict.fromkeys(tag for tag, blob in resources))
    groups = b"".join(tag.encode("ascii") + struct.pack("<I", 3) for tag in tags)
    relocation_table = b"".join(struct.pack("<I", p) for p in relocations)
    name_table_start = aux_start + len(groups) + len(relocation_table)
    pool_start = name_table_start + len(names) * 8
    table, pool = bytearray(), bytearray()
    for resource, key in names:
        table += struct.pack("<II", resource, len(pool))
        pool += key.encode("ascii") + b"\0"
    header = [int.from_bytes(b"xobx", "little"), 4, 0, len(resources), payload_start,
              len(tags), aux_start, len(relocations), aux_start + len(groups), 0,
              name_table_start, len(names), name_table_start, pool_start, len(pool), 0]
    result = bytearray(struct.pack("<16I", *header))
    cumulative = 0
    for index, (tag, blob) in enumerate(resources):
        cumulative += len(blob)
        result += struct.pack("<I4sII", len(blob), tag.encode("ascii"), 8,
                              cumulative if index < len(resources) - 1 else 0)
    result += b"\0" * (payload_start - len(result))
    result += payload
    result += b"\0" * (aux_start - len(result))
    result += groups + relocation_table + table + pool
    return bytes(result)


def make_index(records):
    """Explicit field-relative records, independent of production serializer."""
    records = sorted(records, key=lambda r: r["key"])
    count = len(records)
    raw = bytearray(struct.pack("<Ii", count, 4) + b"\0" * (count * 20))
    for index, record in enumerate(records):
        row = 8 + index * 20
        key = record["key"].encode("ascii")
        filename = record["archive"].encode("ascii")
        key_start = len(raw)
        raw += key + b"\0"
        archive_start = len(raw)
        raw += filename + b"\0"
        struct.pack_into("<IiIi4s", raw, row, len(key), key_start - (row + 4),
                         len(filename), archive_start - (row + 12),
                         record["tag"].encode("ascii"))
    return make_archive([("indx", bytes(raw))], [(0, "index")])


@pytest.fixture
def level():
    return make_archive([("node", b"\x01\x02original node\x00"),
                         ("levl", b"opaque level; portals/IDs/collision stay here\x00")],
                        [(0, "levels/training_room/fx/vasebreak"),
                         (1, "levels/training_room")], [4100])


@pytest.fixture
def index():
    return make_index([
        {"key": "characters/azurik", "archive": "characters.xbr", "tag": "body"},
        {"key": "levels/town", "archive": "town.xbr", "tag": "levl"},
        {"key": "levels/training_room", "archive": "training_room.xbr", "tag": "levl"},
        {"key": "levels/training_room/fx/vasebreak", "archive": "training_room.xbr", "tag": "node"},
    ])


def test_clone_renames_native_names_and_preserves_payload(level):
    cloned, registrations = clone_level(level, "training_room", "my_test")
    assert registrations == [
        {"key": "levels/custom/my_test/fx/vasebreak", "archive": "my_test.xbr", "tag": "node"},
        {"key": "levels/custom/my_test", "archive": "my_test.xbr", "tag": "levl"},
    ]
    names_offset = struct.unpack_from("<I", level, 48)[0]
    assert cloned[64:names_offset] == level[64:names_offset]
    assert named_resources(cloned) == [
        {"key": "levels/custom/my_test/fx/vasebreak", "tag": "node", "resourceIndex": 0},
        {"key": "levels/custom/my_test", "tag": "levl", "resourceIndex": 1},
    ]
    # Footer buffer size from the native loader still reaches exactly EOF.
    h = struct.unpack_from("<16I", cloned)
    assert h[6] + (h[5] + h[11]) * 8 + h[7] * 4 + h[9] * 12 + h[14] == len(cloned)


def test_alias_preserves_existing_names_and_points_to_same_resource(level):
    result = alias_level(level, ["levels/water/w1", "levels/town"])
    assert named_resources(result) == named_resources(level) + [
        {"key": "levels/water/w1", "tag": "levl", "resourceIndex": 1},
        {"key": "levels/town", "tag": "levl", "resourceIndex": 1},
    ]
    old_names = struct.unpack_from("<I", level, 48)[0]
    assert result[64:old_names] == level[64:old_names]
    assert alias_level(result, ["levels/water/w1"]) == result
    assert alias_level(level, []) == level


def test_alias_target_key_and_conflicts(level):
    assert alias_level(level, ["levels/town"], target_key="levels/training_room")
    with pytest.raises(ValueError, match="unambiguous"):
        alias_level(level, ["levels/town"], target_key="levels/missing")
    with pytest.raises(ValueError, match="conflicts"):
        alias_level(level, ["levels/training_room/fx/vasebreak"])
    with pytest.raises(ValueError, match="iterable"):
        alias_level(level, "levels/town")


def test_clone_drops_old_level_aliases(level):
    aliased = alias_level(level, ["levels/water/w1"])
    cloned, records = clone_level(aliased, "training_room", "new_room")
    assert [r["key"] for r in records] == ["levels/custom/new_room/fx/vasebreak", "levels/custom/new_room"]
    assert all(n["key"] != "levels/water/w1" for n in named_resources(cloned))


@pytest.mark.parametrize("new_id", ["../w1", "a/b", "", "con", "LPT1", "épreuve", "a" * 65, "a.b"])
def test_clone_rejects_unsafe_or_oversized_ids(level, new_id):
    with pytest.raises(ValueError):
        clone_level(level, "training_room", new_id)


def test_clone_requires_matching_source_and_different_id(level):
    with pytest.raises(ValueError, match="Source"):
        clone_level(level, "w1", "my_room")
    with pytest.raises(ValueError, match="different"):
        clone_level(level, "training_room", "TRAINING_ROOM")
    shared = make_archive([("node", b"node"), ("levl", b"level")],
                          [(0, "shared/fx"), (1, "levels/town")])
    cloned, registrations = clone_level(shared, "town", "my_town")
    assert registrations[0]["key"] == "levels/custom/my_town/assets/shared/fx"
    assert all(n["key"].startswith("levels/custom/my_town") for n in named_resources(cloned))


def test_clone_supports_maximum_native_path_length(level):
    cloned, records = clone_level(level, "training_room", "a" * 64)
    main = next(r for r in records if r["tag"] == "levl")
    assert len(main["key"]) == 78
    assert named_resources(cloned)


def test_index_decodes_exact_length_and_field_relative_pointers(index):
    assert read_index(index) == [
        {"key": "characters/azurik", "archive": "characters.xbr", "tag": "body"},
        {"key": "levels/town", "archive": "town.xbr", "tag": "levl"},
        {"key": "levels/training_room", "archive": "training_room.xbr", "tag": "levl"},
        {"key": "levels/training_room/fx/vasebreak", "archive": "training_room.xbr", "tag": "node"},
    ]
    assert update_index(index) == index


def test_index_adds_clones_and_keeps_unrelated_records(index, level):
    cloned, registrations = clone_level(level, "training_room", "my_room")
    result = update_index(index, add=registrations)
    assert read_index(result) == sorted(read_index(index) + registrations, key=lambda r: r["key"])
    assert named_resources(result) == named_resources(index)
    assert cloned


def test_index_redirects_only_level_and_retains_auxiliary_nodes(index, level):
    alias = alias_level(level, ["levels/town"])
    result = update_index(index,
                          remove=[{"key": "levels/town", "tag": "levl"}],
                          add=[{"key": "levels/town", "archive": "training_room.xbr", "tag": "levl"}])
    records = read_index(result)
    assert next(r for r in records if r["key"] == "levels/town")["archive"] == "training_room.xbr"
    assert next(r for r in records if r["tag"] == "node") in read_index(index)
    assert any(n["key"] == "levels/town" and n["tag"] == "levl" for n in named_resources(alias))
    assert update_index(index, remove=[{"key": "levels/town", "tag": "body"}]) == index


def test_index_expands_across_alignment_and_preserves_footer(index):
    records = [{"key": f"levels/custom/room_{i:04}", "archive": f"room_{i:04}.xbr", "tag": "levl"}
               for i in range(500)]
    result = update_index(index, add=records)
    old, new = struct.unpack_from("<16I", index), struct.unpack_from("<16I", result)
    assert new[6] > old[6] and new[6] % 4096 == 0
    assert result[new[6]:] == index[old[6]:]
    for field in (6, 8, 10, 12, 13):
        assert new[field] - old[field] == new[6] - old[6]
    assert new[13] + new[14] == len(result)
    assert read_index(result) == sorted(read_index(index) + records, key=lambda r: r["key"])


def test_index_can_remove_an_entire_custom_archive(index):
    result = update_index(index, remove_archives=["TRAINING_ROOM.xbr"])
    assert all(r["archive"] != "training_room.xbr" for r in read_index(result))
    assert len(read_index(result)) == 2


def test_index_can_be_empty():
    source = make_index([{"key": "levels/town", "archive": "town.xbr", "tag": "levl"}])
    result = update_index(source, remove=[{"key": "levels/town", "tag": "levl"}])
    assert read_index(result) == []


@pytest.mark.parametrize("record", [
    {"key": "../bad", "archive": "town.xbr", "tag": "levl"},
    {"key": "levels/test", "archive": "../town.xbr", "tag": "levl"},
    {"key": "levels/test", "archive": "con.xbr", "tag": "levl"},
    {"key": "levels/test", "archive": "test.xbr", "tag": "bad"},
    {"key": "levels/test\0", "archive": "test.xbr", "tag": "levl"},
    {"key": "levels/" + "x" * 80, "archive": "test.xbr", "tag": "levl"},
    {"key": "levels/test", "tag": "levl"},
])
def test_index_rejects_bad_additions(index, record):
    with pytest.raises(ValueError):
        update_index(index, add=[record])


def test_index_rejects_conflicting_additions(index):
    town = {"key": "levels/town", "archive": "town.xbr", "tag": "levl"}
    with pytest.raises(ValueError, match="Duplicate"):
        update_index(index, add=[town, town])
    with pytest.raises(ValueError, match="type"):
        update_index(index, add=[{**town, "tag": "node"}])
    with pytest.raises(ValueError, match="exact"):
        update_index(index, remove=[{"archive": "town.xbr"}])


@pytest.mark.parametrize("field,value", [(4, 3), (12, 200001), (16, 4097), (24, 4096),
                                          (28, 2), (44, 20001), (48, 0), (52, 0), (56, 2**32-1)])
def test_archive_rejects_malformed_headers(level, field, value):
    data = bytearray(level)
    struct.pack_into("<I", data, field, value)
    with pytest.raises(ValueError):
        named_resources(data)


def test_archive_rejects_bad_names_and_resource_extents(level):
    table = struct.unpack_from("<I", level, 48)[0]
    pool = struct.unpack_from("<I", level, 52)[0]
    for offset, raw in [(table, struct.pack("<I", 1000)),
                        (table + 4, struct.pack("<I", 2**32-1)),
                        (pool, b"\xff"), (len(level) - 1, b"X"),
                        (64, struct.pack("<I", 2**32-1)),
                        (76, struct.pack("<I", 1))]:
        data = bytearray(level)
        data[offset:offset + len(raw)] = raw
        with pytest.raises(ValueError):
            named_resources(data)


@pytest.mark.parametrize("offset,fmt,value", [(4096, "<I", 200001), (4100, "<i", -8),
                                             (4104, "<I", 256), (4108, "<i", -12),
                                             (4116, "<i", 2**31-1)])
def test_index_rejects_bad_counts_lengths_and_relative_pointers(index, offset, fmt, value):
    data = bytearray(index)
    struct.pack_into(fmt, data, offset, value)
    with pytest.raises(ValueError):
        read_index(data)


def test_index_rejects_wrong_tag_duplicates_unsorted_and_embedded_nuls(index):
    mutations = []
    wrong_tag = bytearray(index)
    wrong_tag[64 + 4:64 + 8] = b"levl"
    mutations.append(wrong_tag)
    unsorted = make_index([
        {"key": "levels/a", "archive": "a.xbr", "tag": "levl"},
        {"key": "levels/z", "archive": "z.xbr", "tag": "levl"},
    ])
    unsorted = bytearray(unsorted)
    first = 4104 + 4 + struct.unpack_from("<i", unsorted, 4108)[0]
    second = 4124 + 4 + struct.unpack_from("<i", unsorted, 4128)[0]
    unsorted[first + 7], unsorted[second + 7] = ord("z"), ord("a")
    mutations.append(unsorted)
    duplicate = make_index([
        {"key": "levels/a", "archive": "a.xbr", "tag": "levl"},
        {"key": "levels/a", "archive": "b.xbr", "tag": "levl"},
    ])
    mutations.append(duplicate)
    nul = bytearray(index)
    start = 4108 + struct.unpack_from("<i", index, 4108)[0]
    nul[start + 2] = 0
    mutations.append(nul)
    for data in mutations:
        with pytest.raises(ValueError):
            read_index(data)


@pytest.mark.parametrize("data", [b"", b"not XBR" + b"\0" * 100])
def test_rejects_non_archives(data):
    with pytest.raises(ValueError):
        named_resources(data)
    with pytest.raises(ValueError):
        read_index(data)
