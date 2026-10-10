"""Source cell bindings and material products, without global light guesses."""
import copy
import math
import struct

import pytest

from renderer_parser import Section
from retail_lighting import (apply_source_lighting, read_cell_light_ids,
                             read_light_state, select_cell_lights, _direction)


def fixture(kind=1, falloff=0, intensity=1., exclusions=0):
    data = bytearray(1024)
    node_section = Section(0, "node", 64, 256, 0)
    level_section = Section(1, "levl", 512, 512, 0)
    record, params = 96, 160
    struct.pack_into("<I", data, record + 8, 10)
    struct.pack_into("<i", data, record + 12, params - record - 12)
    struct.pack_into("<10f", data, params, 1, 0, 0, 0, .2, .4, .6, intensity, 0, 0)
    struct.pack_into("<IH", data, record + 28, kind | (falloff << 8) | exclusions, 12)
    struct.pack_into("<Ii", data, 512 + 28, 1, 576 - 512 - 32)
    struct.pack_into("<Ii", data, 576 + 64, 1, 704 - 576 - 68)
    struct.pack_into("<H", data, 704, 12)
    node = {"index": 0, "type": "light", "recordOffset": record, "paramsOffset": params,
            "worldPositionVerified": True, "worldPosition": [0., 0., 0.],
            "worldMatrix": [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]}
    return data, [node_section, level_section], node


def light(state, *, position=(0., 0., 0.), verified=True):
    return {"state": state, "position": list(position), "transformVerified": verified,
            "direction": [0., 0., -1.], "nodeIndex": 0}


def test_serialized_light_offsets_and_rgb_are_preserved_without_intensity_gain():
    data, sections, node = fixture(intensity=100)
    original = bytes(data), copy.deepcopy(node)
    node["paramsOffset"] = 200  # Source pointer remains authoritative.
    state = read_light_state(data, node, sections)
    assert state["sourceId"] == 12 and state["type"] == "directional"
    assert state["paramsOffset"] == 160 and state["idOffset"] == 128
    assert state["color"] == pytest.approx([.2, .4, .6])
    assert state["intensity"] == 100 and state["active"]
    assert bytes(data) == original[0]


@pytest.mark.parametrize("intensity,active,color_sign", [(0, False, 1), (-1, True, -1), (.0001, False, 1)])
def test_intensity_selects_activity_and_negative_rgb_but_not_brightness_gain(intensity, active, color_sign):
    data, sections, node = fixture(intensity=intensity)
    state = read_light_state(data, node, sections)
    assert state["active"] is active
    assert state["color"] == pytest.approx([.2 * color_sign, .4 * color_sign, .6 * color_sign])


@pytest.mark.parametrize("damage", ["nan", "unknown_count", "pointer_outside", "record_outside"])
def test_invalid_light_layout_is_bounded_or_explicitly_unsupported(damage):
    data, sections, node = fixture()
    if damage == "nan":
        struct.pack_into("<f", data, 176, float("nan"))
    elif damage == "unknown_count":
        struct.pack_into("<I", data, 104, 11)
        assert read_light_state(data, node, sections) is None
        return
    elif damage == "pointer_outside":
        struct.pack_into("<i", data, 108, 304 - 108)
    else:
        node["recordOffset"] = 310
    with pytest.raises(ValueError):
        read_light_state(data, node, sections)


def test_cell_binding_reads_uint16_source_ids_not_neighboring_records():
    data, sections, _ = fixture()
    assert read_cell_light_ids(data, sections) == [[12]]
    struct.pack_into("<I", data, 640, 0)
    struct.pack_into("<i", data, 644, -100000)
    assert read_cell_light_ids(data, sections) == [[]]
    struct.pack_into("<I", data, 640, 1)
    with pytest.raises(ValueError):
        read_cell_light_ids(data, sections)


def test_zero_diffuse_rgb_is_inactive_even_with_nonzero_intensity():
    data, sections, node = fixture(intensity=100)
    struct.pack_into("<3f", data, 176, 0, 0, 0)
    assert not read_light_state(data, node, sections)["active"]
    data[124] = 0  # Ambient path CF7B0 preserves the attenuation flag.
    assert read_light_state(data, node, sections)["active"]


def test_source_static_exclusion_preserves_baked_black_objects_with_valid_normals():
    data, sections, node = fixture(exclusions=0x10000)
    state = read_light_state(data, node, sections)
    selected, limits = select_cell_lights([[12]], [0], {12: light(state)}, [-1, -1, -1, 1, 1, 1])
    assert selected == [] and limits == set()
    assert state["excludeStatic"] and not state["excludeDynamic"]


def test_cell_selection_deduplicates_and_keeps_first_four_equal_strength_lights():
    data, sections, node = fixture()
    state = read_light_state(data, node, sections)
    lights = {source_id: light({**state, "sourceId": source_id}) for source_id in range(1, 7)}
    selected, _ = select_cell_lights([[1, 2, 3, 4], [1, 5, 6]], [0, 1], lights, [-1, -1, -1, 1, 1, 1])
    assert [value["state"]["sourceId"] for value in selected] == [1, 2, 3, 4]


def test_sphere_bounds_and_point_strength_affect_selection_without_point_rendering():
    data, sections, node = fixture(kind=2, falloff=1, intensity=1)
    state = read_light_state(data, node, sections)
    assert state["radius"] == 3
    assert state["attenuation"][1] == pytest.approx(100)
    selected, _ = select_cell_lights([[12]], [0], {12: light(state, position=(10, 0, 0))}, [-1, -1, -1, 1, 1, 1])
    assert selected == []
    selected, _ = select_cell_lights([[12]], [0], {12: light(state)}, [-1, -1, -1, 1, 1, 1])
    assert len(selected) == 1


def test_runtime_direction_sign_and_uniform_scale_are_verified():
    assert _direction([2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1]) == [0, 0, -1]
    assert _direction([1, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]) == [0, 0, -1]


def test_sheared_d2_direction_matches_executed_retail_quaternion_path():
    # Expected ray comes from executing A07A0 -> CED30 in the European XBE,
    # not from normalizing a guessed matrix column or quaternion.
    matrix = [1151.7620485963537, -20.716514174414357, 633.1613936657861, 152.8112487792969,
              58.412719563853045, 1279.3058905930639, -81.1156320545903, -22.099716186523438,
              -561.5082274421115, 90.59037594162973, 1290.2982428122511, 465.5301818847656,
              0., 0., 0., 1.]
    original = matrix[:]
    ray = _direction(matrix)
    assert ray == pytest.approx([-.46675238013267517, .05956864729523659, -.8865231275558472], abs=1e-6)
    assert abs(math.hypot(*ray) - 1) > 1e-4  # Native output is not normalized here.
    assert matrix == original


@pytest.mark.parametrize("axis,expected", [(0, [0, 0, 1]), (1, [0, 0, 1]), (2, [0, 0, -1])])
def test_negative_trace_quaternion_branches_retain_direction(axis, expected):
    matrix = [2., 0, 0, 0, 0, 2., 0, 0, 0, 0, 2., 0, 0, 0, 0, 1]
    for other in range(3):
        if other != axis:
            matrix[other * 5] *= -1
    assert _direction(matrix) == pytest.approx(expected)


@pytest.mark.parametrize("damage", ["zero_first_column", "nan", "short"])
def test_invalid_direction_transform_is_rejected_without_changing_source(damage):
    matrix = [1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1.]
    if damage == "zero_first_column":
        matrix[0] = 0
    elif damage == "nan":
        matrix[5] = math.nan
    else:
        matrix.pop()
    with pytest.raises(ValueError):
        _direction(matrix)


def test_scene_products_are_per_instance_and_do_not_recolor_or_mutate_source_normals():
    data, sections, node = fixture()
    node["lightState"] = read_light_state(data, node, sections)
    material = {"lighting": {"sourceVerified": True, "diffuse": [.5, .5, .5, 1],
                             "ambient": [.25, .25, .25, 1], "emissive": [.1, .2, .3, 1]}}
    mesh = {"id": "static", "staticGeometry": True, "cellIndices": [0],
            "serializedBounds": [-1, -1, -1, 1, 1, 1], "sourceNormals": [0, 0, 1],
            "colors": [0, 0, 0], "material": material}
    original = copy.deepcopy(mesh)
    result = {"nodes": [node], "meshes": [mesh]}
    apply_source_lighting(data, sections, result)
    config = mesh["sourceLighting"]
    assert config["ambient"] == [0, 0, 0]
    assert config["emissive"] == [.1, .2, .3]
    assert config["directionals"][0]["direction"] == [0, 0, -1]
    assert config["directionals"][0]["diffuse"] == pytest.approx([.1, .2, .3])
    assert config["selectedSourceIds"] == [12]
    assert mesh["material"] == original["material"] and mesh["colors"] == original["colors"]
    assert mesh["sourceNormals"] == original["sourceNormals"]


def test_ambient_only_and_point_limit_are_explicit():
    data, sections, node = fixture(kind=0)
    node["lightState"] = read_light_state(data, node, sections)
    mesh = {"staticGeometry": True, "cellIndices": [0], "serializedBounds": [-1, -1, -1, 1, 1, 1],
            "sourceNormals": [0, 0, 1], "material": {"lighting": {"sourceVerified": True,
            "diffuse": [1, 1, 1, 1], "ambient": [.5, .5, .5, 1], "emissive": [0, 0, 0, 1]}}}
    result = {"nodes": [node], "meshes": [mesh]}
    apply_source_lighting(data, sections, result)
    assert mesh["sourceLighting"]["ambient"] == pytest.approx([.1, .2, .3])
    assert mesh["sourceLighting"]["directionals"] == []
    node["lightState"]["type"] = "point"
    apply_source_lighting(data, sections, result)
    assert mesh["sourceLighting"]["omittedSourceIds"] == [12]
    assert "point-spot-shaders" in mesh["sourceLighting"]["limitations"]


def test_recalculation_removes_stale_configuration_when_source_is_unsupported():
    data, sections, node = fixture()
    result = {"meshes": [{"sourceNormals": [], "sourceLighting": {"sourceVerified": True}}],
              "lighting": {"sourceVerified": True}, "stats": {"sourceLightingMeshes": 99}}
    apply_source_lighting(data, sections, result)
    assert "sourceLighting" not in result["meshes"][0]
    assert result["stats"]["sourceLightingMeshes"] == 0
    apply_source_lighting(data, [sections[0]], result)
    assert "lighting" not in result and "sourceLightingMeshes" not in result["stats"]


def lighting_backend_fixture():
    from test_transform_backend import transform_fixture
    original, pool = transform_fixture()
    data = bytearray(original[:1984]) + bytearray(512)
    struct.pack_into("<I", data, 12, 5)
    struct.pack_into("<I4sII", data, 64 + 4 * 16, 512, b"levl", 8, 2240)
    record, name, params = 256 + 512, 256 + 950, 256 + 1400
    data[name:name + 5] = b"light"
    struct.pack_into("<IiI", data, record, 5, name - record - 4, 10)
    struct.pack_into("<i", data, record + 12, params - record - 12)
    struct.pack_into("<10f", data, params, 1, 0, 0, 0, .2, .4, .6, 1, 0, 0)
    struct.pack_into("<IH", data, record + 28, 1, 12)
    base, cell, ids = 1984, 2048, 2176
    struct.pack_into("<Ii", data, base + 28, 1, cell - base - 32)
    struct.pack_into("<Ii", data, cell + 64, 1, ids - cell - 68)
    struct.pack_into("<H", data, ids, 12)
    pool["sourceNormals"] = [0., 0., 1.] * 4
    pool["normals"] = pool["sourceNormals"][:]
    pool["material"] = {"lighting": {"sourceVerified": True, "diffuse": [1, 1, 1, 1],
                                    "ambient": [1, 1, 1, 1], "emissive": [0, 0, 0, 1]}}
    return bytes(data), pool


def test_backend_recomputes_light_movement_rotation_and_undo_in_a_temporary_project(tmp_path):
    from editor_backend import StudioBackend
    from renderer_parser import read_sections
    from scene_graph import resolve_scene

    class LightingBackend(StudioBackend):
        def _parse_scene(self, data, level):
            _, pool = lighting_backend_fixture()
            result = resolve_scene(data, [pool], read_sections(data))
            apply_source_lighting(data, read_sections(data), result)
            return result

    source = tmp_path / "source" / "gamedata"
    source.mkdir(parents=True)
    original, _ = lighting_backend_fixture()
    path = source / "training_room.xbr"
    path.write_bytes(original)
    backend = LightingBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    initial = backend.get_scene("training_room")
    selected = next(item for item in initial["objects"] if item["kind"] == "light")
    first_ray = initial["meshes"][0]["sourceLighting"]["directionals"][0]["direction"]
    backend.move("training_room", selected["id"], [100, 200, 300])
    moved = backend.get_scene("training_room")
    moved_node = next(node for node in moved["nodes"] if node["type"] == "light")
    assert moved_node["worldPosition"] == pytest.approx([100, 200, 300])
    assert moved["meshes"][0]["sourceLighting"]["directionals"][0]["direction"] == first_ray
    changed = backend.transform("training_room", selected["id"], rotation=[.4, -.2, .3])["scene"]
    new_ray = changed["meshes"][0]["sourceLighting"]["directionals"][0]["direction"]
    assert new_ray != first_ray and math.sqrt(sum(v * v for v in new_ray)) == pytest.approx(1)
    backend.undo("training_room")
    assert backend.get_scene("training_room")["meshes"][0]["sourceLighting"]["directionals"][0]["direction"] == first_ray
    backend.undo("training_room")
    restored = backend.get_scene("training_room")
    assert restored["nodes"][4]["worldPosition"] == initial["nodes"][4]["worldPosition"]
    assert backend.pending_count() == 0 and path.read_bytes() == original
