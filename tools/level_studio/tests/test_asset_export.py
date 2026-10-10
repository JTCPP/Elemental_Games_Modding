import json
import struct

import pytest
from PIL import Image

from asset_export import write_gltf
from export_assets import browser_rows_for_index, finalize_existing
from validate_asset_export import validate_gltf


def mesh(**overrides):
    result = {"name": "Source", "positions": [0., 0., 0., 1., 0., 0., 0., 1., 0.],
              "indices": [0, 1, 2], "uvs": [0., 0., 1., 0., 0., 1.],
              "uv2": [.1, .2, .3, .4, .5, .6], "colors": [0., 0., 0.] * 3,
              "vertexAlphas": [.25, .5, 1.], "normals": [0., 0., 2.] * 3,
              "material": {"technique": 0, "flags": 16, "opacity": 1.},
              "textureStages": [{"stage": 0, "textureId": "tex", "resourceIndex": 9}]}
    result.update(overrides)
    return result


def test_gltf_preserves_secondary_uv_raw_attributes_and_shared_texture(tmp_path):
    image = tmp_path / "images" / "source.png"
    image.parent.mkdir()
    Image.new("RGBA", (4, 4), (10, 20, 30, 255)).save(image)
    file = tmp_path / "models" / "model.gltf"
    result = write_gltf(file, {"name": "Test", "meshes": [mesh()]}, {"tex": image})
    assert result["triangles"] == 1
    document = json.loads(file.read_text(encoding="utf-8"))
    attributes = document["meshes"][0]["primitives"][0]["attributes"]
    assert {"POSITION", "NORMAL", "TEXCOORD_0", "TEXCOORD_1", "COLOR_0", "_AZURIK_SOURCE_COLOR", "_AZURIK_SOURCE_NORMAL"} <= attributes.keys()
    assert document["images"] == [{"uri": "../images/source.png"}]
    assert document["materials"][0]["doubleSided"] is False
    assert validate_gltf(file, tmp_path)["triangles"] == 1
    normal = document["accessors"][attributes["NORMAL"]]
    view = document["bufferViews"][normal["bufferView"]]
    binary = file.with_suffix(".bin").read_bytes()
    assert struct.unpack_from("<3f", binary, view["byteOffset"]) == (0., 0., 1.)


def test_shader_unused_black_colours_stay_source_data_without_blackening_texture(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (4, 4), "red").save(image)
    file = tmp_path / "model.gltf"
    write_gltf(file, {"meshes": [mesh(material={"technique": 0, "flags": 32})]}, {"tex": image})
    doc = json.loads(file.read_text())
    attributes = doc["meshes"][0]["primitives"][0]["attributes"]
    assert "_AZURIK_SOURCE_COLOR" in attributes and "COLOR_0" not in attributes
    assert doc["materials"][0]["doubleSided"] is True


@pytest.mark.parametrize("technique", [8, 6])
def test_material_mask_does_not_hide_character_or_cut_opaque_terrain(tmp_path, technique):
    image = tmp_path / "source.png"
    Image.new("RGBA", (4, 4), (10, 20, 30, 0)).save(image)
    original = image.read_bytes()
    source = {"technique": technique, "flags": 16, "opacity": 1.,
              "blendType": 500, "alphaFunction": 500, "alphaReference": .1}
    stages = [{"stage": 0, "textureId": "tex", "resourceIndex": 9, "flags": 1 << 29}]
    file = tmp_path / "model.gltf"
    write_gltf(file, {"meshes": [mesh(material=source, textureStages=stages)]},
               {"tex": image}, {"tex": {"alpha": True}})
    material = json.loads(file.read_text())["materials"][0]
    assert material["alphaMode"] == "OPAQUE" and "alphaCutoff" not in material
    assert material["pbrMetallicRoughness"]["baseColorFactor"][3] == 1.
    assert material["extras"]["azurikMaterial"] == source
    assert material["extras"]["azurikTextureStages"] == stages
    assert image.read_bytes() == original
    assert validate_gltf(file, tmp_path)["triangles"] == 1


def test_primitive_restart_index_65535_uses_uint32(tmp_path):
    positions = [0.] * (65536 * 3)
    positions[3:6] = [1., 0., 0.]
    positions[-3:] = [0., 1., 0.]
    file = tmp_path / "large.gltf"
    write_gltf(file, {"meshes": [{"positions": positions, "indices": [0, 1, 65535]}]}, {})
    doc = json.loads(file.read_text())
    accessor = doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]
    assert accessor["componentType"] == 5125
    assert validate_gltf(file, tmp_path)["triangles"] == 1


def test_writer_and_validator_reject_missing_links_and_out_of_range_indices(tmp_path):
    bad = mesh(indices=[0, 1, 3])
    with pytest.raises(ValueError, match="Indices"):
        write_gltf(tmp_path / "invalid.gltf", {"meshes": [bad]}, {})
    file = tmp_path / "valid.gltf"
    write_gltf(file, {"meshes": [mesh()]}, {})
    file.with_suffix(".bin").unlink()
    with pytest.raises(ValueError, match="absent"):
        validate_gltf(file, tmp_path)


def test_existing_exports_are_not_overwritten(tmp_path):
    file = tmp_path / "same.gltf"
    file.write_text("original")
    with pytest.raises(FileExistsError):
        write_gltf(file, {"meshes": [mesh()]}, {})
    assert file.read_text() == "original"


def test_gallery_lists_every_cube_face_and_animation_frame_as_a_png():
    rows = [{"archive": "source.xbr", "categorie": "textures", "identifiant": "cube-1",
             "nom_source": "Cube", "type": "cube", "fichier": "face0.png",
             "images": [f"face{i}.png" for i in range(6)]},
            {"archive": "source.xbr", "categorie": "textures", "identifiant": "anim-2",
             "nom_source": "Animation", "type": "animation", "fichier": "sequence.json",
             "images": ["frame0.png", "frame1.png"]}]
    gallery = browser_rows_for_index(rows)
    assert len(gallery) == 8
    assert len({row["id"] for row in gallery}) == 8
    assert all(row["path"].endswith(".png") and row["images"] == [row["path"]] for row in gallery)


def test_finalize_refuses_to_replace_an_existing_zip(tmp_path):
    output = tmp_path / "pack"
    output.with_suffix(".zip").write_bytes(b"original")
    with pytest.raises(FileExistsError):
        finalize_existing(output, tmp_path)
    assert output.with_suffix(".zip").read_bytes() == b"original"
