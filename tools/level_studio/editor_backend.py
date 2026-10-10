"""Read-only Azurik source loader and validated, reversible XBR edit projects.

Geometry and entities come from the decoded retail node graph. Transform
edits target node parameters, preserving pivots and evaluating parents first.
Unsupported records remain untouched and the ISO builder is never imported.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from studio_paths import ASSET_ROOT, DATA_ROOT
from project_assets import ProjectAssets
from project_levels import ProjectLevels, LEVEL_WARNING

STUDIO_ROOT = DATA_ROOT


def default_source():
    """Keep an existing local project usable without a machine-specific path."""
    if os.environ.get("AZURIK_SOURCE"):
        return Path(os.environ["AZURIK_SOURCE"]).expanduser()
    saved = STUDIO_ROOT / "projects" / "default" / "project.json"
    try:
        source = json.loads(saved.read_text("utf-8")).get("sourceDir")
        if isinstance(source, str) and source:
            return Path(source)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return STUDIO_ROOT / "source"


def default_toolkit():
    if os.environ.get("AZURIK_TOOLKIT"):
        return Path(os.environ["AZURIK_TOOLKIT"]).expanduser()
    for candidate in (ASSET_ROOT.parent / "randomizer", ASSET_ROOT.parent):
        if (candidate / "azurik_mod").is_dir():
            return candidate
    return ASSET_ROOT.parent / "randomizer"


DEFAULT_SOURCE = default_source()
DEFAULT_TOOLKIT = default_toolkit()
LEVEL_NAMES = {
    "town": "Perathia — Ville", "life": "Royaume de la Vie",
    "airship": "Vaisseau aérien", "training_room": "Salle d’entraînement",
    "selector": "Sélecteur de niveaux", "a1": "Air — A1", "a3": "Air — A3",
    "a5": "Air — A5", "a6": "Air — A6", "w1": "Eau — W1",
    "w2": "Eau — W2", "w3": "Eau — W3", "w4": "Eau — W4",
    "e2": "Terre — E2", "e5": "Terre — E5", "e6": "Terre — E6",
    "e7": "Terre — E7", "f1": "Feu — F1", "f2": "Feu — F2",
    "f3": "Feu — F3", "f4": "Feu — F4", "f6": "Feu — F6",
    "d1": "Mort — D1", "d2": "Mort — D2",
    "airship_docking": "Cinématique — Accostage du vaisseau",
    "airship_docking_water": "Cinématique — Accostage sur l’eau",
    "airship_trans": "Cinématique — Voyage du vaisseau",
}
MESH_WARNING = "Transformer ce décor modifie son nœud de placement, ses parties liées et ses descendants. Les collisions restent à leur position d’origine."
SCENE_WARNING = "Le décor est extrait des fichiers du jeu. Les animations, effets et objets dynamiques ne sont pas encore reproduits intégralement."
PREVIEW_WARNING = "Transformation conservée dans le projet de l’éditeur ; ce placement n’est pas encore exportable dans le jeu. Les collisions restent inchangées."


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scene_copy(value):
    """Copy scene containers while bulk-copying immutable numeric attributes.

    Deepcopy walks every Python float even though these atoms are immutable;
    a flat list copy preserves isolation and is substantially cheaper.
    """
    if isinstance(value, dict):
        return {key: _scene_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        if not value or not isinstance(value[0], (dict, list, tuple)):
            # Scene flat arrays contain numeric/string atoms only. Fall back
            # for heterogeneous metadata to retain ordinary deepcopy semantics.
            if all(not isinstance(item, (dict, list, tuple)) for item in value):
                return value[:]
        return [_scene_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scene_copy(item) for item in value)
    return value


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _position(value) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("La position doit contenir trois nombres X, Y, Z.")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise ValueError("Les coordonnées doivent être des nombres.")
    point = [float(v) for v in value]
    if not all(math.isfinite(v) and abs(v) < 50000 for v in point):
        raise ValueError("Les coordonnées doivent être finies et comprises dans les limites du jeu.")
    # Persist the actual single-precision values that will be written to XBR.
    return list(struct.unpack("<3f", struct.pack("<3f", *point)))


def _rotation(value):
    result = _position(value)
    if any(abs(v) > 720 for v in result):
        raise ValueError("La rotation locale doit être exprimée en radians finis (±720 maximum).")
    return result


def _world_target(value):
    # World coordinates are derived doubles. Only the converted local XBR
    # parameters are float32; rounding a world target breaks exact undo.
    _position(value)
    return [float(v) for v in value]


def _scale(value):
    result = _position(value)
    if any(not 0.0001 <= abs(v) <= 1000 for v in result):
        raise ValueError("L’échelle locale doit être finie et non nulle (valeur absolue de 0,0001 à 1000).")
    return result


_CHANNELS = {"position": ("localPosition", "editOffset", "originalLocalPosition", _position),
             "rotation": ("localRotation", "rotationOffset", "originalLocalRotation", _rotation),
             "scale": ("localScale", "scaleOffset", "originalLocalScale", _scale)}


def _same_linear(a, b):
    return all(abs(a[r * 4 + c] - b[r * 4 + c]) <= 1e-12 for r in range(3) for c in range(3))


def _transformed_centre(matrix, positions):
    from scene_graph import transform_point
    low, high = [math.inf] * 3, [-math.inf] * 3
    for index in range(0, len(positions), 3):
        point = transform_point(matrix, positions[index:index + 3])
        for axis in range(3):
            low[axis], high[axis] = min(low[axis], point[axis]), max(high[axis], point[axis])
    return [(low[a] + high[a]) / 2 for a in range(3)]


def _kind(name: str) -> str:
    value = name.lower()
    if any(k in value for k in ("trigger", "switch", "toggle", "timer", "camera")):
        return "trigger"
    if value in ("diamond", "emerald", "sapphire", "obsidian", "ruby"):
        return "collectible"
    if value.startswith(("power_", "frag_")):
        return "powerup"
    if any(k in value for k in ("elemental", "splinter", "shard", "overlord", "gargoyle", "critter", "sleeth", "fish", "boss")):
        return "enemy"
    return "object"


def _node_regions(data: bytes) -> list[tuple[int, int]]:
    """v4 TOC offsets are resource ENDs relative to the payload base."""
    if len(data) < 0x40 or data[:4] != b"xobx" or struct.unpack_from("<I", data, 4)[0] != 4:
        return []
    count, payload = struct.unpack_from("<II", data, 0x0C)
    if count > 65536 or 0x40 + count * 16 > payload or payload > len(data):
        return []
    regions, previous = [], 0
    for row in range(count):
        size, tag, flags, end = struct.unpack_from("<I4sII", data, 0x40 + row * 16)
        base = payload + previous
        if end == 0 and row == count - 1:
            # Retail files terminate with a levl record whose end is zero.
            if tag == b"node" and base + size <= len(data):
                regions.append((base, base + size))
            break
        if end < previous or base + size > len(data) or size > end - previous:
            break
        if tag == b"node" and size:
            regions.append((base, base + size))
        previous = end
    return regions


def scan_objects(data: bytes) -> list[dict]:
    """Find named transform records only inside decoded node resources.

    The two supported layouts have a position immediately after a 1.0
    marker, rotation XYZ, scale XYZ, a handle, then either a second 1.0
    marker or a padded critterGenerator type. Other layouts are omitted.
    """
    candidates = {}
    regions = _node_regions(data)
    marker = re.compile(re.escape(b"\x00\x00\x80\x3f") + rb"(?=[!-~])")
    direct = re.compile(rb"(?:power_(?:water|air|earth|fire|ammo|staff)|frag_(?:water|fire|earth|air|life))[!-~]*\x00")
    for base, stop in regions:
        candidates.update({m.end(): (base, stop) for m in marker.finditer(data, base, stop)})
        for match in direct.finditer(data, base, stop):
            start = match.start()
            if any(start + offset + 4 <= stop and data[start + offset:start + offset + 4] == b"\x00\x00\x80\x3f" for offset in range(20, 40, 4)):
                candidates[start] = (base, stop)
    objects, seen = [], set()
    for start in sorted(candidates):
        base, stop = candidates[start]
        end = data.find(b"\0", start, min(start + 81, stop))
        if end < 0:
            continue
        raw = data[start:end]
        if not re.fullmatch(rb"[A-Za-z0-9_:/;.-]{3,80}", raw):
            continue
        name = raw.decode("ascii")
        if name.startswith("movies/") or any(k in name for k in ("Locator", "Shape", "Snap")):
            continue
        if not (name.islower() or any(c in name for c in "_:/;") or data.count(raw) >= 2 or _kind(name) == "trigger"):
            continue
        for delta in (-96, -116):
            offset = start + delta
            if offset - 4 < base or offset + 44 > stop or data[offset - 4:offset] != b"\x00\x00\x80\x3f":
                continue
            x, y, z, rx, ry, rz, sx, sy, sz = struct.unpack_from("<9f", data, offset)
            if (not all(math.isfinite(v) and abs(v) < 50000 and (v == 0 or abs(v) >= 1e-5) for v in (x, y, z))
                    or not all(math.isfinite(v) and abs(v) <= 720 for v in (rx, ry, rz))
                    or not all(math.isfinite(v) and 0.0001 <= abs(v) <= 100 for v in (sx, sy, sz))):
                continue
            if delta == -96 and data[offset + 40:offset + 44] != b"\x00\x00\x80\x3f":
                continue
            if delta == -116 and not data[offset + 40:offset + 60].startswith(b"critterGenerator\0"):
                continue
            if offset not in seen:
                seen.add(offset)
                objects.append({"id": f"entity-{offset:08x}", "name": name,
                                "kind": _kind(name), "position": [x, y, z],
                                "originalPosition": [x, y, z], "coordOffset": offset,
                                "nameOffset": start})
            break
    return objects


class StudioBackend(ProjectLevels, ProjectAssets):
    """Thread-safe projects; all source bytes stay read-only throughout."""
    def __init__(self, source_dir=DEFAULT_SOURCE, project_dir=None, exports_dir=None, toolkit_dir=DEFAULT_TOOLKIT, texture_dir=None):
        self.source_dir = Path(source_dir).resolve()
        self.gamedata_dir = self.source_dir if self.source_dir.name.lower() == "gamedata" else self.source_dir / "gamedata"
        self.project_dir = Path(project_dir or STUDIO_ROOT / "projects" / "default").resolve()
        self.exports_dir = Path(exports_dir or STUDIO_ROOT / "exports").resolve()
        self.toolkit_dir = Path(toolkit_dir)
        self.texture_dir = Path(texture_dir or STUDIO_ROOT / "cache" / "textures").resolve()
        if not self.gamedata_dir.is_dir():
            raise FileNotFoundError(f"Dossier gamedata introuvable : {self.gamedata_dir}")
        if any(_inside(path, self.source_dir) for path in (self.project_dir, self.exports_dir, self.texture_dir)):
            raise ValueError("Le projet et les exports doivent être séparés du dump source.")
        self.project_path = self.project_dir / "project.json"
        self._lock, self._cache, self._cache_stamps = threading.RLock(), {}, {}
        self._project = {"version": 1, "sourceDir": str(self.source_dir), "levels": {}}
        if self.project_path.exists():
            self._project = json.loads(self.project_path.read_text("utf-8"))
            if (type(self._project.get("version")) is not int or self._project["version"] not in (1, 2, 3)
                    or Path(self._project.get("sourceDir", "")).resolve() != self.source_dir):
                raise ValueError("Ce projet appartient à un autre dump source.")
            if not isinstance(self._project.get("levels"), dict):
                raise ValueError("Projet invalide.")
            self._initialize_level_management()
            for level in self._project["levels"]:
                self._source_path(level)
                state = self._project["levels"][level]
                if (not isinstance(state, dict) or not isinstance(state.get("edits"), dict)
                        or not isinstance(state.get("undo"), list) or not isinstance(state.get("redo"), list)
                        or not isinstance(state.get("sourceHash"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", state["sourceHash"])):
                    raise ValueError("État de niveau invalide dans le projet.")
                if (not isinstance(state.get("locks", {}), dict)
                        or any(not isinstance(key, str) or type(value) is not bool for key, value in state.get("locks", {}).items())
                        or not isinstance(state.get("previewEdits", {}), dict)):
                    raise ValueError("Verrouillages ou transformations de scène invalides.")
                self._asset_defaults(state)
        self._initialize_level_management()
        self._seed_history_order()

    def _source_path(self, level_id: str) -> Path:
        if not isinstance(level_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", level_id):
            raise ValueError("Identifiant de niveau invalide.")
        managed = self._managed_source_path(level_id)
        if managed is not None:
            return managed
        path = (self.gamedata_dir / f"{level_id}.xbr").resolve()
        if not _inside(path, self.gamedata_dir) or not path.is_file():
            raise FileNotFoundError(level_id)
        return path

    def catalog(self) -> list[dict]:
        result = []
        for path in self.gamedata_dir.glob("*.xbr"):
            level = path.stem
            known = level in LEVEL_NAMES or level.startswith(("diskreplace_", "airship_")) or level == "wirlpoolfixed"
            if not known and not self._has_level_resource(path):
                continue
            family = {"a": "air", "w": "water", "e": "earth", "f": "fire", "d": "death"}.get(level[0], "perathia") if re.fullmatch(r"[awefd]\d+", level) else "cinematic" if level.startswith(("diskreplace_", "airship_")) or level == "wirlpoolfixed" else "perathia"
            result.append({"id": level, "name": LEVEL_NAMES.get(level, level.replace("_", " ")),
                           "family": family, "path": str(path), "sizeBytes": path.stat().st_size,
                           "pendingCount": self.pending_count(level)})
        for level, record in self._level_manager()['created'].items():
            path = self._source_path(level)
            result.append({'id': level, 'name': record['name'], 'family': record['family'],
                           'path': str(path), 'sizeBytes': path.stat().st_size, 'custom': True,
                           'nativeKey': 'levels/custom/' + level, 'template': record['template'],
                           'pendingCount': self.pending_count(level)})
        for row in result:
            row['templateOrigin'] = self._template_origin(row['id'])
            row['technical'] = row['id'] in {'training_room', 'selector'}
            row['canClone'] = bool(row.get('custom')) or (row['family'] != 'cinematic' and self._has_level_resource(Path(row['path'])))
        return sorted((row for row in result if row['id'] not in self._level_manager()['deleted']),
                      key=lambda row: (row["family"], row["id"]))

    @staticmethod
    def _has_level_resource(path):
        # Inspect the small TOC, never load multi-megabyte textures for catalog.
        with path.open("rb") as stream:
            header = stream.read(0x40)
            if len(header) < 0x40 or header[:4] != b"xobx" or struct.unpack_from("<I", header, 4)[0] != 4:
                return False
            count, payload = struct.unpack_from("<II", header, 0x0C)
            if count > 65536 or 0x40 + count * 16 > payload:
                return False
            toc = stream.read(count * 16)
        return len(toc) == count * 16 and any(toc[i + 4:i + 8] == b"levl" for i in range(0, len(toc), 16))

    get_catalog = catalog

    def _state(self, level):
        state = self._project["levels"].setdefault(level, {"sourceHash": None, "edits": {}, "undo": [], "redo": []})
        # Loading older projects is an in-memory upgrade; no save is implied.
        state.setdefault("locks", {})
        state.setdefault("previewEdits", {})
        self._asset_defaults(state)
        return state

    def _parse_scene(self, data, level):
        from renderer_parser import parse_level
        return parse_level(data, level, self.texture_dir)

    def _load(self, level):
        path = self._source_path(level)
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if level in self._cache:
            if self._cache_stamps[level] == stamp:
                # Keep at most two levels resident, in least-recent-use order.
                cached = self._cache.pop(level)
                self._cache[level] = cached
                return cached
            self._cache.pop(level)
            self._cache_stamps.pop(level)
        data = path.read_bytes()
        digest, state = _hash(data), self._state(level)
        if state["sourceHash"] and state["sourceHash"] != digest:
            raise ValueError(f"Le fichier source {level}.xbr a changé ; les modifications ne peuvent pas être appliquées.")
        state["sourceHash"] = digest
        refs = [{"kind": "texture", "name": m.group(0)[:-1].decode("ascii"), "offset": m.start()}
                for m in re.finditer(rb"(?i)(?:[A-Za-z0-9_./-]+/)?[A-Za-z0-9_.-]+\.(?:dds|tga|bmp|png)\x00", data)]
        refs.extend({"kind": "level", "name": m.group(0)[:-1].decode("ascii"), "offset": m.start()}
                    for m in re.finditer(rb"levels/[A-Za-z0-9_/-]+\x00", data))
        parsed = self._parse_scene(self._native_buffer(data, state), level)
        scene = {"id": level, "name": self._level_name(level, LEVEL_NAMES.get(level, level)), "coordinateSystem": "Z-up",
                 "sourceHash": digest, "meshes": parsed.get("meshes", []), "objects": parsed.get("objects", []),
                 "nodes": parsed.get("nodes", []), "assets": parsed.get("assets", []), "sceneGraphResolved": bool(parsed.get("nodes")),
                 "collisions": parsed.get("collisions", {"positions": [], "indices": []}), "references": refs,
                 "textures": parsed.get("textures", []),
                 "environment": parsed.get("environment", {"sky": {"available": False, "meshIds": [], "variants": []}}),
                 "lighting": parsed.get("lighting", {}),
                 "capabilities": {"entityTranslation": any(o.get("editable") for o in parsed.get("objects", [])),
                                  "meshTranslation": any(m.get("editable") for m in parsed.get("meshes", [])),
                                  "textures": bool(parsed.get("textures")),
                                  "rotation": any(m.get("rotationEditable") for m in parsed.get("meshes", []) + parsed.get("objects", [])),
                                  "scale": any(m.get("scaleEditable") for m in parsed.get("meshes", []) + parsed.get("objects", [])),
                                  "meshRotation": any(m.get("rotationEditable") for m in parsed.get("meshes", [])),
                                  "meshScale": any(m.get("scaleEditable") for m in parsed.get("meshes", [])),
                                  "entityRotation": any(m.get("rotationEditable") for m in parsed.get("objects", [])),
                                  "entityScale": any(m.get("scaleEditable") for m in parsed.get("objects", [])),
                                  "warnings": list(dict.fromkeys([SCENE_WARNING, MESH_WARNING] + parsed.get("warnings", [])))},
                 "stats": {"sourceBytes": len(data)}}
        scene["stats"].update(meshCount=len(scene["meshes"]), triangleCount=sum(len(m["indices"]) // 3 for m in scene["meshes"]),
                              objectCount=len(scene["objects"]), collisionFaceCount=len(scene["collisions"]["indices"]) // 6,
                              textureReferenceCount=sum(r["kind"] == "texture" for r in refs))
        scene["stats"].update(parsed.get("stats", {}))
        if len(self._cache) >= 2:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest)
            self._cache_stamps.pop(oldest)
        self._cache[level] = (data, scene)
        self._cache_stamps[level] = stamp
        return data, scene

    def _validated_edit(self, level, item_id, edit):
        """Derive every writable channel from decoded records, never project offsets."""
        data, scene = self._load(level)
        item = self._item(level, item_id)
        binding = item["editBinding"]
        if not isinstance(edit, dict) or type(edit.get("offset")) is not int or edit["offset"] != binding["editOffset"]:
            raise ValueError("Projet incompatible : offset de modification non validé.")
        schema = edit.get("transformVersion", 1)
        if schema not in (1, 2) or type(schema) is not int:
            raise ValueError("Format de transformation inconnu.")
        if "originalBytes" in edit and not isinstance(edit["originalBytes"], dict):
            raise ValueError("Projet incompatible : octets originaux non valides.")
        if schema == 1 and any(k in edit for k in ("localRotation", "localScale", "rotationOffset", "scaleOffset")):
            raise ValueError("Projet V1 incompatible : canaux de transformation non validés.")
        channels = {}
        for channel, (value_key, offset_key, original_key, validate) in _CHANNELS.items():
            if value_key not in edit:
                if channel == "position":
                    raise ValueError("Projet incompatible : position locale non validée.")
                if offset_key in edit or channel in edit.get("originalBytes", {}):
                    raise ValueError(f"Projet incompatible : canal {channel} incomplet.")
                continue
            offset = binding.get(offset_key)
            if type(offset) is not int or not 0 <= offset <= len(data) - 12 or original_key not in binding:
                raise ValueError(f"Canal {channel} sans offset sérialisé validé.")
            if channel != "position" and (type(edit.get(offset_key)) is not int or edit[offset_key] != offset):
                raise ValueError(f"Projet incompatible : offset {channel} non validé.")
            expected = struct.pack("<3f", *binding[original_key]).hex()
            decoded_original = binding.get("originalBytes", {}).get(channel, expected)
            if decoded_original != expected or data[offset:offset + 12].hex() != expected:
                raise ValueError("Les octets originaux des paramètres ne correspondent pas à la source.")
            if schema == 2 and (not isinstance(edit.get("originalBytes"), dict) or edit["originalBytes"].get(channel) != expected):
                raise ValueError(f"Projet incompatible : octets originaux {channel} non validés.")
            channels[channel] = {"value": validate(edit[value_key]), "offset": offset, "original": expected}
        return item, channels

    @staticmethod
    def _local_matrix(node, values):
        from scene_graph import local_matrix
        if "rotation" in node and "scale" in node:
            return local_matrix(values["position"], values["rotation"], values["scale"],
                                node.get("rotatePivot", [0, 0, 0]), node.get("rotatePivotTranslation", [0, 0, 0]),
                                node.get("scalePivot", [0, 0, 0]), node.get("scalePivotTranslation", [0, 0, 0]),
                                rotation_order=node.get("rotationOrder", 0))
        # Translation-only V1/test records preserve their existing linear matrix.
        result = node["localMatrix"][:]
        for axis in range(3):
            result[axis * 4 + 3] += values["position"][axis] - node["position"][axis]
        return result

    def _world_state(self, level):
        from scene_graph import multiply, identity, effective_parent_matrix
        _, scene = self._load(level)
        nodes = scene["nodes"]
        local = {node["index"]: node["localMatrix"][:] for node in nodes}
        local_values = {node["index"]: {channel: node[channel][:] for channel in _CHANNELS if channel in node}
                        for node in nodes if "position" in node}
        modified = set()
        for item_id, edit in self._state(level)["edits"].items():
            item, channels = self._validated_edit(level, item_id, edit)
            binding = item["editBinding"]
            index = binding["transformNode"]
            if index in modified:
                raise ValueError("Projet incompatible : plusieurs modifications du même nœud.")
            modified.add(index)
            for channel, record in channels.items():
                local_values[index][channel] = record["value"]
            local[index] = self._local_matrix(nodes[index], local_values[index])
        matrices, visiting = {}, set()
        def world(index):
            if index in matrices:
                return matrices[index]
            if index in visiting:
                raise ValueError("Cycle dans les transformations.")
            visiting.add(index)
            parent = nodes[index]["parent"]
            parent_world = world(parent) if 0 <= parent < len(nodes) else identity()
            node = nodes[index]
            effective = effective_parent_matrix(node, parent_world, local_values[index]["position"]) if node.get("transformVerified", True) and node.get("inheritMode", 0) else parent_world
            matrices[index] = multiply(effective, local[index])
            visiting.remove(index)
            return matrices[index]
        for index in range(len(nodes)):
            world(index)
        return scene, matrices, local_values

    @staticmethod
    def _world_position(item, scene, matrices):
        from scene_graph import inverse_affine, multiply, transform_point
        index = item["nodeIndex"]
        old, new = scene["nodes"][index]["worldMatrix"], matrices[index]
        if old == new:
            return item["originalPosition"][:]
        # Preserve exact centres for translations; rotation/scale require new bounds.
        if _same_linear(old, new):
            return [item["originalPosition"][axis] + new[axis * 4 + 3] - old[axis * 4 + 3] for axis in range(3)]
        if not item.get("positions"):
            return transform_point(new, [0, 0, 0])
        if item.get("_localPositions") is not None:
            return _transformed_centre(new, item["_localPositions"])
        delta = multiply(new, inverse_affine(old))
        if item.get("positions"):
            return _transformed_centre(delta, item["positions"])
        return transform_point(delta, item["originalPosition"])

    def get_scene(self, level_id: str) -> dict:
        with self._lock:
            if not self._level_active(level_id):
                raise ValueError('Ce niveau est supprimé du mod. Restaurez-le depuis le menu Niveaux.')
            from scene_graph import identity, inverse_affine, multiply, transform_point, transform_normals, effective_parent_matrix
            self._source_path(level_id)
            self._load(level_id)  # Check source stamps even when the public scene is reusable.
            state = self._state(level_id)
            from project_assets import ASSET_KEYS
            signature = _hash(json.dumps({key: state[key] for key in ASSET_KEYS} | {"locks": state["locks"],
                                          **self._combined_history(),
                                          "sourceHash": state["sourceHash"]}, sort_keys=True, allow_nan=False).encode("utf-8"))
            prepared = getattr(self, "_prepared_scenes", {})
            self._prepared_scenes = prepared
            if level_id in prepared and prepared[level_id][0] == signature:
                cached = _scene_copy(prepared[level_id][1])
                cached["pendingCount"], cached["previewCount"] = self.pending_count(), self.preview_count()
                return cached
            original, matrices, locals_ = self._world_state(level_id)
            scene = _scene_copy(original)
            edits = self._state(level_id)["edits"]
            for node in scene["nodes"]:
                index, parent = node["index"], node["parent"]
                node["worldMatrix"] = matrices[index][:]
                parent_world = matrices[parent][:] if 0 <= parent < len(scene["nodes"]) else identity()
                node["rawParentWorldMatrix"] = parent_world
                node["parentWorldMatrix"] = effective_parent_matrix(node, parent_world, locals_[index]["position"]) if node.get("transformVerified", True) and node.get("inheritMode", 0) else parent_world
                node["worldPosition"] = transform_point(matrices[index], [0, 0, 0])
                if index in locals_:
                    node.update(copy.deepcopy(locals_[index]))
                    node["localMatrix"] = self._local_matrix(original["nodes"][index], locals_[index])
            for item in scene["objects"] + scene["meshes"]:
                binding = item.get("editBinding")
                if binding:
                    values = locals_[binding["transformNode"]]
                    for channel, (value_key, _, original_key, _) in _CHANNELS.items():
                        if channel in values and original_key in binding:
                            item[value_key] = values[channel][:]
                            item[original_key] = binding[original_key][:]
                    transform_node = scene["nodes"][binding["transformNode"]]
                    parent = transform_node["parent"]
                    parent_world = matrices[parent] if 0 <= parent < len(scene["nodes"]) else identity()
                    effective = transform_node["parentWorldMatrix"]
                    binding["parentWorldMatrix"] = effective[:]
                    binding["parentWorldInverse"] = inverse_affine(effective)
                    binding["translationParentWorldMatrix"] = parent_world[:]
                    binding["translationParentWorldInverse"] = inverse_affine(parent_world)
                if "nodeIndex" not in item:
                    continue
                old, new = original["nodes"][item["nodeIndex"]]["worldMatrix"], matrices[item["nodeIndex"]]
                item["worldMatrix"] = new[:]
                if not item["id"].startswith("mesh-"):
                    item["placementMatrix"] = new[:]
                if old == new:
                    continue
                if item["id"].startswith("mesh-"):
                    if item.get("sourceNormals"):
                        item["normals"], verified = transform_normals(new, item["sourceNormals"])
                        item["normalSpace"] = "world" if verified else "local-fallback"
                        item["normalTransformVerified"] = verified
                        item["normalTransform"] = "inverse-transpose-preview" if verified else "singular-source-local-fallback"
                    if item.get("_localPositions") is not None:
                        item["origin"] = transform_point(new, item["_localOrigin"])
                        item["positions"] = [v for i in range(0, len(item["_localPositions"]), 3)
                                             for v in transform_point(new, item["_localPositions"][i:i + 3])]
                    else:
                        delta = multiply(new, inverse_affine(old))
                        item["origin"] = transform_point(delta, item["origin"])
                        item["positions"] = [v for i in range(0, len(item["positions"]), 3)
                                             for v in transform_point(delta, item["positions"][i:i + 3])]
                    if _same_linear(old, new):
                        item["position"] = self._world_position(item, original, matrices)
                    else:
                        item["position"] = [(min(item["positions"][a::3]) + max(item["positions"][a::3])) / 2 for a in range(3)]
                else:
                    item["position"] = self._world_position(item, original, matrices)
            for item in scene["meshes"]:
                item.pop("_localPositions", None)
                item.pop("_localOrigin", None)
            self._apply_assets(level_id, scene)
            self._apply_previews(level_id, scene)
            explicit = self._state(level_id)["locks"]
            for item in scene["meshes"] + scene["objects"]:
                item["exportable"] = bool(item.get("editable") and item.get("editBinding"))
                item["previewOnly"] = not item["exportable"]
                item["previewEditable"] = item["previewOnly"] and self._preview_available(item)
                item["lockedExplicitly"] = bool(explicit.get(item["id"]))
                item["locked"] = self._effective_locked(level_id, item, original)
                if item["previewOnly"]:
                    if not item.get("previewChanged"):
                        item["localRotation"] = [0, 0, 0]
                        item["localScale"] = [1, 1, 1]
                    item["originalLocalRotation"] = [0, 0, 0]
                    item["originalLocalScale"] = [1, 1, 1]
                    item["rotationEditable"] = item["previewEditable"]
                    item["scaleEditable"] = item["previewEditable"]
            # Light parents and mesh bounds can both change in the project.
            # Re-select from the source cells after their world transforms.
            from renderer_parser import read_sections
            from retail_lighting import apply_source_lighting
            data, _ = self._load(level_id)
            try:
                apply_source_lighting(data, read_sections(data), scene)
            except (ValueError, struct.error) as exc:
                scene["capabilities"]["warnings"].append(f"Éclairage source non résolu : {exc}")
            scene["stats"]["pendingCount"] = self.pending_count(level_id)
            scene["stats"]["previewCount"] = self.preview_count(level_id)
            scene["stats"]["lockedCount"] = sum(item["locked"] for item in scene["meshes"] + scene["objects"])
            scene["pendingCount"] = self.pending_count()
            scene["previewCount"] = self.preview_count()
            scene["capabilities"]["previewTransforms"] = True
            scene["history"] = self._combined_history()
            if len(prepared) >= 2 and level_id not in prepared:
                prepared.pop(next(iter(prepared)))
            prepared[level_id] = (signature, _scene_copy(scene))
            return scene

    def _find_item(self, level, item_id):
        if not isinstance(item_id, str):
            raise ValueError("Identifiant d’objet invalide.")
        _, scene = self._load(level)
        item = next((row for row in scene["objects"] + scene["meshes"] if row["id"] == item_id), None)
        if item is None:
            item = self._asset_item(level, item_id)
        if item is None:
            raise ValueError("Objet introuvable dans ce niveau.")
        if item_id in self._state(level)["modelOverrides"]:
            item = copy.deepcopy(item)
            item["editable"] = False
            item.pop("editBinding", None)
        return item

    @staticmethod
    def _preview_available(item):
        point = item.get("position")
        return (isinstance(point, (list, tuple)) and len(point) == 3
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in point))

    @staticmethod
    def _descends(scene, index, ancestor):
        visited = set()
        while type(index) is int and 0 <= index < len(scene["nodes"]) and index not in visited:
            if index == ancestor:
                return True
            visited.add(index)
            index = scene["nodes"][index]["parent"]
        return False

    def _effective_locked(self, level, item, scene):
        locks = self._state(level)["locks"]
        if not locks:
            return False
        if locks.get(item["id"]):
            return True
        for other in scene["meshes"] + scene["objects"]:
            if not locks.get(other["id"]):
                continue
            binding = other.get("editBinding")
            if other.get("editable") and binding and self._descends(scene, item.get("nodeIndex"), binding["transformNode"]):
                return True
        return False

    def _assert_unlocked(self, level, item_id):
        item = self._find_item(level, item_id)
        _, scene = self._load(level)
        if self._effective_locked(level, item, scene):
            raise ValueError("Cet objet est verrouillé. Déverrouillez-le avant de le déplacer.")
        if item.get("editable") and item.get("editBinding"):
            ancestor = item["editBinding"]["transformNode"]
            for other in scene["meshes"] + scene["objects"]:
                if self._state(level)["locks"].get(other["id"]) and self._descends(scene, other.get("nodeIndex"), ancestor):
                    raise ValueError("Ce déplacement modifierait un objet lié verrouillé. Déverrouillez-le d’abord.")

    def set_lock(self, level, locked, item_id=None, all_items=False):
        with self._lock:
            if type(locked) is not bool or type(all_items) is not bool:
                raise ValueError("Le verrouillage doit être vrai ou faux.")
            _, scene = self._load(level)
            if all_items:
                displayed = self.get_scene(level)
                items = displayed["meshes"] + displayed["objects"]
            else:
                item = self._find_item(level, item_id)
                items = [item]
                # Material parts sharing one writable placement share its lock.
                if item.get("editable") and item.get("editBinding"):
                    node = item["editBinding"]["transformNode"]
                    items = [other for other in scene["meshes"] + scene["objects"]
                             if other["id"] == item_id or other.get("editBinding", {}).get("transformNode") == node]
            locks = self._state(level)["locks"]
            for item in items:
                if locked:
                    locks[item["id"]] = True
                else:
                    locks.pop(item["id"], None)
            self.save()
            return {"locked": locked, "count": len(items), "scene": self.get_scene(level)}

    def _validated_preview(self, level, item_id, record):
        item = self._find_item(level, item_id)
        if item.get("editable") and item.get("editBinding") or not self._preview_available(item):
            raise ValueError("Transformation de scène incompatible avec cet objet.")
        if (not isinstance(record, dict) or set(record) != {"translation", "rotation", "scale"}):
            raise ValueError("Transformation de scène invalide.")
        return {"translation": _position(record["translation"]), "rotation": _rotation(record["rotation"]),
                "scale": _scale(record["scale"])}

    def _apply_previews(self, level, scene):
        from scene_graph import local_matrix, translation, multiply, transform_point, transform_normals
        by_id = {item["id"]: item for item in scene["meshes"] + scene["objects"]}
        for item_id, record in self._state(level)["previewEdits"].items():
            record = self._validated_preview(level, item_id, record)
            item = by_id[item_id]
            centre = item["position"][:]
            target = [centre[a] + record["translation"][a] for a in range(3)]
            # Order 0 in the retail helper is Rx then Ry then Rz, i.e. Euler ZYX.
            delta = multiply(local_matrix(target, record["rotation"], record["scale"]),
                             translation([-value for value in centre]))
            if item.get("positions"):
                item["positions"] = [value for index in range(0, len(item["positions"]), 3)
                                     for value in transform_point(delta, item["positions"][index:index + 3])]
            if item.get("origin"):
                item["origin"] = transform_point(delta, item["origin"])
            if item.get("placementMatrix"):
                item["placementMatrix"] = multiply(delta, item["placementMatrix"])
            if item.get("worldMatrix"):
                item["worldMatrix"] = multiply(delta, item["worldMatrix"])
                if item.get("sourceNormals"):
                    item["normals"], verified = transform_normals(item["worldMatrix"], item["sourceNormals"])
                    item["normalTransformVerified"] = verified
                    item["normalSpace"] = "world" if verified else "local-fallback"
                elif item.get("normals"):
                    # Imported and replacement geometry carries already-world
                    # normals. Transform these by the preview delta rather than
                    # skipping them merely because a world matrix is present.
                    item["normals"], verified = transform_normals(delta, item["normals"])
                    item["normalTransformVerified"] = verified
            elif item.get("normals"):
                item["normals"], _ = transform_normals(delta, item["normals"])
            item.update(position=target, localRotation=record["rotation"], localScale=record["scale"],
                        previewChanged=True, previewWarning=PREVIEW_WARNING)

    def _preview_transform(self, level, item_id, position=None, rotation=None, scale=None):
        item = self._find_item(level, item_id)
        if not self._preview_available(item):
            raise ValueError("La position de cet objet n’est pas disponible.")
        state = self._state(level)
        current_scene = self.get_scene(level)
        current = next(row for row in current_scene["meshes"] + current_scene["objects"]
                       if row["id"] == item_id)
        before = copy.deepcopy(state["previewEdits"])
        record = copy.deepcopy(before.get(item_id, {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}))
        if position is not None:
            target = _world_target(position)
            record["translation"] = _position([record["translation"][a] + target[a] - current["position"][a] for a in range(3)])
        if rotation is not None:
            record["rotation"] = _rotation(rotation)
        if scale is not None:
            record["scale"] = _scale(scale)
        record = self._validated_preview(level, item_id, record)
        if record == {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}:
            state["previewEdits"].pop(item_id, None)
        else:
            state["previewEdits"][item_id] = record
        changed = before != state["previewEdits"]
        if changed:
            after = [current["position"][a] + record["translation"][a] - before.get(item_id, {}).get("translation", [0, 0, 0])[a]
                     for a in range(3)]
            state["undo"].append({"id": item_id, "operation": "preview", "before": current["position"], "after": after,
                                  "beforeEdits": copy.deepcopy(state["edits"]), "afterEdits": copy.deepcopy(state["edits"]),
                                  "beforePreviewEdits": before, "afterPreviewEdits": copy.deepcopy(state["previewEdits"])})
            state["redo"].clear()
            self.save()
        scene = self.get_scene(level)
        actual = next(row for row in scene["meshes"] + scene["objects"] if row["id"] == item_id)
        return {"id": item_id, "changed": changed, "position": actual["position"],
                "localRotation": actual["localRotation"], "localScale": actual["localScale"],
                "pendingCount": self.pending_count(), "previewCount": self.preview_count(),
                "levelPendingCount": len(state["edits"]), "levelPreviewCount": len(state["previewEdits"]),
                **self._combined_history(), "warning": PREVIEW_WARNING, "scene": scene}

    def _item(self, level, item_id):
        _, scene = self._load(level)
        item = next((row for row in scene["objects"] + scene["meshes"] if row["id"] == item_id), None)
        if item is None:
            raise ValueError("Cet objet n’a pas de position sérialisée validée.")
        if not item.get("editable", False) or not item.get("editBinding"):
            raise ValueError("Le nœud de placement n’est pas validé pour l’édition.")
        return item

    def _store_transform(self, level, item, values):
        """One complete TRS edit per bound node, shared by all its material parts."""
        state, binding = self._state(level), item["editBinding"]
        record = {"transformVersion": 2, "offset": binding["editOffset"], "originalBytes": {},
                  "kind": "mesh" if item["id"].startswith("mesh-") else "entity"}
        changed = False
        for channel, (value_key, offset_key, original_key, validate) in _CHANNELS.items():
            if original_key not in binding:
                continue
            value = validate(values.get(channel, binding[original_key]))
            original = struct.pack("<3f", *binding[original_key]).hex()
            record[value_key] = value
            record["originalBytes"][channel] = original
            if channel != "position":
                record[offset_key] = binding[offset_key]
            changed = changed or struct.pack("<3f", *value).hex() != original
        for key in list(state["edits"]):
            if state["edits"][key]["offset"] == binding["editOffset"]:
                state["edits"].pop(key)
        if changed:
            self._validated_edit(level, item["id"], record)
            state["edits"][item["id"]] = record

    def _set_transform(self, level, item_id, position=None, rotation=None, scale=None):
        from scene_graph import inverse_affine, transform_vector, identity
        item = self._item(level, item_id)
        scene, matrices, locals_ = self._world_state(level)
        binding, index = item["editBinding"], item["editBinding"]["transformNode"]
        values = copy.deepcopy(locals_[index])
        for channel, requested in (("rotation", rotation), ("scale", scale)):
            if requested is not None:
                value_key, offset_key, original_key, validate = _CHANNELS[channel]
                if original_key not in binding or offset_key not in binding or channel not in values:
                    raise ValueError(f"Le canal {channel} de ce nœud n’est pas validé pour l’édition.")
                values[channel] = validate(requested)
        self._store_transform(level, item, values)
        if position is not None:
            position = _world_target(position)
            # New R/S and prior ancestor edits must be evaluated before the
            # requested world-space centre is converted into local translation.
            scene, matrices, _ = self._world_state(level)
            parent = scene["nodes"][index]["parent"]
            parent_world = matrices[parent] if 0 <= parent < len(scene["nodes"]) else identity()
            current = self._world_position(item, scene, matrices)
            delta = transform_vector(inverse_affine(parent_world), [position[a] - current[a] for a in range(3)])
            values["position"] = _position([values["position"][a] + delta[a] for a in range(3)])
            # Cancel double-precision inverse-matrix residue at exact source
            # coordinates, especially zero components during V1 history upgrade.
            values["position"] = [original if abs(value - original) <= 1e-10 else value
                                  for value, original in zip(values["position"], binding["originalLocalPosition"])]
            self._store_transform(level, item, values)

    def _set(self, level, item_id, position):
        self._set_transform(level, item_id, position=position)

    def transform(self, level_id, item_id, rotation=None, scale=None, position=None):
        """Set absolute local Euler radians/scale and an optional world centre."""
        with self._lock:
            self._source_path(level_id)
            self._assert_unlocked(level_id, item_id)
            found = self._find_item(level_id, item_id)
            if not found.get("editable") or not found.get("editBinding"):
                return self._preview_transform(level_id, item_id, position=position, rotation=rotation, scale=scale)
            item, state = self._item(level_id, item_id), self._state(level_id)
            original, matrices, locals_ = self._world_state(level_id)
            before_position = self._world_position(item, original, matrices)
            before_values = copy.deepcopy(locals_[item["editBinding"]["transformNode"]])
            before_edits = copy.deepcopy(state["edits"])
            try:
                self._set_transform(level_id, item_id, position=position, rotation=rotation, scale=scale)
                original, matrices, locals_ = self._world_state(level_id)
            except Exception:
                state["edits"] = before_edits
                raise
            actual = self._world_position(item, original, matrices)
            after_values = copy.deepcopy(locals_[item["editBinding"]["transformNode"]])
            changed = before_values != after_values
            if not changed:
                state["edits"] = before_edits
            if changed:
                state["undo"].append({"id": item_id, "operation": "transform", "before": before_position,
                                      "after": actual, "beforeTransform": before_values, "afterTransform": after_values,
                                      "beforeEdits": before_edits, "afterEdits": copy.deepcopy(state["edits"])})
                state["redo"].clear()
                self.save()
            return {"id": item_id, "changed": changed, "position": actual,
                    "localRotation": after_values.get("rotation"), "localScale": after_values.get("scale"),
                    "pendingCount": self.pending_count(), "levelPendingCount": len(state["edits"]),
                    **self._combined_history(),
                    "warning": MESH_WARNING if item_id.startswith("mesh-") else None,
                    "scene": self.get_scene(level_id)}

    def move(self, level_id, item_id, position):
        with self._lock:
            self._source_path(level_id)
            self._assert_unlocked(level_id, item_id)
            found = self._find_item(level_id, item_id)
            if not found.get("editable") or not found.get("editBinding"):
                return self._preview_transform(level_id, item_id, position=position)
            item, state = self._item(level_id, item_id), self._state(level_id)
            after = _world_target(position)
            original, matrices, _ = self._world_state(level_id)
            before = self._world_position(item, original, matrices)
            if before != after:
                before_edits = copy.deepcopy(state["edits"])
                try:
                    self._set(level_id, item_id, after)
                except Exception:
                    state["edits"] = before_edits
                    raise
                state["undo"].append({"id": item_id, "before": before, "after": after,
                                      "beforeEdits": before_edits, "afterEdits": copy.deepcopy(state["edits"])})
                state["redo"].clear()
                self.save()
            original, matrices, _ = self._world_state(level_id)
            actual = self._world_position(item, original, matrices)
            related = {row["id"]: self._world_position(row, original, matrices)
                       for row in original["meshes"] + original["objects"] if "nodeIndex" in row}
            return {"id": item_id, "position": actual, "relatedPositions": related,
                    "pendingCount": self.pending_count(), "levelPendingCount": len(state["edits"]),
                    **self._combined_history(),
                    "warning": MESH_WARNING if item_id.startswith("mesh-") else None}

    def _history(self, level, undo):
        with self._lock:
            self._source_path(level)
            self._load(level)
            state = self._state(level)
            source, target = (state["undo"], state["redo"]) if undo else (state["redo"], state["undo"])
            if not source:
                return {"changed": False, "pendingCount": self.pending_count(), "levelPendingCount": len(state["edits"]), **self._combined_history()}
            row = source[-1]
            if isinstance(row, dict) and row.get("operation") == "assets":
                return self._asset_history(level, undo)
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or "before" not in row or "after" not in row:
                raise ValueError("Entrée d’historique invalide.")
            self._assert_unlocked(level, row["id"])
            point = row["before"] if undo else row["after"]
            _world_target(point)
            snapshot = row.get("beforeEdits" if undo else "afterEdits")
            if snapshot is not None and not isinstance(snapshot, dict):
                raise ValueError("Snapshot d’historique invalide.")
            previous = copy.deepcopy(state["edits"])
            previews = copy.deepcopy(state["previewEdits"])
            preview_snapshot = row.get("beforePreviewEdits" if undo else "afterPreviewEdits", previews)
            if not isinstance(preview_snapshot, dict):
                raise ValueError("Snapshot de transformations de scène invalide.")
            # A history entry is allowed to restore only objects which are
            # still unlocked, including older snapshots affecting other nodes.
            for item_id in set(previous) | set(snapshot if snapshot is not None else previous) | set(previews) | set(preview_snapshot):
                if (previous.get(item_id) != (snapshot if snapshot is not None else previous).get(item_id)
                        or previews.get(item_id) != preview_snapshot.get(item_id)):
                    self._assert_unlocked(level, item_id)
            if snapshot is None:
                # Older V1 projects recorded world before/after positions only.
                # Upgrade the visited history entry to complete node snapshots.
                try:
                    self._set(level, row["id"], point)
                    snapshot = copy.deepcopy(state["edits"])
                    row["beforeEdits" if undo else "afterEdits"] = snapshot
                    row["afterEdits" if undo else "beforeEdits"] = previous
                except Exception:
                    state["edits"] = previous
                    raise
            try:
                if not isinstance(snapshot, dict):
                    raise ValueError("Snapshot d’historique invalide.")
                state["edits"] = copy.deepcopy(snapshot)
                state["previewEdits"] = copy.deepcopy(preview_snapshot)
                self._world_state(level)
                for item_id, record in state["previewEdits"].items():
                    self._validated_preview(level, item_id, record)
            except Exception:
                state["edits"] = previous
                state["previewEdits"] = previews
                raise
            target.append(source.pop())
            self.save()
            return {"changed": True, "id": row["id"], "position": point, "pendingCount": self.pending_count(), "levelPendingCount": len(state["edits"]),
                    **self._combined_history()}

    def undo(self, level_id):
        return self._global_history(level_id, True)

    def redo(self, level_id):
        return self._global_history(level_id, False)

    def pending_count(self, level_id=None):
        if level_id is not None:
            state = self._project["levels"].get(level_id, {})
            return len(state.get("edits", {})) + len(state.get("assetEdits", {}))
        return (sum(len(row.get("edits", {})) + len(row.get("assetEdits", {}))
                    for level, row in self._project["levels"].items() if self._level_active(level))
                + self.level_management()['changeCount'])

    def preview_count(self, level_id=None):
        def count(state):
            return (len(state.get("previewEdits", {})) + sum(len(state.get(key, {})) for key in ("models", "textures", "modelOverrides", "textureOverrides")))
        if level_id is not None:
            return count(self._project["levels"].get(level_id, {}))
        return sum(count(row) for level, row in self._project["levels"].items() if self._level_active(level))

    def project_summary(self):
        with self._lock:
            return {"path": str(self.project_path), "sourceDir": str(self.source_dir),
                    "pendingCount": self.pending_count(), "previewCount": self.preview_count(),
                    "levelManagement": self.level_management(), **self._combined_history(),
                    "editedLevels": [level for level, row in self._project["levels"].items() if self.pending_count(level) or self.preview_count(level)],
                    "levels": {level: {"pendingCount": self.pending_count(level), "previewCount": self.preview_count(level),
                                       "lockedCount": sum(bool(value) for value in row.get("locks", {}).values()),
                                       "canUndo": bool(row.get("undo")), "canRedo": bool(row.get("redo"))} for level, row in self._project["levels"].items()}}

    def save(self):
        with self._lock:
            # Ordering may clear redo across several levels. Restore those
            # shared lists in place if writing fails, so callers can roll back
            # their own operation without losing another level's history.
            missing = object()
            serial = self._project.get('historySerial', missing)
            stacks = [state[key] for state in self._project['levels'].values() for key in ('undo', 'redo')]
            stacks += [self._level_manager()[key] for key in ('undo', 'redo')]
            checkpoint = [(stack, list(stack), [(row, row.get('_seq', missing))
                           for row in stack if isinstance(row, dict)]) for stack in stacks]
            try:
                self._record_history_order()
                self.project_dir.mkdir(parents=True, exist_ok=True)
                temporary = self.project_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self._project, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
                temporary.replace(self.project_path)
            except Exception:
                for stack, entries, sequences in checkpoint:
                    stack[:] = entries
                    for row, sequence in sequences:
                        if sequence is missing:
                            row.pop('_seq', None)
                        else:
                            row['_seq'] = sequence
                if serial is missing:
                    self._project.pop('historySerial', None)
                else:
                    self._project['historySerial'] = serial
                raise
            return {"path": str(self.project_path), "pendingCount": self.pending_count(), "previewCount": self.preview_count()}

    def export(self):
        """Validate every source, then write only fresh copies and a manifest."""
        with self._lock:
            prepared, edits, reports, warnings, preview_levels = [], [], [], [], {}
            for level, state in self._project["levels"].items():
                if not self._level_active(level):
                    continue
                if state.get("previewEdits"):
                    original, _ = self._load(level)
                    preview_levels[level] = {"sourceSha256": _hash(original), "overrides": {}}
                    for item_id, record in state["previewEdits"].items():
                        preview_levels[level]["overrides"][item_id] = self._validated_preview(level, item_id, record)
                    if PREVIEW_WARNING not in warnings:
                        warnings.append(PREVIEW_WARNING)
                if not state["edits"] and not state.get("assetEdits"):
                    continue
                source = self._source_path(level).read_bytes()
                if _hash(source) != state["sourceHash"]:
                    raise ValueError(f"Export annulé : le SHA-256 de {level}.xbr a changé.")
                original, _ = self._load(level)
                if source != original:
                    raise ValueError(f"Export annulé : {level}.xbr diffère de la source analysée.")
                self._world_state(level)  # Reject conflicting node edits before creating any output.
                buffer, allowed_ranges, allowed_offsets = bytearray(source), [], set()
                for offset, payload, entry in self._native_patches(source, state):
                    allowed_ranges.append((offset, offset + len(payload)))
                    allowed_offsets.add(offset)
                    buffer[offset:offset + len(payload)] = payload
                    edits.append({"op": "replace_bytes", "xbr_file": f"{level}.xbr", "offset": offset,
                                  "value": payload.hex(), "value_kind": "hex", "label": f"Studio asset: {entry['name']}",
                                  "original": source[offset:offset + len(payload)].hex()})
                if state.get("assetEdits"):
                    from asset_editing import MODEL_WARNING, TEXTURE_WARNING
                    warnings.extend(warning for warning in (MODEL_WARNING, TEXTURE_WARNING)
                                    if any(e["kind"] == ("mesh" if warning == MODEL_WARNING else "texture") for e in state["assetEdits"].values()) and warning not in warnings)
                for item_id, edit in state["edits"].items():
                    item, channels = self._validated_edit(level, item_id, edit)
                    mesh = item_id.startswith("mesh-")
                    for channel, validated in channels.items():
                        offset, payload = validated["offset"], struct.pack("<3f", *validated["value"])
                        if payload.hex() == validated["original"]:
                            continue
                        if source[offset:offset + 12].hex() != validated["original"]:
                            raise ValueError("Export annulé : octets originaux inattendus.")
                        if any(offset < end and start < offset + 12 for start, end in allowed_ranges):
                            raise ValueError("Export annulé : modifications superposées.")
                        allowed_ranges.append((offset, offset + 12))
                        allowed_offsets.add(offset)
                        buffer[offset:offset + 12] = payload
                        edits.append({"op": "replace_bytes", "xbr_file": f"{level}.xbr", "offset": offset,
                                      "value": payload.hex(), "value_kind": "hex", "label": f"Studio: {item['name']} · {channel}",
                                      "original": source[offset:offset + 12].hex()})
                    if mesh and MESH_WARNING not in warnings:
                        warnings.append(MESH_WARNING)
                if len(buffer) != len(source):
                    raise ValueError("Export annulé : modification en dehors des paramètres autorisés.")
                # Validate unchanged gaps using bulk comparisons, then count
                # changes only inside native/transform ranges. Pixel edits can
                # span megabytes; allocating a Python set per byte is avoided.
                merged = []
                for start, end in sorted(allowed_ranges):
                    if merged and start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                    else:
                        merged.append((start, end))
                cursor, changed_bytes = 0, 0
                for start, end in merged:
                    if source[cursor:start] != buffer[cursor:start]:
                        raise ValueError("Export annulé : modification en dehors des paramètres autorisés.")
                    changed_bytes += sum(a != b for a, b in zip(source[start:end], buffer[start:end]))
                    cursor = end
                if source[cursor:] != buffer[cursor:]:
                    raise ValueError("Export annulé : modification en dehors des paramètres autorisés.")
                prepared.append((level, bytes(buffer)))
                reports.append({"level": level, "sourceSha256": _hash(source), "exportSha256": _hash(buffer),
                                "sourceBytes": len(source), "exportBytes": len(buffer), "changedBytes": changed_bytes,
                                "edits": self.pending_count(level), "allowedOffsets": sorted(allowed_offsets)})
            native_files, iso_plan = self._prepare_level_export(dict(prepared))
            prepared = list(native_files.items())
            has_assets = any(any(state.get(key) for key in ("models", "modelOverrides", "textures", "textureOverrides", "assetEdits"))
                             for level, state in self._project["levels"].items() if self._level_active(level))
            if self.level_management()['changeCount']:
                warnings.append(LEVEL_WARNING)
            if not prepared and not preview_levels and not has_assets:
                raise ValueError("Aucune modification à exporter.")
            folder = self.exports_dir / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
            if _inside(folder, self.source_dir):
                raise ValueError("Le dump source ne peut pas être une destination d’export.")
            (folder / "gamedata").mkdir(parents=True, exist_ok=False)
            for level, data in prepared:
                destination = folder / "gamedata" / f"{level}.xbr"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            (folder / 'iso-plan.json').write_text(json.dumps(iso_plan, ensure_ascii=False, indent=2), 'utf-8')
            self._export_assets(folder, preview_levels)
            if self.preview_count() and has_assets:
                from asset_editing import ASSET_WARNING
                if ASSET_WARNING not in warnings:
                    warnings.append(ASSET_WARNING)
            mod = {"name": "Azurik Level Studio", "source_dump": str(self.source_dir), "xbr_edits": edits,
                   "source_hashes": {r["level"] + ".xbr": r["sourceSha256"] for r in reports}}
            (folder / "mod.json").write_text(json.dumps(mod, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
            if preview_levels:
                overrides = {"version": 2 if has_assets else 1, "format": "azurik-studio-scene-overrides", "gameExportable": False,
                             "warning": PREVIEW_WARNING, "levels": preview_levels}
                (folder / "scene-overrides.json").write_text(json.dumps(overrides, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
            report = {"createdUtc": datetime.now(timezone.utc).isoformat(), "sourceUnchanged": True,
                      "files": reports, "warnings": warnings, "pendingCount": self.pending_count(), "previewCount": self.preview_count(),
                      "levelManagement": self.level_management(), "gameFiles": iso_plan['gameFiles'],
                      "removedFiles": iso_plan['removedFiles'],
                      "instructions": "Utilisez Niveaux → Construire une ISO du mod avec ce dossier exporté et votre ISO source correspondante. iso-plan.json décrit les fichiers, le registre et les suppressions nécessaires; l’ISO d’origine est conservée."}
            if preview_levels:
                report["instructions"] += " Les transformations dans scene-overrides.json concernent seulement l’éditeur et ne sont pas appliquées au jeu."
            (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")
            return {"path": str(folder), "directory": str(folder), "modPath": str(folder / "mod.json"), "reportPath": str(folder / "report.json"),
                    "fileCount": len(prepared), "editCount": len(edits), **report}


EditorBackend = StudioBackend
