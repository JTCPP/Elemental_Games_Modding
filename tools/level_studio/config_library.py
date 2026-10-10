"""Read the retail critters_engine TABL; do not infer names from BODY assets.

Retail XBE 0x496DA–0x496FD reads the `body` row, and 0x49913–0x49940
reads `scale` with default 1.0. Generator 0x6F700–0x6F78F splits its name
at the first colon, resolves a 255-byte prefix and stores the path separately.
0x40602 copies scale into entity+0x188; rendering 0x3F049–0x3F070 applies
it through 0xA39E0 to the matrix's 3x3 basis, preserving its translation.
Resource lookup 0xA5CDC calls ASCII lowercase routine 0xF5D3A before lookup.
All relative pointers below are bounded to the selected TABL resource.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import struct

from renderer_parser import read_sections


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
ENGINE_RESOURCE = "config/critters_engine"


def _bounded(start, length, section):
    if length < 0 or not section.offset <= start <= section.offset + section.size - length:
        raise ValueError("Pointeur TABL hors de sa ressource.")
    return start


def _string(data, field, length, section):
    _bounded(field, 4, section)
    if not 0 <= length <= 4096:
        raise ValueError("Longueur de texte TABL non valide.")
    target = field + struct.unpack_from("<i", data, field)[0]
    _bounded(target, length + 1, section)
    raw = data[target:target + length]
    if data[target + length] != 0 or b"\0" in raw:
        raise ValueError("Texte TABL non terminé ou longueur incohérente.")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Texte TABL non ASCII.") from exc


def _named_engine(data, sections):
    if len(data) < 64:
        raise ValueError("En-tête de configuration tronqué.")
    count, table, pool, pool_size = struct.unpack_from("<4I", data, 44)
    if count > 4096 or table < 64 or table + count * 8 > pool or pool + pool_size > len(data):
        raise ValueError("Table des noms de configuration hors de l’archive.")
    found = []
    for index in range(count):
        resource, offset = struct.unpack_from("<2I", data, table + index * 8)
        if resource >= len(sections) or offset >= pool_size:
            raise ValueError("Référence nommée de configuration hors de l’archive.")
        end = data.find(b"\0", pool + offset, min(pool + pool_size, pool + offset + 4097))
        if end < 0:
            raise ValueError("Nom de configuration non terminé.")
        name = data[pool + offset:end]
        if name == ENGINE_RESOURCE.encode("ascii"):
            section = sections[resource]
            if section.tag != "tabl":
                raise ValueError("La configuration des créatures ne référence pas TABL.")
            found.append(section)
    if len(found) != 1:
        raise ValueError("Ressource critters_engine absente ou ambiguë.")
    return found[0]


def read_engine_table(data, section):
    """Return proven config entries, with byte offsets for their source cells."""
    if section.tag != "tabl" or section.offset < 64 or section.offset + section.size > len(data):
        raise ValueError("Ressource de configuration non TABL.")
    _bounded(section.offset, 20, section)
    rows, row_relative, columns, cell_count, cell_relative = struct.unpack_from("<5I", data, section.offset)
    if not 2 <= rows <= 256 or not 1 <= columns <= 4096 or cell_count != rows * columns or cell_count > 262144:
        raise ValueError("Dimensions TABL non valides.")
    row_table = _bounded(section.offset + 4 + row_relative, rows * 8, section)
    cells = _bounded(section.offset + 16 + cell_relative, cell_count * 16, section)
    if row_table < section.offset + 20 or cells < row_table + rows * 8:
        raise ValueError("Tables TABL superposées.")
    row_names = []
    for index in range(rows):
        row = row_table + index * 8
        length = struct.unpack_from("<I", data, row)[0]
        row_names.append(_string(data, row + 4, length, section))
    nonempty = [name for name in row_names if name]
    if len(set(nonempty)) != len(nonempty) or not {"name", "body", "scale", "ai"}.issubset(row_names):
        raise ValueError("Lignes critters_engine absentes ou ambiguës.")
    entries, names = [], set()
    for column in range(columns):
        values, offsets = {}, {}
        for row_index, row_name in enumerate(row_names):
            cell = cells + (rows * column + row_index) * 16
            kind = struct.unpack_from("<I", data, cell)[0]
            if kind == 0:
                value = None
            elif kind == 1:
                value = struct.unpack_from("<d", data, cell + 8)[0]
                if not math.isfinite(value):
                    raise ValueError("Valeur TABL non finie.")
            elif kind == 2:
                value = _string(data, cell + 12, struct.unpack_from("<I", data, cell + 8)[0], section)
            else:
                raise ValueError("Type de cellule TABL non décodé.")
            if row_name:
                values[row_name], offsets[row_name] = value, cell
        name, body, scale = values["name"], values["body"], values["scale"]
        if not isinstance(name, str) or not name or name in names or len(name) > 255:
            raise ValueError("Nom d’archétype absent, ambigu ou trop long.")
        if body is not None and (not isinstance(body, str) or len(body) > 1024):
            raise ValueError("Nom de corps non valide.")
        if scale is not None and (not isinstance(scale, float) or not 0 < scale <= 1000):
            raise ValueError("Échelle de configuration non valide.")
        if values["ai"] is not None and not isinstance(values["ai"], str):
            raise ValueError("Type d’IA non valide.")
        names.add(name)
        entries.append({"name": name, "body": body or None,
                        "bodyResource": "characters/" + body.lower() if body else None,
                        "scale": 1.0 if scale is None else scale,
                        "scaleSource": "default" if scale is None else "table",
                        "scaleSemantics": "uniform-body-scale", "scaleVerified": True,
                        "ai": values["ai"], "realm": values.get("realm"),
                        "sourceArchive": "config.xbr", "sourceResource": ENGINE_RESOURCE,
                        "sourceResourceIndex": section.index, "sourceOffset": offsets["name"],
                        "bodyOffset": offsets["body"], "scaleOffset": offsets["scale"],
                        "columnIndex": column})
    return entries


class CritterLibrary:
    """Read-only exact archetype mapping; unknown runtime-created names return None."""
    def __init__(self, source_dir):
        source = Path(source_dir)
        gamedata = source if source.name.lower() == "gamedata" else source / "gamedata"
        self.path = gamedata / "config.xbr"
        self._stamp = None
        self._entries = {}
        self._hash = None

    def _load(self):
        if not self.path.is_file():
            self._stamp, self._entries, self._hash = None, {}, None
            return False
        stat = self.path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self._stamp == stamp:
            return True
        if not 64 <= stat.st_size <= MAX_ARCHIVE_BYTES:
            raise ValueError("Taille de l’archive de configuration non valide.")
        with self.path.open("rb") as stream:
            data = stream.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) != stat.st_size or self.path.stat().st_mtime_ns != stat.st_mtime_ns:
            raise ValueError("La configuration source a changé pendant sa lecture.")
        if data[:4] != b"xobx" or struct.unpack_from("<I", data, 4)[0] != 4:
            raise ValueError("La configuration ne possède pas un en-tête XBR retail v4.")
        sections = read_sections(data)
        entries = read_engine_table(data, _named_engine(data, sections))
        self._entries = {entry["name"]: entry for entry in entries}
        self._hash = hashlib.sha256(data).hexdigest()
        self._stamp = stamp
        return True

    def resolve(self, archetype):
        if not isinstance(archetype, str) or not archetype or len(archetype) > 4096 or "\0" in archetype:
            raise ValueError("Nom d’archétype non valide.")
        try:
            archetype.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("Nom d’archétype non ASCII.") from exc
        prefix, separator, path = archetype.partition(":")
        matched_name = prefix[:255]
        if not self._load() or matched_name not in self._entries:
            return None
        result = copy.deepcopy(self._entries[matched_name])
        result.update(archetype=archetype, matchedName=matched_name,
                      matchKind="path" if separator else "exact", sourceHash=self._hash)
        if separator:
            result["path"] = path
        if len(prefix) > 255:
            result["nameTruncated"] = True
        return result

    def catalog(self):
        available = self._load()
        return {"available": available, "sourceArchive": "config.xbr", "sourceHash": self._hash,
                "entryCount": len(self._entries), "entries": copy.deepcopy(list(self._entries.values()))}

    def coverage(self, archetypes):
        requested = list(archetypes)
        resolved, missing = [], []
        for archetype in requested:
            mapping = self.resolve(archetype)
            if mapping is None:
                missing.append(archetype)
            else:
                resolved.append(mapping)
        return {"requestedCount": len(requested), "resolvedCount": len(resolved),
                "missing": sorted(set(missing)), "mappings": resolved}
