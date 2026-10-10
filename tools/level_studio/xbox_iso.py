"""Bounded, read-only XDVDFS import for a locally owned Azurik disc image.

Format reference: XboxDev/extract-xiso's public filesystem reader (sector
size, volume descriptor, partition offsets and directory record layout).
https://github.com/XboxDev/extract-xiso/blob/master/extract-xiso.c
This implementation does not execute disc programs or alter an input image.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
PARTITION_OFFSETS = (0, 0x18300000, 0x0FD90000, 0x02080000)
MAX_IMAGE_BYTES = 16 * 1024 ** 3
MAX_DIRECTORY_BYTES = 16 * 1024 ** 2
MAX_DIRECTORY_TOTAL = 128 * 1024 ** 2
MAX_ENTRIES = 100000
CHUNK = 1024 * 1024
JOB_ID = re.compile(r"[0-9a-f]{32}")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


def _name(raw):
    name = raw.decode("latin1")
    if (not name or name in (".", "..") or name[-1] in " ."
            or any(ord(c) < 32 or ord(c) == 127 or c in '/\\:*?"<>|' for c in name)
            or _RESERVED.fullmatch(name)):
        raise ValueError("L’ISO contient un nom de fichier non sûr.")
    return name


class XboxISO:
    """Validate all directory links and file extents before extracting anything."""
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file() or self.path.suffix.lower() not in (".iso", ".xiso"):
            raise ValueError("Choisissez un fichier ISO/XISO Xbox local.")
        self.size = self.path.stat().st_size
        if not SECTOR * 33 <= self.size <= MAX_IMAGE_BYTES:
            raise ValueError("L’ISO est tronquée ou dépasse 16 Gio.")
        self.partition = None
        self.entries = []
        with self.path.open("rb") as stream:
            for partition in PARTITION_OFFSETS:
                at = partition + 0x10000
                if at + SECTOR > self.size:
                    continue
                stream.seek(at)
                descriptor = stream.read(SECTOR)
                if descriptor[:20] != MAGIC:
                    continue
                if descriptor[-20:] != MAGIC:
                    raise ValueError("Descripteur XDVDFS tronqué ou incorrect.")
                sector, size = struct.unpack_from("<II", descriptor, 20)
                if not sector or not size:
                    raise ValueError("L’ISO Xbox ne contient aucun fichier.")
                self.partition = partition
                self._index(stream, sector, size)
                break
            if self.partition is None:
                raise ValueError("Aucun système de fichiers Xbox XDVDFS trouvé dans cette ISO.")
            self.selected = [entry for entry in self.entries if not entry["directory"]
                             and (entry["path"].lower() == "default.xbe"
                                  or entry["path"].lower().startswith("gamedata/"))]
            self._validate_azurik(stream)
        self.total_bytes = sum(entry["size"] for entry in self.selected)
        if self.total_bytes > MAX_IMAGE_BYTES:
            raise ValueError("Le contenu extrait dépasse 16 Gio.")

    def _extent(self, sector, size):
        offset = self.partition + sector * SECTOR
        if offset < self.partition or size < 0 or offset > self.size or size > self.size - offset:
            raise ValueError("L’ISO contient des fichiers ou dossiers hors de l’image.")
        return offset

    def _index(self, stream, root_sector, root_size):
        pending = [(root_sector, root_size, "", 0)]
        directories, total = set(), 0
        while pending:
            sector, size, prefix, depth = pending.pop()
            if depth > 64 or size > MAX_DIRECTORY_BYTES:
                raise ValueError("Arborescence ISO trop profonde ou trop volumineuse.")
            if size == 0:
                continue
            if (sector, size) in directories:
                raise ValueError("Cycle ou dossier partagé dans l’ISO.")
            directories.add((sector, size))
            total += size
            if total > MAX_DIRECTORY_TOTAL:
                raise ValueError("Les tables de dossiers dépassent la limite autorisée.")
            stream.seek(self._extent(sector, size))
            table = stream.read(size)
            if len(table) != size:
                raise ValueError("Table de dossier ISO tronquée.")
            offsets, visited, occupied, names = [0], set(), [], set()
            while offsets:
                offset = offsets.pop()
                if offset in visited or offset % 4 or not 0 <= offset <= size - 14:
                    raise ValueError("Lien de dossier ISO invalide ou cyclique.")
                visited.add(offset)
                if table[offset:offset + 2] == b"\xff\xff":
                    # Directory nodes may continue at the next sector after padding.
                    if offset == 0:
                        continue
                    offset = (offset // SECTOR + 1) * SECTOR
                    if offset in visited or offset > size - 14:
                        raise ValueError("Lien de dossier ISO dans une zone de remplissage.")
                    visited.add(offset)
                left, right, start, length, flags, name_size = struct.unpack_from("<HHIIBB", table, offset)
                end = offset + 14 + name_size
                if not name_size or end > size or any(offset < b and end > a for a, b in occupied):
                    raise ValueError("Entrée de dossier ISO tronquée ou superposée.")
                occupied.append((offset, end))
                name = _name(table[offset + 14:end])
                if name.casefold() in names:
                    raise ValueError("L’ISO contient des noms de fichiers dupliqués.")
                names.add(name.casefold())
                path = prefix + name
                self._extent(start, length)
                directory = bool(flags & 0x10)
                entry = {"path": path, "sector": start, "size": length, "directory": directory}
                self.entries.append(entry)
                if len(self.entries) > MAX_ENTRIES:
                    raise ValueError("L’ISO contient trop de fichiers.")
                if directory and length:
                    pending.append((start, length, path + "/", depth + 1))
                for child in (right, left):
                    if child:
                        offsets.append(child * 4)

    def _read_entry(self, stream, entry, relative, size):
        if relative < 0 or relative + size > entry["size"]:
            raise ValueError("Fichier de jeu tronqué dans l’ISO.")
        stream.seek(self._extent(entry["sector"], entry["size"]) + relative)
        result = stream.read(size)
        if len(result) != size:
            raise ValueError("Fichier de jeu tronqué dans l’ISO.")
        return result

    def _validate_azurik(self, stream):
        by_name = {entry["path"].lower(): entry for entry in self.selected}
        executable = by_name.get("default.xbe")
        if executable is None:
            raise ValueError("default.xbe est absent : une ISO Azurik complète est nécessaire.")
        header = self._read_entry(stream, executable, 0, 0x11c)
        if header[:4] != b"XBEH":
            raise ValueError("Le programme Xbox de cette ISO est invalide.")
        base = struct.unpack_from("<I", header, 0x104)[0]
        cert = struct.unpack_from("<I", header, 0x118)[0] - base
        certificate = self._read_entry(stream, executable, cert, 92)
        title_id = struct.unpack_from("<I", certificate, 8)[0]
        title = certificate[12:92].decode("utf-16-le", errors="strict").split("\0", 1)[0]
        if title_id != 0x4D530007 or "azurik" not in title.lower():
            raise ValueError("Cette ISO n’est pas Azurik: Rise of Perathia.")
        archives = [entry for entry in self.selected if entry["path"].lower().startswith("gamedata/")
                    and entry["path"].lower().endswith(".xbr")]
        if not archives or not any(entry["path"].lower() in ("gamedata/town.xbr", "gamedata/a5.xbr",
                                                            "gamedata/training_room.xbr") for entry in archives):
            raise ValueError("Les niveaux Azurik sont absents du dossier gamedata.")
        for entry in archives:
            header = self._read_entry(stream, entry, 0, 24)
            if header[:4] != b"xobx" or struct.unpack_from("<I", header, 4)[0] != 4:
                raise ValueError("Une archive XBR de l’ISO est invalide.")
            count, payload = struct.unpack_from("<II", header, 12)
            # The European retail disc has a 60-byte, zero-resource loc.xbr
            # placeholder. Preserve it during extraction; it is not a level.
            # Accept only its exact metadata shape, never a truncated archive.
            if (entry["path"].lower() == "gamedata/loc.xbr" and entry["size"] == 60
                    and count == 0 and payload == 4096):
                metadata = struct.unpack("<15I", self._read_entry(stream, entry, 0, 60))
                if metadata[2:] == (0, 0, 4096, 0, 4096, 0, 4096, 0, 4096, 0, 4096, 4096, 0):
                    continue
            if count > 65536 or 64 + count * 16 > payload or payload > entry["size"]:
                raise ValueError("Une archive XBR de l’ISO est tronquée.")
        self.title = title
        self.title_id = title_id

    def extract(self, destination, progress=None):
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("Le dossier d’import existe déjà ; aucun fichier ne sera remplacé.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(destination.parent).free < self.total_bytes + CHUNK:
            raise ValueError("Espace disque insuffisant pour importer cette ISO.")
        destination.mkdir(exist_ok=False)
        completed, reports = 0, []
        with self.path.open("rb") as stream:
            for number, entry in enumerate(self.selected):
                # Canonicalise these two root names for the existing source loader.
                parts = entry["path"].split("/")
                parts[0] = parts[0].lower()
                if parts[0] == "gamedata":
                    parts = [part.lower() for part in parts]
                target = destination.joinpath(*parts).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError("Destination d’extraction hors du dossier d’import.")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream.seek(self._extent(entry["sector"], entry["size"]))
                remaining, digest = entry["size"], hashlib.sha256()
                with target.open("xb") as output:
                    while remaining:
                        chunk = stream.read(min(CHUNK, remaining))
                        if not chunk:
                            raise ValueError("L’ISO a été tronquée pendant l’import.")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                        completed += len(chunk)
                        if progress:
                            progress(completed, self.total_bytes, number, len(self.selected), entry["path"])
                reports.append({"path": str(target.relative_to(destination)), "bytes": entry["size"],
                                "sha256": digest.hexdigest()})
        return {"title": self.title, "titleId": hex(self.title_id), "partitionOffset": self.partition,
                "sourceDir": str(destination), "fileCount": len(reports), "bytes": completed, "files": reports,
                "scope": "default.xbe and gamedata", "inputUnchanged": True}


class ISOImports:
    """One bounded import at a time, with durable manifests and pollable jobs."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self._lock = threading.RLock()
        self._jobs = {}
        if self.directory.is_dir():
            for path in self.directory.glob("*/import.json"):
                if not JOB_ID.fullmatch(path.parent.name) or not path.resolve().is_relative_to(self.directory):
                    continue
                try:
                    value = json.loads(path.read_text("utf-8"))
                    if value.get("id") == path.parent.name and value.get("status") == "ready":
                        self._jobs[value["id"]] = value
                except (OSError, ValueError, TypeError):
                    continue

    def _new(self, name):
        with self._lock:
            if any(row["status"] in ("uploading", "reading", "extracting") for row in self._jobs.values()):
                raise ValueError("Un import ISO est déjà en cours.")
            identifier = uuid4().hex
            folder = self.directory / identifier
            folder.mkdir(parents=True, exist_ok=False)
            self._jobs[identifier] = {"id": identifier, "name": Path(name).name, "status": "reading", "progress": 0,
                                      "createdUtc": datetime.now(timezone.utc).isoformat()}
            return identifier, folder

    def _update(self, identifier, **values):
        with self._lock:
            self._jobs[identifier].update(values)

    def status(self, identifier):
        with self._lock:
            if not isinstance(identifier, str) or not JOB_ID.fullmatch(identifier) or identifier not in self._jobs:
                raise ValueError("Import ISO inconnu.")
            return copy.deepcopy(self._jobs[identifier])

    def list(self):
        with self._lock:
            return [copy.deepcopy(row) for row in self._jobs.values()]

    def start(self, path):
        if not isinstance(path, (str, Path)) or not str(path).strip() or len(str(path)) > 4096:
            raise ValueError("Choisissez un fichier ISO/XISO Xbox local.")
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in (".iso", ".xiso"):
            raise ValueError("Choisissez un fichier ISO/XISO Xbox local.")
        if not SECTOR * 33 <= path.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError("L’ISO est tronquée ou dépasse 16 Gio.")
        identifier, folder = self._new(path.name)
        threading.Thread(target=self._run, args=(identifier, path, folder), daemon=True).start()
        return self.status(identifier)

    def upload(self, stream, length, name):
        if type(length) is not int or not SECTOR * 33 <= length <= MAX_IMAGE_BYTES:
            raise ValueError("L’ISO est tronquée ou dépasse 16 Gio.")
        if not isinstance(name, str) or Path(name).suffix.lower() not in (".iso", ".xiso"):
            raise ValueError("Choisissez un fichier ISO/XISO Xbox local.")
        identifier, folder = self._new(name)
        path = folder / "upload.iso"
        self._update(identifier, status="uploading")
        try:
            if shutil.disk_usage(folder).free < length + CHUNK:
                raise ValueError("Espace disque insuffisant pour recevoir cette ISO.")
            remaining = length
            with path.open("xb") as output:
                while remaining:
                    chunk = stream.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError("Le transfert de l’ISO est incomplet.")
                    output.write(chunk)
                    remaining -= len(chunk)
                    self._update(identifier, uploadedBytes=length - remaining, uploadBytes=length)
            self._update(identifier, status="reading")
            threading.Thread(target=self._run, args=(identifier, path, folder, True), daemon=True).start()
        except Exception as exc:
            if path.exists():
                path.unlink()
            self._update(identifier, status="error", error=str(exc))
            raise
        return self.status(identifier)

    def _run(self, identifier, path, folder, remove_upload=False):
        try:
            before = path.stat()
            image = XboxISO(path)
            self._update(identifier, status="extracting", bytesTotal=image.total_bytes,
                         fileCount=len(image.selected), title=image.title)
            def progress(done, total, number, count, current):
                self._update(identifier, bytesDone=done, bytesTotal=total, filesDone=number,
                             fileCount=count, currentFile=current, progress=done / max(1, total))
            report = image.extract(folder / "source", progress)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("L’ISO a changé pendant l’import ; le résultat ne peut pas être ouvert.")
            self._update(identifier, status="ready", progress=1, filesDone=report["fileCount"],
                         sourceDir=report["sourceDir"], report=report)
        except Exception as exc:
            self._update(identifier, status="error", error=str(exc))
        finally:
            (folder / "import.json").write_text(json.dumps(self.status(identifier), ensure_ascii=False,
                                                          indent=2, allow_nan=False), "utf-8")
            if remove_upload and path.is_file() and path.parent == folder:
                path.unlink()

    def source(self, identifier):
        job = self.status(identifier)
        source = (self.directory / identifier / "source").resolve()
        if job["status"] != "ready" or not source.is_relative_to(self.directory) or not source.is_dir():
            raise ValueError("Cet import n’est pas prêt à être ouvert.")
        return source
