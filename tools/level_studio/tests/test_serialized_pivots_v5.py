"""Synthetic NODE records checked against independent retail matrix results.

Fixtures contain decoded numeric inputs and original-loader reference matrices,
never copied serialized source records, pointers, or executable program bytes.
The test constructs every directory, record, and parameter byte itself.
"""
import json
import struct
from pathlib import Path

import pytest

from renderer_parser import Section
from scene_graph import read_graph

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests/fixtures/serialized-transform-fixtures-v5.json').read_text())['fixtures']


@pytest.mark.parametrize('case', CASES, ids=[f"{case['level']}-{case['node']}" for case in CASES])
def test_serialized_pivot_order_matches_original_loader(case):
    payload = bytearray(1024)
    struct.pack_into('<Ii', payload, 0, 1, 28)
    struct.pack_into('<i', payload, 32, 128-32)
    payload[400:409] = b'transform'
    struct.pack_into('<IiIi', payload, 128, 9, 400-132, 10, 512-140)
    struct.pack_into('<i', payload, 152, -1)
    struct.pack_into('<I', payload, 128 + 40, case['inheritMode'])
    for offset, field in ((44, 'scalePivot'), (56, 'scalePivotTranslation'),
                          (68, 'rotatePivot'), (80, 'rotatePivotTranslation')):
        struct.pack_into('<3f', payload, 128 + offset, *case[field])
    struct.pack_into('<4f', payload, 128 + 92, 0, 0, 0, 1)
    struct.pack_into('<I', payload, 128 + 108, case['rotationOrder'])
    struct.pack_into('<10f', payload, 512, case['visibility'], *case['position'],
                     *case['rotation'], *case['scale'])
    _, nodes = read_graph(payload, [Section(0, 'node', 0, len(payload), 8)])
    node = nodes[0]
    for field in ('scalePivot', 'scalePivotTranslation', 'rotatePivot', 'rotatePivotTranslation',
                  'position', 'rotation', 'scale', 'inheritMode', 'rotationOrder', 'visibility'):
        assert node[field] == case[field]
    assert node['transformVerified']
    # Retail arithmetic is float32 while the reader composes in float64.
    assert node['localMatrix'] == pytest.approx(case['matrix'], abs=5e-4, rel=1e-6)
