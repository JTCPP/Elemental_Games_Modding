"""Azurik node graph transforms and explicit platform/primitive instances.

The node resource's +4 field points at a self-relative pointer array. Its
entries point to typed records. Transform parameters are visibility, Txyz,
Euler Rxyz (radians), Sxyz; +24 is the parent index. Resource pools are
instantiated only through a platform's validated render-wrapper bindings.
"""
from __future__ import annotations

import math
import struct
from collections import Counter

from platform_render import read_platform_render_state
from retail_lighting import read_light_state


def identity():
    return [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]


def multiply(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def transform_point(matrix, point):
    return [sum(matrix[r * 4 + k] * point[k] for k in range(3)) + matrix[r * 4 + 3] for r in range(3)]


def transform_vector(matrix, point):
    return [sum(matrix[r * 4 + k] * point[k] for k in range(3)) for r in range(3)]


def inverse_affine(m):
    a, b, c, d, e, f, g, h, i = (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10])
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("Transformation parente non inversible.")
    inv = [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
           (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
           (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]
    result = identity()
    for row in range(3):
        for col in range(3):
            result[row * 4 + col] = inv[row * 3 + col]
        result[row * 4 + 3] = -sum(inv[row * 3 + col] * m[col * 4 + 3] for col in range(3))
    return result


def transform_normals(matrix, normals):
    """Apply inverse transpose without normalizing source vector magnitudes.

    This is the standard affine preview normal transform, not a claim that
    every retail lighting shader uses this exact nonuniform-scale path.
    A singular matrix returns unchanged local vectors and a false status.
    """
    if len(normals) % 3 or not all(math.isfinite(value) for value in normals):
        raise ValueError("Normales source non valides.")
    try:
        inverse = inverse_affine(matrix)
    except ValueError:
        return list(normals), False
    transformed = [sum(inverse[column * 4 + row] * normals[offset + column] for column in range(3))
                   for offset in range(0, len(normals), 3) for row in range(3)]
    if not all(math.isfinite(value) for value in transformed):
        raise ValueError("Transformation des normales non finie.")
    return transformed, True


def normal_attributes(pool, used, matrix=None):
    """Keep normals and packed source words aligned with selected vertices."""
    source = pool.get("sourceNormals") or pool.get("normals")
    if not source:
        return {}
    if len(source) != len(pool["positions"]):
        raise ValueError("Nombre de normales source incompatible avec les sommets.")
    local = [value for index in used for value in source[index * 3:index * 3 + 3]]
    transformed, verified = transform_normals(identity() if matrix is None else matrix, local)
    attributes = {"sourceNormals": local, "normals": transformed,
                  "normalFormat": pool.get("normalFormat", "float3"),
                  "normalSourceVerified": pool.get("normalSourceVerified", True),
                  "normalSpace": ("world" if matrix is not None else "local") if verified else "local-fallback",
                  "normalTransformVerified": verified,
                  "normalTransform": "inverse-transpose-preview" if verified else "singular-source-local-fallback"}
    packed = pool.get("packedNormals")
    if packed:
        if len(packed) != len(pool["positions"]) // 3 * 4:
            raise ValueError("Nombre de normales compactes incompatible avec les sommets.")
        attributes["packedNormals"] = [value for index in used for value in packed[index * 4:index * 4 + 4]]
    return attributes


def translation(value):
    result = identity()
    for axis in range(3):
        result[axis * 4 + 3] = value[axis]
    return result


def local_matrix(position, rotation, scale, rotate_pivot=(0, 0, 0), rotate_pivot_translation=(0, 0, 0),
                 scale_pivot=(0, 0, 0), scale_pivot_translation=(0, 0, 0), rotation_order=0):
    """Retail Euler orders (BC7B0/199B78), preserving serialized pivots."""
    rx, ry, rz = rotation
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    x = [1, 0, 0, 0, 0, cx, -sx, 0, 0, sx, cx, 0, 0, 0, 0, 1]
    y = [cy, 0, sy, 0, 0, 1, 0, 0, -sy, 0, cy, 0, 0, 0, 0, 1]
    z = [cz, -sz, 0, 0, sz, cz, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    s = identity()
    for axis in range(3):
        s[axis * 4 + axis] = scale[axis]
    result = identity()
    orders = ((0, 1, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0), (1, 0, 2), (0, 2, 1))
    if not isinstance(rotation_order, int) or not 0 <= rotation_order < len(orders):
        raise ValueError("Ordre Euler non décodé.")
    rotations = [x, y, z]
    for part in (translation(position), translation(rotate_pivot_translation), translation(rotate_pivot),
                 *(rotations[axis] for axis in reversed(orders[rotation_order])), translation([-v for v in rotate_pivot]), translation(scale_pivot_translation),
                 translation(scale_pivot), s, translation([-v for v in scale_pivot])):
        result = multiply(result, part)
    return result


def _matrix_quaternion(m):
    """Retail A06A0 conversion: intentionally no scale removal/normalization."""
    trace = m[0] + m[5] + m[10]
    if trace > 0:
        root = math.sqrt(trace + 1)
        factor = 0.5 / root
        return [(m[9] - m[6]) * factor, (m[2] - m[8]) * factor,
                (m[4] - m[1]) * factor, 0.5 * root]
    i = max(range(3), key=lambda axis: m[axis * 5])
    j, k = (i + 1) % 3, (i + 2) % 3
    root = math.sqrt(m[i * 5] - m[j * 5] - m[k * 5] + 1)
    if root == 0:
        raise ValueError("Quaternion parent singulier.")
    result = [0., 0., 0., 0.]
    result[i] = 0.5 * root
    factor = 0.5 / root
    result[j] = (m[i * 4 + j] + m[j * 4 + i]) * factor
    result[k] = (m[i * 4 + k] + m[k * 4 + i]) * factor
    result[3] = (m[k * 4 + j] - m[j * 4 + k]) * factor
    return result


def _quaternion_product(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w * X + x * W + y * Z - z * Y, w * Y - x * Z + y * W + z * X,
            w * Z + x * Y - y * X + z * W, w * W - x * X - y * Y - z * Z]


def _quaternion_matrix(q):
    x, y, z, w = q
    return [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.,
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.,
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.,
            0., 0., 0., 1.]


def effective_parent_matrix(node, parent_world, position=None, camera_quaternion=(0., 0., 0., 1.)):
    """Verified parent modes 0..3 from retail 68190.

    Mode 1 drops inherited orientation/scale. Mode 2 removes parent tilt by
    the exact quaternion swing calculation in 68309, keeping rotation around
    world Z. Both preserve the world position of T + rotatePivot. The raw
    parent remains the derivative for edits to T, not this effective basis.
    """
    mode = node.get("inheritMode", 0)
    if mode == 0:
        return parent_world[:]
    if mode not in (1, 2, 3):
        raise ValueError("Mode d'héritage parent non décodé.")
    effective = identity()
    if mode == 3:
        # 687BA: camera quaternion (identity when no camera is active),
        # uniform scale from the parent's first basis column, same pivot.
        effective = _quaternion_matrix(camera_quaternion)
        uniform = math.sqrt(sum(parent_world[axis * 4] ** 2 for axis in range(3)))
        for row in range(3):
            for column in range(3):
                effective[row * 4 + column] *= uniform
    if mode == 2:
        q = _matrix_quaternion(parent_world)
        x, y, z, w = q
        direction = [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]
        length = math.sqrt(sum(v * v for v in direction))
        if length:
            direction = [v / length for v in direction]
        half = [direction[0], direction[1], direction[2] + 1]
        length = math.sqrt(sum(v * v for v in half))
        if length:
            half = [v / length for v in half]
        swing = [-half[1], half[0], 0., half[2]]
        effective = _quaternion_matrix(_quaternion_product(q, swing))
    pivot = node.get("rotatePivot", [0., 0., 0.])
    point = [a + b for a, b in zip(position if position is not None else node["position"], pivot)]
    world_point = transform_point(parent_world, point)
    effective_point = transform_vector(effective, point)
    for axis in range(3):
        effective[axis * 4 + 3] = world_point[axis] - effective_point[axis]
    return effective


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def _bounded(pointer, length, base, stop):
    if pointer < base or length < 0 or pointer + length > stop:
        raise ValueError("Pointeur du graphe hors de la section node.")
    return pointer


def _debug_names(data, section, count):
    if section is None or section.size < 24:
        return {}
    base, stop = section.offset, section.offset + section.size
    if struct.unpack_from("<I", data, base + 8)[0] != count:
        return {}
    try:
        table = _bounded(_relative(data, base + 12), count * 4, base, stop)
        result = {}
        for index in range(count):
            record = _bounded(_relative(data, table + index * 4), 8, base, stop)
            length = struct.unpack_from("<I", data, record)[0]
            if length > 400:
                continue
            name = _bounded(_relative(data, record + 4), length, base, stop)
            result[index] = data[name:name + length].decode("ascii", "replace")
        return result
    except (ValueError, struct.error):
        return {}


def read_graph(data, sections, *, node_index=None, resolve_world=True):
    candidates = [section for section in sections if section.tag == "node"]
    if not candidates:
        raise ValueError("Le niveau ne contient pas de graphe node.")
    section = candidates[-1] if node_index is None else next((s for s in candidates if s.index == node_index), None)
    if section is None:
        raise ValueError("Le graphe demandé n’existe pas dans cette archive.")
    base, stop = section.offset, section.offset + section.size
    if section.size < 32:
        raise ValueError("Graphe node tronqué.")
    count = struct.unpack_from("<I", data, base)[0]
    if not 0 < count <= 50000:
        raise ValueError("Nombre de nœuds non valide.")
    table = _bounded(_relative(data, base + 4), count * 4, base, stop)
    debug = next((row for row in reversed(sections) if row.tag == "ndbg" and row.index < section.index), None)
    names = _debug_names(data, debug, count)
    nodes = []
    for index in range(count):
        record = _bounded(_relative(data, table + index * 4), 28, base, stop)
        length, _pointer, parameter_count = struct.unpack_from("<III", data, record)
        if not 0 < length <= 80 or parameter_count > 20000:
            raise ValueError("Déclaration de nœud inconnue.")
        type_start = _bounded(_relative(data, record + 4), length, base, stop)
        kind = data[type_start:type_start + length].decode("ascii")
        if not kind.isascii() or not all(c.isalnum() or c == "_" for c in kind):
            raise ValueError("Type de nœud illisible.")
        parameter_start = _bounded(_relative(data, record + 12), parameter_count * 4, base, stop)
        parent = struct.unpack_from("<i", data, record + 24)[0]
        node = {"index": index, "name": names.get(index, f"Nœud {index:04d}"), "type": kind,
                "recordOffset": record, "paramsOffset": parameter_start, "parent": parent,
                "localMatrix": identity(), "transformVerified": True}
        # Logical owners may reference themselves (retail gameState/timer).
        # They have no spatial transform and must not abort the level tree.
        if parent == index and kind not in ("transform", "joint", "platform", "critterGenerator"):
            node.update(parent=-1, declaredParent=parent, transformVerified=False)
        if kind in ("transform", "joint", "lod"):
            # Retail lod loader 69640 calls the same 67F30 transform loader,
            # including all pivots, before reading its nine distance fields.
            # Its parameter descriptor inherits the first ten TRS channels;
            # cameraDistance and output[0..9] occupy channels 10..20 (FE1B0).
            _bounded(record, 156 if kind == "lod" else 120, base, stop)
            if parameter_count != (21 if kind == "lod" else 10):
                raise ValueError("Nombre de paramètres de transform non décodé.")
            params = list(struct.unpack_from("<10f", data, parameter_start))
            if not all(math.isfinite(v) and abs(v) < 100000 for v in params):
                raise ValueError("Paramètres de transform non valides.")
            mode, order = struct.unpack_from("<I", data, record + 40)[0], struct.unpack_from("<I", data, record + 108)[0]
            orient = struct.unpack_from("<4f", data, record + 92)
            pivots = [struct.unpack_from("<3f", data, record + offset) for offset in (44, 56, 68, 80)]
            valid = mode in (0, 1, 2, 3) and order in range(6) and orient == (0., 0., 0., 1.) and all(math.isfinite(v) and abs(v) < 100000 for pivot in pivots for v in pivot)
            node.update(position=params[1:4], rotation=params[4:7], scale=params[7:10], visibility=params[0],
                        coordOffset=parameter_start + 4, rotationOffset=parameter_start + 16,
                        scaleOffset=parameter_start + 28, transformVerified=valid, inheritMode=mode, rotationOrder=order,
                        rotatePivot=list(pivots[2]), rotatePivotTranslation=list(pivots[3]),
                        scalePivot=list(pivots[0]), scalePivotTranslation=list(pivots[1]),
                        originalParameterBytes={"position": data[parameter_start + 4:parameter_start + 16].hex(),
                                                "rotation": data[parameter_start + 16:parameter_start + 28].hex(),
                                                "scale": data[parameter_start + 28:parameter_start + 40].hex()})
            if valid:
                # 67F30/68B90: serialized +44/+56 are scale pivots;
                # +68/+80 are rotation pivots, not the reverse.
                node["localMatrix"] = local_matrix(params[1:4], params[4:7], params[7:10],
                                                 pivots[2], pivots[3], pivots[0], pivots[1], rotation_order=order)
            if kind == "lod":
                thresholds = list(struct.unpack_from("<9f", data, record + 120))
                node["lodThresholds"] = thresholds
                node["lodThresholdsVerified"] = all(math.isfinite(v) and v >= 0 for v in thresholds)
                node["lodSourceOutputs"] = list(struct.unpack_from("<10f", data, parameter_start + 44))
        elif kind == "platform" and parameter_count:
            visibility = struct.unpack_from("<f", data, parameter_start)[0]
            if math.isfinite(visibility):
                node["visibility"] = visibility
            render_state = read_platform_render_state(data, node, sections)
            if render_state is not None:
                node["platformRender"] = render_state
        elif kind == "light":
            light_state = read_light_state(data, node, sections)
            if light_state is not None:
                node["lightState"] = light_state
        nodes.append(node)
    _resolve_lod_visibility(data, section, nodes)
    if not resolve_world:
        for node in nodes:
            node["worldPositionVerified"] = False
        return section, nodes
    visiting = set()
    def world(index):
        node = nodes[index]
        if "worldMatrix" in node:
            return node["worldMatrix"]
        if index in visiting:
            raise ValueError("Cycle détecté dans les parents du graphe.")
        visiting.add(index)
        parent = node["parent"]
        if parent == -1:
            parent_world, parent_verified, parent_visible, parent_editor_visible = identity(), True, True, True
            parent_lod = []
        elif 0 <= parent < count:
            parent_world = world(parent)
            parent_verified = nodes[parent]["worldPositionVerified"]
            parent_visible = nodes[parent]["initiallyVisible"]
            parent_editor_visible = nodes[parent]["editorVisible"]
            parent_lod = nodes[parent].get("lodSelections", [])
        else:
            parent_world, parent_verified, parent_visible, parent_editor_visible = identity(), False, True, True
            parent_lod = []
        node["rawParentWorldMatrix"] = parent_world
        effective = effective_parent_matrix(node, parent_world) if node["transformVerified"] and node.get("inheritMode", 0) else parent_world
        node["parentWorldMatrix"] = effective
        node["worldMatrix"] = multiply(effective, node["localMatrix"])
        node["worldPositionVerified"] = bool(parent_verified and node["transformVerified"])
        node["cameraDependent"] = node.get("inheritMode", 0) == 3 or (0 <= parent < count and nodes[parent].get("cameraDependent", False))
        node["worldPosition"] = transform_point(node["worldMatrix"], (0, 0, 0))
        node["initiallyVisible"] = parent_visible and bool(node.get("visibility", 1))
        node["editorVisible"] = parent_editor_visible and bool(node.get("editorVisibility", node.get("visibility", 1)))
        node["lodSelections"] = parent_lod + ([node["lodSelection"]] if "lodSelection" in node else [])
        visiting.remove(index)
        return node["worldMatrix"]
    for index in range(count):
        world(index)
    return section, nodes


def _resolve_lod_visibility(data, section, nodes):
    """Select output[0] only through verified serialized LOD visibility links.

    658A0 loads 24-byte outgoing links at record +16/+20; parameters +12/+16
    are uint16 and the destination node is +20. 696E0 evaluates the camera
    distance and writes the ten output channels. The editor previews the
    most detailed output without changing authored parameters or running a
    camera-dependent game simulation. Unknown/curved links stay untouched.
    """
    base, stop = section.offset, section.offset + section.size
    for node in nodes:
        if node["type"] != "lod":
            continue
        node["lodPreviewMode"] = "highest-detail"
        node["lodBindingsVerified"] = False
        record = node["recordOffset"]
        count = struct.unpack_from("<I", data, record + 16)[0]
        if not 0 < count <= 20000 or not node.get("lodThresholdsVerified"):
            node["lodBindingWarning"] = "Liaisons de visibilité LOD non validées."
            continue
        try:
            table = _bounded(_relative(data, record + 20), count * 24, base, stop)
            links = []
            for index in range(count):
                offset = table + index * 24
                weight, curve, factor = struct.unpack_from("<fif", data, offset)
                source_parameter = struct.unpack_from("<H", data, offset + 12)[0]
                target_parameter = struct.unpack_from("<H", data, offset + 16)[0]
                target_index = struct.unpack_from("<I", data, offset + 20)[0]
                if not (weight == factor == 1. and curve == 1 and 11 <= source_parameter <= 20
                        and target_parameter == 0 and target_index < len(nodes)
                        and nodes[target_index]["type"] in ("transform", "joint", "lod")
                        and nodes[target_index]["parent"] == node["index"]):
                    raise ValueError("Liaison LOD indirecte, courbe ou paramètre non validé.")
                links.append({"outputIndex": source_parameter - 11, "sourceParameter": source_parameter,
                              "targetNode": target_index, "targetParameter": target_parameter,
                              "sourceOffset": offset})
            if not any(link["outputIndex"] == 0 for link in links):
                raise ValueError("La branche LOD de détail maximal n'est pas liée.")
            if len({link["targetNode"] for link in links}) != len(links):
                raise ValueError("Plusieurs sorties LOD contrôlent la même branche.")
            node["lodBindings"] = links
            node["lodBindingsVerified"] = True
            for link in links:
                target = nodes[link["targetNode"]]
                target["editorVisibility"] = 1. if link["outputIndex"] == 0 else 0.
                target["lodSelection"] = {"nodeIndex": node["index"], "branchNode": target["index"],
                                          "outputIndex": link["outputIndex"], "selectedOutput": 0,
                                          "mode": "highest-detail", "runtimeDriven": True,
                                          "selectionVerified": True}
        except (ValueError, struct.error) as exc:
            node["lodBindingWarning"] = str(exc)


def _binding(transform):
    if transform["type"] not in ("transform", "joint", "lod") or not transform["worldPositionVerified"]:
        return None
    try:
        parent_inverse = inverse_affine(transform["parentWorldMatrix"])
        translation_inverse = inverse_affine(transform["rawParentWorldMatrix"])
        inverse_affine(transform["worldMatrix"])
    except ValueError:
        return None
    return {"editOffset": transform["coordOffset"], "originalLocalPosition": transform["position"][:],
            "rotationOffset": transform["rotationOffset"], "scaleOffset": transform["scaleOffset"],
            "originalLocalRotation": transform["rotation"][:], "originalLocalScale": transform["scale"][:],
            "originalBytes": transform["originalParameterBytes"].copy(),
            "parentWorldInverse": parent_inverse, "parentWorldMatrix": transform["parentWorldMatrix"],
            "translationParentWorldMatrix": transform["rawParentWorldMatrix"],
            "translationParentWorldInverse": translation_inverse,
            "inheritMode": transform.get("inheritMode", 0), "rotationOrder": transform.get("rotationOrder", 0),
            "transformNode": transform["index"]}


def _item_transform(item, binding):
    """Expose only channels whose serialized transform record was verified."""
    if binding:
        item.update(localPosition=binding["originalLocalPosition"][:],
                    localRotation=binding["originalLocalRotation"][:],
                    localScale=binding["originalLocalScale"][:],
                    originalLocalPosition=binding["originalLocalPosition"][:],
                    originalLocalRotation=binding["originalLocalRotation"][:],
                    originalLocalScale=binding["originalLocalScale"][:],
                    rotationEditable=True, scaleEditable=True)


def _kind(name):
    if name in ("diamond", "emerald", "sapphire", "ruby", "obsidian"):
        return "collectible"
    if name.startswith(("power_", "frag_")):
        return "powerup"
    if any(k in name for k in ("fuel", "health")):
        return "pickup"
    return "enemy"


def resolve_scene(data, mesh_library, sections=None):
    from renderer_parser import read_sections, decode_pushbuffer
    from library_bindings import read_library_platform_bindings
    sections = sections or read_sections(data)
    section, nodes = read_graph(data, sections)
    library = {mesh["resourceIndex"]: mesh for mesh in mesh_library}
    instances, objects, warnings = [], [], []
    failures = Counter()
    primitive_cache = {}
    for node in nodes:
        parent = nodes[node["parent"]] if 0 <= node["parent"] < len(nodes) else None
        if node["type"] in ("critterGenerator", "playerLocation", "camera", "light") and parent is not None:
            if not node["worldPositionVerified"]:
                failures["objets sans transformation parente validée"] += 1
                continue
            record = node["recordOffset"]
            if node["type"] == "critterGenerator":
                length = struct.unpack_from("<I", data, record + 28)[0]
                if not 0 < length <= 100:
                    continue
                pointer = _relative(data, record + 32)
                if not section.offset <= pointer <= section.offset + section.size - length:
                    continue
                name = data[pointer:pointer + length].decode("ascii", "replace")
            else:
                name = {"playerLocation": "Départ joueur", "camera": "Caméra", "light": "Lumière"}[node["type"]] + " · " + node["name"]
            binding = _binding(parent)
            point = parent["worldPosition"][:]
            offset = parent.get("coordOffset", node["recordOffset"])
            item = {"id": f"entity-{offset:08x}-{node['index']:04d}", "name": name, "nodeName": node["name"],
                    "kind": _kind(name) if node["type"] == "critterGenerator" else {"playerLocation": "spawn", "camera": "camera", "light": "light"}[node["type"]],
                    "position": point, "originalPosition": point[:], "coordOffset": offset,
                    "coordinateSpace": "world", "worldPositionVerified": parent["worldPositionVerified"],
                    "editable": binding is not None, "nodeIndex": node["index"], "localPosition": parent.get("position")}
            _item_visibility(item, node)
            if binding:
                item["editBinding"] = binding
                _item_transform(item, binding)
            objects.append(item)
        if node["type"] != "platform":
            continue
        try:
            bindings = read_library_platform_bindings(data, section, node["recordOffset"], sections)
        except (ValueError, struct.error) as exc:
            failures[str(exc)] += 1
            continue
        # Pairs inside a material descriptor are distance alternatives.
        # Display its most detailed version once, retaining all materials.
        detail = {}
        for reference in bindings:
            key = reference["descriptorIndex"]
            if key not in detail or reference["maxDistance"] < detail[key]["maxDistance"]:
                detail[key] = reference
        for index, reference in sorted(detail.items()):
            pool = library.get(reference["meshResource"])
            if pool is None:
                failures["réserve de sommets non décodée"] += 1
                continue
            key = (reference["meshResource"], reference["primitiveResource"])
            try:
                if key not in primitive_cache:
                    primitive_cache[key] = decode_pushbuffer(data, sections[reference["primitiveResource"]], len(pool["positions"]) // 3)
                indices = primitive_cache[key]
                if not indices:
                    failures["aucun triangle dans la primitive"] += 1
                    continue
            except (ValueError, struct.error) as exc:
                failures[str(exc)] += 1
                continue
            if not node["worldPositionVerified"]:
                failures["parent spatial non résolu"] += 1
                continue
            matrix = node["worldMatrix"]
            # GPU buffers can select a small portion of a shared vertex pool.
            # Keep only referenced vertices, including their matching UV/color.
            used = sorted(set(indices))
            remap = {source: destination for destination, source in enumerate(used)}
            positions, colors, uvs = [], [], []
            for source in used:
                positions.extend(transform_point(matrix, pool["positions"][source * 3:source * 3 + 3]))
                colors.extend(pool["colors"][source * 3:source * 3 + 3])
                uvs.extend(pool["uvs"][source * 2:source * 2 + 2])
            indices = [remap[source] for source in indices]
            centre = [(min(positions[axis::3]) + max(positions[axis::3])) / 2 for axis in range(3)]
            part = {"start": 0, "count": len(indices), "partIndex": 0}
            if pool.get("textureStages"):
                texture = pool["textureStages"][0].get("textureId")
                if texture:
                    part["textureId"] = texture
            edit = _binding(parent) if parent is not None else None
            item = {"id": f"mesh-{pool['sourceOffset']:08x}-node-{node['index']:04d}-{index}",
                    "name": node["name"], "tag": "platform", "sourceOffset": pool["sourceOffset"],
                    "origin": transform_point(matrix, pool["origin"]), "position": centre, "originalPosition": centre[:],
                    "positions": positions, "colors": colors, "uvs": uvs, "indices": indices,
                    "parts": [part], "resourceIndex": pool["resourceIndex"], "primitiveResource": reference["primitiveResource"],
                    "shaderResource": pool.get("shaderResource"), "shaderIndex": pool.get("shaderIndex"),
                    "material": pool.get("material", {}),
                    "textureStages": pool.get("textureStages", []), "coordinateSpace": "world",
                    "worldPositionVerified": True, "instance": True, "nodeIndex": node["index"],
                    "worldMatrix": matrix, "editable": edit is not None, "lodMaxDistance": reference["maxDistance"]}
            item["descriptorIndex"] = index
            item["bindingOffset"] = reference["bindingOffset"]
            if "platformRender" in node:
                item["platformRender"] = node["platformRender"].copy()
            item.update(normal_attributes(pool, used, matrix))
            _item_visibility(item, node)
            if pool.get("vertexAlphas"):
                item["vertexAlphas"] = [pool["vertexAlphas"][source] for source in used]
            if pool.get("uv2"):
                item["uv2"] = [component for source in used for component in pool["uv2"][source * 2:source * 2 + 2]]
                item["uvSetCount"] = 2
            try:
                inverse_affine(matrix)
            except ValueError:
                # A source child can have a zero scale. Retain only that rare
                # instance's local vertices so edits to its ancestors remain exact.
                item["_localPositions"] = [v for source in used for v in pool["positions"][source * 3:source * 3 + 3]]
                item["_localOrigin"] = pool["origin"][:]
            if edit:
                item["editBinding"] = edit
                _item_transform(item, edit)
            instances.append(item)
    if failures:
        warnings.append("Instances non affichées : " + "; ".join(f"{key} ({value})" for key, value in failures.items()))
    lod_nodes = [node for node in nodes if node["type"] == "lod"]
    if lod_nodes:
        verified_lods = sum(node.get("lodBindingsVerified", False) for node in lod_nodes)
        warnings.append(f"{verified_lods} groupes LOD affichent leur branche de détail maximal dans l'éditeur ; la sélection du jeu selon la distance caméra n'est pas simulée.")
        for node in lod_nodes:
            if node.get("lodBindingWarning"):
                warnings.append(f"LOD {node['name']} : {node['lodBindingWarning']}")
    warnings.append("Les transformations utilisent les valeurs de départ du graphe ; les animations et changements d’état pendant la partie ne sont pas simulés.")
    return {"meshes": instances, "objects": objects, "nodes": nodes, "warnings": warnings,
            "stats": {"sceneNodeCount": len(nodes), "placedMeshCount": len(instances), "placedTriangleCount": sum(len(m["indices"]) // 3 for m in instances),
                      "generatorCount": sum(n["type"] == "critterGenerator" for n in nodes),
                      "placedObjectCount": len(objects), "nodeTypes": dict(Counter(n["type"] for n in nodes)), "placementFailures": dict(failures),
                      "lodNodeCount": len(lod_nodes), "verifiedLodCount": sum(n.get("lodBindingsVerified", False) for n in lod_nodes),
                      "authoredVisibleMeshCount": sum(m["authoredVisible"] for m in instances),
                      "editorVisibleMeshCount": sum(m["visible"] for m in instances),
                      "lodRecoveredMeshCount": sum(m["visible"] and not m["authoredVisible"] for m in instances)}}


def _item_visibility(item, node):
    item["authoredVisible"] = node["initiallyVisible"]
    item["visible"] = node["editorVisible"]
    item["editorVisible"] = node["editorVisible"]
    item["lodControlled"] = bool(node.get("lodSelections"))
    item["cameraDependent"] = bool(node.get("cameraDependent"))
    if item["cameraDependent"]:
        # The displayed bounding centre changes with the view. Keep these
        # effects inspectable without treating that centre as a serialized T.
        item["editable"] = False
    item["visibilityReason"] = "editor-lod-highest-detail" if node.get("lodSelections") else "source-initial"
    if node.get("lodSelections"):
        item["lodSelections"] = node["lodSelections"]
