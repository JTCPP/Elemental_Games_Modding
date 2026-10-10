"""Retail LEVL static render descriptors, independently of the NODE graph.

The EU executable reads count at LEVL+0x2c and a field-relative pointer at
+0x30 (C04B3/C0917). C0921 indexes 72-byte records and calls C2850 with no
NODE owner. C2850 initializes the world matrix to identity; C3398 sends it
to the GPU. RDMS quantization origins are copied without an extra unit
conversion at C3289/C3342. These vertices are already in world metres.

The descriptor schema is shared with NODE platforms: cell IDs are uint16
at +0x20/+0x24, sub-bounds at +0x30/+0x34, and 16-byte RDMS/PBR/distance
pairs at +0x40/+0x44. The source tables and their order are preserved.
"""
from __future__ import annotations

from collections import Counter
import math
import struct


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def _array(data, field, count, stride, lower, upper, label):
    # Empty serialized arrays use sentinel relative values. Never follow
    # such a pointer into the next object/resource.
    if not count:
        return None
    pointer = _relative(data, field)
    if not lower <= pointer <= upper or count > (upper - pointer) // stride:
        raise ValueError(f"Table statique {label} hors de la section LEVL.")
    return pointer


def _bounds(data, offset):
    bounds = list(struct.unpack_from("<6f", data, offset))
    if not all(math.isfinite(v) for v in bounds) or any(bounds[a] > bounds[a + 3] for a in range(3)):
        raise ValueError("Bornes statiques LEVL non valides.")
    return bounds


def read_static_descriptors(data, sections):
    """Read explicit static bindings; fail on unknown/truncated source data.

    Archives without LEVL are asset libraries and have no static scene.
    Array extents derive from resource boundaries, without an arbitrary
    rendering cap or a scan for plausible neighbouring resource indices.
    """
    candidates = [section for section in sections if section.tag == "levl"]
    if not candidates:
        return {"section": None, "descriptors": [], "count": 0}
    if len(candidates) != 1:
        raise ValueError("Plusieurs sections LEVL : scène statique ambiguë.")
    section = candidates[0]
    base, end = section.offset, section.offset + section.size
    if section.size < 64 or not 0 <= base <= end <= len(data):
        raise ValueError("En-tête LEVL statique tronqué.")
    count, cell_count = _u32(data, base + 44), _u32(data, base + 28)
    start = _array(data, base + 48, count, 72, base + 64, end, "descripteurs")
    if not count:
        return {"section": section, "descriptors": [], "count": 0}
    header_end = start + count * 72
    descriptors = []
    for index in range(count):
        offset = start + index * 72
        if _u32(data, offset + 28) != 5:
            raise ValueError(f"Type du descripteur statique {index} non décodé.")
        bounds = _bounds(data, offset + 4)
        cells_count = _u32(data, offset + 32)
        cells_start = _array(data, offset + 36, cells_count, 2, header_end, end, "cellules")
        cells = list(struct.unpack_from(f"<{cells_count}H", data, cells_start)) if cells_count else []
        if any(cell >= cell_count for cell in cells):
            raise ValueError(f"Cellule du descripteur statique {index} hors de LEVL.")
        bounds_count = _u32(data, offset + 48)
        bounds_start = _array(data, offset + 52, bounds_count, 24, header_end, end, "sous-bornes")
        for bound in range(bounds_count):
            _bounds(data, bounds_start + bound * 24)
        # C2B05..C2C02 reads uint32 encoded lighting references at +40/+44.
        # C2C13..C2D83 reads 12-byte per-part references at +56/+60, then
        # signed int16 lists through each row's +4/+8 count/pointer fields.
        # Preserve this assignment metadata; lighting itself is not executed.
        light_count = _u32(data, offset + 40)
        light_start = _array(data, offset + 44, light_count, 4, header_end, end, "références d’éclairage")
        light_refs = list(struct.unpack_from(f"<{light_count}I", data, light_start)) if light_count else []
        part_light_count = _u32(data, offset + 56)
        part_light_start = _array(data, offset + 60, part_light_count, 12, header_end, end, "éclairage par partie")
        part_lights = []
        for light in range(part_light_count):
            row = part_light_start + light * 12
            encoded_reference, amount = struct.unpack_from("<II", data, row)
            indices_start = _array(data, row + 8, amount, 2, header_end, end, "parties d’éclairage")
            indices = list(struct.unpack_from(f"<{amount}h", data, indices_start)) if amount else []
            part_lights.append({"encodedReference": encoded_reference, "indices": indices, "sourceOffset": row})
        pair_count = _u32(data, offset + 64)
        pair_start = _array(data, offset + 68, pair_count, 16, header_end, end, "liaisons")
        bindings = []
        for pair_index in range(pair_count):
            binding_offset = pair_start + pair_index * 16
            mesh, primitive, tag, distance = struct.unpack_from("<II4sf", data, binding_offset)
            if (mesh >= len(sections) or sections[mesh].index != mesh or sections[mesh].tag != "rdms"
                    or primitive >= len(sections) or sections[primitive].index != primitive
                    or tag not in (b"pbrc", b"pbrw") or sections[primitive].tag.encode("ascii") != tag
                    or not math.isfinite(distance) or distance < 0):
                raise ValueError(f"Liaison du descripteur statique {index} non valide.")
            bindings.append({"meshResource": mesh, "primitiveResource": primitive,
                             "primitiveTag": tag.decode("ascii"), "maxDistance": distance,
                             "bindingOffset": binding_offset, "descriptorIndex": index,
                             "sourceOrder": pair_index})
        descriptors.append({"index": index, "offset": offset, "cells": cells, "bounds": bounds,
                            "boundsCount": bounds_count, "bindings": bindings,
                            "flags": _u32(data, offset),
                            "lightReferences": light_refs, "partLightReferences": part_lights})
    return {"section": section, "descriptors": descriptors, "count": count}


def resolve_static_scene(data, mesh_library, sections):
    """Assemble verified world vertices with the most detailed source LOD.

    Cell/portal culling and distance selection are runtime operations. The
    editor displays all cells and the first distance alternative, without
    changing their source tables, parameters, or geometries.
    """
    from renderer_parser import decode_pushbuffer
    from scene_graph import identity, normal_attributes
    table = read_static_descriptors(data, sections)
    # Names are optional presentation metadata. The environment reader
    # validates the original ASCII cell table and self-relative strings.
    # An unreadable label never substitutes an inferred geometry binding.
    from environment_scene import read_environment_header
    try:
        environment = read_environment_header(data, sections)
        cell_names = {cell["index"]: cell["name"] for cell in environment["cells"]} if environment else {}
    except (ValueError, struct.error):
        cell_names = {}
    library = {mesh["resourceIndex"]: mesh for mesh in mesh_library}
    instances, warnings, failures = [], [], Counter()
    primitive_cache = {}
    refs = [reference for descriptor in table["descriptors"] for reference in descriptor["bindings"]]
    for descriptor in table["descriptors"]:
        if not descriptor["bindings"]:
            failures["descripteur statique sans liaison de rendu"] += 1
            continue
        # C38FF initializes alternative 0; C3997 advances when its distance
        # is exceeded. Retail source distances are nondecreasing. Preserve
        # order and reject ambiguous ordering instead of guessing a LOD.
        distances = [ref["maxDistance"] for ref in descriptor["bindings"]]
        if distances != sorted(distances):
            failures["distances statiques LOD non ordonnées"] += 1
            continue
        reference = descriptor["bindings"][0]
        pool = library.get(reference["meshResource"])
        if pool is None:
            failures["réserve statique de sommets non décodée"] += 1
            continue
        key = (reference["meshResource"], reference["primitiveResource"])
        try:
            if key not in primitive_cache:
                primitive_cache[key] = decode_pushbuffer(data, sections[reference["primitiveResource"]], len(pool["positions"]) // 3)
            original_indices = primitive_cache[key]
            if not original_indices:
                raise ValueError("primitive statique sans triangle")
            used = sorted(set(original_indices))
            positions = [component for source in used for component in pool["positions"][source * 3:source * 3 + 3]]
            actual_bounds = ([min(positions[a::3]) for a in range(3)]
                             + [max(positions[a::3]) for a in range(3)])
            # Packed coordinates are rounded down to 1/256 metre. The
            # serialized unquantized bounds can differ by one such step.
            epsilon = 1 / 256 + max(abs(v) for v in descriptor["bounds"]) * 1e-6
            if any(actual_bounds[a] < descriptor["bounds"][a] - epsilon
                   or actual_bounds[a + 3] > descriptor["bounds"][a + 3] + epsilon for a in range(3)):
                raise ValueError("géométrie statique hors de ses bornes monde")
        except (ValueError, struct.error) as exc:
            failures[str(exc)] += 1
            continue
        remap = {source: index for index, source in enumerate(used)}
        indices = [remap[source] for source in original_indices]
        centre = [(actual_bounds[a] + actual_bounds[a + 3]) / 2 for a in range(3)]
        part = {"start": 0, "count": len(indices), "partIndex": 0}
        if pool.get("textureStages") and pool["textureStages"][0].get("textureId"):
            part["textureId"] = pool["textureStages"][0]["textureId"]
        names = [cell_names[cell] for cell in descriptor["cells"] if cell_names.get(cell)]
        label = " / ".join(names) if names else "Décor statique"
        instance = {"id": f"mesh-{pool['sourceOffset']:08x}-static-{descriptor['index']:04d}",
                    "name": f"{label} · Partie {descriptor['index']:04d}", "tag": "static",
                    "sceneRole": "static", "staticGeometry": True,
                    "levlResource": table["section"].index, "staticDescriptorIndex": descriptor["index"],
                    "staticDescriptorOffset": descriptor["offset"], "cellIndices": descriptor["cells"], "cellNames": names,
                    "serializedBounds": descriptor["bounds"], "bindingOffset": reference["bindingOffset"],
                    "lightReferences": descriptor["lightReferences"], "partLightReferences": descriptor["partLightReferences"],
                    "sourceOffset": pool["sourceOffset"], "resourceIndex": pool["resourceIndex"],
                    "primitiveResource": reference["primitiveResource"], "origin": pool["origin"][:],
                    "positions": positions, "indices": indices, "position": centre, "originalPosition": centre[:],
                    "colors": [component for source in used for component in pool["colors"][source * 3:source * 3 + 3]],
                    "uvs": [component for source in used for component in pool["uvs"][source * 2:source * 2 + 2]],
                    "parts": [part], "coordinateSpace": "world", "worldPositionVerified": True,
                    "worldMatrix": [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.],
                    "instance": True, "editable": False, "cameraDependent": False,
                    "visible": True, "editorVisible": True, "authoredVisible": True, "lodControlled": False,
                    "visibilityReason": "editor-static-all-cells", "runtimeVisibilityMode": "cell-and-distance",
                    "lodMaxDistance": reference["maxDistance"], "lodBindingCount": len(descriptor["bindings"]),
                    "lodPreviewMode": "highest-detail", "lodAlternatives": descriptor["bindings"],
                    "shaderResource": pool.get("shaderResource"), "shaderIndex": pool.get("shaderIndex"),
                    "material": pool.get("material", {}), "materialValid": pool.get("materialValid", False),
                    "textureStages": pool.get("textureStages", []), "uvSetCount": pool.get("uvSetCount", 1)}
        if pool.get("vertexAlphas"):
            instance["vertexAlphas"] = [pool["vertexAlphas"][source] for source in used]
        if pool.get("uv2"):
            instance["uv2"] = [component for source in used for component in pool["uv2"][source * 2:source * 2 + 2]]
        instance.update(normal_attributes(pool, used, identity()))
        instances.append(instance)
    if failures:
        warnings.append("Décors statiques non affichés : " + "; ".join(f"{key} ({value})" for key, value in failures.items()))
    if table["count"]:
        warnings.append("Les décors statiques LEVL sont affichés dans toutes leurs cellules, avec leur détail maximal ; les portails et distances de visibilité du jeu ne sont pas simulés.")
    lighting_count = sum(bool(descriptor["lightReferences"] or descriptor["partLightReferences"]) for descriptor in table["descriptors"])
    if lighting_count:
        warnings.append(f"{lighting_count} décors statiques déclarent des liaisons d’éclairage conservées comme métadonnées ; ces lumières du moteur Xbox ne sont pas exécutées.")
    return {"meshes": instances, "warnings": warnings,
            "stats": {"staticDescriptorCount": table["count"], "staticBindingCount": len(refs),
                      "staticMeshResources": len({ref["meshResource"] for ref in refs}),
                      "staticMeshCount": len(instances), "staticLodAlternatives": len(refs) - table["count"],
                      "staticTriangleCount": sum(len(mesh["indices"]) // 3 for mesh in instances),
                      "staticVertexCount": sum(len(mesh["positions"]) // 3 for mesh in instances),
                      "staticPlacementFailures": dict(failures)}}
