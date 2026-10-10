"""Bounded retail LEVL sky metadata and explicit render-cell membership.

XBE C0B37 reads LEVL+0x1A (sky cell); C0B49..C0B92 reads +0x0C..14
and converts centimetres with 0.01. A platform's 72-byte descriptor stores
cell count at +0x20 and a relative pointer at +0x24 to uint16 cell indices.
C297E..C29C9 reads that exact list and indexes runtime cells with stride B4.
The sky pass C1AA0 uses the source anchor and camera orientation separately.
Day/night labels below are explicit editor choices from authored node names;
the runtime gameState animation is not evaluated.
"""
from __future__ import annotations

import math
import struct

from library_bindings import read_library_platform_bindings


def _bounded(start, length, section):
    if length < 0 or not section.offset <= start <= section.offset + section.size - length:
        raise ValueError("Données d’environnement hors de leur ressource.")
    return start


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def read_environment_header(data, sections):
    """Read the unique LEVL sky cell and source-unit anchor, without geometry."""
    candidates = [section for section in sections if section.tag == "levl"]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("Plusieurs descripteurs LEVL rendent l’environnement ambigu.")
    section = candidates[0]
    if section.offset < 64 or section.offset + section.size > len(data):
        raise ValueError("Descripteur LEVL hors de l’archive.")
    _bounded(section.offset, 64, section)
    base = section.offset
    anchor_source = struct.unpack_from("<3f", data, base + 12)
    if not all(math.isfinite(value) and abs(value) <= 100000000 for value in anchor_source):
        raise ValueError("Origine du ciel non finie ou hors limites.")
    index = struct.unpack_from("<H", data, base + 26)[0]
    count = struct.unpack_from("<I", data, base + 28)[0]
    if count > 65535:
        raise ValueError("Nombre de cellules LEVL non valide.")
    cells = []
    if count:
        table = _bounded(_relative(data, base + 32), count * 72, section)
        if table < base + 64:
            raise ValueError("Table des cellules LEVL superposée à l’en-tête.")
        for cell_index in range(count):
            record = table + cell_index * 72
            length = struct.unpack_from("<I", data, record)[0]
            if length > 1024:
                raise ValueError("Nom de cellule LEVL trop long.")
            name_pointer = _bounded(_relative(data, record + 4), length + 1, section)
            raw = data[name_pointer:name_pointer + length]
            if data[name_pointer + length] or b"\0" in raw:
                raise ValueError("Nom de cellule LEVL non terminé.")
            try:
                name = raw.decode("ascii")
            except UnicodeDecodeError as exc:
                raise ValueError("Nom de cellule LEVL non ASCII.") from exc
            cells.append({"index": cell_index, "name": name, "sourceOffset": record})
    if index != 0xFFFF and index >= count:
        raise ValueError("Le ciel référence une cellule LEVL inexistante.")
    return {"skyIndex": None if index == 0xFFFF else index,
            "anchor": [value * 0.01 for value in anchor_source],
            "sourceAnchor": list(anchor_source), "unitScale": 0.01,
            "cells": cells, "sourceOffset": base,
            "anchorOffset": base + 12, "skyIndexOffset": base + 26,
            "sourceTag": "levl", "sourceVerified": True}


def _descriptor_memberships(data, node, sections, cell_count):
    record = node.get("recordOffset")
    if isinstance(record, bool) or not isinstance(record, int):
        raise ValueError("Offset de plateforme d’environnement absent.")
    sections_for_node = [section for section in sections if section.tag == "node"
                         and section.offset <= record <= section.offset + section.size - 36]
    if len(sections_for_node) != 1:
        raise ValueError("Plateforme d’environnement hors de son graphe.")
    section = sections_for_node[0]
    references = read_library_platform_bindings(data, section, record, sections)
    count = struct.unpack_from("<I", data, record + 28)[0]
    if not count:
        return [], {}
    table = _relative(data, record + 32)
    memberships = {}
    for descriptor_index in range(count):
        descriptor = table + descriptor_index * 72
        amount = struct.unpack_from("<I", data, descriptor + 32)[0]
        if amount > cell_count:
            raise ValueError("Nombre de cellules de plateforme non valide.")
        if amount:
            pointer = _bounded(_relative(data, descriptor + 36), amount * 2, section)
            if pointer < table + count * 72:
                raise ValueError("Cellules de plateforme superposées aux descripteurs.")
            indices = list(struct.unpack_from("<" + str(amount) + "H", data, pointer))
            if len(set(indices)) != len(indices) or any(index >= cell_count for index in indices):
                raise ValueError("Référence de cellule de plateforme non valide.")
        else:
            indices = []
        memberships[descriptor_index] = indices
    return references, memberships


def _preview_variant(node, by_index):
    """Classify authored day/night names only as an explicit preview option."""
    seen = set()
    while node and node["index"] not in seen:
        seen.add(node["index"])
        name = node.get("name", "").rsplit(":", 1)[-1].lower()
        if name.startswith("night"):
            return "night"
        if name.startswith("day"):
            return "day"
        node = by_index.get(node.get("parent"))
    return "common"


def _static_sky_meshes(data, sections, meshes, header):
    """Cross-check placed static metadata against the actual LEVL descriptors."""
    static_meshes = [mesh for mesh in meshes if mesh.get("sceneRole") == "static"]
    if not static_meshes:
        return set(), set(), []
    try:
        from static_scene import read_static_descriptors
        parsed = read_static_descriptors(data, sections)
        section = parsed["section"]
        if section.offset != header["sourceOffset"] or section.tag != "levl":
            raise ValueError("La table statique ne provient pas du LEVL de ce ciel.")
        descriptors = {row["index"]: row for row in parsed["descriptors"]}
        if len(descriptors) != len(parsed["descriptors"]):
            raise ValueError("Indices de descripteurs statiques ambigus.")
    except (ValueError, struct.error, KeyError, ImportError) as exc:
        return set(), set(), ["Cellules statiques du ciel non résolues : " + str(exc)]
    matched, descriptor_indices, warnings = set(), set(), []
    for mesh in static_meshes:
        label = mesh.get("id", "maillage statique")
        try:
            index = mesh.get("staticDescriptorIndex")
            if isinstance(index, bool) or not isinstance(index, int) or index not in descriptors:
                raise ValueError("Indice de descripteur statique non valide.")
            descriptor = descriptors[index]
            if (mesh.get("staticDescriptorOffset") != descriptor["offset"]
                    or mesh.get("levlResource") != section.index):
                raise ValueError("Le placement ne référence pas son descripteur LEVL.")
            cells = mesh.get("cellIndices")
            if (not isinstance(cells, list) or cells != descriptor["cells"]
                    or any(isinstance(cell, bool) or not isinstance(cell, int)
                           or not 0 <= cell < len(header["cells"]) for cell in cells)):
                raise ValueError("Les cellules statiques ne correspondent pas aux données source.")
            references = [row for row in descriptor["bindings"]
                          if row["bindingOffset"] == mesh.get("bindingOffset")
                          and row["meshResource"] == mesh.get("resourceIndex")
                          and row["primitiveResource"] == mesh.get("primitiveResource")]
            if len(references) != 1:
                raise ValueError("Liaison statique RDMS/primitive non vérifiée.")
            resource = references[0]["meshResource"]
            if (resource >= len(sections) or sections[resource].index != resource
                    or sections[resource].tag != "rdms"
                    or mesh.get("sourceOffset") != sections[resource].offset):
                raise ValueError("Origine de ressource statique non vérifiée.")
            if header["skyIndex"] in cells:
                mesh_id = mesh.get("id")
                if not isinstance(mesh_id, str) or not mesh_id or mesh_id in matched:
                    raise ValueError("Identifiant de maillage statique absent ou ambigu.")
                matched.add(mesh_id)
                descriptor_indices.add(index)
        except (ValueError, KeyError, TypeError) as exc:
            warnings.append(f"Cellules statiques de {label} non résolues : {exc}")
    return matched, descriptor_indices, warnings


def build_environment(data, sections, nodes, meshes):
    """Return scene.environment; select sky meshes through serialized cell refs.

    This does not change nodes, meshes, source arrays or visibility. meshIds
    contain existing placed mesh IDs only, never an inferred source pool.
    """
    try:
        header = read_environment_header(data, sections)
    except (ValueError, struct.error) as exc:
        return {"sky": {"available": False, "meshIds": [], "variants": [], "sourceVerified": False},
                "warnings": ["Ciel non résolu : " + str(exc)]}
    if header is None:
        return {"sky": {"available": False, "meshIds": [], "variants": [], "sourceVerified": False}}
    sky = {key: value for key, value in header.items() if key != "cells"}
    sky.update(available=header["skyIndex"] is not None, meshIds=[], variants=[],
               cameraRelative=True, animationStateVerified=False,
               selectionMode="authored-name-preview")
    if not sky["available"]:
        return {"sky": sky, "cellCount": len(header["cells"])}
    sky["cellName"] = header["cells"][sky["skyIndex"]]["name"]
    sky["cellOffset"] = header["cells"][sky["skyIndex"]]["sourceOffset"]
    by_index = {node["index"]: node for node in nodes}
    if len(by_index) != len(nodes):
        raise ValueError("Indices de nœuds d’environnement ambigus.")
    matches, sky_nodes, warnings = {}, set(), []
    for node in nodes:
        if node.get("type") != "platform":
            continue
        try:
            references, memberships = _descriptor_memberships(data, node, sections, len(header["cells"]))
        except (ValueError, struct.error) as exc:
            warnings.append(f"Cellules de {node.get('name', node['index'])} non résolues : {exc}")
            continue
        for reference in references:
            if sky["skyIndex"] in memberships[reference["descriptorIndex"]]:
                key = (node["index"], reference["meshResource"], reference["primitiveResource"])
                matches[key] = _preview_variant(node, by_index)
                sky_nodes.add(node["index"])
    static_ids, static_indices, static_warnings = _static_sky_meshes(data, sections, meshes, header)
    warnings.extend(static_warnings)
    ids = set()
    phases = {"day": [], "night": [], "common": []}
    for mesh in meshes:
        key = (mesh.get("nodeIndex"), mesh.get("resourceIndex"), mesh.get("primitiveResource"))
        phase = ("common" if mesh.get("id") in static_ids else
                 None if mesh.get("sceneRole") == "static" else matches.get(key))
        if phase is None:
            continue
        mesh_id = mesh.get("id")
        if not isinstance(mesh_id, str) or not mesh_id or mesh_id in ids:
            raise ValueError("Identifiant de maillage du ciel absent ou ambigu.")
        ids.add(mesh_id)
        sky["meshIds"].append(mesh_id)
        phases[phase].append(mesh_id)
    for phase, label in (("day", "Jour"), ("night", "Nuit")):
        if phases[phase]:
            sky["variants"].append({"id": phase, "label": label,
                                    "meshIds": phases[phase] + phases["common"],
                                    "stateVerified": False})
    sky["variants"].append({"id": "all", "label": "Tous les éléments",
                            "meshIds": sky["meshIds"][:], "stateVerified": False})
    sky.update(nodeIndices=sorted(sky_nodes), geometryAvailable=bool(sky["meshIds"]),
               staticDescriptorIndices=sorted(static_indices),
               classificationVerified=not warnings,
               defaultVariant="day" if phases["day"] else "all")
    return {"sky": sky, "cellCount": len(header["cells"]), "warnings": warnings}
