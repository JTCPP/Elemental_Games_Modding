"""Export original decoded Azurik graphics into one indexed, validated ZIP.

Run `py -3 export_assets.py --pilot` for a small real-data compatibility
sample, then `py -3 export_assets.py` for the complete pack. Source archives
and editor projects are read-only. Existing output paths are never replaced.
"""
from __future__ import annotations

import argparse
import copy
import csv
import gc
import hashlib
import json
from pathlib import Path
import shutil
import struct
import time
from urllib.parse import urlsplit
import zipfile

from asset_export import attach_static_normals, slug, write_gltf
from asset_library import AssetLibrary
from character_library import CharacterLibrary, decode_body, named_resources
from editor_backend import DEFAULT_SOURCE
from environment_scene import read_environment_header
from library_bindings import read_library_platform_bindings
from renderer_parser import decode_mesh, decode_pushbuffer
from static_scene import read_static_descriptors
from validate_asset_export import validate_export
from studio_paths import DATA_ROOT
from studio_version import VERSION

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = DATA_ROOT / "exports" / ("assets-" + VERSION)


def unique_output(base):
    base = Path(base).resolve()
    candidate, suffix = base, 1
    while candidate.exists() or candidate.with_suffix(".zip").exists():
        candidate = base.with_name(base.name + f"-{suffix:02d}")
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def browser_rows_for_index(rows):
    """Expose every physical PNG, including each cube face and sequence frame."""
    result = []
    for row in rows:
        category = row["categorie"]
        if category == "textures":
            images = row.get("images") or ([row["fichier"]] if row.get("fichier", "").endswith(".png") else [])
            for number, path in enumerate(images):
                if not str(path).endswith(".png"):
                    raise ValueError("Une entrée de galerie doit désigner une image PNG.")
                suffix = (f" — image {number:04d}" if row.get("type") == "animation"
                          else f" — face {number:02d}" if len(images) > 1 else "")
                result.append({"archive": row["archive"], "kind": "texture", "name": row["nom_source"] + suffix,
                               "id": row["identifiant"] + (f"-image-{number:04d}" if suffix else ""),
                               "path": path, "images": [path],
                               "width": row.get("largeur"), "height": row.get("hauteur")})
            continue
        kind = ("lod" if row.get("lod", 0) > 0 else "character" if category == "personnages"
                else "static" if category == "decors" else "model")
        result.append({"archive": row["archive"], "kind": kind, "name": row["nom_source"],
                       "id": row["identifiant"], "path": row["fichier"],
                       "triangles": row.get("triangles"), "vertices": row.get("vertices")})
    return result


class Export:
    def __init__(self, source, output, pilot=False):
        self.source, self.output, self.pilot = Path(source).resolve(), Path(output).resolve(), pilot
        self.cache = self.output / "_decode_cache"
        self.library = AssetLibrary(self.source, self.cache)
        self.characters = {archive: CharacterLibrary(self.source, self.cache, archive=archive)
                           for archive in ("characters", "diskreplchars")}
        self.rows, self.errors, self.archives, self.omitted = [], [], [], []
        self.stats = {"libraryModelsExpected": 0, "libraryModelsExported": 0, "staticDescriptorsExported": 0,
                      "nodeLodAlternativesExported": 0, "staticLodAlternativesExported": 0, "bodyLodAlternativesExported": 0,
                      "inlineResourceModelsExported": 0, "gltfFiles": 0, "primaryPngFiles": 0,
                      "cubeFacePngFiles": 0, "animationFramePngFiles": 0, "animations": 0, "triangles": 0}

    def relative(self, path):
        return Path(path).relative_to(self.output).as_posix()

    def row(self, archive, category, identifier, name, path, **extra):
        item = {"archive": archive + ".xbr", "categorie": category, "identifiant": identifier,
                "nom_source": name, "fichier": self.relative(path) if path else None}
        item.update(extra)
        self.rows.append(item)
        return item

    def textures(self, archive, catalog, selected=None):
        texture_paths, metadata = {}, {}
        by_id = {texture["id"]: texture for texture in catalog}
        if selected is not None:
            selected = set(selected)
            for texture in catalog:
                if texture["resourceIndex"] in selected and texture["kind"] == "animation":
                    selected.update(texture["frameResourceIndices"])
        for texture in catalog:
            resource, identifier = texture["resourceIndex"], texture["id"]
            if selected is not None and resource not in selected:
                continue
            if texture["kind"] == "animation":
                continue
            urls = texture.get("faceUrls", [texture["url"]])
            exported = []
            for face, url in enumerate(urls):
                filename = Path(urlsplit(url).path).name
                original = self.cache / archive / filename
                if texture["kind"] == "cube":
                    target = self.output / archive / "textures" / "cubiques" / f"surf-{resource:05d}" / f"face-{face:02d}.png"
                    self.stats["cubeFacePngFiles"] += 1
                else:
                    target = self.output / archive / "textures" / "images" / f"surf-{resource:05d}.png"
                    self.stats["primaryPngFiles"] += 1
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                exported.append(target)
            texture_paths[resource] = texture_paths[identifier] = exported[0]
            for face_id, face_path in zip(texture.get("faceIds", []), exported):
                texture_paths[face_id] = face_path
            metadata[resource] = metadata[identifier] = texture
            self.row(archive, "textures", f"surf-{resource:05d}", texture["name"], exported[0],
                     type=texture["kind"], images=[self.relative(path) for path in exported],
                     largeur=texture["width"], hauteur=texture["height"], faces=len(exported),
                     donnees_source=texture)
        for texture in catalog:
            if texture["kind"] != "animation" or (selected is not None and texture["resourceIndex"] not in selected):
                continue
            resource = texture["resourceIndex"]
            folder = self.output / archive / "textures" / "animations" / f"anim-{resource:05d}"
            folder.mkdir(parents=True, exist_ok=True)
            frames = []
            for frame_index, (frame_id, resource_id) in enumerate(zip(texture["frameIds"], texture["frameResourceIndices"])):
                original = texture_paths.get(frame_id) or texture_paths.get(resource_id)
                if original is None:
                    frames.append({"index": frame_index, "ressource": resource_id, "fichier": None})
                    self.errors.append({"archive": archive, "id": texture["id"], "error": f"Animation frame {frame_index} missing"})
                    continue
                target = folder / f"frame-{frame_index:04d}--surf-{resource_id:05d}.png"
                shutil.copyfile(original, target)
                self.stats["animationFramePngFiles"] += 1
                frames.append({"index": frame_index, "ressource": resource_id, "fichier": self.relative(target),
                               "original": self.relative(original)})
            document = folder / "sequence.json"
            document.write_text(json.dumps({"source": texture, "cadence_jeu_verifiee": False, "images": frames}, ensure_ascii=False, indent=2), encoding="utf-8")
            with (folder / "sequence.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["index", "ressource", "fichier", "original"])
                writer.writeheader(); writer.writerows(frames)
            first = next((texture_paths.get(frame_id) for frame_id in texture["frameIds"] if texture_paths.get(frame_id)), None)
            if first:
                texture_paths[resource] = texture_paths[texture["id"]] = first
                metadata[resource] = metadata[texture["id"]] = texture
            self.stats["animations"] += 1
            self.row(archive, "textures", f"anim-{resource:05d}", texture["name"], document, type="animation",
                     images=[frame["fichier"] for frame in frames if frame["fichier"]],
                     nombre_images=len(frames), donnees_source=texture)
        return texture_paths, metadata

    def static_model(self, value, references, name, identifier, matrix=None, metadata=None):
        meshes = self.library._static_parts(value, references, matrix)
        populated = []
        for reference in references:
            pool = value["pools"][reference["meshResource"]]
            indices = decode_pushbuffer(value["data"], value["sections"][reference["primitiveResource"]], len(pool["positions"]) // 3)
            if indices:
                populated.append((reference, sorted(set(indices))))
        if len(meshes) != len(populated):
            raise ValueError("Les références et les parties décodées ne correspondent pas.")
        meshes = [attach_static_normals(dict(mesh, resourceIndex=reference["meshResource"],
                                             primitiveResource=reference["primitiveResource"], sourceBinding=reference,
                                             sourceWorldMatrix=matrix), value["data"], value["sections"][reference["meshResource"]], used, matrix)
                  for mesh, (reference, used) in zip(meshes, populated)]
        return {"id": identifier, "name": name, "meshes": meshes, "sourceHash": value["hash"],
                "pose": "static", **(metadata or {})}

    def library_model(self, archive, spec, value):
        if spec.get("_body") is not None:
            if archive in self.characters and not spec["name"].startswith("Corps "):
                return self.characters[archive].model(spec["name"])
            return self.library.model(archive, spec["id"])
        if "_graphParts" in spec:
            meshes = []
            for part in spec["_graphParts"]:
                meshes.extend(self.static_model(value, part["references"], spec["name"], spec["id"], part["matrix"])["meshes"])
            return {"id": spec["id"], "name": spec["name"], "meshes": meshes, "sourceHash": value["hash"], "pose": "static"}
        return self.static_model(value, spec["_references"], spec["name"], spec["id"])

    def save(self, archive, category, identifier, name, model, path, textures, metadata, **extra):
        stats = write_gltf(path, model, textures, metadata)
        self.stats["gltfFiles"] += 1
        self.stats["triangles"] += stats["triangles"]
        self.row(archive, category, identifier, name, path, type="gltf", **stats, **extra)

    def run(self):
        inventory = self.library.inventory()
        self.stats["libraryModelsExpected"] = inventory["stats"]["modelCount"] if not self.pilot else 2
        self.omitted.extend(inventory["skipped"])
        for archive_row in sorted(inventory["archives"], key=lambda row: row["id"]):
            archive = archive_row["id"]
            if self.pilot and archive not in ("characters", "w1"):
                continue
            print(f"ARCHIVE {archive}: chargement", flush=True)
            catalog = self.library.catalog(archive)
            value = self.library._load(archive)
            source_path = self.library._archives[archive]["_path"]
            specs = list(self.library._specs[archive].values())
            table = read_static_descriptors(value["data"], value["sections"])
            if self.pilot:
                # The original player resource is named garret4 in retail,
                # although the visible game character is Azurik.
                specs = [spec for spec in specs if "_body" in spec and (spec["name"].rsplit("/", 1)[-1] in ("garret4", "sapphire"))]
                specs = specs[:2] if archive == "characters" else []
                table["descriptors"] = [desc for desc in table["descriptors"] if desc["index"] in (25, 32)] if archive == "w1" else []
            selected = None
            if self.pilot:
                selected = set()
                for spec in specs:
                    model = self.library_model(archive, spec, value)
                    selected.update(stage["resourceIndex"] for mesh in model["meshes"] for stage in mesh.get("textureStages", []) if "resourceIndex" in stage)
                for desc in table["descriptors"]:
                    model = self.static_model(value, desc["bindings"][:1], "pilote", "pilote")
                    selected.update(stage["resourceIndex"] for mesh in model["meshes"] for stage in mesh.get("textureStages", []) if "resourceIndex" in stage)
            textures, metadata = self.textures(archive, catalog["textures"], selected)
            self.archives.append({"archive": archive + ".xbr", "sha256": value["hash"], "sourceBytes": len(value["data"]),
                                  "catalogueModeles": len(specs), "warnings": catalog["warnings"]})
            all_referenced = set()
            for ordinal, spec in enumerate(sorted(specs, key=lambda spec: (spec["kind"], spec["name"], spec["id"]))):
                category = "personnages" if "_body" in spec else "modeles"
                filename = f"{slug(spec['name'].rsplit('/',1)[-1])}--{spec['id']}"
                try:
                    model = self.library_model(archive, spec, value)
                    model = dict(model, id=spec["id"], name=spec["name"], sourceArchive=archive + ".xbr", sourceHash=value["hash"],
                                 sourceSpecification={key: val for key, val in spec.items() if not key.startswith("_")})
                    self.save(archive, category, spec["id"], spec["name"], model,
                              self.output / archive / category / (filename + ".gltf"), textures, metadata,
                              pose="liaison" if "_body" in spec else "source", lod=0)
                    self.stats["libraryModelsExported"] += 1
                    if "_body" in spec and not self.pilot:
                        for lod in range(1, model["lodCount"]):
                            alternative = decode_body(value["data"], value["sections"][spec["_body"]], value["sections"], value["shaders"], value["references"], lod=lod)
                            alternative.update(name=spec["name"], sourceArchive=archive + ".xbr", sourceHash=value["hash"])
                            self.save(archive, category, spec["id"] + f"-lod-{lod:02d}", spec["name"], alternative,
                                      self.output / archive / category / "lod-originales" / filename / f"lod-{lod:02d}.gltf", textures, metadata,
                                      pose="liaison", lod=lod)
                            self.stats["bodyLodAlternativesExported"] += 1
                    if "_references" in spec:
                        graph = value["sections"][spec["_graph"]]
                        bindings = read_library_platform_bindings(value["data"], graph, spec["sourceOffset"], value["sections"])
                        selected_offsets = {ref["bindingOffset"] for ref in spec["_references"]}
                        all_referenced.update(ref["meshResource"] for ref in bindings)
                        if not self.pilot:
                            for alternative_index, reference in enumerate(bindings):
                                if reference["bindingOffset"] in selected_offsets:
                                    continue
                                alt_id = spec["id"] + f"-part-{reference['descriptorIndex']:04d}-binding-{reference['bindingOffset']:08x}"
                                alternative = self.static_model(value, [reference], spec["name"], alt_id,
                                                                metadata={"sourceArchive": archive + ".xbr", "LOD": reference})
                                self.save(archive, "modeles", alt_id, spec["name"], alternative,
                                          self.output / archive / "modeles" / "lod-originales" / filename / f"part-{reference['descriptorIndex']:04d}-alternative-{alternative_index:03d}.gltf", textures, metadata,
                                          sourceLiaison=reference)
                                self.stats["nodeLodAlternativesExported"] += 1
                except Exception as exc:
                    self.errors.append({"archive": archive, "id": spec["id"], "error": str(exc)})
                if ordinal % 100 == 0:
                    print(f"  bibliotheque {ordinal+1}/{len(specs)}; glTF total {self.stats['gltfFiles']}", flush=True)
            environment = read_environment_header(value["data"], value["sections"])
            cells = {cell["index"]: cell["name"] for cell in (environment or {}).get("cells", [])}
            for desc in table["descriptors"]:
                all_referenced.update(ref["meshResource"] for ref in desc["bindings"])
                label = " / ".join(cells.get(cell, str(cell)) for cell in desc["cells"]) or "Decor statique"
                identifier = f"levl-{table['section'].index}-part-{desc['index']:05d}"
                folder = self.output / archive / "decors" / f"{slug(label,48)}--{identifier}"
                alternatives = desc["bindings"] if not self.pilot else desc["bindings"][:1]
                for lod, reference in enumerate(alternatives):
                    try:
                        model = self.static_model(value, [reference], label + f" · Partie {desc['index']:04d}", identifier,
                                                  metadata={"sourceArchive": archive + ".xbr", "sourceDescriptor": desc,
                                                            "coordinateSpace": "world", "LOD": reference, "lod": lod})
                        self.save(archive, "decors", identifier + f"-lod-{lod:02d}", model["name"], model,
                                  folder / f"lod-{lod:02d}.gltf", textures, metadata, lod=lod,
                                  cellules=[cells.get(cell, str(cell)) for cell in desc["cells"]], sourceLiaison=reference)
                        self.stats["staticDescriptorsExported" if lod == 0 else "staticLodAlternativesExported"] += 1
                    except Exception as exc:
                        self.errors.append({"archive": archive, "id": identifier, "lod": lod, "error": str(exc)})
                if desc["index"] % 200 == 0:
                    print(f"  decors {desc['index']+1}/{table['count']}; glTF total {self.stats['gltfFiles']}", flush=True)
            if not self.pilot:
                for section in value["sections"]:
                    if section.tag != "rdms" or section.index in all_referenced:
                        continue
                    pool = decode_mesh(value["data"], section, value["shaders"], value["references"])
                    if not pool["indices"]:
                        self.omitted.append({"archive": archive + ".xbr", "resource": section.index,
                                             "reason": "Réserve de sommets sans liaison de primitives démontrée; aucun mesh inventé."})
                        continue
                    pool = attach_static_normals(pool, value["data"], section, list(range(len(pool["positions"]) // 3)))
                    identifier = f"rdms-{section.index:05d}"
                    model = {"id": identifier, "name": pool["name"], "meshes": [pool], "sourceHash": value["hash"],
                             "sourceArchive": archive + ".xbr", "placementResolved": False,
                             "primitiveProof": "inline source RDMS triangle arrays"}
                    self.save(archive, "modeles", identifier, pool["name"], model,
                              self.output / archive / "modeles" / "ressources-inline" / (identifier + ".gltf"), textures, metadata,
                              placement="ressource non instanciee")
                    self.stats["inlineResourceModelsExported"] += 1
            after = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if after != value["hash"]:
                raise ValueError(f"Source modifiée pendant l’export : {archive}")
            self.archives[-1]["sourcePreserved"] = True
            (self.output / "progress.json").write_text(json.dumps({"archive": archive, "stats": self.stats, "errors": self.errors}, ensure_ascii=False, indent=2), encoding="utf-8")
            self.library._loaded.clear(); self.library._models.clear()
            for owner in self.characters.values():
                owner._models.clear()
            cache_archive = (self.cache / archive).resolve()
            if cache_archive.is_relative_to(self.output) and cache_archive.is_dir():
                shutil.rmtree(cache_archive)
            gc.collect()
            print(f"TERMINE {archive}; glTF={self.stats['gltfFiles']}; erreurs={len(self.errors)}", flush=True)
        if self.cache.is_dir() and self.cache.resolve().is_relative_to(self.output):
            shutil.rmtree(self.cache)
        self.finish()

    def finish(self):
        self.rows.sort(key=lambda row: (row["archive"], row["categorie"], row["nom_source"], row["identifiant"]))
        index = {"version": 5, "pilote": self.pilot, "statistiques": self.stats, "archives": self.archives,
                 "ressources": self.rows, "erreurs": self.errors, "ressources_non_exportees": self.omitted}
        (self.output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        columns = ["archive", "categorie", "identifiant", "nom_source", "fichier", "type", "triangles", "vertices", "lod", "pose", "largeur", "hauteur", "faces", "nombre_images"]
        with (self.output / "index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader(); writer.writerows(self.rows)
        from asset_index import write_index
        browser_rows = browser_rows_for_index(self.rows)
        png_count = sum(self.stats[key] for key in ("primaryPngFiles", "cubeFacePngFiles", "animationFramePngFiles"))
        if sum(row["kind"] == "texture" for row in browser_rows) != png_count:
            raise ValueError("Le catalogue consultable ne couvre pas toutes les images PNG exportées.")
        write_index(self.output, browser_rows, self.stats)
        (self.output / "LISEZ-MOI.txt").write_text(README + "\nComptages réels :\n" + json.dumps(self.stats, ensure_ascii=False, indent=2) + "\nArchives ignorées/limites :\n" + json.dumps(self.omitted, ensure_ascii=False, indent=2), encoding="utf-8")
        files = []
        for path in sorted(self.output.rglob("*")):
            if path.is_file() and path.name not in ("manifest.json", "validation.json"):
                files.append({"path": self.relative(path), "bytes": path.stat().st_size,
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (self.output / "manifest.json").write_text(json.dumps({"files": files}, separators=(",", ":")), encoding="utf-8")
        print("VALIDATION : tous les fichiers et les attributs", flush=True)
        validation = validate_export(self.output)
        validation["sourcePreserved"] = all(archive.get("sourcePreserved") for archive in self.archives)
        validation["exportErrors"] = self.errors
        validation["galleryImageCount"] = png_count
        (self.output / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.errors:
            raise ValueError(f"Export incomplet : {len(self.errors)} erreurs. Résultats conservés dans {self.output}")
        zip_path = self.output.with_suffix(".zip")
        print("ZIP : assemblage", flush=True)
        with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as archive:
            for path in sorted(self.output.rglob("*")):
                if path.is_file():
                    archive.write(path, self.relative(path))
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("Contrôle CRC du ZIP échoué.")
        print(json.dumps({"output": str(self.output), "zip": str(zip_path), "zipBytes": zip_path.stat().st_size,
                          "uncompressedBytes": sum(path.stat().st_size for path in self.output.rglob("*") if path.is_file()),
                          "stats": self.stats, "validation": validation}, ensure_ascii=False), flush=True)


README = """AZURIK — RESSOURCES ORIGINALES DÉCODÉES, EXPORT V5

Ouvrir index.html pour rechercher les ressources ; index.csv est consultable
dans un tableur, index.json contient les métadonnées détaillées et les limites.
Extraire entièrement le ZIP avant d’ouvrir index.html et ses liens. Garder les
fichiers glTF, leurs BIN voisins et les dossiers de PNG ensemble : leurs liens
sont relatifs. Le personnage Azurik porte le nom source characters/garret4.

Classement : archive XBR originale / textures, modeles, personnages, decors.
Les identifiants stables restent dans les noms des fichiers et les index.
Tous les PNG sont les niveaux principaux réellement décodés. Les six faces
des textures cubiques sont séparées ; leur orientation Xbox n’est pas inventée.
Chaque séquence conserve l’ordre et le numéro de ses images en PNG, CSV et JSON.
Sa cadence de jeu n’a pas été démontrée. Les mips secondaires ne sont pas exportés.

Les modèles sont des glTF 2.0 + BIN avec PNG partagés, et non des copies GLB
contenant chacune les mêmes images. UV0, UV1, couleurs et alpha de sommets sont
conservés lorsqu’ils existent. Les couleurs/alpha sources restent dans
_AZURIK_SOURCE_COLOR ; COLOR_0 est présent seulement quand le shader vérifié
utilise la couleur, avec alpha neutre pour l’aperçu standard. Les normales float sources sont gardées dans
_AZURIK_SOURCE_NORMAL ; NORMAL est leur version unitaire compatible glTF.
Les normales compactes non interprétées restent dans _AZURIK_PACKED_NORMAL.
La scène glTF porte une rotation X=-90° pour convertir le monde Z-up en Y-up.
Les positions source, en mètres, ne reçoivent aucune conversion supplémentaire.

Les corps sont dans leur pose de liaison/source. Aucun squelette animé, BANM,
script, système de particules ou shader Xbox n’est exécuté dans glTF. Les
matériaux standard prévisualisent la texture principale et les couleurs ; les
combinaisons de textures, transformations UV, états de rendu et références
originales complètes restent en extras, avec les liens des textures secondaires.
Ces modèles ne constituent donc pas une reproduction pixel par pixel du jeu.
Les matériaux dont le combiner alpha n’est pas vérifié utilisent un aperçu
opaque ; cela empêche un masque spéculaire de rendre Azurik transparent.
Les textures PNG et les paramètres alpha source restent inchangés en extras.
Les terrains de technique 6 à opacité 1 restent opaques, comme leur combiner
vérifié : le canal alpha d’une texture ne découpe pas leur surface.

Les décors LEVL sont des géométries en coordonnées monde, indépendantes du
graphe NODE. lod-00 est la première alternative source ; les autres alternatives
conservent leurs seuils originaux. Les alternatives NODE et BODY sont rangées
dans lod-originales. Les réserves RDMS avec triangles inline explicites sont
rangées à part ; aucune instance ni primitive inconnue n’a été inventée.

Le dump et les projets de l’éditeur ne sont jamais modifiés. Les empreintes
SHA256 sources sont conservées dans index.json ; manifest.json couvre tous
les fichiers et validation.json rapporte les contrôles effectués. loc.xbr est
un fichier placeholder/incomplet et ne peut pas fournir de ressources XBR.
"""

INDEX_HTML = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Azurik — Bibliothèque exportée</title><style>body{font:16px system-ui;background:#111b26;color:#eaf1f7;max-width:1400px;margin:auto;padding:32px}a{color:#70d4ff}input,select,button{font:inherit;padding:10px;background:#213244;color:white;border:1px solid #425467;border-radius:6px}input{min-width:300px}table{width:100%;border-collapse:collapse;margin-top:20px}td,th{text-align:left;padding:10px;border-bottom:1px solid #304152}small{color:#aab9c8}nav{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}footer{padding:20px}</style>
<h1>Azurik — Ressources originales</h1><p>Textures et modèles classés par archive du jeu. Les matières Xbox sont documentées dans les modèles ; leurs shaders et animations ne sont pas exécutés.</p>
<p><a href="LISEZ-MOI.txt">Guide et limites</a> · <a href="index.csv">Index CSV</a> · <a href="index.json">Métadonnées JSON</a> · <a href="validation.json">Contrôles</a></p>
<nav><input id="search" placeholder="Rechercher un nom ou un identifiant"><select id="archive"><option value="">Toutes les archives</option></select><select id="category"><option value="">Toutes les catégories</option></select><select id="sort"><option value="name">Trier par nom</option><option value="archive">Trier par archive</option><option value="category">Trier par catégorie</option></select></nav>
<p id="count"></p><table><thead><tr><th>Nom source</th><th>Archive</th><th>Catégorie</th><th>Ressource</th><th>Fichier</th></tr></thead><tbody id="rows"></tbody></table><footer><button id="previous">Précédent</button> <span id="page"></span> <button id="next">Suivant</button></footer>
<script src="index-data.js"></script><script>const data=window.AZURIK_ASSETS,el=id=>document.getElementById(id);let page=0;for(const [id,key]of [['archive','archive'],['category','categorie']])for(const value of [...new Set(data.map(row=>row[key]))].sort()){const opt=document.createElement('option');opt.value=value;opt.textContent=value;el(id).append(opt)}function show(){const q=el('search').value.toLowerCase();let list=data.filter(row=>(!el('archive').value||row.archive===el('archive').value)&&(!el('category').value||row.categorie===el('category').value)&&(!q||(row.nom_source+' '+row.identifiant+' '+row.archive).toLowerCase().includes(q)));const sort=el('sort').value;list.sort((a,b)=>String(sort==='name'?a.nom_source:sort==='archive'?a.archive:a.categorie).localeCompare(String(sort==='name'?b.nom_source:sort==='archive'?b.archive:b.categorie)));page=Math.max(0,Math.min(page,Math.ceil(list.length/200)-1));el('rows').replaceChildren();for(const row of list.slice(page*200,(page+1)*200)){const tr=document.createElement('tr');for(const key of ['nom_source','archive','categorie','identifiant','fichier']){const td=document.createElement('td');if(key==='fichier'&&row.fichier){const a=document.createElement('a');a.href=row.fichier;a.textContent=row.type==='gltf'?'Modèle glTF':row.type==='animation'?'Séquence':'Image PNG';td.append(a)}else td.textContent=row[key]||'';tr.append(td)}el('rows').append(tr)}el('count').textContent=list.length.toLocaleString('fr')+' ressources trouvées';el('page').textContent=' Page '+(page+1)+' / '+Math.max(1,Math.ceil(list.length/200))+' ';el('previous').disabled=page===0;el('next').disabled=(page+1)*200>=list.length}for(const id of ['search','archive','category','sort'])el(id).addEventListener(id==='search'?'input':'change',()=>{page=0;show()});el('previous').onclick=()=>{page--;show()};el('next').onclick=()=>{page++;show()};show();</script></html>"""


def finalize_existing(output, source):
    """Resume only index, validation and ZIP creation after interrupted extraction."""
    output, source = Path(output).resolve(), Path(source).resolve()
    if output.with_suffix(".zip").exists():
        raise FileExistsError("Une archive ZIP existante ne sera pas remplacée.")
    index = json.loads((output / "index.json").read_text(encoding="utf-8"))
    if index.get("version") != 5 or not index.get("ressources") or (output / "_decode_cache").exists():
        raise ValueError("L’extraction doit être complète avant la reprise de sa phase finale.")
    for archive in index["archives"]:
        path = source / archive["archive"]
        if not path.is_file():
            path = source / "gamedata" / archive["archive"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != archive["sha256"]:
            raise ValueError(f"Empreinte source différente : {archive['archive']}")
        archive["sourcePreserved"] = True
    instance = Export.__new__(Export)
    instance.output, instance.source, instance.pilot = output, source, bool(index["pilote"])
    instance.rows, instance.stats = index["ressources"], index["statistiques"]
    instance.archives, instance.errors, instance.omitted = index["archives"], index["erreurs"], index["ressources_non_exportees"]
    instance.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--finalize-existing", type=Path, help="Reprendre seulement index, validation et ZIP après extraction interrompue.")
    args = parser.parse_args()
    if args.finalize_existing:
        finalize_existing(args.finalize_existing, args.source)
        return
    requested = args.output.with_name(args.output.name + "-pilote") if args.pilot else args.output
    output = unique_output(requested)
    start = time.monotonic()
    print("SORTIE " + str(output), flush=True)
    Export(args.source, output, args.pilot).run()
    print(f"Durée {time.monotonic()-start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
