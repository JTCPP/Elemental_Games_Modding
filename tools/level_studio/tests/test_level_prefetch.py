"""Synthetic native text manifests; no retail game assets in fixtures."""
import pytest

from level_prefetch import (MAX_LEVEL_ID_BYTES, MAX_OPERATIONS,
                           MAX_PREFETCH_BYTES, read_prefetch, update_prefetch)


@pytest.fixture
def source():
    return (b"tag=always\r\nfile=index\\index.xbr\r\nfile=%LANGUAGE%.xbr\r\n"
            b"file=config.xbr\r\n\r\ntag=default\r\nfile=training_room.xbr\r\n\r\n"
            b"tag=w1\r\nfile=gamedata\\W1.xbr\r\nfile=shared_extra.xbr\r\n"
            b"neighbor=w1-extra\r\nneighbor=town\r\nneighbor=w2\r\n\r\n"
            b"tag=w1-extra\r\nfile=cutscene.xbr\r\n\r\n"
            b"tag=w2\r\nfile=W2.xbr\r\nneighbor=w1\r\n\r\n"
            b"tag=town\r\nfile=town.xbr\r\nneighbor=w1\r\n")


def by_tag(data):
    return {row["tag"]: row for row in read_prefetch(data)}


def test_no_operation_preserves_exact_bytes(source):
    assert update_prefetch(source) == source


def test_clone_preserves_shared_preloads_neighbors_and_path_prefix(source):
    result = update_prefetch(source, created=[{"id": "my_water", "template": "w1"}])
    originals, changed = by_tag(source), by_tag(result)
    assert changed["my_water"] == {
        "tag": "my_water", "files": ["gamedata\\my_water.xbr", "shared_extra.xbr"],
        "neighbors": ["w1-extra", "town", "w2"],
    }
    assert {key: changed[key] for key in originals} == originals
    assert result.count(b"\n") == result.count(b"\r\n")
    assert update_prefetch(result, created=[{"id": "my_water", "template": "w1"}]) == result


def test_clone_from_new_template_is_order_independent(source):
    result = update_prefetch(source, created=[
        {"id": "second_water", "template": "first_water"},
        {"id": "first_water", "template": "w1"},
    ])
    assert by_tag(result)["second_water"]["files"] == ["gamedata\\second_water.xbr", "shared_extra.xbr"]
    assert by_tag(result)["second_water"]["neighbors"] == by_tag(source)["w1"]["neighbors"]


def test_missing_optional_template_tag_gets_direct_archive_tag(source):
    result = update_prefetch(source, created=[{"id": "my_selector", "template": "selector"}])
    assert by_tag(result)["my_selector"] == {"tag": "my_selector", "files": ["my_selector.xbr"], "neighbors": []}


def test_empty_or_neighbor_only_template_adds_custom_file():
    source = b"tag=town\nneighbor=w1\n"
    result = update_prefetch(source, created=[{"id": "new_town", "template": "town"}])
    assert by_tag(result)["new_town"] == {"tag": "new_town", "files": ["new_town.xbr"], "neighbors": ["w1"]}


def test_delete_rewrites_all_files_keeps_old_tag_and_neighbors(source):
    source += b"\r\ntag=other\r\nfile=W1.xbr\r\nfile=gamedata/w1.xbr\r\nfile=notw1.xbr\r\n"
    result = update_prefetch(source, deleted=[{"id": "w1", "replacement": "new_water"}])
    assert by_tag(result)["w1"]["files"] == ["gamedata\\new_water.xbr", "shared_extra.xbr"]
    assert by_tag(result)["other"]["files"] == ["new_water.xbr", "gamedata/new_water.xbr", "notw1.xbr"]
    assert by_tag(result)["w2"]["neighbors"] == ["w1"]
    assert by_tag(result)["w1"]["neighbors"] == by_tag(source)["w1"]["neighbors"]
    assert all(not path.lower().endswith(("\\w1.xbr", "/w1.xbr")) and path.lower() != "w1.xbr"
               for row in read_prefetch(result) for path in row["files"])


def test_delete_uses_resolved_replacement_chain(source):
    result = update_prefetch(source, created=[{"id": "my_water", "template": "w1"}],
                              deleted=[{"id": "w1", "replacement": "my_water"},
                                       {"id": "my_water", "replacement": "w2"}])
    assert by_tag(result)["w1"]["files"] == ["gamedata\\w2.xbr", "shared_extra.xbr"]
    assert by_tag(result)["my_water"]["files"] == ["gamedata\\w2.xbr", "shared_extra.xbr"]


def test_deleted_custom_without_old_stanza_is_valid(source):
    result = update_prefetch(source, deleted=[{"id": "never_exported", "replacement": "town"}])
    assert read_prefetch(result) == read_prefetch(source)
    assert "never_exported" not in by_tag(result)


def test_created_and_deleted_same_custom_id_redirects_without_deleted_file(source):
    result = update_prefetch(source, created=[{"id": "new_water", "template": "w1"}],
                             deleted=[{"id": "new_water", "replacement": "w1"}])
    assert by_tag(result)["new_water"]["files"] == ["gamedata\\w1.xbr", "shared_extra.xbr"]


def test_parser_accepts_case_whitespace_comments_and_emits_native_safe_text():
    source = (b"# heading\r\n  TAG = W1  ; explanation\r\n"
              b"\tFiLe = W1.xbr // file\r\n; group\r\n Neighbor = town # link\r\n")
    result = update_prefetch(source, created=[{"id": "test", "template": "w1"}])
    assert result == (b"tag=W1\r\nfile=W1.xbr\r\nneighbor=town\r\n\r\n"
                      b"tag=test\r\nfile=test.xbr\r\nneighbor=town\r\n")
    assert by_tag(result)["test"]["files"] == ["test.xbr"]


def test_case_insensitive_template_file_and_deleted_lookup(source):
    result = update_prefetch(source, created=[{"id": "Custom", "template": "W1"}],
                              deleted=[{"id": "W1", "replacement": "Custom"}])
    assert by_tag(result)["Custom"]["files"] == ["gamedata\\Custom.xbr", "shared_extra.xbr"]
    assert by_tag(result)["w1"]["files"] == ["gamedata\\Custom.xbr", "shared_extra.xbr"]


@pytest.mark.parametrize("operation", [
    {"id": "../bad", "template": "w1"}, {"id": "bad/path", "template": "w1"},
    {"id": "con", "template": "w1"}, {"id": "always", "template": "w1"},
    {"id": "default", "template": "w1"}, {"id": "é", "template": "w1"},
    {"id": "a" * (MAX_LEVEL_ID_BYTES + 1), "template": "w1"},
    {"id": "w1", "template": "W1"}, {"id": "custom", "template": "../w1"},
    {"id": "custom"},
])
def test_rejects_invalid_operations(source, operation):
    with pytest.raises(ValueError):
        update_prefetch(source, created=[operation])


def test_rejects_duplicate_casefold_operation_ids(source):
    with pytest.raises(ValueError, match="Duplicate"):
        update_prefetch(source, created=[{"id": "test", "template": "w1"}, {"id": "TEST", "template": "w2"}])
    with pytest.raises(ValueError, match="Duplicate"):
        update_prefetch(source, deleted=[{"id": "w1", "replacement": "w2"}, {"id": "W1", "replacement": "town"}])


def test_rejects_template_cycles_replacement_cycles_and_tag_collision(source):
    with pytest.raises(ValueError, match="Cyclic"):
        update_prefetch(source, created=[{"id": "a", "template": "b"}, {"id": "b", "template": "a"}])
    with pytest.raises(ValueError, match="Cyclic"):
        update_prefetch(source, deleted=[{"id": "w1", "replacement": "w2"}, {"id": "w2", "replacement": "w1"}])
    with pytest.raises(ValueError, match="conflicts"):
        update_prefetch(source, created=[{"id": "w1-extra", "template": "w2"}])


@pytest.mark.parametrize("data", [
    b"tag=w1\ntag=W1\n", b"file=w1.xbr\n", b"neighbor=w1\n",
    b"tag=w1\nexec=anything\n", b"tag=w1\nfile=../w1.xbr\n",
    b"tag=w1\nfile=C:\\game\\w1.xbr\n", b"tag=w1\nfile=/w1.xbr\n",
    b"tag=w1\nfile=w1.xbr\0\n", b"tag=w1\nfile=\xff.xbr\n",
    b"tag=w1\nfile=%USER%.xbr\n", b"tag=w1\nneighbor=../town\n",
    b"tag=w1\nfile=" + b"a" * 260 + b"\n",
])
def test_rejects_ambiguous_unsafe_or_oversized_source_commands(data):
    with pytest.raises(ValueError):
        update_prefetch(data, created=[{"id": "test", "template": "w1"}])


def test_input_and_operation_bounds(source):
    with pytest.raises(ValueError, match="size"):
        update_prefetch(b" " * (MAX_PREFETCH_BYTES + 1))
    with pytest.raises(ValueError, match="Too many"):
        update_prefetch(source, created=[{"id": f"id_{i}", "template": "w1"} for i in range(MAX_OPERATIONS + 1)])
    with pytest.raises(ValueError, match="iterable"):
        update_prefetch(source, created={"id": "test", "template": "w1"})


def test_empty_optional_manifest_can_receive_custom_stanza():
    assert update_prefetch(b"", created=[{"id": "test", "template": "town"}]) == b"tag=test\nfile=test.xbr\n"


def test_input_bytearray_remains_unchanged(source):
    data = bytearray(source)
    update_prefetch(data, created=[{"id": "test", "template": "w1"}])
    assert data == source
