import math
import struct

import pytest

from renderer_parser import Section, read_shader_tables


def material_table(vectors, power=16.0, stage_count=0):
    data = bytearray(120)
    struct.pack_into("<II", data, 0, 1, 112)
    # The material table begins at 116, with four source padding bytes.
    # Material records must be exactly 100+124*n bytes, so place at 16.
    struct.pack_into("<i", data, 116, 16 - 116)
    struct.pack_into("<17f", data, 20, *vectors, power)
    struct.pack_into("<I", data, 16 + 96, stage_count)
    return bytes(data), [Section(7, "gshd", 0, len(data), 0)]


def test_material_vectors_preserve_runtime_lighting_inputs():
    # Emission is independent of diffuse and ambient. A black source
    # vertex with nonzero emission must receive that source RGB through
    # VSH fragment52 (v5+c107), even when no runtime light is selected.
    vectors = [1, .75, .5, 1, .2, .3, .4, 1,
               0, 0, 0, 1, .0625, .125, .25, 1]
    data, sections = material_table(vectors)
    row = read_shader_tables(data, sections)[7][0]
    assert row["valid"] is True
    lighting = row["material"]["lighting"]
    assert lighting == {"diffuse": [1, .75, .5, 1],
                        "ambient": pytest.approx([.2, .3, .4, 1]),
                        "specular": [0, 0, 0, 1],
                        "emissive": [.0625, .125, .25, 1],
                        "power": 16, "sourceVerified": True}
    # Alpha remains a separate GSHD field. Emissive.w is not opacity.
    assert row["material"]["opacity"] == 0
    assert row["material"]["lighting"]["emissive"][3] == 1


def test_nonfinite_lighting_is_preserved_but_not_verified():
    vectors = [0.0] * 16
    vectors[12] = math.nan
    data, sections = material_table(vectors)
    lighting = read_shader_tables(data, sections)[7][0]["material"]["lighting"]
    assert lighting["sourceVerified"] is False
    assert math.isnan(lighting["emissive"][0])


def test_incomplete_stage_record_cannot_verify_lighting():
    data, sections = material_table([1.0] * 16, stage_count=1)
    row = read_shader_tables(data, sections)[7][0]
    assert row["valid"] is False
    assert row["material"]["lighting"]["sourceVerified"] is False
