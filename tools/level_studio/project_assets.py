"""Persistent imported assets, project transactions and native replacement plans."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from asset_editing import ASSET_WARNING, MODEL_WARNING, TEXTURE_WARNING, geometry, name, native_plan, png_image

ASSET_KEYS = ("edits", "previewEdits", "models", "modelOverrides", "textures", "textureOverrides", "assetEdits")


class ProjectAssets:
    def _asset_defaults(self, state):
        for key in ASSET_KEYS:
            state.setdefault(key, {})
            if not isinstance(state[key], dict):
                raise ValueError("Ressources du projet invalides.")
        return state

    def _put_asset(self, payload, extension):
        identifier = hashlib.sha256(payload).hexdigest() + extension
        directory = self.project_dir / "imports"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / identifier
        if not path.exists():
            path.write_bytes(payload)
        return identifier

    def _read_asset(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}\.(json|png)", identifier):
            raise ValueError("Référence de ressource importée invalide.")
        path = self.project_dir / "imports" / identifier
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Ressource du projet absente ou trop volumineuse.")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != identifier.split(".")[0]:
            raise ValueError("La ressource importée a changé depuis sa validation.")
        return payload

    def _asset_snapshot(self, level):
        state = self._asset_defaults(self._state(level))
        return {key: copy.deepcopy(state[key]) for key in ASSET_KEYS}

    def _asset_result(self, level, identifier=None, warning=None, native=False, changed=True):
        state = self._state(level)
        return {"changed": changed, "id": identifier, "scene": self.get_scene(level), "warning": warning,
                "gameExportable": native, "pendingCount": self.pending_count(), "previewCount": self.preview_count(),
                "levelPendingCount": self.pending_count(level), "levelPreviewCount": self.preview_count(level),
                **self._combined_history()}

    def _asset_commit(self, level, before, identifier, operation, warning=None, native=False):
        state = self._state(level)
        after = self._asset_snapshot(level)
        changed = before != after
        if changed:
            previous_redo = copy.deepcopy(state["redo"])
            previous_version = self._project["version"]
            self._project["version"] = max(2, self._project["version"])
            state["undo"].append({"operation": "assets", "action": operation, "id": identifier,
                                  "beforeState": before, "afterState": after})
            state["redo"].clear()
            self._invalidate_asset_scene(level, native=before["assetEdits"] != after["assetEdits"])
            try:
                self.get_scene(level)
                self.save()
            except Exception:
                for key, value in before.items():
                    state[key] = value
                state["undo"].pop()
                state["redo"] = previous_redo
                self._project["version"] = previous_version
                self._invalidate_asset_scene(level, native=before["assetEdits"] != after["assetEdits"])
                raise
        return self._asset_result(level, identifier, warning, native, changed)

    def _invalidate_asset_scene(self, level, native=True):
        if native:
            self._cache.pop(level, None)
            self._cache_stamps.pop(level, None)
        getattr(self, "_prepared_scenes", {}).pop(level, None)

    def _native_patches(self, data, state):
        cache = getattr(self, "_asset_plan_cache", {})
        self._asset_plan_cache = cache
        result, ranges = [], []
        source_hash = hashlib.sha256(data).hexdigest()
        for key, entry in state.get("assetEdits", {}).items():
            if not isinstance(entry, dict) or set(entry) - {"kind", "resourceIndex", "primitiveResource", "asset", "name", "targetId"}:
                raise ValueError("Remplacement de ressource invalide.")
            self._read_asset(entry.get("asset"))
            cache_key = (source_hash, json.dumps(entry, sort_keys=True))
            patches = cache.get(cache_key)
            if patches is None:
                patches = native_plan(data, entry, self._read_asset)
                if len(cache) >= 64:
                    cache.clear()
                cache[cache_key] = patches
            for offset, payload in patches:
                if offset < 0 or offset + len(payload) > len(data):
                    raise ValueError("Remplacement de ressource hors du fichier.")
                ranges.append((offset, offset + len(payload)))
                result.append((offset, payload, entry))
        ranges.sort()
        if any(start < previous_end for (_, previous_end), (start, _) in zip(ranges, ranges[1:])):
            raise ValueError("Remplacements de ressources superposés.")
        return result

    def _native_buffer(self, data, state):
        if not state.get("assetEdits"):
            return data
        buffer = bytearray(data)
        for offset, payload, _ in self._native_patches(data, state):
            buffer[offset:offset + len(payload)] = payload
        return bytes(buffer)

    def _texture_entry(self, level, identifier, record):
        from PIL import Image
        import io
        payload = self._read_asset(record["asset"])
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            width, height = image.size
            alpha = image.convert("RGBA").getextrema()[3][0] < 255
        filename = "custom-" + record["asset"]
        destination = self.texture_dir / level / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(payload)
        return {"id": identifier, "name": record["name"], "url": f"/textures/{level}/{filename}",
                "kind": "2d", "format": "PNG", "width": width, "height": height, "alpha": alpha,
                "imported": True, "gameExportable": False, "previewOnly": True}

    def _model_entry(self, identifier, record):
        from scene_graph import local_matrix, transform_point, transform_normals
        from editor_backend import _position, _rotation, _scale
        model = geometry(json.loads(self._read_asset(record["asset"])))
        matrix = local_matrix(_position(record.get("position", [0, 0, 0])),
                              _rotation(record.get("rotation", [0, 0, 0])), _scale(record.get("scale", [1, 1, 1])))
        positions = [v for i in range(0, len(model["positions"]), 3) for v in transform_point(matrix, model["positions"][i:i + 3])]
        centre = [(min(positions[a::3]) + max(positions[a::3])) / 2 for a in range(3)]
        result = {"id": identifier, "name": name(record["name"]), "positions": positions, "indices": model["indices"],
                  "uvs": model.get("uvs", []), "colors": [1, 1, 1] * (len(positions) // 3),
                  "position": centre, "originalPosition": centre[:], "origin": centre[:], "worldMatrix": matrix,
                  "editable": False, "imported": True, "previewOnly": True, "exportable": False,
                  "previewEditable": True, "coordinateSpace": "world", "worldPositionVerified": True,
                  "visible": True, "editorVisible": True, "authoredVisible": True,
                  "localRotation": [0, 0, 0], "localScale": [1, 1, 1],
                  "parts": [{"start": 0, "count": len(model["indices"]), "partIndex": 0}],
                  "materialValid": True, "material": {"technique": 6, "opacity": 1, "depthWrite": 1, "blendType": 0},
                  "textureStages": [], "declaredTextureStages": 0, "assetWarning": ASSET_WARNING}
        if model.get("textureId"):
            result["textureStages"] = [{"stage": 0, "textureId": model["textureId"]}]
            result["parts"][0]["textureId"] = model["textureId"]
            result["declaredTextureStages"] = 1
        if model.get("normals"):
            result["normals"], _ = transform_normals(matrix, model["normals"])
        return result

    def _asset_item(self, level, identifier):
        state = self._state(level)
        if identifier in state.get("models", {}):
            return self._model_entry(identifier, state["models"][identifier])
        return None

    def _apply_assets(self, level, scene):
        from scene_graph import identity
        state = self._asset_defaults(self._state(level))
        for identifier, record in state["models"].items():
            imported = self._model_entry(identifier, record)
            scene["meshes"].append(imported)
            scene["assets"].append({"id": identifier, "name": imported["name"], "imported": True,
                                    "vertices": len(imported["positions"]) // 3, "triangles": len(imported["indices"]) // 3,
                                    "coordinateSpace": "asset", "editable": False, "previewOnly": True})
        by_id = {m["id"]: m for m in scene["meshes"]}
        for identifier, record in state["modelOverrides"].items():
            if identifier not in by_id:
                raise ValueError("Cible de remplacement de modèle introuvable.")
            original = by_id[identifier]
            model = geometry(json.loads(self._read_asset(record["asset"])))
            centre = [(min(model["positions"][a::3]) + max(model["positions"][a::3])) / 2 for a in range(3)]
            target = original["position"][:]
            positions = [v + target[i % 3] - centre[i % 3] for i, v in enumerate(model["positions"])]
            original.update(positions=positions, indices=model["indices"], uvs=model.get("uvs", []),
                            colors=[1, 1, 1] * (len(positions) // 3), normals=model.get("normals", []),
                            sourceNormals=[], originalPosition=target[:], position=target, origin=target[:],
                            worldMatrix=identity(), modelReplaced=True, assetWarning=ASSET_WARNING,
                            editable=False, exportable=False, previewOnly=True,
                            parts=[{"start": 0, "count": len(model["indices"]), "partIndex": 0}])
            original.pop("editBinding", None)
            original.pop("packedNormals", None)
            original.pop("uv2", None)
            if model.get("textureId"):
                original["textureStages"] = [{"stage": 0, "textureId": model["textureId"]}]
            if original.get("textureStages"):
                texture = original["textureStages"][0].get("textureId")
                if texture:
                    original["parts"][0]["textureId"] = texture
        for identifier, record in state["textures"].items():
            scene["textures"].append(self._texture_entry(level, identifier, record))
        by_texture = {t["id"]: t for t in scene["textures"]}
        for identifier, record in state["textureOverrides"].items():
            if identifier not in by_texture:
                raise ValueError("Texture remplacée introuvable.")
            replacement = self._texture_entry(level, identifier, record)
            by_texture[identifier].update(replacement, replaced=True)
        for entry in state["assetEdits"].values():
            target = by_texture.get(entry.get("targetId")) if entry["kind"] == "texture" else None
            if target:
                target.update(replaced=True, gameExportable=True, previewOnly=False, replacementName=entry["name"])
            if entry["kind"] == "mesh":
                for item in scene["meshes"]:
                    if item.get("resourceIndex") == entry["resourceIndex"]:
                        item.update(modelReplaced=True, assetGameExportable=True, assetWarning=MODEL_WARNING)
        scene["capabilities"].update(modelImport=True, modelDuplicate=True, modelReplace=True,
                                     textureImport=True, textureReplace=True, levelReset=True,
                                     assetAllocationGameExport=False, nativeAssetReplacement=True)
        if any(state[k] for k in ("models", "modelOverrides", "textures", "textureOverrides")):
            scene["capabilities"]["warnings"].append(ASSET_WARNING)
        scene["stats"].update(meshCount=len(scene["meshes"]), triangleCount=sum(len(m["indices"]) // 3 for m in scene["meshes"]),
                              importedModelCount=len(state["models"]), importedTextureCount=len(state["textures"]),
                              nativeAssetEditCount=len(state["assetEdits"]))

    def _check_texture_ref(self, level, model):
        if model.get("textureId") and not any(t["id"] == model["textureId"] for t in self.get_scene(level)["textures"]):
            raise ValueError("La texture choisie n’existe pas dans ce niveau.")

    def import_model(self, level, asset_name, model, position=None):
        from editor_backend import _position
        with self._lock:
            self._load(level)
            model = geometry(model)
            self._check_texture_ref(level, model)
            asset_name = name(asset_name)
            placement = _position(position or [0, 0, 0])
            before = self._asset_snapshot(level)
            identifier = "import-" + uuid4().hex[:16]
            asset = self._put_asset(json.dumps(model, allow_nan=False, separators=(",", ":")).encode("utf-8"), ".json")
            self._state(level)["models"][identifier] = {"asset": asset, "name": asset_name, "position": placement}
            return self._asset_commit(level, before, identifier, "import-model", ASSET_WARNING)

    def duplicate(self, level, identifier, offset=None):
        from editor_backend import _position
        with self._lock:
            self._assert_unlocked(level, identifier)
            item = next((m for m in self.get_scene(level)["meshes"] if m["id"] == identifier), None)
            if item is None:
                raise ValueError("Sélectionnez un modèle à dupliquer.")
            delta = _position(offset if offset is not None else [2, 0, 0])
            centre = item["position"]
            model = {"positions": [v - centre[i % 3] for i, v in enumerate(item["positions"])], "indices": item["indices"][:], "uvs": item.get("uvs", [])}
            texture = (item.get("textureStages") or [{}])[0].get("textureId")
            if texture:
                model["textureId"] = texture
            if item.get("normals"):
                model["normals"] = item["normals"][:]
            return self.import_model(level, (item["name"][:150] + " · copie"), model, [centre[a] + delta[a] for a in range(3)])

    def import_texture(self, level, asset_name, image):
        with self._lock:
            self._load(level)
            asset_name = name(asset_name)
            _, png = png_image(image)
            before = self._asset_snapshot(level)
            identifier = "import-tex-" + uuid4().hex[:16]
            self._state(level)["textures"][identifier] = {"asset": self._put_asset(png, ".png"), "name": asset_name}
            return self._asset_commit(level, before, identifier, "import-texture", ASSET_WARNING)

    def replace_texture(self, level, identifier, asset_name, image):
        with self._lock:
            data, _ = self._load(level)
            current = next((t for t in self.get_scene(level)["textures"] if t["id"] == identifier), None)
            if current is None:
                raise ValueError("Texture à remplacer introuvable.")
            asset_name = name(asset_name)
            rgba, png = png_image(image)
            entry = {"asset": self._put_asset(png, ".png"), "name": asset_name}
            before = self._asset_snapshot(level)
            state = self._state(level)
            if identifier in state["textures"]:
                state["textures"][identifier] = entry
                return self._asset_commit(level, before, identifier, "replace-texture", ASSET_WARNING)
            native = {**entry, "kind": "texture", "resourceIndex": current.get("resourceIndex"), "targetId": identifier}
            warning, exportable = TEXTURE_WARNING, True
            try:
                native_plan(data, native, self._read_asset)
            except (ValueError, OSError) as exc:
                warning, exportable = str(exc) + " " + ASSET_WARNING, False
            key = f"texture-{current.get('resourceIndex')}"
            state["assetEdits"].pop(key, None)
            state["textureOverrides"].pop(identifier, None)
            if exportable:
                state["assetEdits"][key] = native
            else:
                state["textureOverrides"][identifier] = entry
            return self._asset_commit(level, before, identifier, "replace-texture", warning, exportable)

    def replace_model(self, level, identifier, asset_name, model):
        with self._lock:
            self._assert_unlocked(level, identifier)
            data, _ = self._load(level)
            current = next((m for m in self.get_scene(level)["meshes"] if m["id"] == identifier), None)
            if current is None:
                raise ValueError("Sélectionnez un modèle à remplacer.")
            asset_name, model = name(asset_name), geometry(model)
            self._check_texture_ref(level, model)
            entry = {"asset": self._put_asset(json.dumps(model, allow_nan=False, separators=(",", ":")).encode("utf-8"), ".json"), "name": asset_name}
            before = self._asset_snapshot(level)
            state = self._state(level)
            if identifier in state["models"]:
                state["models"][identifier].update(entry)
                return self._asset_commit(level, before, identifier, "replace-model", ASSET_WARNING)
            native = {**entry, "kind": "mesh", "resourceIndex": current.get("resourceIndex"),
                      "primitiveResource": current.get("primitiveResource"), "targetId": identifier}
            warning, exportable = MODEL_WARNING, True
            try:
                if model.get("coordinateSpace") not in ("asset", "world"):
                    raise ValueError("Un remplacement natif exige un choix explicite des coordonnées du modèle.")
                local_model = model
                if model["coordinateSpace"] == "world":
                    if current.get("modelReplaced"):
                        raise ValueError("Restaurez le modèle d’origine avant un remplacement natif en coordonnées du niveau.")
                    from scene_graph import identity, inverse_affine, transform_point, transform_normals
                    inverse = inverse_affine(current.get("worldMatrix") or identity())
                    local_model = {**model, "coordinateSpace": "asset", "positions": [
                        value for index in range(0, len(model["positions"]), 3)
                        for value in transform_point(inverse, model["positions"][index:index + 3])]}
                    if model.get("normals"):
                        local_model["normals"], verified = transform_normals(inverse, model["normals"])
                        if not verified:
                            raise ValueError("Les normales du modèle ne peuvent pas être converties.")
                    local_model = geometry(local_model)
                native["asset"] = self._put_asset(json.dumps(local_model, allow_nan=False, separators=(",", ":")).encode("utf-8"), ".json")
                stages = current.get("textureStages") or [{}]
                if model.get("textureId") and model["textureId"] != stages[0].get("textureId"):
                    raise ValueError("Le changement de matériau d’un modèle natif reste une prévisualisation du projet.")
                for other in self.get_scene(level)["meshes"]:
                    if other.get("resourceIndex") == current.get("resourceIndex"):
                        self._assert_unlocked(level, other["id"])
                native_plan(data, native, self._read_asset)
            except (ValueError, OSError) as exc:
                warning, exportable = str(exc) + " " + ASSET_WARNING, False
            key = f"mesh-{current.get('resourceIndex')}"
            # A preview belongs to the selected instance. It must not erase a
            # native replacement made through a different instance of the same
            # shared pool. Replacing the native edit's own target with a preview
            # deliberately restores that pool's original native bytes.
            if not exportable and state["assetEdits"].get(key, {}).get("targetId") == identifier:
                state["assetEdits"].pop(key, None)
            state["modelOverrides"].pop(identifier, None)
            if exportable:
                state["assetEdits"][key] = native
            else:
                state["modelOverrides"][identifier] = entry
            return self._asset_commit(level, before, identifier, "replace-model", warning, exportable)

    def delete_asset(self, level, identifier):
        with self._lock:
            self._assert_unlocked(level, identifier)
            state = self._state(level)
            if identifier not in state["models"]:
                raise ValueError("Seuls les modèles importés ou dupliqués peuvent être supprimés.")
            before = self._asset_snapshot(level)
            state["models"].pop(identifier)
            state["previewEdits"].pop(identifier, None)
            return self._asset_commit(level, before, identifier, "delete-model", ASSET_WARNING)

    def reset_level(self, level):
        with self._lock:
            self._load(level)
            before = self._asset_snapshot(level)
            state = self._state(level)
            for key in ASSET_KEYS:
                state[key] = {}
            return self._asset_commit(level, before, None, "reset-level", "Niveau restauré depuis le dump original. Les verrouillages sont conservés ; cette restauration peut être annulée.")

    def _asset_history(self, level, undo):
        state = self._state(level)
        source, target = (state["undo"], state["redo"]) if undo else (state["redo"], state["undo"])
        row = source[-1]
        snapshot = row.get("beforeState" if undo else "afterState")
        if not isinstance(snapshot, dict) or set(snapshot) != set(ASSET_KEYS) or any(not isinstance(snapshot[k], dict) for k in ASSET_KEYS):
            raise ValueError("Historique de ressources invalide.")
        before = self._asset_snapshot(level)
        previous_undo, previous_redo = copy.deepcopy(state["undo"]), copy.deepcopy(state["redo"])
        if row.get("action") != "reset-level":
            identifier = row.get("id")
            # Undoing addition/deletion can refer to an item absent in one state.
            if identifier and state["locks"].get(identifier):
                raise ValueError("La ressource est verrouillée. Déverrouillez-la pour annuler cette modification.")
            if identifier and row.get("action") == "replace-model":
                self._assert_unlocked(level, identifier)
                resources = {entry["resourceIndex"] for view in (before, snapshot) for entry in view["assetEdits"].values()
                             if entry.get("kind") == "mesh" and entry.get("targetId") == identifier}
                for item in self.get_scene(level)["meshes"]:
                    if item.get("resourceIndex") in resources:
                        self._assert_unlocked(level, item["id"])
        try:
            for key in ASSET_KEYS:
                state[key] = copy.deepcopy(snapshot[key])
            self._invalidate_asset_scene(level, native=before["assetEdits"] != snapshot["assetEdits"])
            self.get_scene(level)
            target.append(source.pop())
            self.save()
        except Exception:
            for key in ASSET_KEYS:
                state[key] = before[key]
            state["undo"], state["redo"] = previous_undo, previous_redo
            self._invalidate_asset_scene(level, native=before["assetEdits"] != snapshot["assetEdits"])
            raise
        return self._asset_result(level, row.get("id"))

    def _export_assets(self, folder, preview_levels):
        """Include portable immutable inputs and every project asset placement."""
        imports = set()
        for level, state in self._project["levels"].items():
            if not self._level_active(level):
                continue
            custom = {key: copy.deepcopy(state.get(key, {})) for key in ASSET_KEYS if key not in ("edits", "previewEdits")}
            if any(custom.values()):
                preview_levels.setdefault(level, {"sourceSha256": state["sourceHash"], "overrides": {}})["assets"] = custom
                for entries in custom.values():
                    for entry in entries.values():
                        imports.add(entry["asset"])
        if imports:
            directory = folder / "assets"
            directory.mkdir()
            for identifier in sorted(imports):
                (directory / identifier).write_bytes(self._read_asset(identifier))
