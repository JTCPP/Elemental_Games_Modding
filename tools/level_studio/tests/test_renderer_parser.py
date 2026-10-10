import importlib.util
import struct
from pathlib import Path

import pytest
from PIL import Image

from renderer_parser import Section, decode_pushbuffer, decode_texture, decode_texture_faces, parse_level, read_platform_bindings, read_sections, scan_collision


def fixture_level() -> bytes:
    data = bytearray(0x1600)
    data[:4] = b"xobx"
    struct.pack_into("<4I", data, 4, 4, 0, 3, 0x1000)
    struct.pack_into("<I4sII", data, 64, 192, b"surf", 128, 192)
    struct.pack_into("<I4sII", data, 80, 240, b"gshd", 8, 432)
    struct.pack_into("<I4sII", data, 96, 148, b"rdms", 16, 0)
    surface = 0x1000
    struct.pack_into("<II", data, surface, 0x10000010, 0x40001)
    struct.pack_into("<2f", data, surface + 28, 4, 4)
    struct.pack_into("<I", data, surface + 40, 88)
    data[surface + 128:surface + 192] = bytes((10, 20, 30, 255)) * 16
    shader = 0x10c0
    struct.pack_into("<II", data, shader, 1, 232)
    record = shader + 12
    struct.pack_into("<I", data, record + 96, 1)
    struct.pack_into("<III", data, record + 100, 0, 0, 0)
    struct.pack_into("<i", data, shader + 236, -224)
    mesh = 0x11b0
    struct.pack_into("<8I3f5I", data, mesh, 5, 1, 0, 1, 0, 0, 3, 36,
                     10, 20, 30, 0x80002111, 2, 68, 0, 0)
    for i, xyz in enumerate(((0, 0, 0), (256, 0, 0), (0, 256, 0))):
        vertex = mesh + 64 + i * 18
        struct.pack_into("<3h", data, vertex, *xyz)
        data[vertex + 10:vertex + 14] = bytes((10, 20, 30, 255))
        struct.pack_into("<2h", data, vertex + 14, 1024 if i == 1 else 0, 1024 if i == 2 else 0)
    table = mesh + 120  # three 18-byte vertices have two padding bytes.
    struct.pack_into("<IiIi", data, table, 3, 12, 3, 10)
    struct.pack_into("<6H", data, table + 16, 0, 1, 2, 0, 2, 1)
    return bytes(data)


def test_retail_toc_uses_payload_base_and_previous_end():
    sections = read_sections(fixture_level())
    assert [(s.tag, s.offset) for s in sections] == [("surf", 0x1000), ("gshd", 0x10c0), ("rdms", 0x11b0)]


def test_mesh_padding_each_primitive_pointer_and_exact_material_reference(tmp_path):
    scene = parse_level(fixture_level(), "fixture", tmp_path, assemble_scene=False)
    mesh = scene["meshes"][0]
    assert mesh["sourceOffset"] == 0x11b0
    assert mesh["origin"] == [10, 20, 30]
    assert mesh["positions"] == [10, 20, 30, 11, 20, 30, 10, 21, 30]
    assert mesh["indices"] == [0, 1, 2, 0, 2, 1]
    assert mesh["parts"][1] == {"start": 3, "count": 3, "partIndex": 1, "textureId": "surf-0000"}
    assert mesh["uvs"] == [0, 0, 1, 0, 0, 1]
    assert mesh["colors"][:3] == pytest.approx([30 / 255, 20 / 255, 10 / 255])
    assert mesh["editable"] is False  # raw assets require scene-placement mapping.
    assert scene["stats"]["texturedMeshes"] == 1


def test_texture_cache_regenerates_when_source_changes(tmp_path):
    data = fixture_level()
    parse_level(data, "fixture", tmp_path, assemble_scene=False)
    path = tmp_path / "fixture" / "surf-0000.png"
    assert Image.open(path).getpixel((0, 0)) == (30, 20, 10, 255)
    changed = bytearray(data)
    changed[0x1080:0x1084] = bytes((0, 0, 200, 255))
    parse_level(bytes(changed), "fixture", tmp_path, assemble_scene=False)
    assert Image.open(path).getpixel((0, 0)) == (200, 0, 0, 255)


@pytest.mark.parametrize("surface_type, compressed, expected_alpha", [
    (0x10000020, struct.pack("<HHI", 0xf800, 0, 0), 255),
    (0x10000040, b"\x88" * 8 + struct.pack("<HHI", 0xf800, 0, 0), 136),
])
def test_dxt_surfaces_decode_base_mip(surface_type, compressed, expected_alpha):
    data = bytearray(128 + len(compressed))
    struct.pack_into("<I", data, 0, surface_type)
    struct.pack_into("<2f", data, 28, 4, 4)
    struct.pack_into("<I", data, 40, 88)
    data[128:] = compressed
    image, fmt, kind = decode_texture(bytes(data), Section(0, "surf", 0, len(data), 128))
    assert image.getpixel((0, 0)) == (255, 0, 0, expected_alpha)
    assert kind == "2d"


def test_collision_visualization_requires_long_planar_runs():
    data = bytearray(32 * 12)
    for i in range(8):
        for j, point in enumerate(((10 + i, 20, 5), (11 + i, 20, 5), (10 + i, 21, 5), (11 + i, 21, 5))):
            struct.pack_into("<3f", data, i * 48 + j * 12, *point)
    collision = scan_collision(bytes(data), [Section(0, "sdsr", 0, len(data), 16)])
    assert len(collision["indices"]) == 8 * 6
    assert collision["editable"] is False
    assert collision["heuristic"] is True


def test_malformed_resource_bounds_are_rejected():
    data = bytearray(fixture_level())
    struct.pack_into("<I", data, 76, 2)
    with pytest.raises(ValueError, match="chevauchent"):
        read_sections(bytes(data))


def test_gpu_primitive_packets_are_not_mistaken_for_vertex_indices():
    data = bytearray(80)
    struct.pack_into("<II", data, 0, 3, 28)
    # A strip crosses two GPU packets and ends with an unpaired index.
    struct.pack_into("<8I", data, 32, 0x417fc, 6, 0x40081800,
                     0 | (1 << 16), 2 | (3 << 16), 0x41808, 4, 0x417fc)
    struct.pack_into("<I", data, 64, 0)
    result = decode_pushbuffer(bytes(data), Section(0, "pbrc", 0, 72, 8), 5)
    assert result == [0, 1, 2, 2, 1, 3, 2, 3, 4]
    with pytest.raises(ValueError, match="hors"):
        decode_pushbuffer(bytes(data), Section(0, "pbrc", 0, 72, 8), 4)


def test_platform_binding_uses_explicit_resource_pair():
    data = bytearray(256)
    # Platform at 0x80 references wrapper at 0, wrapper points to binding 0x64.
    struct.pack_into("<i", data, 0x80 + 32, -(0x80 + 32))
    struct.pack_into("<I", data, 0x80 + 28, 1)
    struct.pack_into("<I", data, 28, 5)
    struct.pack_into("<Ii", data, 32, 1, 36)
    struct.pack_into("<Ii", data, 64, 1, 32)
    struct.pack_into("<II4sf", data, 100, 1, 2, b"pbrc", 1000)
    sections = [Section(0, "node", 0, 256, 8), Section(1, "rdms", 0, 64, 16), Section(2, "pbrc", 0, 64, 8)]
    bindings = read_platform_bindings(bytes(data), sections[0], 0x80, sections)
    assert bindings == [{"meshResource": 1, "primitiveResource": 2, "primitiveTag": "pbrc",
                         "maxDistance": 1000, "bindingOffset": 100, "descriptorIndex": 0}]
    struct.pack_into("<I", data, 104, 1)  # An RDMS resource cannot masquerade as a GPU primitive buffer.
    with pytest.raises(ValueError, match="ressources"):
        read_platform_bindings(bytes(data), sections[0], 0x80, sections)


def animation_fixture():
    data = bytearray(fixture_level())
    struct.pack_into("<I", data, 12, 5)
    struct.pack_into("<I", data, 108, 580)  # Existing RDMS is no longer the last resource.
    struct.pack_into("<I4sII", data, 112, 192, b"surf", 128, 772)
    struct.pack_into("<I4sII", data, 128, 20, b"surf", 8, 0)
    data[0x1244:0x1304] = data[0x1000:0x10c0]
    data[0x12c4:0x1304] = bytes((0, 0, 200, 255)) * 16
    struct.pack_into("<5I", data, 0x1304, 1, 2, 4, 0, 3)
    struct.pack_into("<I", data, 0x1130, 4)  # The material uses animated surface 4, not image 0.
    return data


def test_animated_materials_keep_distinct_identity_and_exact_frame_order(tmp_path):
    scene = parse_level(bytes(animation_fixture()), "fixture", tmp_path, assemble_scene=False)
    by_id = {t["id"]: t for t in scene["textures"]}
    animation = by_id["anim-0004"]
    assert by_id["surf-0000"]["kind"] == "2d"
    assert animation["kind"] == "animation"
    assert animation["frameIds"] == ["surf-0000", "surf-0003"]
    assert animation["frameUrls"] == ["/textures/fixture/surf-0000.png", "/textures/fixture/surf-0003.png"]
    assert animation["frameResourceIndices"] == [0, 3]
    assert animation["frameCount"] == animation["availableFrameCount"] == 2
    assert animation["previewFps"] == 12 and animation["timingVerified"] is False
    assert scene["meshes"][0]["parts"][0]["textureId"] == "anim-0004"
    assert scene["meshes"][0]["textureStages"][0]["textureId"] == "anim-0004"


def test_missing_animation_frame_keeps_its_timeline_slot(tmp_path):
    data = animation_fixture()
    struct.pack_into("<I", data, 0x1314, 999)
    scene = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)
    animation = next(t for t in scene["textures"] if t["kind"] == "animation")
    assert animation["frameUrls"] == ["/textures/fixture/surf-0000.png", None]
    assert animation["frameCount"] == 2 and animation["availableFrameCount"] == 1
    assert any("emplacements" in w for w in scene["warnings"])


def cube_fixture():
    data = bytearray(128 + 6 * 64)
    struct.pack_into("<I", data, 0, 0x30000010)
    struct.pack_into("<2f", data, 28, 4, 4)
    for i in range(6):
        field, pointer = 40 + i * 4, 128 + i * 64
        struct.pack_into("<i", data, field, pointer - field)
        data[pointer:pointer + 64] = bytes((10 + i, 20 + i, 30 + i, 255)) * 16
    return data


def test_cube_face_pointers_are_relative_to_each_individual_field():
    data = cube_fixture()
    faces, fmt, kind = decode_texture_faces(bytes(data), Section(0, "surf", 0, len(data), 128))
    assert fmt == "BGRA8" and kind == "cube" and len(faces) == 6
    assert [face.getpixel((0, 0)) for face in faces] == [(30 + i, 20 + i, 10 + i, 255) for i in range(6)]
    # Same relative offset at a different pointer field would point four
    # bytes into face zero, yielding an invalid overlapping face.
    struct.pack_into("<i", data, 44, 128 - 44)
    with pytest.raises(ValueError, match="chevauchent"):
        decode_texture_faces(bytes(data), Section(0, "surf", 0, len(data), 128))


def test_material_blend_depth_and_vertex_alpha_preserve_real_channels(tmp_path):
    data = bytearray(fixture_level())
    material = 0x10c0 + 12
    struct.pack_into("<I", data, material + 88, 2)
    struct.pack_into("<I", data, material + 92, 500)
    struct.pack_into("<I", data, material + 100 + 24, 1 << 29)
    data[0x11b0 + 64 + 13] = 128
    mesh = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)["meshes"][0]
    assert mesh["material"]["depthWrite"] == 2
    assert mesh["material"]["blendType"] == 500
    assert mesh["textureStages"][0]["flags"] == 1 << 29
    assert mesh["vertexAlphas"] == pytest.approx([128 / 255, 1, 1])


def test_shader_uv_parameters_preserve_static_transform_and_distinct_clock(tmp_path):
    data = bytearray(fixture_level())
    stage = 0x10c0 + 12 + 100
    struct.pack_into("<6fI", data, stage + 32, 3, 90, 0.25, -0.5, 0.1, -0.2, 2)
    matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.5, 0.75, 0, 1]
    struct.pack_into("<16f", data, stage + 60, *matrix)
    mesh = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)["meshes"][0]
    uv = mesh["textureStages"][0]["uvTransform"]
    assert uv["scale"] == 3 and uv["rotationDegrees"] == 90
    assert uv["offset"] == [0.25, -0.5]
    assert uv["scrollVelocity"] == pytest.approx([0.1, -0.2])
    assert uv["matrixMode"] == 2 and uv["matrix"] == matrix
    assert uv["matrixLayout"] == "row-major" and uv["parametersVerified"] is True
    assert uv["scrollTimeUnitsVerified"] is False


@pytest.mark.parametrize("surface_type,block,format_name,alpha", [
    (0x20, struct.pack("<HHI", 0xf800, 0x07e0, 0), "DXT1", 255),
    (0x40, bytes.fromhex("8888888888888888") + struct.pack("<HHI", 0xf800, 0x07e0, 0), "DXT3", 136),
])
def test_ui_compressed_surfaces_without_mipmap_flag_are_real_images(surface_type, block, format_name, alpha):
    data = bytearray(128) + block
    struct.pack_into("<I", data, 0, surface_type)
    struct.pack_into("<2f", data, 28, 4, 4)
    struct.pack_into("<i", data, 40, 88)
    images, fmt, kind = decode_texture_faces(bytes(data), Section(0, "surf", 0, len(data), 128))
    assert fmt == format_name and kind == "2d" and len(images) == 1
    assert images[0].getpixel((0, 0)) == (255, 0, 0, alpha)


def test_catalog_distinguishes_single_level_surface_from_source_mipmaps(tmp_path):
    data = bytearray(fixture_level())
    struct.pack_into("<I", data, 0x1000, 0x10)
    texture = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)["textures"][0]
    assert texture["surfaceType"] == 0x10
    assert texture["mipmapsInSource"] is False and texture["sourceMipCount"] == 1
    struct.pack_into("<I", data, 0x1000, 0x10000010)
    texture = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)["textures"][0]
    assert texture["mipmapsInSource"] is True and texture["sourceMipCount"] == 3


def test_zero_stage_material_is_not_reported_as_missing_texture(tmp_path):
    data = bytearray(fixture_level())
    shader, record = 0x10c0, 0x10c0 + 12
    struct.pack_into("<I", data, shader + 4, 108)
    struct.pack_into("<I", data, record + 96, 0)
    struct.pack_into("<i", data, shader + 112, -100)
    scene = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)
    assert scene["stats"]["untexturedMeshes"] == 1
    assert scene["stats"]["missingTextureMeshes"] == 0
    assert scene["meshes"][0]["declaredTextureStages"] == 0
    assert scene["meshes"][0]["materialValid"] is True
    assert any("matériau d'origine" in warning for warning in scene["warnings"])
    data = bytearray(fixture_level())
    struct.pack_into("<I", data, record + 100, 999)
    scene = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)
    assert scene["stats"]["untexturedMeshes"] == 0
    assert scene["stats"]["missingTextureMeshes"] == 1


@pytest.mark.parametrize("declaration,stride,secondary", [(0x80004111, 22, False), (0x80040111, 30, True), (0x252, 44, True)])
def test_retail_float_uv_declarations_keep_coordinate_sets_distinct(tmp_path, declaration, stride, secondary):
    data = bytearray(fixture_level())
    mesh = 0x11b0
    table = (64 + 3 * stride + 3) & ~3
    size = table + 8 + 6
    struct.pack_into("<I", data, 96, size)
    struct.pack_into("<8I3f5I", data, mesh, 5, 1, 0, 1, 0, 0, 3, 36,
                     10, 20, 30, declaration, 1, table - 52, 0, 0)
    primary = [(1.7645, -0.9982), (3.2355, -0.9982), (1.7645, 20.0056)]
    second = [(0.25, 0.125), (0.75, 0.125), (0.25, 0.875)]
    for i, xyz in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        vertex = mesh + 64 + i * stride
        if stride == 44:
            struct.pack_into("<3f", data, vertex, *xyz)
            data[vertex + 24:vertex + 28] = bytes((10, 20, 30, 255))
            struct.pack_into("<2f", data, vertex + 28, *primary[i])
            struct.pack_into("<2f", data, vertex + 36, *second[i])
        else:
            struct.pack_into("<3h", data, vertex, *(int(x * 256) for x in xyz))
            data[vertex + 10:vertex + 14] = bytes((10, 20, 30, 255))
            struct.pack_into("<2f", data, vertex + 14, *primary[i])
            if secondary:
                struct.pack_into("<2f", data, vertex + 22, *second[i])
    struct.pack_into("<Ii3H", data, mesh + table, 3, 4, 0, 1, 2)
    decoded = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)["meshes"][0]
    assert decoded["uvs"] == pytest.approx([v for pair in primary for v in pair])
    assert decoded["uv2"] == pytest.approx([v for pair in second for v in pair] if secondary else [])
    assert decoded["uvSetCount"] == (2 if secondary else 1)
    assert decoded["positions"] == [10, 20, 30, 11, 20, 30, 10, 21, 30]
    assert decoded["indices"] == [0, 1, 2]


def test_available_secondary_stage_does_not_hide_missing_primary_texture(tmp_path):
    data = bytearray(fixture_level())
    original_mesh = bytes(data[0x11b0:0x11b0 + 148])
    shader = 0x10c0
    struct.pack_into("<I4sII", data, 80, 364, b"gshd", 8, 556)
    data[shader:shader + 364] = bytes(364)
    struct.pack_into("<II", data, shader, 1, 356)
    struct.pack_into("<I", data, shader + 12 + 96, 2)
    struct.pack_into("<3I", data, shader + 112, 999, 0, 0)
    struct.pack_into("<3I", data, shader + 236, 0, 0, 1)
    struct.pack_into("<i", data, shader + 360, -348)
    data[0x122c:0x122c + 148] = original_mesh
    scene = parse_level(bytes(data), "fixture", tmp_path, assemble_scene=False)
    assert scene["stats"]["texturedMeshes"] == 0
    assert scene["stats"]["missingTextureMeshes"] == 1
    assert scene["meshes"][0]["textureStages"][0]["stage"] == 1
    assert all("textureId" not in part for part in scene["meshes"][0]["parts"])


def test_scene_coverage_counters_include_static_geometry_after_sky_classification(tmp_path, monkeypatch):
    import scene_graph
    import static_scene
    import environment_scene
    def item(name, visible, role=None):
        row = {"id": name, "positions": [0., 0., 0., 1., 0., 0., 0., 1., 0.],
               "indices": [0, 1, 2], "authoredVisible": visible, "editorVisible": visible}
        if role:
            row["sceneRole"] = role
        return row
    monkeypatch.setattr(scene_graph, "resolve_scene", lambda *args: {
        "meshes": [item("node-visible", True), item("node-hidden", False)], "nodes": [], "objects": [],
        "stats": {"placedMeshCount": 2, "placedTriangleCount": 2,
                  "authoredVisibleMeshCount": 1, "editorVisibleMeshCount": 1}})
    monkeypatch.setattr(static_scene, "resolve_static_scene", lambda *args: {
        "meshes": [item("static-sky", True, "static")], "stats": {"staticMeshCount": 1}, "warnings": []})
    monkeypatch.setattr(environment_scene, "build_environment", lambda *args: {
        "sky": {"meshIds": ["static-sky"]}, "warnings": []})
    parsed = parse_level(fixture_level(), "fixture", tmp_path)
    stats = parsed["stats"]
    assert stats["placedMeshCount"] == stats["sceneInstances"] == 3
    assert stats["placedTriangleCount"] == stats["sceneTriangles"] == 3
    assert stats["nodePlacedMeshCount"] == stats["nodePlacedTriangleCount"] == 2
    assert stats["authoredVisibleMeshCount"] == stats["editorVisibleMeshCount"] == 2
    assert stats["nodeAuthoredVisibleMeshCount"] == stats["nodeEditorVisibleMeshCount"] == 1
    assert stats["editorVisibleWorldMeshCount"] == stats["editorVisibleSkyMeshCount"] == 1
    assert parsed["meshes"][2]["sceneRole"] == "sky"
