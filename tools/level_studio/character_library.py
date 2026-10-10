"""Read original BODY/BMSH geometry in its bind pose, without simulating BANM.

Verified against the retail body renderer at XBE VA CC2C0/CC890/D3550:
BODY +8/+12 selects its BMSH distance alternative; +16/+20 references
one eight-byte primitive-array descriptor per alternative. BMSH groups
are forty bytes; their FVF 0x1021C vertices are sixty bytes. BODY contains
the corresponding GPU streams, one 28-byte descriptor per mesh group.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import math
from pathlib import Path
import struct

from renderer_parser import Section, read_sections, read_shader_tables, decode_pushbuffer, _texture_catalog


def _bounded(pointer, length, section):
    if length < 0 or not section.offset <= pointer <= section.offset + section.size - length:
        raise ValueError("Pointeur du modèle hors de sa ressource.")
    return pointer


def _relative(data, field):
    return field + struct.unpack_from("<i", data, field)[0]


def named_resources(data, sections):
    if len(data) < 64:
        raise ValueError("En-tête de bibliothèque tronqué.")
    count, table, pool, pool_size = struct.unpack_from("<4I", data, 44)
    if count > 20000 or table < 64 or table + count * 8 > pool or pool + pool_size > len(data):
        raise ValueError("Table des ressources nommées hors de l’archive.")
    result = {}
    for row in range(count):
        resource, offset = struct.unpack_from("<2I", data, table + row * 8)
        if resource >= len(sections) or offset >= pool_size:
            raise ValueError("Référence nommée hors de l’archive.")
        end = data.find(b"\0", pool + offset, pool + pool_size)
        if end < 0:
            raise ValueError("Nom de ressource non terminé.")
        name = data[pool + offset:end].decode("ascii")
        key = (sections[resource].tag, name)
        if key in result:
            raise ValueError("Nom de ressource ambigu.")
        result[key] = sections[resource]
    return result


def decode_body(data, body, sections, shader_tables, texture_refs, lod=0):
    """Decode a genuine body/bmsh pairing into a static source-pose template."""
    _bounded(body.offset, 32, body)
    if body.tag != "body" or isinstance(lod, bool) or not isinstance(lod, int):
        raise ValueError("Ressource de corps non valide.")
    bone_count, _, lod_count = struct.unpack_from("<3I", data, body.offset)
    primitive_lods = struct.unpack_from("<I", data, body.offset + 16)[0]
    if not 0 < bone_count <= 2048 or not 0 < lod_count <= 32 or primitive_lods != lod_count or not 0 <= lod < lod_count:
        raise ValueError("Alternatives de détail du corps non décodées.")
    mesh_table = _bounded(_relative(data, body.offset + 12), lod_count * 4, body)
    primitive_table = _bounded(_relative(data, body.offset + 20), lod_count * 8, body)
    resource = struct.unpack_from("<I", data, mesh_table + lod * 4)[0]
    if resource >= len(sections) or sections[resource].tag != "bmsh":
        raise ValueError("Le corps ne référence pas une ressource bmsh.")
    bmsh = sections[resource]
    _bounded(bmsh.offset, 8, bmsh)
    group_count = struct.unpack_from("<I", data, bmsh.offset)[0]
    if not 0 < group_count <= 2048:
        raise ValueError("Nombre de groupes du modèle non valide.")
    groups = _bounded(_relative(data, bmsh.offset + 4), group_count * 40, bmsh)
    primitive_lod = primitive_table + lod * 8
    primitive_count = struct.unpack_from("<I", data, primitive_lod)[0]
    if primitive_count != group_count:
        raise ValueError("Les primitives ne correspondent pas aux groupes bmsh.")
    primitives = _bounded(_relative(data, primitive_lod + 4), primitive_count * 28, body)
    meshes = []
    for group_index in range(group_count):
        group = groups + group_index * 40
        declaration, palette_count = struct.unpack_from("<2I", data, group)
        vertex_count = struct.unpack_from("<I", data, group + 24)[0]
        shader_resource, shader_index = struct.unpack_from("<2I", data, group + 32)
        if declaration != 0x1021C or not 0 < palette_count <= 256 or not 0 < vertex_count <= 65536:
            raise ValueError("Déclaration de sommets de corps non décodée.")
        palette = _bounded(_relative(data, group + 8), palette_count * 4, bmsh)
        if any(bone >= bone_count for (bone,) in struct.iter_unpack("<I", data[palette:palette + palette_count * 4])):
            raise ValueError("Palette de squelette hors du corps.")
        vertices = _bounded(_relative(data, group + 28), vertex_count * 60, bmsh)
        descriptor = primitives + group_index * 28
        command_size, allocated_size = struct.unpack_from("<2I", data, descriptor + 20)
        if command_size < 8 or command_size % 4 or allocated_size < command_size:
            raise ValueError("Taille du stream GPU du corps non valide.")
        stream = _bounded(_relative(data, descriptor), command_size, body)
        # Reuse the packet decoder with an in-memory header around the exact
        # source stream; no index or triangle is generated heuristically.
        packet = struct.pack("<2I", 3, 28) + bytes(24) + data[stream:stream + command_size]
        indices = decode_pushbuffer(packet, Section(0, "pbrc", 0, len(packet), 8), vertex_count)
        if not indices:
            raise ValueError("Aucun triangle dans le groupe du corps.")
        shader_rows = shader_tables.get(shader_resource, [])
        if not 0 <= shader_index < len(shader_rows) or not shader_rows[shader_index].get("valid"):
            raise ValueError("Matériau du corps non validé.")
        shader = shader_rows[shader_index]
        stage_map = {stage["stage"]: stage for stage in shader.get("stages", [])}
        stage_count = shader.get("stageCount", max(stage_map, default=-1) + 1)
        if not 0 <= stage_count <= 32:
            raise ValueError("Nombre de couches du corps non décodé.")
        # Keep stage numbers even when the main image is unavailable. A
        # supported secondary layer must never become the main texture.
        stages = [dict(stage_map.get(index, {"stage": index}),
                       textureId=texture_refs.get(stage_map.get(index, {}).get("resourceIndex")))
                  for index in range(stage_count)]
        positions, normals, uvs = [], [], []
        for index in range(vertex_count):
            vertex = vertices + index * 60
            xyz = struct.unpack_from("<3f", data, vertex)
            normal = struct.unpack_from("<3f", data, vertex + 28)
            uv = struct.unpack_from("<2f", data, vertex + 40)
            weights = struct.unpack_from("<4f", data, vertex + 12)
            if (not all(math.isfinite(v) and abs(v) < 50000 for v in xyz + uv)
                    or not all(math.isfinite(v) and abs(v) <= 1.01 for v in normal)
                    or not all(math.isfinite(v) and -0.001 <= v <= 1.001 for v in weights)
                    or abs(sum(weights) - 1) > 0.002):
                raise ValueError("Sommets du corps non validés.")
            positions.extend(xyz)
            normals.extend(normal)
            uvs.extend(uv)
        part = {"start": 0, "count": len(indices), "partIndex": group_index}
        if stages and stages[0].get("textureId"):
            part["textureId"] = stages[0]["textureId"]
        meshes.append({"id": f"body-{body.index}-mesh-{resource}-{group_index}",
                       "name": f"Partie {group_index + 1}", "positions": positions,
                       "normals": normals, "uvs": uvs, "indices": indices, "parts": [part],
                       "textureStages": stages, "material": shader.get("material", {}),
                       "sourceOffset": vertices, "resourceIndex": resource,
                       "primitiveOffset": stream, "editable": False, "pose": "bind"})
    bounds = {"min": [min(min(m["positions"][a::3]) for m in meshes) for a in range(3)],
              "max": [max(max(m["positions"][a::3]) for m in meshes) for a in range(3)]}
    return {"meshes": meshes, "pose": "bind", "boneCount": bone_count,
            "lod": lod, "lodCount": lod_count, "bounds": bounds,
            "meshCount": len(meshes), "triangleCount": sum(len(m["indices"]) // 3 for m in meshes),
            "sourceArchive": "characters.xbr", "sourceOffset": body.offset,
            "resourceIndex": body.index, "editable": False,
            "warning": "Pose de liaison originale : les animations du squelette, les effets et la logique du jeu ne sont pas exécutés."}


class CharacterLibrary:
    def __init__(self, source_dir, texture_dir, *, archive="characters"):
        if archive not in ("characters", "diskreplchars"):
            raise ValueError("Archive de personnages inconnue.")
        source = Path(source_dir)
        gamedata = source if source.name.lower() == "gamedata" else source / "gamedata"
        self.path = gamedata / (archive + ".xbr")
        self.archive = archive
        self._companion = None
        self.texture_dir = Path(texture_dir)
        self._stamp = None
        self._data = None
        self._models = OrderedDict()
        self._textures = None
        self._refs = None
        self._warnings = []
        from config_library import CritterLibrary
        self.critters = CritterLibrary(source)

    def _load(self):
        if not self.path.is_file():
            return False
        stat = self.path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self._data is not None and stamp == self._stamp:
            return True
        data = self.path.read_bytes()
        sections = read_sections(data)
        names = named_resources(data, sections)
        self._data, self._sections, self._names = data, sections, names
        self._shaders = read_shader_tables(data, sections)
        self._hash = hashlib.sha256(data).hexdigest()
        self._stamp = stamp
        self._models.clear()
        self._textures = self._refs = None
        self._warnings = []
        return True

    def _load_textures(self):
        if self._textures is not None:
            return
        catalog, refs = _texture_catalog(self._data, self._sections, self.archive, self.texture_dir, self._warnings)
        prefix = "chars-" if self.archive == "characters" else "cinema-"
        self._refs = {index: prefix + identifier for index, identifier in refs.items()}
        self._textures = []
        for entry in catalog:
            entry = copy.deepcopy(entry)
            entry["id"] = prefix + entry["id"]
            entry["name"] = "Personnages · " + entry["name"]
            entry["sourceArchive"] = self.path.name
            for key in ("frameIds", "faceIds"):
                if key in entry:
                    entry[key] = [prefix + identifier if identifier is not None else None
                                  for identifier in entry[key]]
            self._textures.append(entry)

    def catalog(self):
        if not self._load():
            return {"available": False, "models": [], "warning": "characters.xbr absent du dump."}
        return {"available": True, "sourceArchive": "characters.xbr", "sourceHash": self._hash,
                "pose": "bind", "models": [
                    {"name": name, "label": name.removeprefix("characters/"),
                     "resourceIndex": section.index, "sourceOffset": section.offset,
                     "bytes": section.size, "boneCount": struct.unpack_from("<I", self._data, section.offset)[0]}
                    for (tag, name), section in self._names.items() if tag == "body"],
                "warning": "Bibliothèque originale en pose de liaison, sans animation du squelette."}

    @staticmethod
    def _canonical_name(name):
        # XBE resource lookup folds ASCII A..Z before matching the archive table.
        return name.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))

    def _owner(self, name):
        if self._load() and ("body", name) in self._names:
            return self
        if self.archive == "characters":
            if self._companion is None:
                self._companion = CharacterLibrary(self.path.parent, self.texture_dir, archive="diskreplchars")
            if self._companion._load() and ("body", name) in self._companion._names:
                return self._companion
        return None

    def model(self, name):
        if not isinstance(name, str):
            raise ValueError("Ce corps n’existe pas dans la bibliothèque originale.")
        name = self._canonical_name(name)
        owner = self._owner(name)
        if owner is None:
            raise ValueError("Ce corps n’existe pas dans la bibliothèque originale.")
        if owner is not self:
            return owner.model(name)
        if name in self._models:
            model = self._models.pop(name)
            self._models[name] = model
            return model
        self._load_textures()
        model = decode_body(self._data, self._names[("body", name)], self._sections,
                            self._shaders, self._refs)
        model.update(name=name, sourceHash=self._hash, sourceArchive=self.path.name)
        required = {stage["textureId"] for mesh in model["meshes"] for stage in mesh["textureStages"] if stage.get("textureId")}
        for texture in self._textures:
            if texture["id"] in required:
                required.update(texture.get("frameIds", []))
        model["textures"] = [entry for entry in self._textures if entry["id"] in required]
        if len(self._models) >= 48:
            self._models.popitem(last=False)
        self._models[name] = model
        return model

    def enrich(self, scene):
        if not self._load():
            return scene
        models, textures, failures, missing, mappings = {}, {}, [], [], []
        candidates = [o for o in scene.get("objects", []) if o.get("kind") in ("collectible", "powerup", "pickup", "enemy", "npc")]
        names = sorted({o["name"] for o in candidates})
        for archetype in names:
            definition = self.critters.resolve(archetype)
            name = self._canonical_name("characters/" + (definition["body"] if definition else archetype))
            if self._owner(name) is None:
                missing.append({"name": archetype, "body": name, "configured": definition is not None})
                continue
            try:
                model = self.model(name)
                models[name] = {key: value for key, value in model.items() if key != "textures"}
                textures.update({entry["id"]: entry for entry in model["textures"]})
                for item in candidates:
                    if item["name"] != archetype:
                        continue
                    item["modelKey"] = name
                    item["modelScale"] = definition["scale"] if definition else 1
                    item["modelNote"] = "Modèle original en pose de liaison, relié par la configuration du jeu." if definition else "Modèle original correspondant au nom de ressource."
                    if definition:
                        item["modelBinding"] = definition
                    if name.startswith("characters/townspeople/"):
                        item["kind"] = "npc"
                mappings.append({"name": archetype, "body": name, "scale": definition["scale"] if definition else 1,
                                 "matchKind": definition.get("matchKind", "resource-name") if definition else "resource-name"})
            except (ValueError, struct.error) as exc:
                failures.append({"name": name, "reason": str(exc)})
        spawn_points = [item for item in scene.get("objects", []) if item.get("kind") == "spawn"]
        if spawn_points and ("body", "characters/garret4") in self._names:
            try:
                player = self.model("characters/garret4")
                models["azurik-player"] = {key: value for key, value in player.items() if key != "textures"}
                textures.update({entry["id"]: entry for entry in player["textures"]})
                for item in spawn_points:
                    item["modelKey"] = "azurik-player"
                    item["modelNote"] = "Référence Azurik en pose de liaison, au départ joueur."
            except (ValueError, struct.error) as exc:
                failures.append({"name": "characters/garret4", "reason": str(exc)})
        scene["entityModels"] = models
        existing = {entry["id"] for entry in scene["textures"]}
        scene["textures"].extend(entry for entry in textures.values() if entry["id"] not in existing)
        scene["stats"].update(characterModelCount=len(models),
                               characterInstanceCount=sum(o.get("modelKey", o["name"]) in models for o in scene["objects"]))
        scene["characterLibrary"] = {"available": True, "pose": "bind", "sourceArchive": "characters.xbr",
                                     "modelCount": sum(tag == "body" for tag, _ in self._names), "failures": failures,
                                     "mappings": mappings, "unresolved": missing,
                                     "configuredModelCount": sum(m["matchKind"] != "resource-name" for m in mappings)}
        scene["characterLibrary"]["sources"] = sorted({model.get("sourceArchive", "characters.xbr") for model in models.values()})
        for item in scene["objects"]:
            if "nodeIndex" in item and "placementMatrix" not in item:
                item["placementMatrix"] = scene["nodes"][item["nodeIndex"]]["worldMatrix"]
        return scene
