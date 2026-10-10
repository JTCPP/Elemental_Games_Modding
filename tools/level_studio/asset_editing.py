"""Validated project geometry and bounded in-place retail asset replacements.

New allocations are project assets. Native replacements preserve resource
sizes, indices, headers, shader bindings and collision data. Every writable
range is re-derived from the original resource rather than saved offsets.
"""
from __future__ import annotations

import base64
import io
import math
import struct

ASSET_WARNING = "Les modèles importés ou dupliqués et les nouvelles textures sont conservés dans le projet et dans le dossier assets de l’export. L’allocation de nouvelles ressources dans le jeu n’est pas encore prise en charge."
MODEL_WARNING = "Remplacement natif : les sommets de la réserve partagée sont modifiés dans toutes ses instances ; les collisions et les matériaux d’origine sont conservés."
TEXTURE_WARNING = "Remplacement natif : toutes les utilisations de cette surface et tous ses niveaux de détail utilisent la nouvelle image."


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise ValueError("Nom de ressource invalide (1 à 160 caractères).")
    return value.strip()


def _floats(values, width, count=None, maximum=100000):
    if not isinstance(values, list) or not values or len(values) % width:
        raise ValueError("Tableau de géométrie invalide.")
    if count is not None and len(values) != count * width:
        raise ValueError("Les attributs du modèle doivent correspondre au nombre de sommets.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > maximum for v in values):
        raise ValueError("Attributs du modèle non finis ou hors limites.")
    return [float(v) for v in values]


def geometry(value):
    if not isinstance(value, dict) or set(value) - {"positions", "indices", "uvs", "normals", "textureId", "coordinateSpace"}:
        raise ValueError("Le modèle doit contenir positions, indices et éventuellement uvs, normals et textureId.")
    positions = _floats(value.get("positions"), 3)
    count = len(positions) // 3
    indices = value.get("indices")
    if count > 100000 or not isinstance(indices, list) or not indices or len(indices) > 600000 or len(indices) % 3:
        raise ValueError("Modèle trop volumineux ou triangles invalides (100 000 sommets maximum).")
    if any(type(i) is not int or not 0 <= i < count for i in indices):
        raise ValueError("Indice de triangle hors du modèle.")
    if any(len(set(indices[n:n + 3])) < 3 for n in range(0, len(indices), 3)):
        raise ValueError("Les triangles dégénérés ne sont pas acceptés.")
    result = {"positions": positions, "indices": indices[:]}
    if "coordinateSpace" in value:
        if value["coordinateSpace"] not in ("asset", "world"):
            raise ValueError("Choisissez les coordonnées locales du modèle ou les coordonnées du niveau.")
        result["coordinateSpace"] = value["coordinateSpace"]
    for key, width in (("uvs", 2), ("normals", 3)):
        if value.get(key):
            result[key] = _floats(value[key], width, count)
    if value.get("textureId") is not None:
        if not isinstance(value["textureId"], str) or len(value["textureId"]) > 100:
            raise ValueError("Référence de texture invalide.")
        result["textureId"] = value["textureId"]
    return result


def png_image(value):
    from PIL import Image, UnidentifiedImageError
    if not isinstance(value, str) or len(value) > 24 * 1024 * 1024:
        raise ValueError("Image trop volumineuse (16 Mo maximum).")
    if not value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
        raise ValueError("Importez une image PNG, JPEG ou WebP.")
    try:
        blob = base64.b64decode(value.split(",", 1)[1], validate=True)
        if len(blob) > 16 * 1024 * 1024:
            raise ValueError("Image trop volumineuse.")
        with Image.open(io.BytesIO(blob)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or max(image.size) > 4096 or image.width * image.height > 16_777_216:
                raise ValueError("L’image doit mesurer au maximum 4096 × 4096 pixels.")
            image.load()
            rgba = image.convert("RGBA")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Image invalide ou endommagée.") from exc
    output = io.BytesIO()
    rgba.save(output, "PNG")
    return rgba, output.getvalue()


def _rgb565(rgb):
    return ((rgb[0] * 31 + 127) // 255 << 11) | ((rgb[1] * 63 + 127) // 255 << 5) | ((rgb[2] * 31 + 127) // 255)


def _rgb888(value):
    return (((value >> 11) & 31) * 255 // 31, ((value >> 5) & 63) * 255 // 63, (value & 31) * 255 // 31)


def _dxt(image, fmt):
    """Encode standard row-major BC1/BC2 blocks, including edge replication."""
    pixels, width, height = image.load(), image.width, image.height
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block = [pixels[min(bx + x, width - 1), min(by + y, height - 1)] for y in range(4) for x in range(4)]
            opaque = [p for p in block if fmt != "DXT1" or p[3] >= 128]
            # Pick actual colours along the block's longest colour axis.
            # Independent channel minima/maxima invent yellow endpoints for
            # red/green blocks and visibly degrade imported textures.
            if opaque:
                seed = min(opaque, key=lambda p: sum(p[:3]))
                high = max(opaque, key=lambda p: sum((p[c] - seed[c]) ** 2 for c in range(3)))
                low = max(opaque, key=lambda p: sum((p[c] - high[c]) ** 2 for c in range(3)))
            else:
                low = high = (0, 0, 0)
            a, b = _rgb565(high), _rgb565(low)
            transparent = fmt == "DXT1" and len(opaque) != 16
            if transparent:
                a, b = min(a, b), max(a, b)
            else:
                a, b = max(a, b), min(a, b)
                if a == b:
                    if a < 65535:
                        a += 1
                    else:
                        b -= 1
            colors = [_rgb888(a), _rgb888(b)]
            if transparent:
                colors.extend([tuple((colors[0][c] + colors[1][c]) // 2 for c in range(3)), (0, 0, 0)])
            else:
                colors.extend([tuple((2 * colors[0][c] + colors[1][c]) // 3 for c in range(3)), tuple((colors[0][c] + 2 * colors[1][c]) // 3 for c in range(3))])
            bits = 0
            for index, pixel in enumerate(block):
                choice = 3 if transparent and pixel[3] < 128 else min(range(3 if transparent else 4), key=lambda c: sum((pixel[a] - colors[c][a]) ** 2 for a in range(3)))
                bits |= choice << (2 * index)
            if fmt == "DXT3":
                alpha = sum(((p[3] * 15 + 127) // 255) << (4 * i) for i, p in enumerate(block))
                out.extend(struct.pack("<Q", alpha))
            out.extend(struct.pack("<HHI", a, b, bits))
    return bytes(out)


def texture_plan(data, section, image):
    """Write only the independently validated contiguous original mip pixels."""
    from PIL import Image
    from renderer_parser import decode_texture_faces
    faces, fmt, kind = decode_texture_faces(data, section)
    if kind != "2d":
        raise ValueError("Remplacement natif réservé aux images 2D ; choisissez une image de la séquence pour une texture animée.")
    if image.size != faces[0].size:
        raise ValueError(f"L’export dans le jeu exige la taille originale {faces[0].width} × {faces[0].height}.")
    if max(image.size) > 1024:
        raise ValueError("Remplacement natif limité à 1024 pixels ; cette image reste une prévisualisation du projet.")
    base, stop = section.offset, section.offset + section.size
    flags = struct.unpack_from("<I", data, base)[0]
    levels = max(image.size).bit_length() if flags & 0x10000000 else 1
    if flags & 0x10000000 and any(v & (v - 1) for v in image.size):
        raise ValueError("Chaîne de mipmaps non validée pour une taille non puissance de deux.")
    offset = base + 40 + struct.unpack_from("<i", data, base + 40)[0]
    result = []
    current = image
    for _ in range(levels):
        payload = current.tobytes("raw", "BGRA") if fmt == "BGRA8" else _dxt(current, fmt)
        if offset + len(payload) > stop:
            raise ValueError("La chaîne de mipmaps dépasse la surface d’origine.")
        result.append((offset, payload))
        offset += len(payload)
        current = current.resize((max(1, current.width // 2), max(1, current.height // 2)), Image.Resampling.LANCZOS)
    return result


def _validate_static_bounds(data, sections, resource_index, positions):
    """Keep every static use, including unshown LODs, inside original culling bounds."""
    from renderer_parser import decode_pushbuffer
    from static_scene import read_static_descriptors
    descriptors = read_static_descriptors(data, sections)["descriptors"]
    primitive_cache = {}
    for descriptor in descriptors:
        for binding in descriptor["bindings"]:
            if binding["meshResource"] != resource_index:
                continue
            primitive = binding["primitiveResource"]
            if primitive not in primitive_cache:
                indices = decode_pushbuffer(data, sections[primitive], len(positions) // 3)
                if not indices:
                    raise ValueError("Primitive statique sans triangle : remplacement natif non validé.")
                primitive_cache[primitive] = sorted(set(indices))
            used = primitive_cache[primitive]
            actual = ([min(positions[vertex * 3 + axis] for vertex in used) for axis in range(3)]
                      + [max(positions[vertex * 3 + axis] for vertex in used) for axis in range(3)])
            bounds = descriptor["bounds"]
            # Match static_scene exactly, after native float/SHORT quantization.
            epsilon = 1 / 256 + max(abs(value) for value in bounds) * 1e-6
            if any(actual[axis] < bounds[axis] - epsilon
                   or actual[axis + 3] > bounds[axis + 3] + epsilon for axis in range(3)):
                raise ValueError("Le remplacement dépasse les bornes statiques LEVL d’une instance ou d’une alternative LOD ; il reste un aperçu du projet.")


def mesh_plan(data, section, primitive, model):
    """Preserve native topology; update exactly its selected vertex attributes."""
    from renderer_parser import decode_mesh, decode_pushbuffer, read_sections
    sections = read_sections(data)
    if section.offset < 0 or section.size < 64 or section.offset + section.size > len(data):
        raise ValueError("Les bornes de la ressource du modèle sont invalides.")
    # decode_mesh also reads old synthetic fixtures; native editing has a
    # stricter ownership rule, including vertex pools without index tables.
    count = struct.unpack_from("<I", data, section.offset + 24)[0]
    declaration = struct.unpack_from("<I", data, section.offset + 44)[0]
    from renderer_parser import _DECLARATIONS
    stride = _DECLARATIONS.get(declaration)
    vertex_end = section.offset + 64 + count * (stride or 0)
    table = section.offset + 52 + struct.unpack_from("<I", data, section.offset + 52)[0]
    if not stride or count <= 0 or vertex_end > section.offset + section.size or table > section.offset + section.size:
        raise ValueError("Le flux de sommets dépasse sa ressource native.")
    pool = decode_mesh(data, section, {}, {})
    if primitive is None:
        source_indices = pool["indices"]
        used = list(range(len(pool["positions"]) // 3))
    else:
        if type(primitive) is not int or not 0 <= primitive < len(sections) or sections[primitive].tag != "pbrc":
            raise ValueError("Primitives du modèle non validées.")
        source_indices = decode_pushbuffer(data, sections[primitive], len(pool["positions"]) // 3)
        used = sorted(set(source_indices))
    remap = {source: target for target, source in enumerate(used)}
    indices = [remap[i] for i in source_indices]
    if len(model["positions"]) != len(used) * 3 or model["indices"] != indices:
        raise ValueError("L’export natif exige le même nombre de sommets et exactement les triangles du modèle d’origine.")
    stride = pool["vertexStride"]
    patches, candidate_positions = [], pool["positions"][:]
    for index, source in enumerate(used):
        offset = section.offset + 64 + source * stride
        xyz = [model["positions"][index * 3 + a] - pool["origin"][a] for a in range(3)]
        if stride == 44:
            position = struct.pack("<3f", *xyz)
        else:
            quantized = [round(v * 256) for v in xyz]
            if any(not -32768 <= v <= 32767 for v in quantized):
                raise ValueError("Les sommets importés dépassent la plage de quantification de ce modèle Xbox.")
            position = struct.pack("<3h", *quantized)
        decoded = struct.unpack("<3f" if stride == 44 else "<3h", position)
        for axis in range(3):
            candidate_positions[source * 3 + axis] = pool["origin"][axis] + decoded[axis] / (1 if stride == 44 else 256)
        patches.append((offset, position))
        if model.get("normals"):
            normal = model["normals"][index * 3:index * 3 + 3]
            if stride == 44:
                packed = struct.pack("<3f", *normal)
                normal_offset = offset + 12
            else:
                if any(abs(v) > 1 for v in normal):
                    raise ValueError("Les normales compressées doivent être comprises entre −1 et 1.")
                values = [round(normal[0] * 1023), round(normal[1] * 1023), round(normal[2] * 511)]
                packed = struct.pack("<I", (values[0] & 2047) | ((values[1] & 2047) << 11) | ((values[2] & 1023) << 22))
                normal_offset = offset + 6
            patches.append((normal_offset, packed))
        if model.get("uvs"):
            uv = model["uvs"][index * 2:index * 2 + 2]
            if stride == 16:
                values = [round(v * 255) for v in uv]
                if any(not 0 <= v <= 255 for v in values):
                    raise ValueError("UV incompatibles avec le format Xbox 8 bits.")
                payload = bytes(values)
            elif stride == 18:
                values = [round(v * 1024) for v in uv]
                if any(not -32768 <= v <= 32767 for v in values):
                    raise ValueError("UV incompatibles avec le format Xbox 16 bits.")
                payload = struct.pack("<2h", *values)
            else:
                payload = struct.pack("<2f", *uv)
            patches.append((offset + (28 if stride == 44 else 14), payload))
    if any(offset < section.offset + 64 or offset + len(payload) > vertex_end
           or offset + len(payload) > section.offset + section.size for offset, payload in patches):
        raise ValueError("Un attribut modifié dépasse la réserve de sommets validée.")
    _validate_static_bounds(data, sections, section.index, candidate_positions)
    return patches


def native_plan(data, entry, read_asset):
    from PIL import Image
    from renderer_parser import read_sections
    sections = read_sections(data)
    index = entry.get("resourceIndex")
    if type(index) is not int or not 0 <= index < len(sections):
        raise ValueError("Ressource native de projet invalide.")
    section = sections[index]
    if entry.get("kind") == "texture" and section.tag == "surf":
        with Image.open(io.BytesIO(read_asset(entry["asset"]))) as image:
            image.load()
            return texture_plan(data, section, image.convert("RGBA"))
    if entry.get("kind") == "mesh" and section.tag == "rdms":
        import json
        model = geometry(json.loads(read_asset(entry["asset"])))
        return mesh_plan(data, section, entry.get("primitiveResource"), model)
    raise ValueError("Type de remplacement natif incompatible avec la ressource.")
