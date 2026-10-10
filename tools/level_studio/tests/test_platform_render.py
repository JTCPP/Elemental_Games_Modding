"""Retail platform opacity and sort bias must remain distinct from GSHD data."""
import copy
import struct

import pytest

from platform_render import read_platform_render_state
from scene_graph import read_graph, resolve_scene
from test_scene_graph import graph_fixture


def platform_fixture(game_visible=0.75, mesh_offset=2_000_000.0):
    data, sections, pool = graph_fixture()
    record, params = 384, 1320
    struct.pack_into('<I', data, record + 8, 37)
    struct.pack_into('<i', data, record + 12, params - record - 12)
    values = [0.0] * 37
    values[0], values[10], values[24] = 1.0, game_visible, mesh_offset
    struct.pack_into('<37f', data, params, *values)
    pool['material'] = {'technique': 4, 'flags': 16, 'opacity': 0.6,
                        'depthWrite': 2, 'blendType': 500}
    _, nodes = read_graph(data, sections)
    return data, sections, pool, nodes[2]


def test_platform_opacity_and_sort_bias_read_exact_retail_channels_without_writing_source():
    data, sections, _, node = platform_fixture()
    original = bytes(data), copy.deepcopy(node), copy.deepcopy(sections)
    state = read_platform_render_state(bytes(data), node, sections)
    assert state['sourceVerified'] is True
    assert state['gameVisible'] == 0.75
    assert state['visibility'] == 1.0
    assert state['meshOffset'] == 2_000_000.0
    assert state['gameVisibleOffset'] == 1320 + 10 * 4
    assert state['meshOffsetOffset'] == 1320 + 24 * 4
    assert state['visibilityOffset'] == 1320
    assert original == (bytes(data), node, sections)


@pytest.mark.parametrize('game_visible', [0.0, 0.25, 0.75, 1.0])
def test_partial_platform_visibility_is_an_opacity_value_not_a_boolean(game_visible):
    data, sections, _, node = platform_fixture(game_visible=game_visible)
    state = read_platform_render_state(bytes(data), node, sections)
    assert state['gameVisible'] == pytest.approx(game_visible)
    assert type(state['gameVisible']) is float


@pytest.mark.parametrize('count', [0, 10, 24, 36, 38, 20001])
def test_unknown_platform_parameter_layout_is_not_guessed(count):
    data, sections, _, node = platform_fixture()
    struct.pack_into('<I', data, node['recordOffset'] + 8, count)
    assert read_platform_render_state(bytes(data), node, sections) is None


@pytest.mark.parametrize('channel', [0, 10, 24])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_non_finite_platform_alpha_or_distance_sort_bias_is_rejected(channel, value):
    data, sections, _, node = platform_fixture()
    struct.pack_into('<f', data, node['paramsOffset'] + channel * 4, value)
    with pytest.raises(ValueError):
        read_platform_render_state(bytes(data), node, sections)


@pytest.mark.parametrize('damage', ['record_before_section', 'record_after_section',
                                  'parameter_table_after_section', 'parameter_table_before_section',
                                  'boolean_record'])
def test_platform_render_fields_cannot_escape_their_node_resource(damage):
    data, sections, _, node = platform_fixture()
    node = copy.deepcopy(node)
    if damage == 'record_before_section':
        node['recordOffset'] = -1
    elif damage == 'record_after_section':
        node['recordOffset'] = sections[0].size - 8
    elif damage == 'parameter_table_after_section':
        struct.pack_into('<i', data, node['recordOffset'] + 12,
                         1496 - node['recordOffset'] - 12)
        node['paramsOffset'] = 1496
    elif damage == 'parameter_table_before_section':
        struct.pack_into('<i', data, node['recordOffset'] + 12,
                         -4 - node['recordOffset'] - 12)
        node['paramsOffset'] = -4
    else:
        node['recordOffset'] = True
    with pytest.raises(ValueError):
        read_platform_render_state(bytes(data), node, sections)


def test_serialized_parameter_pointer_remains_authoritative_over_untrusted_metadata():
    data, sections, _, node = platform_fixture()
    node['paramsOffset'] += 4
    state = read_platform_render_state(bytes(data), node, sections)
    assert state['visibilityOffset'] == 1320
    assert state['gameVisible'] == 0.75
    assert state['meshOffset'] == 2_000_000.0


@pytest.mark.parametrize('channel', [0, 10])
@pytest.mark.parametrize('value', [-0.25, 1.25])
def test_invalid_platform_opacity_is_not_silently_clamped(channel, value):
    data, sections, _, node = platform_fixture()
    struct.pack_into('<f', data, node['paramsOffset'] + channel * 4, value)
    with pytest.raises(ValueError):
        read_platform_render_state(bytes(data), node, sections)


def test_fractional_source_visibility_and_game_visible_are_kept_as_separate_factors():
    data, sections, _, node = platform_fixture(game_visible=0.75)
    struct.pack_into('<f', data, node['paramsOffset'], 0.5)
    state = read_platform_render_state(bytes(data), node, sections)
    assert state['visibility'] == 0.5
    assert state['gameVisible'] == 0.75


def test_resolved_platform_instances_preserve_gshd_opacity_colors_and_positions():
    data, sections, pool, node = platform_fixture()
    original = bytes(data), copy.deepcopy(pool)
    source_scene = resolve_scene(bytes(data), [pool], sections)
    assert len(source_scene['meshes']) == 2
    expected_state = read_platform_render_state(bytes(data), node, sections)
    for mesh in source_scene['meshes']:
        assert mesh['platformRender'] == expected_state
        assert mesh['material']['opacity'] == 0.6
        assert mesh['colors'] == [1.0] * 9
        # MeshOffset biases distance sorting; it never translates geometry.
        assert mesh['positions'][:3] == pytest.approx([10, 22, 30], abs=1e-5)
        assert mesh['worldMatrix'][3:12:4] == pytest.approx([10, 22, 30], abs=1e-5)
        assert mesh['visible'] is True
    first, second = source_scene['meshes']
    assert first['platformRender'] is not second['platformRender']
    assert original == (bytes(data), pool)


def test_non_platform_nodes_do_not_receive_platform_render_metadata():
    data, sections, _, _ = platform_fixture()
    _, nodes = read_graph(data, sections)
    assert read_platform_render_state(bytes(data), nodes[0], sections) is None
