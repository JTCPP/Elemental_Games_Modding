import struct

import pytest

from asset_library import AssetLibrary, _namespace, _used_textures
from renderer_parser import Section
from test_renderer_parser import fixture_level
from test_scene_graph import graph_fixture


def test_archive_inventory_and_texture_ids_preserve_source_and_cache(tmp_path):
    source = tmp_path / "gamedata"
    source.mkdir()
    data = bytearray(fixture_level())
    struct.pack_into("<4I", data, 44, 0, 112, 112, 0)
    original = bytes(data)
    (source / "interface.xbr").write_bytes(original)
    (source / "loc.xbr").write_bytes(b"incomplete")
    library = AssetLibrary(source, tmp_path / "textures")
    inventory = library.inventory()
    assert inventory["stats"] == {"archiveCount": 1, "textureCount": 1, "bodyCount": 0, "modelCount": 0}
    assert inventory["skipped"][0]["file"] == "loc.xbr"
    catalog = library.catalog("interface")
    assert catalog["textures"][0]["id"] == "lib-interface-surf-0000"
    assert catalog["textures"][0]["url"] == "/textures/interface/surf-0000.png"
    assert (tmp_path / "textures/interface/surf-0000.png").is_file()
    assert (source / "interface.xbr").read_bytes() == original
    for archive in ("../interface", "INTERFACE", "missing", None):
        with pytest.raises(ValueError, match="archive"):
            library.catalog(archive)
    with pytest.raises(ValueError, match="modèle"):
        library.model("interface", "../../server.py")


def test_animation_and_cube_references_are_namespaced_without_source_mutation():
    source = [{"id": "anim-1", "frameIds": ["surf-2", None], "faceIds": ["surf-3"], "url": "/textures/fx/image.png"},
              {"id": "surf-2"}, {"id": "surf-3"}]
    catalog, references = _namespace(source, {1: "anim-1", 2: "surf-2"}, "fx")
    assert catalog[0]["frameIds"] == ["lib-fx-surf-2", None]
    assert catalog[0]["faceIds"] == ["lib-fx-surf-3"]
    assert references == {1: "lib-fx-anim-1", 2: "lib-fx-surf-2"}
    assert catalog[0]["url"] == source[0]["url"]
    assert source[0]["id"] == "anim-1"
    assert _used_textures([{"textureStages": [{"textureId": "lib-fx-anim-1"}]}], catalog) == catalog


def test_graph_catalog_selects_materials_and_detail_from_explicit_bindings(tmp_path, monkeypatch):
    data, sections, pool = graph_fixture()
    monkeypatch.setattr("asset_library.named_resources", lambda *_: {("node", "fx/fixture"): sections[0]})
    library = AssetLibrary(tmp_path, tmp_path / "textures")
    specs, warnings = library._index("fx", data, sections)
    assert warnings == []
    platform = specs["platform-0-2"]
    assert platform["meshCount"] == 2
    assert [r["descriptorIndex"] for r in platform["_references"]] == [0, 1]
    assert [r["primitiveResource"] for r in platform["_references"]] == [2, 2]
    assert specs["graph-0"]["name"] == "fx/fixture"
    monkeypatch.setattr("asset_library.decode_mesh", lambda *_: pool)
    pool["name"] = "Original pool"
    pool["uv2"] = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 9, 9]
    value = {"data": data, "sections": sections, "shaders": {}, "references": {}, "pools": {}}
    parts = library._static_parts(value, platform["_references"])
    assert len(parts) == 2
    assert all(part["indices"] == [0, 1, 2] for part in parts)
    assert parts[0]["positions"] == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert parts[0]["uv2"] == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    assembled = library._static_parts(value, platform["_references"], specs["graph-0"]["_graphParts"][0]["matrix"])
    assert assembled[0]["positions"][:3] == pytest.approx([10, 22, 30], abs=1e-5)


def test_malformed_graph_is_reported_without_inventing_geometry(tmp_path, monkeypatch):
    data, sections, _ = graph_fixture()
    struct.pack_into("<i", data, 256 + 24, 1)
    monkeypatch.setattr("asset_library.named_resources", lambda *_: {})
    specs, warnings = AssetLibrary(tmp_path, tmp_path / "textures")._index("fx", data, sections)
    assert list(specs) == ["platform-0-2"]  # Original local geometry remains inspectable.
    assert len(warnings) == 1 and "Cycle" in warnings[0]
