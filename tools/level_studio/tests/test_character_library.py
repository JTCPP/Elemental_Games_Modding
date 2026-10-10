import struct

import pytest

from character_library import decode_body, named_resources
from renderer_parser import Section


def fixture_body():
    data = bytearray(2200)
    body, bmsh = 256, 768
    struct.pack_into("<8I", data, body, 1, 28, 1, 20, 1, 16, 0, 0)
    struct.pack_into("<I", data, body + 32, 1)
    struct.pack_into("<Ii", data, body + 36, 1, 16)
    descriptor = body + 56
    struct.pack_into("<7I", data, descriptor, 28, 4, 0x10001, 0, 0, 32, 32)
    struct.pack_into("<8I", data, descriptor + 28,
                     0x417fc, 6, 0x40081800, 0 | (1 << 16), 2 | (2 << 16), 0x417fc, 0, 0)
    struct.pack_into("<2I", data, bmsh, 1, 4)
    struct.pack_into("<10I", data, bmsh + 8, 0x1021c, 1, 32, 1, 0, 0, 3, 28, 2, 0)
    struct.pack_into("<I", data, bmsh + 48, 0)
    for index, point in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        vertex = bmsh + 64 + index * 60
        struct.pack_into("<3f4f3f4fI", data, vertex, *point, 1, 0, 0, 0, 0, 0, 1,
                         index / 2, index / 3, 0, 0, 1)
    sections = [Section(0, "body", body, 512, 8), Section(1, "bmsh", bmsh, 512, 8),
                Section(2, "gshd", 1280, 224, 8), Section(3, "surf", 1536, 128, 128)]
    shaders = {2: [{"valid": True, "stages": [{"resourceIndex": 3, "stage": 0}],
                    "material": {"flags": 16, "opacity": 1}}]}
    return data, sections, shaders


def test_body_pairs_original_vertices_material_and_gpu_indices():
    data, sections, shaders = fixture_body()
    model = decode_body(bytes(data), sections[0], sections, shaders, {3: "chars-surf-0003"})
    assert model["pose"] == "bind"
    assert model["meshCount"] == 1
    mesh = model["meshes"][0]
    assert mesh["positions"] == [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert mesh["indices"] == [0, 1, 2]
    assert mesh["normals"] == [0, 0, 1] * 3
    assert mesh["parts"][0]["textureId"] == "chars-surf-0003"
    assert model["bounds"] == {"min": [0, 0, 0], "max": [1, 1, 0]}
    assert mesh["editable"] is False


def test_body_rejects_wrong_resource_and_primitive_pairings():
    data, sections, shaders = fixture_body()
    struct.pack_into("<I", data, 288, 2)
    with pytest.raises(ValueError, match="bmsh"):
        decode_body(bytes(data), sections[0], sections, shaders, {})
    data, sections, shaders = fixture_body()
    struct.pack_into("<I", data, 292, 2)
    with pytest.raises(ValueError, match="groupes"):
        decode_body(bytes(data), sections[0], sections, shaders, {})


def test_body_gpu_indices_and_weights_are_bounded():
    data, sections, shaders = fixture_body()
    struct.pack_into("<I", data, 352, 0 | (7 << 16))
    with pytest.raises(ValueError, match="hors"):
        decode_body(bytes(data), sections[0], sections, shaders, {})
    data, sections, shaders = fixture_body()
    struct.pack_into("<f", data, 844, 0.1)
    with pytest.raises(ValueError, match="Sommets"):
        decode_body(bytes(data), sections[0], sections, shaders, {})


def test_named_body_resource_uses_archive_name_table():
    data, sections, _ = fixture_body()
    name = b"characters/example\0"
    struct.pack_into("<4I", data, 44, 1, 2048, 2056, len(name))
    struct.pack_into("<2I", data, 2048, 0, 0)
    data[2056:2056 + len(name)] = name
    assert named_resources(bytes(data), sections)[("body", "characters/example")] == sections[0]
    struct.pack_into("<I", data, 2052, 99999)
    with pytest.raises(ValueError, match="hors"):
        named_resources(bytes(data), sections)


def test_missing_primary_texture_never_promotes_a_secondary_layer():
    data, sections, shaders = fixture_body()
    shaders[2][0]["stages"] = [{"resourceIndex": 3, "stage": 1}]
    shaders[2][0]["stageCount"] = 2
    mesh = decode_body(bytes(data), sections[0], sections, shaders, {3: "chars-surf-0003"})["meshes"][0]
    assert mesh["textureStages"][0]["textureId"] is None
    assert mesh["textureStages"][1]["textureId"] == "chars-surf-0003"
    assert "textureId" not in mesh["parts"][0]


def test_configured_body_and_colon_variant_share_original_model_without_moving_entities(tmp_path, monkeypatch):
    from character_library import CharacterLibrary
    library = CharacterLibrary(tmp_path, tmp_path / "textures")
    library._names = {("body", "characters/large_health"): object(),
                      ("body", "characters/small_health"): object()}
    monkeypatch.setattr(library, "_load", lambda: True)
    monkeypatch.setattr(library.critters, "resolve", lambda name: {
        "body": "large_health", "scale": 0.9, "matchKind": "exact" if ":" not in name else "path",
        "matchedName": "small_health"})
    calls = []
    def model(name):
        calls.append(name)
        return {"meshes": [], "textures": [{"id": "health-image"}], "pose": "bind"}
    monkeypatch.setattr(library, "model", model)
    scene = {"objects": [{"name": name, "kind": "powerup", "position": [100, 200, 300]}
                         for name in ("small_health", "small_health:path01")], "textures": [], "stats": {}}
    library.enrich(scene)
    assert set(calls) == {"characters/large_health"}
    assert list(scene["entityModels"]) == ["characters/large_health"]
    assert all(item["modelScale"] == 0.9 and item["position"] == [100, 200, 300] for item in scene["objects"])
    assert scene["stats"]["characterInstanceCount"] == 2
    library.enrich(scene)
    assert len(scene["textures"]) == 1


def test_configured_missing_body_is_reported_instead_of_substituted(tmp_path, monkeypatch):
    from character_library import CharacterLibrary
    library = CharacterLibrary(tmp_path, tmp_path / "textures")
    library._names = {("body", "characters/example"): object()}
    monkeypatch.setattr(library, "_load", lambda: True)
    monkeypatch.setattr(library.critters, "resolve", lambda _: {"body": "missing", "scale": 1})
    monkeypatch.setattr(library, "model", lambda _: pytest.fail("Unrelated direct model must not be substituted"))
    scene = {"objects": [{"name": "example", "kind": "enemy"}], "textures": [], "stats": {}}
    library.enrich(scene)
    assert scene["entityModels"] == {}
    assert scene["characterLibrary"]["unresolved"] == [{"name": "example", "body": "characters/missing", "configured": True}]


def test_resource_lookup_folds_ascii_case_and_can_load_exact_cinematic_body(tmp_path, monkeypatch):
    from character_library import CharacterLibrary
    library = CharacterLibrary(tmp_path, tmp_path / "textures")
    companion = CharacterLibrary(tmp_path, tmp_path / "textures", archive="diskreplchars")
    library._companion = companion
    library._names = {("body", "characters/water_lizardx"): object()}
    companion._names = {("body", "characters/movies/jayden_diskreplace"): object()}
    monkeypatch.setattr(library, "_load", lambda: True)
    monkeypatch.setattr(companion, "_load", lambda: True)
    monkeypatch.setattr(library.critters, "resolve", lambda name: {
        "body": "water_lizardX", "scale": 1.3, "matchKind": "exact"} if name == "sleeth_x" else None)
    monkeypatch.setattr(library, "model", lambda name: {"name": name, "meshes": [], "textures": []})
    scene = {"objects": [{"name": name, "kind": "enemy"} for name in ("sleeth_x", "movies/jayden_diskreplace")],
             "textures": [], "stats": {}}
    library.enrich(scene)
    assert scene["objects"][0]["modelKey"] == "characters/water_lizardx"
    assert scene["objects"][1]["modelKey"] == "characters/movies/jayden_diskreplace"
    assert scene["characterLibrary"]["unresolved"] == []
    assert library._canonical_name("AzURIK_É") == "azurik_É"
    with pytest.raises(ValueError, match="Archive"):
        CharacterLibrary(tmp_path, tmp_path / "textures", archive="../../server.py")
