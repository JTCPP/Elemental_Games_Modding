"""Browse original graphics in every valid dump archive, with exact GPU links.

Static previews expose platform assemblies in resource coordinates. Named
effect graphs also retain their verified original node transforms. Neither
preview executes particles, scripts, or animation, and no source is written.
"""
from collections import Counter, OrderedDict
import copy
import hashlib
from pathlib import Path
import re
import struct

from character_library import named_resources, decode_body
from renderer_parser import (read_sections, read_shader_tables,
                             decode_pushbuffer, decode_mesh, _texture_catalog)
from library_bindings import read_library_platform_bindings
from scene_graph import normal_attributes, read_graph, transform_point


LABELS = {"characters": "Personnages et objets", "fx": "Effets du jeu",
          "interface": "Interface et menus", "diskreplchars": "Personnages des cinématiques",
          "hourglass": "Sablier de chargement", "french": "Ressources françaises",
          "english": "Ressources anglaises", "german": "Ressources allemandes"}


def _public(spec):
    return {key: value for key, value in spec.items() if not key.startswith("_")}


def _detail(bindings):
    result = {}
    for reference in bindings:
        key = reference["descriptorIndex"]
        if key not in result or reference["maxDistance"] < result[key]["maxDistance"]:
            result[key] = reference
    return [result[key] for key in sorted(result)]


def _namespace(textures, references, archive):
    prefix = f"lib-{archive}-"
    catalog = copy.deepcopy(textures)
    for texture in catalog:
        texture["id"] = prefix + texture["id"]
        texture["sourceArchive"] = archive + ".xbr"
        for key in ("frameIds", "faceIds"):
            if key in texture:
                texture[key] = [prefix + value if value is not None else None for value in texture[key]]
    return catalog, {index: prefix + value for index, value in references.items()}


def _used_textures(meshes, catalog):
    required = {stage["textureId"] for mesh in meshes for stage in mesh.get("textureStages", [])
                if stage.get("textureId")}
    for entry in catalog:
        if entry["id"] in required:
            required.update(entry.get("frameIds", []))
            required.update(entry.get("faceIds", []))
    return [entry for entry in catalog if entry["id"] in required]


class AssetLibrary:
    def __init__(self, source_dir, texture_dir):
        source = Path(source_dir)
        self.gamedata = source if source.name.lower() == "gamedata" else source / "gamedata"
        self.texture_dir = Path(texture_dir)
        self._inventory_stamp = None
        self._archives = {}
        self._specs = {}
        self._warnings = {}
        self._loaded = OrderedDict()
        self._models = OrderedDict()

    def _index(self, archive, data, sections):
        names = named_resources(data, sections)
        node_names = {section.index: name for (tag, name), section in names.items() if tag == "node"}
        body_names = {section.index: name for (tag, name), section in names.items() if tag == "body"}
        specs, warnings = {}, []
        for section in sections:
            if section.tag != "body":
                continue
            name = body_names.get(section.index, f"Corps {section.index}")
            spec = {"id": f"body-{section.index}", "name": name, "label": name.removeprefix("characters/"),
                    "kind": "character", "pose": "bind", "archive": archive,
                    "sourceArchive": archive + ".xbr", "sourceOffset": section.offset,
                    "resourceIndex": section.index, "boneCount": struct.unpack_from("<I", data, section.offset)[0],
                    "_body": section.index}
            spec["description"] = f"{spec['boneCount']} os · pose de liaison · {archive}.xbr"
            specs[spec["id"]] = spec
        for graph in (s for s in sections if s.tag == "node"):
            if graph.size >= 4 and struct.unpack_from("<I", data, graph.offset)[0] == 0:
                continue
            try:
                _, nodes = read_graph(data, sections, node_index=graph.index)
            except (ValueError, struct.error) as exc:
                warnings.append(f"Graphe {node_names.get(graph.index, graph.index)} : {exc}")
                if not isinstance(exc, ValueError) or "Cycle détecté" not in str(exc):
                    continue
                # Resource-coordinate previews need no ambiguous scene placement.
                _, nodes = read_graph(data, sections, node_index=graph.index, resolve_world=False)
            graph_parts = []
            for node in nodes:
                if node["type"] != "platform":
                    continue
                try:
                    references = _detail(read_library_platform_bindings(data, graph, node["recordOffset"], sections))
                except (ValueError, struct.error) as exc:
                    warnings.append(f"{node['name']} : {exc}")
                    continue
                if not references:
                    continue
                spec = {"id": f"platform-{graph.index}-{node['index']}", "name": node["name"],
                        "label": node["name"], "kind": "static", "pose": "static", "archive": archive,
                        "sourceArchive": archive + ".xbr", "sourceOffset": node["recordOffset"],
                        "meshCount": len(references), "_references": references,
                        "_graph": graph.index, "_node": node["index"]}
                spec["description"] = f"{len(references)} partie(s) · géométrie originale · {archive}.xbr"
                specs[spec["id"]] = spec
                if node["worldPositionVerified"]:
                    graph_parts.append({"references": references, "matrix": node["worldMatrix"]})
            if graph.index in node_names and graph_parts:
                name = node_names[graph.index]
                spec = {"id": f"graph-{graph.index}", "name": name, "label": name,
                        "kind": "static", "pose": "static", "archive": archive,
                        "sourceArchive": archive + ".xbr", "sourceOffset": graph.offset,
                        "meshCount": sum(len(part["references"]) for part in graph_parts),
                        "_graphParts": graph_parts,
                        "description": "Assemblage d’effet · pose source sans simulation"}
                specs[spec["id"]] = spec
        return specs, warnings

    def inventory(self):
        paths = sorted(self.gamedata.glob("*.xbr"), key=lambda path: path.name.lower())
        stamp = tuple((path.name, path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
        if stamp != self._inventory_stamp:
            archives, specs, warnings, skipped = {}, {}, {}, []
            for path in paths:
                archive = path.stem.lower()
                if not re.fullmatch(r"[a-z0-9_-]+", archive) or archive in archives:
                    skipped.append({"file": path.name, "reason": "Identifiant ambigu ou non pris en charge."})
                    continue
                try:
                    data = path.read_bytes()
                    sections = read_sections(data)
                    graphics, issues = self._index(archive, data, sections)
                except (ValueError, struct.error) as exc:
                    skipped.append({"file": path.name, "reason": str(exc)})
                    continue
                tags = Counter(section.tag for section in sections)
                archives[archive] = {"id": archive, "label": LABELS.get(archive, path.stem),
                                     "file": path.name, "bytes": len(data), "textureCount": tags["surf"],
                                     "bodyCount": tags["body"], "modelCount": len(graphics),
                                     "meshResourceCount": tags["rdms"], "graphCount": tags["node"],
                                     "_path": path, "_stamp": (path.stat().st_size, path.stat().st_mtime_ns)}
                specs[archive], warnings[archive] = graphics, issues
            self._archives, self._specs, self._warnings, self._skipped = archives, specs, warnings, skipped
            self._inventory_stamp = stamp
            self._loaded.clear()
            self._models.clear()
        rows = [_public(row) for row in self._archives.values()]
        return {"archives": rows, "skipped": self._skipped,
                "stats": {"archiveCount": len(rows), "textureCount": sum(r["textureCount"] for r in rows),
                          "bodyCount": sum(r["bodyCount"] for r in rows),
                          "modelCount": sum(r["modelCount"] for r in rows)}}

    def _load(self, archive):
        self.inventory()
        if not isinstance(archive, str) or archive not in self._archives:
            raise ValueError("Cette archive n’existe pas dans la bibliothèque du dump.")
        if archive in self._loaded:
            value = self._loaded.pop(archive)
            self._loaded[archive] = value
            return value
        row = self._archives[archive]
        data = row["_path"].read_bytes()
        if (row["_path"].stat().st_size, row["_path"].stat().st_mtime_ns) != row["_stamp"]:
            raise ValueError("L’archive a changé pendant sa lecture. Rechargez la bibliothèque.")
        sections = read_sections(data)
        warnings = self._warnings[archive][:]
        textures, references = _texture_catalog(data, sections, archive, self.texture_dir, warnings)
        textures, references = _namespace(textures, references, archive)
        value = {"data": data, "sections": sections, "shaders": read_shader_tables(data, sections),
                 "textures": textures, "references": references, "warnings": warnings,
                 "hash": hashlib.sha256(data).hexdigest(), "pools": {}}
        if len(self._loaded) >= 2:
            self._loaded.popitem(last=False)
        self._loaded[archive] = value
        return value

    def catalog(self, archive):
        value = self._load(archive)
        models = [_public(spec) for spec in self._specs[archive].values()]
        return {"archive": _public(self._archives[archive]), "textures": value["textures"],
                "models": models, "warnings": value["warnings"], "sourceHash": value["hash"],
                "stats": {"textureCount": len(value["textures"]), "modelCount": len(models)}}

    def _static_parts(self, value, references, matrix=None):
        result = []
        for reference in references:
            resource = reference["meshResource"]
            if resource not in value["pools"]:
                value["pools"][resource] = decode_mesh(value["data"], value["sections"][resource],
                                                      value["shaders"], value["references"])
            pool = value["pools"][resource]
            indices = decode_pushbuffer(value["data"], value["sections"][reference["primitiveResource"]],
                                        len(pool["positions"]) // 3)
            if not indices:
                continue
            used = sorted(set(indices))
            remap = {old: new for new, old in enumerate(used)}
            mesh = {"id": f"static-{resource}-{reference['primitiveResource']}-{len(result)}",
                    "name": pool["name"], "positions": [], "indices": [remap[i] for i in indices],
                    "material": pool.get("material", {}), "textureStages": pool.get("textureStages", []),
                    "sourceOffset": pool["sourceOffset"], "editable": False}
            for index in used:
                point = pool["positions"][index * 3:index * 3 + 3]
                mesh["positions"].extend(transform_point(matrix, point) if matrix else point)
            for key, width in (("uvs", 2), ("uv2", 2), ("colors", 3), ("vertexAlphas", 1)):
                if pool.get(key):
                    mesh[key] = [component for index in used for component in pool[key][index * width:index * width + width]]
            mesh["uvSetCount"] = 2 if mesh.get("uv2") else 1
            mesh.update(normal_attributes(pool, used, matrix))
            part = {"start": 0, "count": len(indices), "partIndex": len(result)}
            if mesh["textureStages"] and mesh["textureStages"][0].get("textureId"):
                part["textureId"] = mesh["textureStages"][0]["textureId"]
            mesh["parts"] = [part]
            result.append(mesh)
        return result

    def model(self, archive, identifier):
        value = self._load(archive)
        if not isinstance(identifier, str) or identifier not in self._specs[archive]:
            raise ValueError("Ce modèle n’existe pas dans l’archive sélectionnée.")
        key = (archive, identifier)
        if key in self._models:
            model = self._models.pop(key)
            self._models[key] = model
            return model
        spec = self._specs[archive][identifier]
        if "_body" in spec:
            model = decode_body(value["data"], value["sections"][spec["_body"]], value["sections"],
                                value["shaders"], value["references"])
        else:
            meshes = []
            if "_graphParts" in spec:
                for part in spec["_graphParts"]:
                    meshes.extend(self._static_parts(value, part["references"], part["matrix"]))
            else:
                meshes = self._static_parts(value, spec["_references"])
            if not meshes:
                raise ValueError("Cet assemblage ne contient pas de triangles décodés.")
            model = {"meshes": meshes, "pose": "static", "meshCount": len(meshes),
                     "triangleCount": sum(len(mesh["indices"]) // 3 for mesh in meshes),
                     "bounds": {"min": [min(min(mesh["positions"][a::3]) for mesh in meshes) for a in range(3)],
                                "max": [max(max(mesh["positions"][a::3]) for mesh in meshes) for a in range(3)]}}
        model.update(_public(spec), sourceHash=value["hash"])
        model["meshCount"] = len(model["meshes"])
        model["textures"] = _used_textures(model["meshes"], value["textures"])
        model["warning"] = "Aperçu des ressources originales. Les matières complexes, particules, scripts et animations du moteur ne sont pas simulés."
        if len(self._models) >= 24:
            self._models.popitem(last=False)
        self._models[key] = model
        return model
