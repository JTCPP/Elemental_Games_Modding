"""Read Azurik retail XBR geometry and its own texture/material references.

The v4 table stores resource END offsets relative to header[0x10], not
absolute resource starts. Mesh fields +4/+8 reference the gshd resource and
its shader index. All pointers in shader and primitive tables are relative
to the individual pointer field. Source bytes are never changed here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import io
import hashlib
import json
import math
import re
import struct


@dataclass(frozen=True)
class Section:
    index: int
    tag: str
    offset: int
    size: int
    flags: int


def read_sections(data: bytes) -> list[Section]:
    if len(data) < 64 or data[:4] != b"xobx":
        raise ValueError("Le fichier ne possède pas un en-tête XBR valide.")
    version, count, payload = struct.unpack_from("<I", data, 4)[0], struct.unpack_from("<I", data, 12)[0], struct.unpack_from("<I", data, 16)[0]
    if count > 200000 or 64 + count * 16 > len(data):
        raise ValueError("La table des ressources XBR dépasse le fichier.")
    sections = []
    previous_end = 0
    for index in range(count):
        size, tag, flags, end = struct.unpack_from("<I4sII", data, 64 + index * 16)
        if size == flags == end == 0:
            break
        if not all(32 <= c < 127 for c in tag):
            raise ValueError(f"Type de ressource XBR illisible à l'entrée {index}.")
        # Old synthetic/tool fixtures use absolute start offsets; genuine
        # retail version 4 uses cumulative ends relative to payload base.
        start = payload + previous_end if version == 4 else end
        if start < 64 or start + size > len(data):
            raise ValueError(f"La ressource {index} dépasse le fichier XBR.")
        if version == 4 and end < previous_end + size and not (end == 0 and index == count - 1):
            raise ValueError(f"Les bornes de la ressource {index} se chevauchent.")
        sections.append(Section(index, tag.decode("ascii"), start, size, flags))
        previous_end = end
    return sections


def read_shader_tables(data: bytes, sections: list[Section]) -> dict[int, list[dict]]:
    tables = {}
    for section in sections:
        if section.tag != "gshd" or section.size < 12:
            continue
        base, end = section.offset, section.offset + section.size
        count, relative = struct.unpack_from("<II", data, base)
        table = base + 4 + relative
        if count > 20000 or not base + 8 <= table <= end or table + count * 4 > end:
            continue
        starts = [table + i * 4 + struct.unpack_from("<i", data, table + i * 4)[0]
                  for i in range(count)]
        rows = []
        for index, start in enumerate(starts):
            stop = starts[index + 1] if index + 1 < count else table
            if not base + 8 <= start <= stop <= table or stop - start < 100:
                rows.append({"stages": [], "valid": False})
                continue
            stages = []
            stage_count = struct.unpack_from("<I", data, start + 96)[0]
            valid = stop - start == 100 + stage_count * 124
            if valid:
                for stage_index in range(stage_count):
                    stage = start + 100 + stage_index * 124
                    texture_index, preview_index, texture_slot = struct.unpack_from("<III", data, stage)
                    if texture_index < len(sections) and sections[texture_index].tag == "surf":
                        # Retail XBE D5D90/D6120 build the texture matrix from
                        # these fields. FF0D0 initializes the angle conversion
                        # to pi/180. The animation clock's units are separate.
                        scale, angle, offset_u, offset_v, scroll_u, scroll_v = struct.unpack_from("<6f", data, stage + 32)
                        matrix_mode = struct.unpack_from("<I", data, stage + 56)[0]
                        matrix = list(struct.unpack_from("<16f", data, stage + 60))
                        finite = all(math.isfinite(v) for v in (scale, angle, offset_u, offset_v, scroll_u, scroll_v, *matrix))
                        stages.append({"resourceIndex": texture_index, "stage": stage_index,
                                       "textureSlot": texture_slot, "previewResource": preview_index,
                                       "flags": struct.unpack_from("<I", data, stage + 24)[0],
                                       "combinerMode": struct.unpack_from("<I", data, stage + 28)[0],
                                       "uvTransform": {"scale": scale, "rotationDegrees": angle,
                                                       "offset": [offset_u, offset_v],
                                                       "scrollVelocity": [scroll_u, scroll_v],
                                                       "matrixMode": matrix_mode, "matrix": matrix,
                                                       "matrixLayout": "row-major", "parametersVerified": finite,
                                                       "scrollTimeUnitsVerified": False}})
            # D7E65..D7E72 copies GSHD+4 into VSH context+0x130 via
            # D5800. 9A8C0 uses diffuse(+0x130), ambient(+0x140),
            # specular(+0x150), emissive(+0x160) and power(+0x170).
            # Preserve the source vectors: raw vertex RGB is only one
            # input to oD0, and zero RGB must not imply missing textures.
            lighting_vectors = struct.unpack_from("<17f", data, start + 4)
            lighting = {name: list(lighting_vectors[position:position + 4])
                        for name, position in (("diffuse", 0), ("ambient", 4),
                                               ("specular", 8), ("emissive", 12))}
            lighting.update(power=lighting_vectors[16],
                            sourceVerified=valid and all(math.isfinite(v) for v in lighting_vectors))
            rows.append({"stages": stages, "valid": valid, "offset": start,
                         "stageCount": stage_count,
                         "material": {"technique": struct.unpack_from("<I", data, start)[0],
                                      "flags": struct.unpack_from("<I", data, start + 72)[0],
                                      "opacity": struct.unpack_from("<f", data, start + 76)[0],
                                      "alphaFunction": struct.unpack_from("<I", data, start + 80)[0],
                                      "alphaReference": struct.unpack_from("<f", data, start + 84)[0],
                                      "depthWrite": struct.unpack_from("<I", data, start + 88)[0],
                                      "blendType": struct.unpack_from("<I", data, start + 92)[0],
                                      "lighting": lighting}})
        tables[section.index] = rows
    return tables


def _dds_image(blob: bytes, width: int, height: int, fourcc: bytes):
    from PIL import Image
    block_bytes = 8 if fourcc == b"DXT1" else 16
    size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes
    if len(blob) < size:
        raise ValueError("Données de texture compressées incomplètes.")
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x81007, height, width, size, 0, 1)
    struct.pack_into("<II4s5I", header, 76, 32, 4, fourcc, 0, 0, 0, 0, 0)
    struct.pack_into("<I", header, 108, 0x1000)
    with Image.open(io.BytesIO(bytes(header) + blob[:size])) as image:
        return image.convert("RGBA")


def decode_texture_faces(data: bytes, section: Section):
    """Decode base mips through the surface's individual self-relative pointers.

    Cube surfaces have six pointer fields at +40..+60. Face orientation is
    deliberately not interpreted as a scene sky or an environment map.
    """
    from PIL import Image
    base = section.offset
    if section.size < 128:
        raise ValueError("Cette surface n'est pas une texture image autonome.")
    surface_type = struct.unpack_from("<I", data, base)[0]
    pixel_format = surface_type & 0xFFFF
    # XBE 9CC10 uses bit 28 to choose the number of source mip levels;
    # it is not an image marker. UI surfaces without this bit still hold
    # genuine BGRA/DXT base levels (hourglass, selector and interface).
    if pixel_format not in (0x10, 0x20, 0x40) or surface_type & ~0x3000ffff:
        raise ValueError("Format de surface non pris en charge.")
    width_float, height_float = struct.unpack_from("<2f", data, base + 28)
    if not all(math.isfinite(v) and v.is_integer() and 1 <= v <= 4096 for v in (width_float, height_float)):
        raise ValueError("Dimensions de texture non valides.")
    width, height = int(width_float), int(height_float)
    cube = bool(surface_type & 0x20000000)
    face_count = 6 if cube else 1
    pointers = []
    for face_index in range(face_count):
        field = base + 40 + face_index * 4
        pointers.append(field + struct.unpack_from("<i", data, field)[0])
    if (any(not base + 40 + face_count * 4 <= p < base + section.size for p in pointers)
            or pointers != sorted(set(pointers))):
        raise ValueError("Les pointeurs des faces dépassent la surface ou se chevauchent.")
    images = []
    fmt = {0x10: "BGRA8", 0x20: "DXT1", 0x40: "DXT3"}[pixel_format]
    for face_index, pixels in enumerate(pointers):
        stop = pointers[face_index + 1] if face_index + 1 < face_count else base + section.size
        blob = data[pixels:stop]
        if pixel_format == 0x10:
            if len(blob) < width * height * 4:
                raise ValueError("Pixels BGRA incomplets.")
            image = Image.frombytes("RGBA", (width, height), blob[:width * height * 4], "raw", "BGRA")
        else:
            image = _dds_image(blob, width, height, fmt.encode("ascii"))
        images.append(image)
    return images, fmt, "cube" if cube else "2d"


def decode_texture(data: bytes, section: Section):
    """Compatibility API: first base-mip image, format and surface kind."""
    images, fmt, kind = decode_texture_faces(data, section)
    return images[0], fmt, kind


def _texture_catalog(data, sections, level_id, texture_dir, warnings):
    catalog = []
    refs = {}
    texture_dir = Path(texture_dir) / level_id
    texture_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(b"azurik-textures-v4\0" + data).hexdigest()
    metadata = texture_dir / "source.json"
    try:
        reuse = json.loads(metadata.read_text(encoding="utf-8")).get("fingerprint") == fingerprint
    except (OSError, ValueError):
        reuse = False
    unsupported = 0
    animated = []
    for section in sections:
        if section.tag != "surf":
            continue
        base = section.offset
        kind = struct.unpack_from("<I", data, base)[0] if section.size >= 4 else 0
        if kind == 1 and section.size >= 12:
            count = struct.unpack_from("<I", data, base + 4)[0]
            if count and 12 + count * 4 == section.size:
                frames = list(struct.unpack_from(f"<{count}I", data, base + 12))
                raw_parameter = struct.unpack_from("<I", data, base + 8)[0]
                animated.append((section, frames, raw_parameter))
                continue
        try:
            images, fmt, kind = decode_texture_faces(data, section)
            image = images[0]
            texture_id = f"surf-{section.index:04d}"
            face_ids = [texture_id if i == 0 else f"{texture_id}-face-{i}" for i in range(len(images))]
            face_urls = [f"/textures/{level_id}/{face_id}.png" for face_id in face_ids]
            for face_id, face_image in zip(face_ids, images):
                path = texture_dir / (face_id + ".png")
                if not reuse or not path.exists():
                    face_image.save(path, optimize=False)
            refs[section.index] = texture_id
            surface_type = struct.unpack_from("<I", data, base)[0]
            mipmaps = bool(surface_type & 0x10000000)
            power_of_two = all(v & (v - 1) == 0 for v in (image.width, image.height))
            entry = {"id": texture_id, "name": f"Surface {section.index:04d}",
                            "resourceIndex": section.index, "sourceOffset": base,
                            "width": image.width, "height": image.height,
                            "url": f"/textures/{level_id}/{texture_id}.png", "format": fmt,
                            "kind": kind, "alpha": any(im.getextrema()[3][0] < 255 for im in images),
                            "surfaceType": surface_type, "mipmapsInSource": mipmaps,
                            "sourceMipCount": max(image.width, image.height).bit_length() if mipmaps and power_of_two else (1 if not mipmaps else None)}
            if kind == "cube":
                entry.update({"faceIds": face_ids, "faceUrls": face_urls, "faceCount": len(images),
                              "orientationVerified": False})
            catalog.append(entry)
        except (ValueError, OSError, ImportError, struct.error):
            unsupported += 1
    image_catalog = {entry["id"]: entry for entry in catalog}
    missing_frames = 0
    for section, frames, raw_parameter in animated:
        frame_ids = [refs.get(index) if index < len(sections) and sections[index].tag == "surf" else None
                     for index in frames]
        frame_urls = [image_catalog[frame_id]["url"] if frame_id in image_catalog else None for frame_id in frame_ids]
        missing_frames += sum(url is None for url in frame_urls)
        first_id = next((frame_id for frame_id in frame_ids if frame_id in image_catalog), None)
        if first_id is None:
            unsupported += 1
            continue
        first = image_catalog[first_id]
        animation_id = f"anim-{section.index:04d}"
        refs[section.index] = animation_id
        catalog.append({"id": animation_id, "name": f"Animation {section.index:04d}",
                        "resourceIndex": section.index, "sourceOffset": section.offset,
                        "kind": "animation", "format": "animation", "width": first["width"], "height": first["height"],
                        "url": first["url"], "frameIds": frame_ids, "frameUrls": frame_urls,
                        "frameResourceIndices": frames, "frameCount": len(frames),
                        "availableFrameCount": sum(url is not None for url in frame_urls),
                        "previewFps": 12, "timingVerified": False, "rawHeaderParameter": raw_parameter,
                        "alpha": any(image_catalog[frame_id]["alpha"] for frame_id in frame_ids if frame_id in image_catalog)})
    if animated:
        warnings.append(f"{len(animated)} séquences de textures sont extraites dans leur ordre d'origine ; la cadence de prévisualisation est réglable et ne représente pas une cadence du jeu vérifiée.")
    if missing_frames:
        warnings.append(f"{missing_frames} images de séquences animées ne peuvent pas être décodées ; leurs emplacements dans la séquence sont conservés.")
    cube_count = sum(t["kind"] == "cube" for t in catalog)
    if cube_count:
        warnings.append(f"{cube_count} textures cubiques : les six faces sont extraites ; leur orientation dans le jeu et les reflets Xbox ne sont pas reproduits.")
    if unsupported:
        warnings.append(f"{unsupported} surfaces utilisent un format non décodé.")
    metadata.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    return catalog, refs


_DECLARATIONS = {0x80001111: 16, 0x80002111: 18, 0x80004111: 22, 0x80040111: 30, 0x252: 44}


def decode_cmp_normal(word: int) -> list[float]:
    """NV2A buffer CMP: signed X11/Y11/Z10 at bits 0/11/22.

    xemu pgraph/glsl/vsh.c decompress_11_11_10 divides by 1023/1023/511.
    Preserve those buffer values, including the most-negative endpoints;
    neither normalize nor change their direction here.
    """
    result = []
    for shift, bits in ((0, 11), (11, 11), (22, 10)):
        value = (word >> shift) & ((1 << bits) - 1)
        if value & (1 << (bits - 1)):
            value -= 1 << bits
        result.append(value / ((1 << (bits - 1)) - 1))
    return result


def read_platform_bindings(data: bytes, node_section: Section, record_offset: int,
                           sections: list[Section]) -> list[dict]:
    """Read every material descriptor in the platform's wrapper array.

    The first descriptor's first array pointer delimits the contiguous
    72-byte headers. Each header owns explicit RDMS/GPU pairs, including
    alternative distances of detail. Never infer bindings from proximity.
    """
    base, end = node_section.offset, node_section.offset + node_section.size
    if not base <= record_offset <= end - 36:
        raise ValueError("nœud de rendu hors de sa section")
    descriptor_count = struct.unpack_from("<I", data, record_offset + 28)[0]
    # Empty arrays point at the next serialized model. Dereferencing them
    # would duplicate another object's geometry at this node's transform.
    if descriptor_count == 0:
        return []
    if descriptor_count > 1024:
        raise ValueError("nombre de descripteurs de rendu non valide")
    target = record_offset + 32 + struct.unpack_from("<i", data, record_offset + 32)[0]
    if not base <= target <= end - descriptor_count * 72:
        raise ValueError("enveloppe de rendu hors de sa section")
    if struct.unpack_from("<I", data, target + 28)[0] != 5:
        raise ValueError("type d'enveloppe de rendu non décodé")
    first_count = struct.unpack_from("<I", data, target + 32)[0]
    header_end = target + 36 + struct.unpack_from("<i", data, target + 36)[0]
    if not first_count or header_end - target != descriptor_count * 72 or header_end > end:
        raise ValueError("limite des descripteurs de rendu non validée")
    bindings = []
    for descriptor_index in range(descriptor_count):
        descriptor = target + descriptor_index * 72
        if struct.unpack_from("<I", data, descriptor + 28)[0] != 5:
            raise ValueError("descripteur de rendu non décodé")
        count = struct.unpack_from("<I", data, descriptor + 64)[0]
        field = descriptor + 68
        pointer = field + struct.unpack_from("<i", data, field)[0]
        if count > 20000 or not header_end <= pointer <= end - count * 16:
            raise ValueError("table de liaisons de rendu tronquée")
        for index in range(count):
            mesh_index, primitive_index, tag, distance = struct.unpack_from("<II4sf", data, pointer + index * 16)
            if (mesh_index >= len(sections) or sections[mesh_index].tag != "rdms"
                    or primitive_index >= len(sections) or tag not in (b"pbrc", b"pbrw")
                    or sections[primitive_index].tag.encode("ascii") != tag or not math.isfinite(distance)):
                raise ValueError("liaison de rendu ne référence pas ses ressources")
            bindings.append({"meshResource": mesh_index, "primitiveResource": primitive_index,
                             "primitiveTag": tag.decode("ascii"), "maxDistance": distance,
                             "bindingOffset": pointer + index * 16, "descriptorIndex": descriptor_index})
    return bindings


def decode_pushbuffer(data: bytes, section: Section, vertex_count: int | None = None) -> list[int]:
    """Decode GPU ARRAY_ELEMENT packets, preserving triangle-strip parity.

    This exposes external primitives; callers must establish the resource
    relationship to a vertex pool before using them in the scene.
    """
    if section.tag not in ("pbrc", "pbrw") or section.size < 40:
        raise ValueError("tampon de primitives non valide")
    base, stop = section.offset, section.offset + section.size
    relative = struct.unpack_from("<i", data, base + 4)[0]
    cursor = base + 4 + relative
    if not base + 8 <= cursor < stop:
        raise ValueError("pointeur de primitives non valide")
    indices, strip, mode = [], [], None

    def flush():
        nonlocal strip
        if mode == 6:
            for i in range(2, len(strip)):
                triangle = (strip[i - 2], strip[i - 1], strip[i]) if i % 2 == 0 else (strip[i - 1], strip[i - 2], strip[i])
                if len(set(triangle)) == 3:
                    indices.extend(triangle)
        elif mode == 5:
            for i in range(0, len(strip) - 2, 3):
                triangle = strip[i:i + 3]
                if len(set(triangle)) == 3:
                    indices.extend(triangle)
        elif strip:
            raise ValueError("type de primitive GPU non pris en charge")
        strip = []

    while cursor + 4 <= stop:
        command = struct.unpack_from("<I", data, cursor)[0]
        if command == 0:
            break
        count = (command >> 18) & 0x7ff
        method = command & 0x1fff
        if not count or cursor + 4 + count * 4 > stop:
            raise ValueError("paquet GPU tronqué")
        values = struct.unpack_from(f"<{count}I", data, cursor + 4)
        if method == 0x17fc:
            for value in values:
                flush()
                mode = value if value else None
        elif method == 0x1800:
            for value in values:
                strip.extend((value & 0xffff, value >> 16))
        elif method == 0x1808:
            strip.extend(values)
        else:
            raise ValueError(f"commande GPU inconnue {method:04x}")
        cursor += 4 + count * 4
    flush()
    if vertex_count is not None and indices and max(indices) >= vertex_count:
        raise ValueError("indices GPU hors du modèle référencé")
    return indices


def decode_mesh(data: bytes, section: Section, shader_tables: dict, texture_refs: dict) -> dict:
    base, stop = section.offset, section.offset + section.size
    if section.size < 64:
        raise ValueError("en-tête de modèle tronqué")
    header = struct.unpack_from("<8I3f5I", data, base)
    if header[0] != 5 or header[3:6] != (1, 0, 0):
        raise ValueError("déclaration de modèle inconnue")
    count, declaration, parts_count = header[6], header[11], header[12]
    stride = _DECLARATIONS.get(declaration)
    if not stride or not 0 < count <= 200000 or parts_count > 20000:
        raise ValueError("déclaration de sommets inconnue")
    vertex_start = base + 64
    table = base + 52 + header[13]
    if table != ((vertex_start + count * stride + 3) & ~3):
        raise ValueError("longueur du flux de sommets incohérente")
    if parts_count and table + parts_count * 8 > stop:
        raise ValueError("table de primitives tronquée")
    origin = list(header[8:11])
    if not all(math.isfinite(v) and abs(v) < 100000 for v in origin):
        raise ValueError("origine du modèle non valide")
    positions, colors, vertex_alphas, uvs, uv2, normals, packed_normals = [], [], [], [], [], [], []
    for i in range(count):
        offset = vertex_start + i * stride
        if stride == 44:
            local = struct.unpack_from("<3f", data, offset)
            normal = struct.unpack_from("<3f", data, offset + 12)
            uv = struct.unpack_from("<2f", data, offset + 28)
            secondary_uv = struct.unpack_from("<2f", data, offset + 36)
            color_offset = offset + 24
        else:
            local = tuple(x / 256 for x in struct.unpack_from("<3h", data, offset))
            packed_normals.extend(data[offset + 6:offset + 10])
            normal = decode_cmp_normal(struct.unpack_from("<I", data, offset + 6)[0])
            secondary_uv = ()
            if stride == 16:
                uv = tuple(x / 255 for x in data[offset + 14:offset + 16])
            elif stride == 18:
                uv = tuple(x / 1024 for x in struct.unpack_from("<2h", data, offset + 14))
            else:
                # Retail declaration table at XBE 1A9BA8: 80004111
                # (22 bytes) has one FLOAT2 token, not two SHORT2 sets.
                # 80040111 (30 bytes) has distinct FLOAT2 tokens 7 and 8.
                uv = struct.unpack_from("<2f", data, offset + 14)
                if stride == 30:
                    secondary_uv = struct.unpack_from("<2f", data, offset + 22)
            color_offset = offset + 10
        position = [origin[axis] + local[axis] for axis in range(3)]
        if not all(math.isfinite(v) and abs(v) < 100000 for v in position + list(uv) + list(secondary_uv)) or not all(math.isfinite(v) for v in normal):
            raise ValueError("sommet non valide")
        positions.extend(position)
        normals.extend(normal)
        blue, green, red, alpha = data[color_offset:color_offset + 4]
        colors.extend((red / 255, green / 255, blue / 255))
        vertex_alphas.append(alpha / 255)
        uvs.extend(uv)
        uv2.extend(secondary_uv)
    shader_index, material_index = header[1:3]
    shaders = shader_tables.get(shader_index, [])
    shader = shaders[material_index] if material_index < len(shaders) else {}
    stages = [dict(s, textureId=texture_refs.get(s["resourceIndex"])) for s in shader.get("stages", [])]
    texture_id = stages[0].get("textureId") if stages and stages[0]["stage"] == 0 else None
    indices, parts = [], []
    invalid_triangles = 0
    for part_index in range(parts_count):
        field = table + part_index * 8
        index_count, relative = struct.unpack_from("<Ii", data, field)
        index_start = field + 4 + relative
        if index_count % 3 or not table + parts_count * 8 <= index_start <= stop or index_start + index_count * 2 > stop:
            raise ValueError("liste de triangles non valide")
        start = len(indices)
        for tri in struct.iter_unpack("<3H", data[index_start:index_start + index_count * 2]):
            if max(tri) >= count:
                invalid_triangles += 1
                continue
            if len(set(tri)) == 3:
                indices.extend(tri)
        part = {"start": start, "count": len(indices) - start, "partIndex": part_index}
        if texture_id:
            part["textureId"] = texture_id
        parts.append(part)
    if parts_count and not indices:
        raise ValueError("aucun triangle valide")
    centre = [(min(positions[axis::3]) + max(positions[axis::3])) / 2 for axis in range(3)]
    return {"id": f"mesh-{base:08x}", "name": f"Modèle {section.index:04d}", "tag": section.tag,
            "resourceIndex": section.index, "sourceOffset": base, "origin": origin,
            "position": centre, "originalPosition": centre[:], "positions": positions,
            "colors": colors, "vertexAlphas": vertex_alphas, "indices": indices, "uvs": uvs, "uv2": uv2,
            "normals": normals, "sourceNormals": normals[:], "packedNormals": packed_normals,
            "normalFormat": "float3" if stride == 44 else "CMP-S11-S11-S10",
            "normalSpace": "local", "normalSourceVerified": True, "normalTransformVerified": True,
            "uvSetCount": 2 if uv2 else 1, "parts": parts,
            "textureStages": stages, "shaderResource": shader_index, "shaderIndex": material_index,
            "material": shader.get("material", {}),
            "materialValid": bool(shader.get("valid")), "declaredTextureStages": shader.get("stageCount"),
            "vertexStride": stride, "editable": False, "vertexPool": parts_count == 0,
            "coordinateSpace": "asset", "worldPositionVerified": False,
            "invalidTriangles": invalid_triangles}


def _quad_valid(points) -> bool:
    spans = [max(p[a] for p in points) - min(p[a] for p in points) for a in range(3)]
    if max(spans) > 750 or sum(spans) < 0.05:
        return False
    a, b, c, d = points
    ab, ac = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    normal = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
    length = math.sqrt(sum(v * v for v in normal))
    return length >= .01 and abs(sum(normal[i] * (d[i] - a[i]) for i in range(3))) / length <= max(.05, max(spans) * .03)


def scan_collision(data: bytes, sections: list[Section]) -> dict:
    """Planar quad streams within real SDSR bounds; display only, never edit."""
    candidates = []
    for section in sections:
        if section.tag != "sdsr":
            continue
        base, end = section.offset, section.offset + section.size
        for phase in (0, 4, 8):
            start, count = None, 0
            for offset in range(base + phase, end - 11, 12):
                point = struct.unpack_from("<3f", data, offset)
                valid = (all(math.isfinite(v) and abs(v) <= 5000 and (v == 0 or abs(v) >= 1e-5) for v in point)
                         and any(abs(v) >= .1 for v in point))
                if valid:
                    if start is None:
                        start = offset
                    count += 1
                else:
                    if start is not None and count >= 32:
                        candidates.append((start, count))
                    start, count = None, 0
            if start is not None and count >= 32:
                candidates.append((start, count))
    qualified = []
    for start, count in candidates:
        good = 0
        for i in range(count // 4):
            good += int(_quad_valid(tuple(struct.unpack_from("<3f", data, start + i * 48 + j * 12) for j in range(4))))
        if good >= 8 and good / (count // 4) >= .9:
            qualified.append((good, start, count))
    selected = []
    for good, start, count in sorted(qualified, reverse=True):
        if any(start < other + n * 12 and other < start + count * 12 for other, n in selected):
            continue
        selected.append((start, count))
    positions, indices = [], []
    for start, count in selected:
        for i in range(count // 4):
            quad = tuple(struct.unpack_from("<3f", data, start + i * 48 + j * 12) for j in range(4))
            if not _quad_valid(quad):
                continue
            first = len(positions) // 3
            for point in quad:
                positions.extend(point)
            # Quad streams store their four corners as a triangle strip.
            indices.extend((first, first + 1, first + 2, first + 2, first + 1, first + 3))
    return {"positions": positions, "indices": indices, "heuristic": True, "editable": False}


def parse_level(data: bytes, level_id: str, texture_dir: str | Path, *, assemble_scene: bool = True) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", level_id):
        raise ValueError("Identifiant de niveau non valide.")
    sections = read_sections(data)
    warnings = []
    textures, texture_refs = _texture_catalog(data, sections, level_id, texture_dir, warnings)
    shader_tables = read_shader_tables(data, sections)
    meshes, failed = [], Counter()
    for section in sections:
        if section.tag == "rdms":
            try:
                meshes.append(decode_mesh(data, section, shader_tables, texture_refs))
            except (ValueError, struct.error, IndexError) as exc:
                failed[str(exc)] += 1
    textured = sum(bool(m["textureStages"] and m["textureStages"][0]["stage"] == 0
                        and m["textureStages"][0].get("textureId")) for m in meshes)
    untextured = sum(m["materialValid"] and m["declaredTextureStages"] == 0 for m in meshes)
    missing_texture = len(meshes) - textured - untextured
    multistage = sum(len(m["textureStages"]) > 1 for m in meshes)
    if failed:
        warnings.append(f"{sum(failed.values())} modèles non décodés : " + "; ".join(f"{k} ({v})" for k, v in failed.items()))
    if multistage:
        warnings.append(f"{multistage} modèles utilisent plusieurs couches de texture ; les combinaisons vérifiées sont composées, les autres restent en couche principale.")
    if untextured:
        warnings.append(f"{untextured} modèles ne déclarent aucune couche de texture dans leur matériau d'origine.")
    if missing_texture:
        warnings.append(f"{missing_texture} modèles affichent leurs couleurs de sommets car leur matériau ou leur surface principale n'a pas pu être décodé.")
    warnings.append("L'aperçu n'exécute pas les scripts, les animations des personnages ni l'ensemble des effets et shaders Xbox.")
    collisions = scan_collision(data, sections)
    if collisions["indices"]:
        warnings.append("Les collisions sont une visualisation indicative de suites de quadrilatères planaires SDSR ; leur format complet reste à confirmer et elles ne sont pas modifiables.")
    stats = {"resourceCount": len(sections), "meshResources": sum(s.tag == "rdms" for s in sections),
             "decodedMeshes": len(meshes), "texturedMeshes": textured,
             "untexturedMeshes": untextured, "missingTextureMeshes": missing_texture,
             "vertexPools": sum(m["vertexPool"] for m in meshes),
             "triangles": sum(len(m["indices"]) // 3 for m in meshes),
             "vertices": sum(len(m["positions"]) // 3 for m in meshes),
             "surfaceResources": sum(s.tag == "surf" for s in sections),
             "decodedTextures": len(textures), "multiStageMeshes": multistage,
             "animatedTextures": sum(t["kind"] == "animation" for t in textures),
             "cubeTextures": sum(t["kind"] == "cube" for t in textures),
             "collisionFaces": len(collisions["indices"]) // 6,
             "meshFailures": dict(failed), "tags": dict(Counter(s.tag for s in sections))}
    result = {"meshes": meshes, "textures": textures, "collisions": collisions,
              "stats": stats, "warnings": warnings}
    if assemble_scene:
        from scene_graph import resolve_scene
        resolved = resolve_scene(data, meshes, sections)
        result["assets"] = [{"id": m["id"], "name": m["name"], "resourceIndex": m["resourceIndex"],
                             "sourceOffset": m["sourceOffset"], "vertices": len(m["positions"]) // 3,
                             "triangles": len(m["indices"]) // 3, "coordinateSpace": "asset", "editable": False}
                            for m in meshes]
        result["meshes"] = resolved.get("meshes", [])
        result["objects"] = resolved.get("objects", [])
        result["nodes"] = resolved.get("nodes", [])
        from static_scene import resolve_static_scene
        try:
            static = resolve_static_scene(data, meshes, sections)
            result["meshes"].extend(static["meshes"])
            result["stats"].update(static["stats"])
            result["warnings"].extend(static["warnings"])
        except (ValueError, struct.error) as exc:
            result["stats"].update({"staticMeshCount": 0, "staticPlacementFailures": {str(exc): 1}})
            result["warnings"].append(f"Décors statiques LEVL non résolus : {exc}")
        from environment_scene import build_environment
        try:
            result["environment"] = build_environment(data, sections, result["nodes"], result["meshes"])
            result["warnings"].extend(result["environment"].get("warnings", []))
            sky_ids = set(result["environment"]["sky"]["meshIds"])
            for mesh in result["meshes"]:
                if mesh["id"] in sky_ids:
                    mesh["sceneRole"] = "sky"
            result["stats"]["skyMeshCount"] = len(sky_ids)
        except (ValueError, struct.error) as exc:
            result["environment"] = {"sky": {"available": False, "meshIds": [], "variants": []}}
            result["warnings"].append(f"Ciel non résolu : {exc}")
        result["warnings"].extend(resolved.get("warnings", []))
        result["stats"].update(resolved.get("stats", {}))
        # Scene counters include both independent retail render paths.
        # Preserve the NODE contribution explicitly for coverage comparisons.
        for key in ("placedMeshCount", "placedTriangleCount", "authoredVisibleMeshCount", "editorVisibleMeshCount"):
            result["stats"]["node" + key[0].upper() + key[1:]] = resolved.get("stats", {}).get(key, 0)
        all_meshes = result["meshes"]
        triangle_count = sum(len(m["indices"]) // 3 for m in all_meshes)
        result["stats"].update({"sceneInstances": len(all_meshes), "placedMeshCount": len(all_meshes),
                                "sceneTriangles": triangle_count, "placedTriangleCount": triangle_count,
                                "sceneVertices": sum(len(m["positions"]) // 3 for m in all_meshes),
                                "authoredVisibleMeshCount": sum(bool(m.get("authoredVisible")) for m in all_meshes),
                                "editorVisibleMeshCount": sum(bool(m.get("editorVisible")) for m in all_meshes),
                                "editorVisibleWorldMeshCount": sum(bool(m.get("editorVisible")) and m.get("sceneRole") != "sky" for m in all_meshes),
                                "editorVisibleSkyMeshCount": sum(bool(m.get("editorVisible")) and m.get("sceneRole") == "sky" for m in all_meshes)})
        from retail_lighting import apply_source_lighting
        try:
            apply_source_lighting(data, sections, result)
        except (ValueError, struct.error) as exc:
            result["warnings"].append(f"Éclairage source non résolu : {exc}")
    return result
