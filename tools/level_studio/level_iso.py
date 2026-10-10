"""Build a separate level-mod ISO from a validated, explicit export plan.

No executable is patched. Changed assets and rebuilt directory tables are appended;
all original bytes stay in place except the volume descriptor's root fields.
Directory records follow XboxDev/extract-xiso's XDVDFS writer:
https://github.com/XboxDev/extract-xiso/blob/master/extract-xiso.c
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct

from xbox_iso import (XboxISO, SECTOR, CHUNK, MAX_IMAGE_BYTES, MAX_DIRECTORY_BYTES,
                      MAX_DIRECTORY_TOTAL, MAX_ENTRIES, _name)

PLAN_FILENAME = "iso-plan.json"
PLAN_FORMAT = "azurik-level-iso"
MAX_PLAN_BYTES = 2 * 1024 ** 2
MAX_PLAN_FILES = 1024
SHA256 = re.compile(r"[0-9a-fA-F]{64}")
LEVEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
PREFETCH_PATH = "prefetch-lists.txt"


def _align(value, alignment=SECTOR):
    return (value + alignment - 1) // alignment * alignment


def _reject_reparse(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("L’export ne peut pas contenir de lien ou de point de jonction.")
    return info


def _export_file(root, relative):
    current = root
    _reject_reparse(current)
    for part in relative.split("/"):
        current /= part
        _reject_reparse(current)
    target = current.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("Un fichier de l’export est absent ou sort de son dossier.")
    return target


def _game_path(value):
    if not isinstance(value, str) or len(value) > 1024 or "\\" in value:
        raise ValueError("Chemin de fichier de jeu invalide.")
    parts = value.split("/")
    try:
        for part in parts:
            raw = part.encode("ascii")
            if len(raw) > 255:
                raise ValueError
            _name(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Nom de fichier XBR non sûr ou trop long.") from None
    lower = value.casefold()
    if not (len(parts) == 2 and parts[0].casefold() == "gamedata" and lower.endswith(".xbr")
            or lower == "gamedata/index/index.xbr"):
        raise ValueError("Seuls les niveaux gamedata/*.xbr et gamedata/index/index.xbr sont acceptés.")
    return value


def _sha(value, *, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError("SHA-256 absent ou invalide dans le plan ISO.")
    return value.lower()


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Clé JSON dupliquée dans le plan ISO.")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("Valeur JSON non finie dans le plan ISO.")


def _level_id(value):
    if not isinstance(value, str) or not LEVEL_ID.fullmatch(value):
        raise ValueError("Identifiant de niveau invalide dans les opérations ISO.")
    _name(value.encode("ascii"))
    if value.casefold() in {"always", "default"}:
        raise ValueError("Un identifiant de niveau est réservé au préchargement.")
    return value


def _level_operations(value):
    if (not isinstance(value, dict) or set(value) != {"created", "deleted"}
            or any(not isinstance(value[key], list) for key in value)
            or len(value["created"]) + len(value["deleted"]) > MAX_PLAN_FILES):
        raise ValueError("Opérations de niveaux absentes, invalides ou trop nombreuses.")
    result, seen = {"created": [], "deleted": []}, set()
    for action, other in (("created", "template"), ("deleted", "replacement")):
        for row in value[action]:
            if not isinstance(row, dict) or set(row) != {"id", other}:
                raise ValueError("Une opération de niveau est incomplète.")
            identifier, target = _level_id(row["id"]), _level_id(row[other])
            key = identifier.casefold()
            if key in seen or key == target.casefold():
                raise ValueError("Identifiant dupliqué ou redirection de niveau vers lui-même.")
            seen.add(key)
            result[action].append({"id": identifier, other: target})
    redirects = {row["id"].casefold(): row["replacement"].casefold() for row in result["deleted"]}
    for key in redirects:
        visited = set()
        while key in redirects:
            if key in visited:
                raise ValueError("Cycle dans les redirections de niveaux ISO.")
            visited.add(key)
            key = redirects[key]
    return result


def _load_plan(root):
    plan_path = _export_file(root, PLAN_FILENAME)
    if plan_path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("Le plan ISO dépasse la taille autorisée.")
    with plan_path.open("rb") as stream:
        data = stream.read(MAX_PLAN_BYTES + 1)
    if len(data) > MAX_PLAN_BYTES:
        raise ValueError("Le plan ISO dépasse la taille autorisée.")
    plan = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
    if (not isinstance(plan, dict) or plan.get("format") != PLAN_FORMAT
            or type(plan.get("version")) is not int or plan["version"] != 1):
        raise ValueError("Format du plan ISO non pris en charge.")
    if set(plan) - {"format", "version", "gameFiles", "removedFiles", "levelOperations"}:
        raise ValueError("Champ inconnu dans le plan ISO.")
    operations = _level_operations(plan.get("levelOperations", {"created": [], "deleted": []}))
    files, removed = plan.get("gameFiles"), plan.get("removedFiles", [])
    if (not isinstance(files, list) or not isinstance(removed, list)
            or not 0 < len(files) + len(removed) <= MAX_PLAN_FILES):
        raise ValueError("Le plan ISO est vide ou contient trop de fichiers.")
    seen, changes, deletions = set(), [], []
    for rows, target, deletion in ((files, changes, False), (removed, deletions, True)):
        for row in rows:
            if not isinstance(row, dict) or "sourceSha256" not in row:
                raise ValueError("Entrée de fichier incomplète dans le plan ISO.")
            path = _game_path(row.get("path"))
            key = path.casefold()
            if key in seen:
                raise ValueError("Fichier dupliqué ou conflit ajout/suppression dans le plan ISO.")
            seen.add(key)
            item = {"path": path, "key": key,
                    "sourceSha256": _sha(row["sourceSha256"], nullable=not deletion)}
            if not deletion:
                item["sha256"] = _sha(row.get("sha256"))
                item["file"] = _export_file(root, path)
                item["size"] = item["file"].stat().st_size
                if not 24 <= item["size"] <= min(MAX_IMAGE_BYTES, 0xFFFFFFFF):
                    raise ValueError("La taille d’un XBR exporté est invalide.")
            target.append(item)
    return changes, deletions, operations


def _digest(stream, offset, length, notify=None):
    stream.seek(offset)
    remaining, digest = length, hashlib.sha256()
    while remaining:
        block = stream.read(min(CHUNK, remaining))
        if not block:
            raise ValueError("Un fichier a été tronqué pendant la construction ISO.")
        digest.update(block)
        remaining -= len(block)
        if notify:
            notify(length - remaining, length)
    return digest.hexdigest()


def _validate_payload(item):
    with item["file"].open("rb") as stream:
        if _digest(stream, 0, item["size"]) != item["sha256"] or stream.read(1):
            raise ValueError("Le SHA-256 d’un XBR exporté ne correspond pas au plan ISO.")
        stream.seek(0)
        header = stream.read(24)
        if header[:4] != b"xobx" or struct.unpack_from("<I", header, 4)[0] != 4:
            raise ValueError("Une archive XBR exportée est invalide.")
        count, payload = struct.unpack_from("<II", header, 12)
        if item["key"] == "gamedata/loc.xbr" and item["size"] == 60 and count == 0 and payload == 4096:
            stream.seek(0)
            words = struct.unpack("<15I", stream.read(60))
            if words[2:] == (0, 0, 4096, 0, 4096, 0, 4096, 0, 4096, 0, 4096, 4096, 0):
                return
        if count > 65536 or 64 + count * 16 > payload or payload > item["size"]:
            raise ValueError("Une archive XBR exportée est tronquée.")


def _derive_prefetch(image, stream, originals, changes, deletions, operations):
    """Derive the only non-XBR replacement directly from the input image."""
    if not any(operations.values()):
        return None
    from level_prefetch import MAX_PREFETCH_BYTES, update_prefetch
    planned = {item["key"]: item for item in changes}
    removed = {item["key"] for item in deletions}
    retained = {key for key, entry in originals.items() if not entry["directory"]} - removed
    retained.update(planned)
    redirects = {row["id"].casefold(): row["replacement"].casefold() for row in operations["deleted"]}
    for row in operations["created"]:
        key = "gamedata/" + row["id"].casefold() + ".xbr"
        item = planned.get(key)
        template = originals.get("gamedata/" + row["template"].casefold() + ".xbr")
        if item is None or item["sourceSha256"] is not None or template is None or template["directory"]:
            raise ValueError("Un niveau créé ne correspond pas à son ajout ou à son modèle dans l’ISO source.")
    for row in operations["deleted"]:
        target = row["replacement"].casefold()
        while target in redirects:
            target = redirects[target]
        if "gamedata/" + target + ".xbr" not in retained:
            raise ValueError("Le niveau de remplacement est absent de la nouvelle ISO.")
    original = originals.get(PREFETCH_PATH)
    if original is None or original["directory"]:
        raise ValueError("Le fichier de préchargement prefetch-lists.txt est absent de l’ISO source.")
    if not 0 < original["size"] <= MAX_PREFETCH_BYTES:
        raise ValueError("Le fichier de préchargement dépasse la taille autorisée.")
    stream.seek(image._extent(original["sector"], original["size"]))
    data = stream.read(original["size"])
    if len(data) != original["size"]:
        raise ValueError("Le fichier de préchargement est tronqué dans l’ISO source.")
    replacement = update_prefetch(data, **operations)
    if not isinstance(replacement, bytes) or not 0 < len(replacement) <= MAX_PREFETCH_BYTES:
        raise ValueError("Le nouveau fichier de préchargement est invalide ou trop volumineux.")
    if replacement == data:
        return None
    return {"path": original["path"], "key": PREFETCH_PATH, "data": replacement,
            "size": len(replacement), "sha256": hashlib.sha256(replacement).hexdigest(),
            "sourceSha256": hashlib.sha256(data).hexdigest()}


def _attributes(image, stream):
    """Recover original attribute bytes, retaining all original file attributes."""
    stream.seek(image.partition + 0x10000 + 20)
    root = struct.unpack("<II", stream.read(8))
    tables = [("", *root)] + [(e["path"], e["sector"], e["size"])
                               for e in image.entries if e["directory"] and e["size"]]
    expected = {e["path"].casefold(): e for e in image.entries}
    result = {}
    for prefix, sector, size in tables:
        stream.seek(image._extent(sector, size))
        table = stream.read(size)
        offsets, visited = [0], set()
        while offsets:
            at = offsets.pop()
            if at in visited or at % 4 or not 0 <= at <= size - 14:
                raise ValueError("Une table de dossier ISO a changé pendant sa lecture.")
            visited.add(at)
            if table[at:at + 2] == b"\xff\xff":
                if at == 0:
                    continue
                at = (at // SECTOR + 1) * SECTOR
                if at in visited or at > size - 14:
                    raise ValueError("Remplissage de dossier ISO invalide.")
                visited.add(at)
            left, right, start, length, flags, count = struct.unpack_from("<HHIIBB", table, at)
            name = _name(table[at + 14:at + 14 + count])
            path = (prefix + "/" if prefix else "") + name
            key = path.casefold()
            entry = expected.get(key)
            if (entry is None or key in result or entry["sector"] != start or entry["size"] != length
                    or entry["directory"] != bool(flags & 16)):
                raise ValueError("L’arborescence ISO a changé pendant sa lecture.")
            result[key] = flags
            offsets.extend(child * 4 for child in (right, left) if child)
    if set(result) != set(expected):
        raise ValueError("L’arborescence ISO est incomplète.")
    return result


def _directory_table(children):
    """Encode a balanced, case-insensitive tree with bounded 16-bit links.

    Nodes never cross a sector and start on DWORD boundaries. Links are offsets
    divided by four; zero is reserved for a missing child, so the root is first.
    """
    ordered = []
    for entry in children:
        try:
            name = entry["path"].rsplit("/", 1)[-1].encode("ascii")
        except UnicodeError:
            raise ValueError("La reconstruction ISO exige des noms de fichiers ASCII.") from None
        if not 1 <= len(name) <= 255:
            raise ValueError("Nom de fichier ISO trop long.")
        ordered.append((name.lower(), name, entry))
    ordered.sort(key=lambda row: row[0])
    if len({row[0] for row in ordered}) != len(ordered):
        raise ValueError("Noms de fichiers ISO dupliqués.")
    nodes, cursor = [], 0
    def tree(lo, hi):
        nonlocal cursor
        if lo >= hi:
            return None
        mid = (lo + hi) // 2
        _, name, entry = ordered[mid]
        if cursor % SECTOR + 14 + len(name) > SECTOR:
            cursor = _align(cursor)
        if cursor > 0xFFFF * 4:
            raise ValueError("Un dossier contient trop d’entrées pour les liens XDVDFS.")
        node = {"offset": cursor, "entry": entry, "name": name}
        nodes.append(node)
        cursor = _align(cursor + 14 + len(name), 4)
        node["left"] = tree(lo, mid)
        node["right"] = tree(mid + 1, hi)
        return node
    tree(0, len(ordered))
    size = max(SECTOR, _align(cursor))
    if size > MAX_DIRECTORY_BYTES:
        raise ValueError("Une table de dossier ISO dépasse la limite autorisée.")
    output = bytearray(b"\xff" * size)
    for node in nodes:
        entry, name = node["entry"], node["name"]
        struct.pack_into("<HHIIBB", output, node["offset"],
                         node["left"]["offset"] // 4 if node["left"] else 0,
                         node["right"]["offset"] // 4 if node["right"] else 0,
                         entry["sector"], entry["size"], entry["flags"], len(name))
        output[node["offset"] + 14:node["offset"] + 14 + len(name)] = name
    return bytes(output)


def _layout(image, changes, deletions, flags):
    entries = {e["path"].casefold(): dict(e, flags=flags[e["path"].casefold()]) for e in image.entries}
    for item in deletions:
        entries.pop(item["key"])
    position = _align(image.size)
    payloads = []
    for item in sorted(changes, key=lambda row: row["key"]):
        original = entries.get(item["key"])
        if original:
            path, attributes = original["path"], original["flags"]
        else:
            parts, actual = item["path"].split("/"), []
            for part in parts[:-1]:
                candidate = "/".join(actual + [part])
                directory = entries.get(candidate.casefold())
                if directory:
                    if not directory["directory"]:
                        raise ValueError("Un fichier empêche la création du dossier du niveau.")
                    actual = directory["path"].split("/")
                else:
                    actual.append(part)
                    entries["/".join(actual).casefold()] = {
                        "path": "/".join(actual), "directory": True, "flags": 16}
            path, attributes = "/".join(actual + [parts[-1]]), 32
        item["offset"] = position
        entries[item["key"]] = {"path": path, "sector": (position - image.partition) // SECTOR,
                                 "size": item["size"], "directory": False, "flags": attributes}
        payloads.append(item)
        position = _align(position + item["size"])
    if len(entries) > MAX_ENTRIES:
        raise ValueError("La nouvelle ISO contient trop de fichiers.")
    directories = {"": {"path": "", "directory": True, "flags": 16}}
    directories.update({key: e for key, e in entries.items() if e["directory"]})
    children = {key: [] for key in directories}
    for entry in entries.values():
        parent = entry["path"].rsplit("/", 1)[0] if "/" in entry["path"] else ""
        if parent.casefold() not in children:
            raise ValueError("Un dossier parent est absent de la nouvelle ISO.")
        children[parent.casefold()].append(entry)
    tables, total = [], 0
    for key in sorted(directories, key=lambda key: (-key.count("/"), -bool(key), key)):
        table = _directory_table(children[key])
        total += len(table)
        if total > MAX_DIRECTORY_TOTAL:
            raise ValueError("Les nouvelles tables de dossiers dépassent la limite autorisée.")
        directory = directories[key]
        directory.update(sector=(position - image.partition) // SECTOR, size=len(table))
        tables.append({"path": directory["path"], "offset": position, "data": table})
        position += len(table)
    if position > MAX_IMAGE_BYTES:
        raise ValueError("La nouvelle ISO dépasserait 16 Gio.")
    root = directories[""]
    return entries, payloads, tables, position, struct.pack("<II", root["sector"], root["size"])


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def build_iso(input_iso, export_dir, output_iso, progress=None):
    """Create and verify a new ISO; payloads are described by iso-plan.json.

    The plan uses format="azurik-level-iso", version=1, gameFiles containing
    path/sha256/sourceSha256, and removedFiles containing path/sourceSha256.
    Optional levelOperations contains created id/template and deleted id/replacement
    rows; prefetch-lists.txt is derived from the source ISO without staging it.
    sourceSha256 is null only for additions. progress receives (percent, stage).
    """
    source = Path(input_iso).resolve()
    destination = Path(output_iso).absolute()
    try:
        destination.lstat()
    except FileNotFoundError:
        exists = False
    else:
        exists = True
    if exists or destination.resolve() == source:
        raise ValueError("Choisissez une nouvelle ISO : aucun fichier existant ne sera remplacé.")
    if destination.suffix.lower() not in (".iso", ".xiso") or not destination.parent.is_dir():
        raise ValueError("Choisissez une destination ISO/XISO dans un dossier existant.")
    destination = destination.resolve()
    root = Path(export_dir).absolute()
    _reject_reparse(root)
    root = root.resolve()
    changes, deletions, operations = _load_plan(root)
    initial = source.stat()
    image = XboxISO(source)
    originals = {e["path"].casefold(): e for e in image.entries}
    def notify(percent, stage):
        if progress:
            progress(percent, stage)
    notify(2, "Vérification des fichiers de l’export")
    with source.open("rb") as stream:
        flags = _attributes(image, stream)
        for item in changes + deletions:
            original = originals.get(item["key"])
            if item["sourceSha256"] is None:
                if original is not None:
                    raise ValueError("Un ajout de niveau existe déjà dans l’ISO source.")
            else:
                if original is None or original["directory"]:
                    raise ValueError("Un fichier à remplacer ou supprimer est absent de l’ISO source.")
                offset = image._extent(original["sector"], original["size"])
                if _digest(stream, offset, original["size"]) != item["sourceSha256"]:
                    raise ValueError("Le SHA-256 d’un fichier source diffère du plan ISO.")
        for item in changes:
            _validate_payload(item)
        prefetch = _derive_prefetch(image, stream, originals, changes, deletions, operations)
        if prefetch:
            changes.append(prefetch)
        entries, payloads, tables, final_size, root_edit = _layout(image, changes, deletions, flags)
        notify(5, "Vérification de l’ISO source")
        input_hash = _digest(stream, 0, image.size,
                             lambda done, total: notify(5 + 15 * done / total, "Vérification de l’ISO source"))
        if stream.read(1) or _identity(source.stat()) != _identity(initial):
            raise ValueError("L’ISO source a changé pendant sa vérification.")
    if shutil.disk_usage(destination.parent).free < final_size + 16 * 1024 ** 2:
        raise ValueError("Espace disque insuffisant pour créer une nouvelle ISO.")
    notify(20, "Copie de l’ISO dans un nouveau fichier")
    created, complete, owned = False, False, None
    descriptor_edit = image.partition + 0x10000 + 20
    try:
        with source.open("rb") as src, destination.open("xb") as output:
            created, owned = True, os.fstat(output.fileno())
            digest, position = hashlib.sha256(), 0
            while position < image.size:
                block = src.read(min(CHUNK, image.size - position))
                if not block:
                    raise ValueError("L’ISO source a été tronquée pendant sa copie.")
                digest.update(block)
                adjusted = bytearray(block)
                for index, value in enumerate(root_edit):
                    absolute = descriptor_edit + index
                    if position <= absolute < position + len(block):
                        adjusted[absolute - position] = value
                output.write(adjusted)
                position += len(block)
                notify(20 + 40 * position / image.size, "Copie de l’ISO dans un nouveau fichier")
            if src.read(1) or digest.hexdigest() != input_hash:
                raise ValueError("L’ISO source a changé pendant sa copie.")
            output.write(b"\0" * (_align(position) - position))
            for item in payloads:
                if "data" in item:
                    output.write(item["data"])
                else:
                    # Repeat path/reparse validation immediately before opening.
                    path = _export_file(root, item["path"])
                    with path.open("rb") as stream:
                        remaining, payload_hash = item["size"], hashlib.sha256()
                        while remaining:
                            block = stream.read(min(CHUNK, remaining))
                            if not block:
                                raise ValueError("Un XBR exporté a été tronqué pendant sa copie.")
                            output.write(block)
                            payload_hash.update(block)
                            remaining -= len(block)
                        if stream.read(1) or payload_hash.hexdigest() != item["sha256"]:
                            raise ValueError("Un XBR exporté a changé pendant sa copie.")
                output.write(b"\0" * (-item["size"] % SECTOR))
            for table in tables:
                output.write(table["data"])
            if output.tell() != final_size:
                raise ValueError("La taille reconstruite de l’ISO est inattendue.")
            output.flush()
            os.fsync(output.fileno())
        written_stat = destination.stat()
        notify(65, "Relecture et validation de la nouvelle ISO")
        actual = XboxISO(destination)
        actual_entries = {e["path"].casefold(): e for e in actual.entries}
        expected = {key: {field: item[field] for field in ("path", "sector", "size", "directory")}
                    for key, item in entries.items()}
        if actual.partition != image.partition or actual_entries != expected:
            raise ValueError("L’arborescence reconstruite de l’ISO diffère du plan.")
        with source.open("rb") as src, destination.open("rb") as output:
            verified_source, verified_output, position = hashlib.sha256(), hashlib.sha256(), 0
            while position < image.size:
                block = src.read(min(CHUNK, image.size - position))
                if not block:
                    raise ValueError("L’ISO source a été tronquée pendant la relecture.")
                verified_source.update(block)
                adjusted = bytearray(block)
                for index, value in enumerate(root_edit):
                    absolute = descriptor_edit + index
                    if position <= absolute < position + len(block):
                        adjusted[absolute - position] = value
                readback = output.read(len(block))
                if readback != adjusted:
                    raise ValueError("La relecture ISO diffère hors des huit octets du descripteur.")
                verified_output.update(readback)
                position += len(block)
                notify(65 + 15 * position / image.size, "Relecture complète des données originales")
            padding = output.read(_align(image.size) - image.size)
            if padding != b"\0" * len(padding) or len(padding) != _align(image.size) - image.size:
                raise ValueError("Remplissage ISO incorrect pendant la relecture.")
            verified_output.update(padding)
            for item in payloads:
                remaining, digest = item["size"], hashlib.sha256()
                while remaining:
                    block = output.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError("XBR ajouté tronqué pendant la relecture.")
                    digest.update(block)
                    verified_output.update(block)
                    remaining -= len(block)
                if digest.hexdigest() != item["sha256"]:
                    raise ValueError("Le SHA-256 d’un XBR ajouté diffère pendant la relecture.")
                padding = output.read(-item["size"] % SECTOR)
                if padding != b"\0" * (-item["size"] % SECTOR):
                    raise ValueError("Remplissage d’un XBR incorrect pendant la relecture.")
                verified_output.update(padding)
            for table in tables:
                readback = output.read(len(table["data"]))
                if readback != table["data"]:
                    raise ValueError("Une table de dossier ISO diffère pendant la relecture.")
                verified_output.update(readback)
            if output.read(1) or verified_source.hexdigest() != input_hash:
                raise ValueError("L’ISO source ou la taille de sortie a changé pendant la relecture.")
            file_reports = []
            files = sorted((e for e in actual.entries if not e["directory"]), key=lambda e: e["path"].casefold())
            changed_hashes = {item["key"]: item["sha256"] for item in changes}
            for number, entry in enumerate(files):
                key = entry["path"].casefold()
                digest = _digest(output, actual._extent(entry["sector"], entry["size"]), entry["size"])
                expected_hash = changed_hashes.get(key)
                if expected_hash is None:
                    original = originals[key]
                    expected_hash = _digest(src, image._extent(original["sector"], original["size"]), original["size"])
                if digest != expected_hash:
                    raise ValueError("Le SHA-256 d’un fichier du jeu diffère après reconstruction ISO.")
                file_reports.append({"path": entry["path"], "bytes": entry["size"], "sha256": digest,
                                     "changed": key in changed_hashes})
                notify(80 + 19 * (number + 1) / max(1, len(files)), "Vérification de tous les fichiers du jeu")
            if _attributes(actual, output) != {key: entry["flags"] for key, entry in entries.items()}:
                raise ValueError("Les attributs des fichiers de l’ISO ont changé.")
        if (_identity(source.stat()) != _identity(initial)
                or _identity(destination.stat()) != _identity(written_stat)
                or written_stat.st_size != final_size):
            raise ValueError("Un fichier ISO a changé pendant sa vérification.")
        report = {"format": PLAN_FORMAT, "version": 1, "sourceUnchanged": True,
                  "inputSha256": input_hash, "isoSha256": verified_output.hexdigest(),
                  "sourceISOBytes": image.size, "outputISOBytes": final_size,
                  "partitionOffset": image.partition, "volumeRootOffset": descriptor_edit,
                  "changedFiles": [{"path": item["path"], "sha256": item["sha256"],
                                    "sourceSha256": item["sourceSha256"]} for item in changes],
                  "removedFiles": [{"path": item["path"], "sourceSha256": item["sourceSha256"]} for item in deletions],
                  "files": file_reports, "unchangedAssetsRetainExtents": True,
                  "originalPrefixChangedBytesAllowed": 8, "executableUnchanged": True,
                  "imageTestedInGame": False}
        notify(100, "Nouvelle ISO créée et vérifiée")
        complete = True
        return report
    finally:
        if created and not complete:
            try:
                current = destination.lstat()
            except FileNotFoundError:
                pass
            else:
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    destination.unlink()
