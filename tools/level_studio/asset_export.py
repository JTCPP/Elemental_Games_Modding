"""Portable glTF 2.0 writer for decoded Azurik source meshes.

Original Xbox material and declaration data stay in extras. The standard
material represents only the primary texture, original vertex colours and
opacity; it does not pretend to execute the game's shader combiners.
"""
from __future__ import annotations

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import unicodedata


def slug(value, limit=72):
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip(".-") or "ressource"
    return text[:limit].rstrip(".-")


def source_extras(row):
    """Keep metadata, excluding decoded arrays and private/cache objects."""
    excluded = {"meshes", "textures", "positions", "indices", "uvs", "uv2", "colors", "vertexAlphas",
                "normals", "sourceNormals", "packedNormals", "parts", "_localPositions", "_localOrigin"}
    return {key: value for key, value in row.items() if key not in excluded and not key.startswith("_")}


def _float_bytes(values):
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError("Attribut de géométrie non fini.")
    packed = array("f", values)
    if os.sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes(), list(packed)


def neutralize_unverified_alpha(material):
    """Keep unsupported Xbox alpha masks from hiding the glTF preview.

    A standard glTF material cannot execute the Xbox combiner. In particular,
    technique 8's primary texture alpha is a material mask, not proven surface
    coverage. Its source parameters and image are retained without changes.
    Technique 6's verified terrain combiner uses opacity alone as its alpha.
    """
    source = material.get("extras", {}).get("azurikMaterial", {})
    technique = source.get("technique")
    unsupported = isinstance(technique, int) and technique not in (0, 1, 4, 6)
    opaque_terrain = technique == 6 and source.get("opacity", 1.) == 1.
    if not (unsupported or opaque_terrain):
        return False
    before = json.dumps(material, sort_keys=True)
    material["alphaMode"] = "OPAQUE"
    material.pop("alphaCutoff", None)
    material["pbrMetallicRoughness"]["baseColorFactor"][3] = 1.
    material["extras"]["alphaPreview"] = (
        "Opaque preview: primary Xbox alpha is not verified surface coverage; source retained"
        if unsupported else "Opaque terrain: verified technique 6 alpha is source opacity, not a texture mask")
    return before != json.dumps(material, sort_keys=True)


def write_gltf(path, model, texture_paths, texture_metadata=None):
    """Write one self-contained geometry document with shared external PNGs.

    texture_paths maps reader IDs and resource indices to absolute PNG paths.
    All URI paths are relative to the document. POSITION retains source Z-up
    values; an explicit root rotation converts the scene to glTF's Y-up.
    """
    path = Path(path)
    if path.exists() or path.with_suffix(".bin").exists():
        raise FileExistsError(f"Export déjà présent : {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    texture_metadata = texture_metadata or {}
    binary = bytearray()
    doc = {"asset": {"version": "2.0", "generator": "Azurik Level Studio — export original V5"},
           "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"name": model.get("name", "Azurik"), "rotation": [-math.sqrt(.5), 0., 0., math.sqrt(.5)], "children": []}],
           "meshes": [], "accessors": [], "bufferViews": [], "buffers": [], "materials": [],
           "images": [], "textures": [], "samplers": [{"magFilter": 9729, "minFilter": 9729, "wrapS": 10497, "wrapT": 10497}],
           "extensionsUsed": ["KHR_materials_unlit"],
           "extras": {"azurik": source_extras(model), "sourceCoordinateSystem": "Z-up, metres",
                      "gltfCoordinateConversion": "root quaternion X=-90 degrees; source positions unchanged",
                      "materialPreview": "primary texture and original vertex colour; Xbox shaders are metadata",
                      "skeletalAnimationExported": False}}
    texture_indices, material_indices = {}, {}

    def accessor(blob, component, kind, count, target=34962, normalized=False, values=None, width=None):
        binary.extend(b"\0" * (-len(binary) % 4))
        view_index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob), "target": target})
        binary.extend(blob)
        result = {"bufferView": view_index, "byteOffset": 0, "componentType": component, "count": count, "type": kind}
        if normalized:
            result["normalized"] = True
        if values is not None:
            result["min"] = [min(values[axis::width]) for axis in range(width)]
            result["max"] = [max(values[axis::width]) for axis in range(width)]
        index = len(doc["accessors"])
        doc["accessors"].append(result)
        return index

    def float_accessor(values, width, bounds=False):
        if len(values) % width:
            raise ValueError("Longueur d’attribut incompatible avec son type.")
        blob, encoded = _float_bytes(values)
        return accessor(blob, 5126, {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[width], len(values) // width,
                        values=encoded if bounds else None, width=width)

    def texture(identifier, resource=None):
        image = texture_paths.get(identifier) or texture_paths.get(resource)
        if image is None:
            return None
        image = Path(image)
        if not image.is_file():
            raise ValueError(f"Image liée absente : {image}")
        uri = Path(os.path.relpath(image, path.parent)).as_posix()
        if uri not in texture_indices:
            index = len(doc["images"])
            doc["images"].append({"uri": uri})
            doc["textures"].append({"source": index, "sampler": 0})
            texture_indices[uri] = index
        return texture_indices[uri]

    def material(mesh, part):
        source = mesh.get("material", {})
        stages = mesh.get("textureStages", [])
        first = next((stage for stage in stages if stage.get("stage") == 0), None)
        primary = part.get("textureId") or (first or {}).get("textureId")
        primary_index = texture(primary, (first or {}).get("resourceIndex"))
        opacity = source.get("opacity", 1.)
        opacity = max(0., min(1., opacity)) if isinstance(opacity, (int, float)) and math.isfinite(opacity) else 1.
        row = {"name": mesh.get("name", "Matériau"),
               "pbrMetallicRoughness": {"baseColorFactor": [1., 1., 1., opacity], "metallicFactor": 0., "roughnessFactor": 1.},
               "doubleSided": bool(source.get("flags", 0) & 0x20),
               "extensions": {"KHR_materials_unlit": {}},
               "extras": {"azurikMaterial": source, "azurikTextureStages": stages,
                          "previewLimitations": "Xbox combiner, UV transforms, reflection and texture animation are not executed"}}
        if primary_index is not None:
            row["pbrMetallicRoughness"]["baseColorTexture"] = {"index": primary_index, "texCoord": 0}
        # Link every secondary image as well, without inventing a standard
        # glTF effect or turning a missing stage 0 into a primary texture.
        row["extras"]["textureStageLinks"] = [{"stage": stage.get("stage"),
                                                 "gltfTexture": texture(stage.get("textureId"), stage.get("resourceIndex"))}
                                                for stage in stages]
        info = texture_metadata.get(primary) or texture_metadata.get((first or {}).get("resourceIndex")) or {}
        blend = source.get("blendType", 500)
        source_blends = blend in (1, 2, 3, 4, 5, 6) or opacity < 1 or bool((first or {}).get("flags", 0) & (1 << 29))
        alpha_function = source.get("alphaFunction", 0)
        if source_blends:
            row["alphaMode"] = "BLEND"
        elif info.get("alpha") and alpha_function != 0:
            row["alphaMode"] = "MASK"
            reference = source.get("alphaReference", .1)
            row["alphaCutoff"] = max(0., min(1., math.floor(reference * 255) / 255)) if math.isfinite(reference) else .1
        neutralize_unverified_alpha(row)
        key = json.dumps(row, sort_keys=True, allow_nan=False)
        if key not in material_indices:
            material_indices[key] = len(doc["materials"])
            doc["materials"].append(row)
        return material_indices[key]

    for mesh in model.get("meshes", []):
        count = len(mesh["positions"]) // 3
        if not count or len(mesh["positions"]) != count * 3:
            raise ValueError("Modèle exporté sans positions valides.")
        indices = mesh["indices"]
        if not indices or len(indices) % 3 or min(indices) < 0 or max(indices) >= count:
            raise ValueError("Indices du modèle exporté hors de ses sommets.")
        attributes = {"POSITION": float_accessor(mesh["positions"], 3, bounds=True)}
        for key, semantic, width in (("uvs", "TEXCOORD_0", 2), ("uv2", "TEXCOORD_1", 2)):
            if mesh.get(key):
                if len(mesh[key]) != count * width:
                    raise ValueError(f"Attribut {key} de longueur incohérente.")
                attributes[semantic] = float_accessor(mesh[key], width)
        if mesh.get("colors"):
            if len(mesh["colors"]) != count * 3 or (mesh.get("vertexAlphas") and len(mesh["vertexAlphas"]) != count):
                raise ValueError("Couleurs du modèle de longueur incohérente.")
            rgba = bytearray()
            for index in range(count):
                channels = mesh["colors"][index * 3:index * 3 + 3] + [mesh.get("vertexAlphas", [])[index] if mesh.get("vertexAlphas") else 1.]
                if not all(math.isfinite(value) and 0 <= value <= 1 for value in channels):
                    raise ValueError("Couleur hors de l’intervalle glTF.")
                rgba.extend(round(value * 255) for value in channels)
            attributes["_AZURIK_SOURCE_COLOR"] = accessor(rgba, 5121, "VEC4", count, normalized=True)
            stages = mesh.get("textureStages", [])
            primary = next((stage for stage in stages if stage.get("stage") == 0), {})
            has_primary = bool(texture_paths.get(primary.get("textureId")) or texture_paths.get(primary.get("resourceIndex")))
            source_material = mesh.get("material", {})
            uses_colour = (source_material.get("technique") in (0, 1, 4, 6) and bool(source_material.get("flags", 0) & 0x10)) or not has_primary
            if uses_colour:
                preview = bytearray(rgba)
                # The verified Xbox alpha combiner uses texture alpha and
                # shader opacity, not the stored vertex alpha channel.
                preview[3::4] = bytes([255]) * count
                attributes["COLOR_0"] = accessor(preview, 5121, "VEC4", count, normalized=True)
        if mesh.get("normals"):
            if len(mesh["normals"]) != count * 3:
                raise ValueError("Normales de longueur incohérente.")
            normalized, has_zero = [], False
            for index in range(count):
                vector = mesh["normals"][index * 3:index * 3 + 3]
                length = math.sqrt(sum(value * value for value in vector))
                if not math.isfinite(length) or length < 1e-12:
                    has_zero = True
                    break
                normalized.extend(value / length for value in vector)
            if not has_zero:
                attributes["NORMAL"] = float_accessor(normalized, 3)
            # This custom attribute keeps the original float vectors, even
            # when zero source normals cannot be represented as glTF NORMAL.
            attributes["_AZURIK_SOURCE_NORMAL"] = float_accessor(mesh.get("sourceNormals", mesh["normals"]), 3)
        if mesh.get("packedNormals"):
            if len(mesh["packedNormals"]) != count * 4:
                raise ValueError("Normales compactes de longueur incohérente.")
            attributes["_AZURIK_PACKED_NORMAL"] = accessor(bytes(mesh["packedNormals"]), 5121, "VEC4", count)
        # 0xffff/0xffffffff are reserved primitive-restart values in glTF.
        component, code = (5123, "H") if max(indices) < 65535 else (5125, "I")
        parts = mesh.get("parts") or [{"start": 0, "count": len(indices), "partIndex": 0}]
        primitives = []
        for part in parts:
            start, length = part["start"], part["count"]
            if start < 0 or length < 0 or start + length > len(indices) or length % 3:
                raise ValueError("Partie de triangles hors de la géométrie.")
            if not length:
                continue
            packed = array(code, indices[start:start + length])
            if os.sys.byteorder != "little":
                packed.byteswap()
            primitive = {"attributes": attributes, "indices": accessor(packed.tobytes(), component, "SCALAR", length, target=34963),
                         "material": material(mesh, part), "mode": 4,
                         "extras": {"azurikPart": part}}
            primitives.append(primitive)
        if not primitives:
            raise ValueError("Modèle sans partie triangulée exportable.")
        mesh_index = len(doc["meshes"])
        doc["meshes"].append({"name": mesh.get("name", f"Partie {mesh_index}"), "primitives": primitives,
                              "extras": {"azurik": source_extras(mesh)}})
        doc["nodes"][0]["children"].append(len(doc["nodes"]))
        doc["nodes"].append({"name": mesh.get("name", f"Partie {mesh_index}"), "mesh": mesh_index})
    if not doc["meshes"]:
        raise ValueError("Le modèle ne contient pas de géométrie exportable.")
    doc["buffers"] = [{"uri": path.with_suffix(".bin").name, "byteLength": len(binary)}]
    if not doc["images"]:
        for key in ("images", "textures", "samplers"):
            doc.pop(key)
    path.with_suffix(".bin").write_bytes(binary)
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"meshes": len(doc["meshes"]), "vertices": sum(accessor["count"] for accessor in doc["accessors"] if accessor.get("min") and accessor["type"] == "VEC3"),
            "triangles": sum(len(mesh["indices"]) // 3 for mesh in model["meshes"]),
            "binaryBytes": len(binary), "gltfBytes": path.stat().st_size, "imageLinks": len(doc.get("images", []))}


def attach_static_normals(mesh, data, section, used, matrix=None):
    """Preserve source normals through exact selected-vertex correspondence."""
    result = dict(mesh)
    declaration = struct.unpack_from("<I", data, section.offset + 44)[0]
    strides = {0x80001111: 16, 0x80002111: 18, 0x80004111: 22, 0x80040111: 30, 0x252: 44}
    stride = strides.get(declaration)
    if stride is None:
        return result
    if stride != 44:
        result["packedNormals"] = b"".join(data[section.offset + 64 + vertex * stride + 6:section.offset + 64 + vertex * stride + 10] for vertex in used)
        result["normalExport"] = "packed source bytes preserved; interpretation not asserted"
        return result
    normals = [value for vertex in used for value in struct.unpack_from("<3f", data, section.offset + 64 + vertex * 44 + 12)]
    result["sourceNormals"] = normals
    if matrix:
        from scene_graph import inverse_affine
        try:
            inverse = inverse_affine(matrix)
        except ValueError:
            result["normalExport"] = "singular source transform; raw normals preserved"
            result["normals"] = normals
            return result
        transformed = []
        for offset in range(0, len(normals), 3):
            vector = normals[offset:offset + 3]
            transformed.extend(sum(inverse[column * 4 + row] * vector[column] for column in range(3)) for row in range(3))
        result["normals"] = transformed
    else:
        result["normals"] = normals
    result["normalExport"] = "source float3; glTF NORMAL normalized; raw source vectors preserved"
    return result
