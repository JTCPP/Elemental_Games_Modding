"""Initial retail lighting, selected through the serialized LEVL cells.

FE210 declares ten NODE light parameters. 69390 copies source type/falloff,
exclusion bytes and id; 69574/CF660 copies RGB without an intensity gain.
C0F70 binds cell +64/+68 uint16 IDs to light objects. C4A90 selects at most
four unique cell lights, using B6B40 sphere/bounds and CEDB0 attenuation.
Runtime-created global lights, per-part lights and point/spot shaders remain
explicit limitations, never inferred from authored names or zero vertex RGB.
"""
from __future__ import annotations

import math
import struct
from collections import Counter


_MAX_RADIUS = math.sqrt(3.4028234663852886e38)


def _bounded(data, start, size, section, label):
    if (size < 0 or section.offset < 0 or section.offset + section.size > len(data)
            or not section.offset <= start <= section.offset + section.size - size):
        raise ValueError(f"Données d’éclairage {label} hors de leur ressource.")
    return start


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def read_light_state(data, node, sections):
    """Read only the verified ten-channel serialized layout."""
    if node.get("type") != "light":
        return None
    record = node.get("recordOffset")
    if isinstance(record, bool) or not isinstance(record, int):
        raise ValueError("Offset de lumière absent.")
    owners = [s for s in sections if s.tag == "node" and s.offset <= record <= s.offset + s.size - 36]
    if len(owners) != 1:
        raise ValueError("Lumière hors de son graphe.")
    section = owners[0]
    _bounded(data, record, 36, section, "nœud")
    count = struct.unpack_from("<I", data, record + 8)[0]
    if count != 10:
        return None
    start = _bounded(data, _relative(data, record + 12), 40, section, "paramètres")
    parameters = list(struct.unpack_from("<10f", data, start))
    if not all(math.isfinite(v) for v in parameters):
        raise ValueError("Paramètres de lumière non finis.")
    flags = struct.unpack_from("<I", data, record + 28)[0]
    kind, falloff, exclude_static, exclude_dynamic = data[record + 28:record + 32]
    source_id = struct.unpack_from("<H", data, record + 32)[0]
    intensity = parameters[7]
    strength = abs(intensity)
    if falloff == 0:
        radius, attenuation = _MAX_RADIUS, [1., 0., 0.]
    elif falloff == 1 and strength >= 0.0010000000474974513:
        radius, attenuation = strength * 3., [0., 1. / (strength * 0.009999999776482582), 0.]
    elif falloff == 2 and strength >= 0.0010000000474974513:
        power = strength ** 0.6666666865348816
        radius, attenuation = math.sqrt(power) * 3., [0., 0., 1. / (power * 9.999999747378752e-5)]
    else:
        radius, attenuation = 0., [0., 0., 0.]
    active = strength >= 0.0010000000474974513 and sum(attenuation) != 0
    color = [v * (-1. if intensity < 0 else 1.) for v in parameters[4:7]]
    # CF660 also disables an all-zero diffuse input. Ambient CF7B0 keeps
    # the attenuation activity flag even when its ambient RGB is zero.
    if kind != 0 and not any(color):
        active = False
    return {"sourceVerified": kind in range(4) and falloff in range(3), "sourceId": source_id,
            "flags": flags, "type": {0: "ambient", 1: "directional", 2: "point", 3: "spot"}.get(kind, "unknown"),
            "falloff": falloff, "excludeStatic": bool(exclude_static), "excludeDynamic": bool(exclude_dynamic),
            # 69390/69460..69589 never consume the inherited visibility
            # channel; runtime +6C is rebuilt from attenuation and RGB.
            "visibility": parameters[0], "visibilityAffectsInitialLighting": False,
            "color": color, "intensity": intensity,
            "coneAngle": parameters[8], "penumbraAngle": parameters[9],
            "active": active, "radius": radius, "attenuation": attenuation,
            "sourceOffset": record, "paramsOffset": start, "flagsOffset": record + 28, "idOffset": record + 32}


def read_cell_light_ids(data, sections):
    candidates = [s for s in sections if s.tag == "levl"]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("Cellules d’éclairage LEVL ambiguës.")
    section = candidates[0]
    base = _bounded(data, section.offset, 64, section, "en-tête LEVL")
    count = struct.unpack_from("<I", data, base + 28)[0]
    if count > 65535:
        raise ValueError("Nombre de cellules d’éclairage non valide.")
    if not count:
        return []
    start = _bounded(data, _relative(data, base + 32), count * 72, section, "cellules")
    if start < base + 64:
        raise ValueError("Cellules d’éclairage superposées à l’en-tête.")
    cells = []
    for index in range(count):
        record = start + index * 72
        amount = struct.unpack_from("<I", data, record + 64)[0]
        if not amount:
            cells.append([])
            continue
        pointer = _bounded(data, _relative(data, record + 68), amount * 2, section, "identifiants")
        if pointer < start + count * 72:
            raise ValueError("Identifiants de lumière superposés aux cellules.")
        cells.append(list(struct.unpack_from(f"<{amount}H", data, pointer)))
    return cells


def _platform_cells(data, node, descriptor_index, sections, cell_count):
    record = node["recordOffset"]
    section = next(s for s in sections if s.tag == "node" and s.offset <= record <= s.offset + s.size - 36)
    count = struct.unpack_from("<I", data, record + 28)[0]
    if not 0 <= descriptor_index < count:
        raise ValueError("Indice de descripteur d’éclairage non valide.")
    start = _bounded(data, _relative(data, record + 32), count * 72, section, "descripteurs")
    descriptor = start + descriptor_index * 72
    amount = struct.unpack_from("<I", data, descriptor + 32)[0]
    if not amount:
        return [], []
    pointer = _bounded(data, _relative(data, descriptor + 36), amount * 2, section, "cellules de plateforme")
    memberships = list(struct.unpack_from(f"<{amount}H", data, pointer))
    if any(index >= cell_count for index in memberships):
        raise ValueError("Cellule de plateforme hors de LEVL.")
    bounds = list(struct.unpack_from("<6f", data, descriptor + 4))
    return memberships, bounds


def _direction(matrix):
    """CED30/A07A0/A0AB0: recover the retail quaternion and forward ray.

    A07A0 removes one scale, the length of the first basis column, before
    A06A0 converts the matrix. It does not orthogonalize the other columns
    or normalize that quaternion. D2 authors nonuniform, sheared light
    parents; rejecting them silently drops a required directional light.
    The shader constant builder subsequently normalizes the local ray.
    """
    if len(matrix) != 16 or not all(math.isfinite(v) for v in matrix):
        raise ValueError("Transformation de lumière non finie.")
    scale = math.hypot(matrix[0], matrix[4], matrix[8])
    if not math.isfinite(scale) or scale <= 1e-12:
        raise ValueError("Échelle de lumière singulière.")
    orientation = list(matrix)
    for row in range(3):
        for col in range(3):
            orientation[row * 4 + col] /= scale
    # Imported here because graph assembly also imports this lighting reader.
    from scene_graph import _matrix_quaternion
    x, y, z, w = _matrix_quaternion(orientation)
    ray = [-2 * (x * z + y * w), -2 * (y * z - x * w),
           -(1 - 2 * (x * x + y * y))]
    if not all(math.isfinite(v) for v in ray) or math.hypot(*ray) <= 1e-12:
        raise ValueError("Direction de lumière singulière.")
    return ray


def select_cell_lights(cells, memberships, lights, bounds):
    """C4A90 cell pass, preserving first-arriving equal-strength lights."""
    if len(bounds) != 6 or not all(math.isfinite(v) for v in bounds) or any(bounds[i] > bounds[i + 3] for i in range(3)):
        raise ValueError("Bornes d’éclairage non valides.")
    center = [(bounds[i] + bounds[i + 3]) / 2 for i in range(3)]
    selected, scores, seen, limitations = [], [], set(), set()
    for cell in memberships:
        if isinstance(cell, bool) or not isinstance(cell, int) or not 0 <= cell < len(cells):
            raise ValueError("Appartenance de cellule d’éclairage non valide.")
        for source_id in cells[cell]:
            if source_id in seen:
                continue
            seen.add(source_id)
            light = lights.get(source_id)
            if light is None:
                limitations.add("unresolved-cell-light")
                continue
            state = light["state"]
            if not state["sourceVerified"] or not light["transformVerified"]:
                limitations.add("unverified-light")
                continue
            if not state["active"] or state["excludeStatic"]:
                continue
            position, radius = light["position"], state["radius"]
            distance_to_box = sum(max(bounds[i] - position[i], 0., position[i] - bounds[i + 3]) ** 2 for i in range(3))
            if distance_to_box >= radius * radius:
                continue
            distance = math.sqrt(sum((position[i] - center[i]) ** 2 for i in range(3)))
            constant, linear, quadratic = state["attenuation"]
            denominator = constant + distance * linear + distance * distance * quadratic
            score = (1. / denominator if denominator > 0 else math.inf) if distance <= radius else 0.
            if len(selected) < 4:
                selected.append(light)
                scores.append(score)
            else:
                weakest = min(range(4), key=lambda index: scores[index])
                if score > scores[weakest]:
                    selected[weakest], scores[weakest] = light, score
    return selected, limitations


def apply_source_lighting(data, sections, result):
    """Attach products for the source directional stage after assembly."""
    for mesh in result.get("meshes", []):
        mesh.pop("sourceLighting", None)
    result.pop("lighting", None)
    for key in ("sourceLightingMeshes", "sourceDirectionalMeshes", "sourceLightingPartialMeshes"):
        result.setdefault("stats", {}).pop(key, None)
    cells = read_cell_light_ids(data, sections)
    if cells is None:
        return
    nodes = {node["index"]: node for node in result.get("nodes", [])}
    lights = {}
    for node in nodes.values():
        state = node.get("lightState")
        if state is None:
            continue
        source_id = state["sourceId"]
        if source_id in lights:
            raise ValueError("Identifiant de lumière source ambigu.")
        matrix = node.get("worldMatrix", [])
        verified = bool(node.get("worldPositionVerified"))
        ray = None
        try:
            if state["type"] == "directional":
                ray = _direction(matrix)
        except ValueError:
            verified = False
        lights[source_id] = {"state": state, "nodeIndex": node["index"], "direction": ray,
                             "position": node.get("worldPosition", []), "transformVerified": verified}
    counts = Counter(sourceLightingMeshes=0, sourceDirectionalMeshes=0, sourceLightingPartialMeshes=0)
    for mesh in result.get("meshes", []):
        material = mesh.get("material", {}).get("lighting", {})
        if not material.get("sourceVerified") or not mesh.get("sourceNormals"):
            continue
        limits = {"runtime-light-state"}
        if mesh.get("staticGeometry"):
            memberships, bounds = mesh.get("cellIndices", []), mesh.get("serializedBounds", [])
            if mesh.get("lightReferences") or mesh.get("partLightReferences"):
                limits.add("explicit-part-light-bindings")
        elif mesh.get("nodeIndex") in nodes and "descriptorIndex" in mesh:
            memberships, local_bounds = _platform_cells(data, nodes[mesh["nodeIndex"]], mesh["descriptorIndex"], sections, len(cells))
            matrix = mesh["worldMatrix"]
            if local_bounds:
                corners = [[local_bounds[a + (3 if bits & (1 << a) else 0)] for a in range(3)] for bits in range(8)]
                transformed = [[sum(matrix[r * 4 + c] * p[c] for c in range(3)) + matrix[r * 4 + 3] for r in range(3)] for p in corners]
                bounds = [min(p[a] for p in transformed) for a in range(3)] + [max(p[a] for p in transformed) for a in range(3)]
            else:
                positions = mesh.get("positions", [])
                bounds = [min(positions[a::3]) for a in range(3)] + [max(positions[a::3]) for a in range(3)]
        else:
            continue
        selected, selection_limits = select_cell_lights(cells, memberships, lights, bounds)
        limits.update(selection_limits)
        ambient, directionals, omitted = [0., 0., 0.], [], []
        for light in selected:
            state = light["state"]
            if state["type"] == "directional":
                directionals.append({"sourceId": state["sourceId"], "nodeIndex": light["nodeIndex"],
                                     "direction": light["direction"],
                                     "diffuse": [material["diffuse"][a] * state["color"][a] for a in range(3)]})
            elif state["type"] == "ambient":
                for axis in range(3):
                    ambient[axis] += material["ambient"][axis] * state["color"][axis]
            else:
                omitted.append(state["sourceId"])
                limits.add("point-spot-shaders")
        mesh["sourceLighting"] = {"sourceVerified": True, "mode": "retail-directional", "ambient": ambient,
                                  "emissive": material["emissive"][:3], "directionals": directionals,
                                  "cellIndices": memberships, "selectedSourceIds": [l["state"]["sourceId"] for l in selected],
                                  "omittedSourceIds": omitted, "limitations": sorted(limits),
                                  "limit": "État initial ; lumières ponctuelles, projecteurs et changements pendant la partie non reproduits."}
        counts["sourceLightingMeshes"] += 1
        counts["sourceDirectionalMeshes"] += bool(directionals)
        counts["sourceLightingPartialMeshes"] += bool(omitted or selection_limits)
    result.setdefault("stats", {}).update(counts)
    result["lighting"] = {"sourceVerified": True, "mode": "serialized-cells-initial-directional",
                          "sourceLightCount": len(lights), "cellLightIds": cells,
                          "limitations": ["runtime-light-state", "runtime-created-global-lights", "point-spot-shaders", "explicit-part-light-bindings"]}
