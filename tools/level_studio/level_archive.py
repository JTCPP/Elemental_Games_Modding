"""Bounded codecs for native XBR level names and the global asset directory.

The retail v4 archive footer is compact: loader-type records (8 bytes),
resource relocations (4 bytes), type records (12 bytes), named resources
(8 bytes), then a NUL-terminated ASCII name pool.  Its offsets are absolute
file offsets in header fields +24/+32/+40/+48/+52.  A named row contains a
resource index and an offset relative to the name pool.

``indx`` is a different format: count + a field-relative table pointer,
then 20-byte records with two length/pointer pairs and a fourcc.  Its
pointer bases are the individual pointer fields, not the section or pool.

Cloning preserves resource bytes, native script IDs, portals and collision.
It creates a new registered native level from an existing template; it does
not compile an empty level or rewrite quest logic.  Callers own project,
prefetch and ISO integration.  These functions never read or write files.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from typing import Iterable, Mapping


MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_RESOURCES = 200_000
MAX_NAMES = 20_000
MAX_INDEX_RECORDS = 200_000
MAX_KEY_BYTES = 255
MAX_LEVEL_PATH_BYTES = 79  # scene->pending_path has a 0x50-byte buffer
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.xbr\Z", re.IGNORECASE)
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class _Resource:
    index: int
    tag: str
    offset: int
    size: int
    flags: int


@dataclass(frozen=True)
class _Archive:
    header: tuple[int, ...]
    resources: tuple[_Resource, ...]

    @property
    def names_offset(self) -> int:
        return self.header[12]


def _bytes(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("XBR data must be bytes.")
    if not 64 <= len(data) <= MAX_ARCHIVE_BYTES:
        raise ValueError("XBR archive size is outside the supported bounds.")
    return bytes(data)


def _ascii(value: str, label: str, maximum: int = MAX_KEY_BYTES) -> bytes:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"Invalid {label} length.")
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must use ASCII characters.") from exc
    if any(c < 32 or c >= 127 for c in raw):
        raise ValueError(f"Invalid character in {label}.")
    return raw


def _key(value: str, *, level: bool = False) -> bytes:
    raw = _ascii(value, "asset key", MAX_LEVEL_PATH_BYTES if level else MAX_KEY_BYTES)
    if not _PATH.fullmatch(value) or any(p in (".", "..") for p in value.split("/")):
        raise ValueError("Asset keys must be safe slash-separated paths.")
    if level and not value.startswith("levels/"):
        raise ValueError("A playable level key must begin with levels/.")
    return raw


def _archive_name(value: str) -> bytes:
    raw = _ascii(value, "archive filename")
    if not _ARCHIVE.fullmatch(value) or value.split(".")[0].lower() in _RESERVED:
        raise ValueError("Archive filenames must be safe XBR basenames.")
    return raw


def _tag(value: str) -> bytes:
    raw = _ascii(value, "fourcc", 4)
    if len(raw) != 4:
        raise ValueError("A fourcc must contain exactly four ASCII characters.")
    return raw


def _level_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value.lower() in _RESERVED:
        raise ValueError("Level IDs must contain 1-64 ASCII letters, digits, underscores or hyphens.")
    return value


def _parse_archive(data: bytes) -> _Archive:
    if data[:4] != b"xobx":
        raise ValueError("Invalid XBR magic.")
    h = struct.unpack_from("<16I", data)
    if h[1] != 4:
        raise ValueError("Only the native v4 XBR layout is supported.")
    count, payload, aux = h[3], h[4], h[6]
    if not 1 <= count <= MAX_RESOURCES or 64 + count * 16 > payload:
        raise ValueError("Invalid XBR resource table bounds.")
    if payload % 4096 or aux % 4096 or not payload <= aux <= len(data):
        raise ValueError("Invalid XBR payload/footer alignment or bounds.")
    # The engine computes the footer buffer size from counts.  Gaps, a
    # trailing appended name table, or a relocated pool outside that buffer
    # would pass a superficial name parser but fail the actual loader.
    if (h[5] > MAX_RESOURCES or h[7] > MAX_RESOURCES * 100 or h[9] > MAX_RESOURCES
            or h[11] > MAX_NAMES or h[14] > MAX_ARCHIVE_BYTES
            or h[6] + h[5] * 8 != h[8]
            or h[8] + h[7] * 4 != h[10]
            or h[10] + h[9] * 12 != h[12]
            or h[12] + h[11] * 8 != h[13]
            or h[13] + h[14] != len(data)):
        raise ValueError("Unsupported or truncated XBR metadata footer layout.")
    resources = []
    previous_end = 0
    for index in range(count):
        size, tag, flags, end = struct.unpack_from("<I4sII", data, 64 + index * 16)
        if any(c < 32 or c >= 127 for c in tag):
            raise ValueError("Invalid XBR resource fourcc.")
        start = payload + previous_end
        if start < payload or start + size > aux:
            raise ValueError("XBR resource overlaps or exceeds the payload area.")
        if end < previous_end + size and not (index == count - 1 and end == 0):
            raise ValueError("Overlapping XBR resource offsets.")
        if end and payload + end > aux:
            raise ValueError("XBR resource end exceeds the payload area.")
        resources.append(_Resource(index, tag.decode("ascii"), start, size, flags))
        previous_end = end
    return _Archive(h, tuple(resources))


def _named(data: bytes, archive: _Archive) -> list[dict]:
    h = archive.header
    result = []
    seen = set()
    for index in range(h[11]):
        resource, name_offset = struct.unpack_from("<II", data, h[12] + index * 8)
        if resource >= len(archive.resources) or name_offset >= h[14]:
            raise ValueError("Named XBR resource points outside its table or pool.")
        start = h[13] + name_offset
        end = data.find(b"\0", start, min(len(data), start + MAX_KEY_BYTES + 1))
        if end < 0:
            raise ValueError("Unterminated or oversized XBR resource name.")
        try:
            key = data[start:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("XBR resource names must use ASCII.") from exc
        _key(key)
        if key in seen:
            raise ValueError("Duplicate named XBR resource key.")
        seen.add(key)
        result.append({"key": key, "tag": archive.resources[resource].tag,
                       "resourceIndex": resource})
    return result


def named_resources(data: bytes) -> list[dict]:
    """Return native named resources as key/tag/resourceIndex dictionaries."""
    data = _bytes(data)
    return _named(data, _parse_archive(data))


def _write_names(data: bytes, archive: _Archive, names: list[dict]) -> bytes:
    if len(names) > MAX_NAMES:
        raise ValueError("Too many named XBR resources.")
    table = bytearray()
    pool = bytearray()
    seen = set()
    for name in names:
        raw = _key(name["key"])
        resource = name["resourceIndex"]
        if not isinstance(resource, int) or not 0 <= resource < len(archive.resources):
            raise ValueError("Invalid named resource index.")
        if name["key"] in seen or archive.resources[resource].tag != name["tag"]:
            raise ValueError("Conflicting named resource key or type.")
        seen.add(name["key"])
        table += struct.pack("<II", resource, len(pool))
        pool += raw + b"\0"
    result = bytearray(data[:archive.names_offset])
    result += table + pool
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("Rebuilt XBR archive is too large.")
    struct.pack_into("<IIII", result, 44, len(names), archive.names_offset,
                     archive.names_offset + len(table), len(pool))
    _named(result, _parse_archive(result))
    return bytes(result)


def _level_resource(names: list[dict], target_key: str | None = None) -> dict:
    levels = [n for n in names if n["tag"] == "levl" and n["key"].startswith("levels/")]
    if target_key is not None:
        levels = [n for n in levels if n["key"] == target_key]
    if not levels or len({n["resourceIndex"] for n in levels}) != 1:
        raise ValueError("Expected one unambiguous playable LEVL resource.")
    return levels[0]


def clone_level(data: bytes, source_id: str, new_id: str) -> tuple[bytes, list[dict]]:
    """Clone a playable template under levels/custom/<new_id> and <new_id>.xbr.

    The returned registrations are ready for ``update_index(add=...)``.
    Auxiliary names below the template key receive the same prefix change;
    other local names move below the custom level's assets/ namespace.
    Prior LEVL aliases are dropped so clones cannot steal existing level keys.
    """
    data = _bytes(data)
    source_id, new_id = _level_id(source_id), _level_id(new_id)
    if source_id.lower() == new_id.lower():
        raise ValueError("A clone needs a different level ID.")
    archive = _parse_archive(data)
    names = _named(data, archive)
    matching = [n for n in names if n["tag"] == "levl"
                and n["key"].startswith("levels/")
                and n["key"].rsplit("/", 1)[-1].lower() == source_id.lower()]
    if len(matching) != 1:
        raise ValueError("Source level ID does not match its native LEVL registration.")
    primary = _level_resource(names, matching[0]["key"])
    prefix = primary["key"]
    new_key = "levels/custom/" + new_id.lower()
    _key(new_key, level=True)
    filename = new_id + ".xbr"
    _archive_name(filename)
    cloned = []
    for name in names:
        if name["tag"] == "levl":
            if name["resourceIndex"] != primary["resourceIndex"]:
                raise ValueError("Templates with multiple LEVL payloads are unsupported.")
            continue
        if name["key"].startswith(prefix + "/"):
            key = new_key + name["key"][len(prefix):]
        else:
            key = new_key + "/assets/" + name["key"]
        cloned.append({**name, "key": key})
    cloned.append({**primary, "key": new_key})
    result = _write_names(data, archive, cloned)
    registrations = [{"key": n["key"], "tag": n["tag"], "archive": filename}
                     for n in cloned]
    return result, registrations


def alias_level(data: bytes, aliases: Iterable[str], target_key: str | None = None) -> bytes:
    """Add playable level keys for the same existing LEVL resource.

    Redirecting an old level path also requires its global index entry to
    point to this archive.  Aliases do not map unrelated body/node resources
    to LEVL, or manufacture destination-specific teleport spots.
    """
    data = _bytes(data)
    if isinstance(aliases, (str, bytes)):
        raise ValueError("Level aliases must be an iterable of asset keys.")
    archive = _parse_archive(data)
    names = _named(data, archive)
    primary = _level_resource(names, target_key)
    existing = {n["key"]: n for n in names}
    for key in aliases:
        _key(key, level=True)
        if key in existing:
            if (existing[key]["tag"] != "levl"
                    or existing[key]["resourceIndex"] != primary["resourceIndex"]):
                raise ValueError("Level alias conflicts with another named resource.")
            continue
        name = {**primary, "key": key}
        names.append(name)
        existing[key] = name
        if len(names) > MAX_NAMES:
            raise ValueError("Too many level aliases.")
    if len(names) == archive.header[11]:
        return data
    return _write_names(data, archive, names)


def _index_archive(data: bytes) -> tuple[_Archive, _Resource]:
    archive = _parse_archive(data)
    if len(archive.resources) != 1 or archive.resources[0].tag != "indx":
        raise ValueError("Expected an XBR containing only the native indx resource.")
    if archive.header[7] or archive.header[9]:
        raise ValueError("Index archives with resource relocations/type records are unsupported.")
    _named(data, archive)
    return archive, archive.resources[0]


def _relative_string(data: bytes, field: int, length: int, lo: int, hi: int) -> str:
    if not 1 <= length <= MAX_KEY_BYTES:
        raise ValueError("Invalid index string length.")
    start = field + struct.unpack_from("<i", data, field)[0]
    if not lo <= start or start + length >= hi or data[start + length] != 0:
        raise ValueError("Index string pointer/terminator is outside its string pool.")
    raw = data[start:start + length]
    if b"\0" in raw:
        raise ValueError("Index string contains an embedded terminator.")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Index strings must use ASCII.") from exc


def _read_index(data: bytes, section: _Resource) -> list[dict]:
    start, end = section.offset, section.offset + section.size
    if section.size < 8:
        raise ValueError("Truncated native index header.")
    count, relative = struct.unpack_from("<Ii", data, start)
    table = start + 4 + relative
    pool_minimum = table + count * 20
    if count > MAX_INDEX_RECORDS or table < start + 8 or pool_minimum > end:
        raise ValueError("Invalid native index record table bounds.")
    records = []
    seen = set()
    previous = None
    for index in range(count):
        row = table + index * 20
        key_len = struct.unpack_from("<I", data, row)[0]
        archive_len = struct.unpack_from("<I", data, row + 8)[0]
        key = _relative_string(data, row + 4, key_len, pool_minimum, end)
        filename = _relative_string(data, row + 12, archive_len, pool_minimum, end)
        try:
            tag = data[row + 16:row + 20].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid native index fourcc.") from exc
        _key(key)
        _archive_name(filename)
        _tag(tag)
        if key in seen or (previous is not None and key <= previous):
            raise ValueError("Native index keys must be unique and sorted.")
        seen.add(key)
        previous = key
        records.append({"key": key, "archive": filename, "tag": tag})
    return records


def read_index(data: bytes) -> list[dict]:
    """Decode complete native index records with bounded field-relative reads."""
    data = _bytes(data)
    _, section = _index_archive(data)
    return _read_index(data, section)


def _record(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("Index records must be mappings.")
    try:
        key, filename, tag = value["key"], value["archive"], value["tag"]
    except KeyError as exc:
        raise ValueError("Index records require key, archive and tag.") from exc
    _key(key)
    _archive_name(filename)
    _tag(tag)
    if tag == "levl" and key.startswith("levels/"):
        _key(key, level=True)
    return {"key": key, "archive": filename, "tag": tag}


def _encode_index(records: list[dict]) -> bytes:
    if len(records) > MAX_INDEX_RECORDS:
        raise ValueError("Too many native index records.")
    payload = bytearray(8 + len(records) * 20)
    struct.pack_into("<Ii", payload, 0, len(records), 4)
    strings: dict[str, int] = {}
    for index, record in enumerate(records):
        row = 8 + index * 20
        for value, length_field, pointer_field in ((record["key"], row, row + 4),
                                                    (record["archive"], row + 8, row + 12)):
            raw = value.encode("ascii")
            if value not in strings:
                strings[value] = len(payload)
                payload += raw + b"\0"
            relative = strings[value] - pointer_field
            if not -(1 << 31) <= relative < 1 << 31:
                raise ValueError("Index relative pointer exceeds its native bounds.")
            struct.pack_into("<I", payload, length_field, len(raw))
            struct.pack_into("<i", payload, pointer_field, relative)
        payload[row + 16:row + 20] = record["tag"].encode("ascii")
    return bytes(payload)


def update_index(data: bytes, add: Iterable[Mapping] = (), remove: Iterable[Mapping] = (),
                 *, remove_archives: Iterable[str] = ()) -> bytes:
    """Upsert records and remove exact key/tag pairs, preserving other entries.

    A replacement with the same key and tag is explicit via ``add``.  A key
    changing its native type is rejected.  ``remove_archives`` is optional
    and is appropriate only when callers established the entire archive is
    no longer referenced.  Exact removal never deletes sibling node assets.
    """
    data = _bytes(data)
    archive, section = _index_archive(data)
    originals = _read_index(data, section)
    records = {r["key"]: r for r in originals}
    for filename in remove_archives:
        _archive_name(filename)
        records = {k: r for k, r in records.items() if r["archive"].lower() != filename.lower()}
    for value in remove:
        if not isinstance(value, Mapping) or "key" not in value or "tag" not in value:
            raise ValueError("Index removals require an exact key/tag pair.")
        _key(value["key"])
        _tag(value["tag"])
        if value["key"] in records and records[value["key"]]["tag"] == value["tag"]:
            del records[value["key"]]
    additions = set()
    for value in add:
        record = _record(value)
        key = record["key"]
        if key in additions:
            raise ValueError("Duplicate added index key.")
        if key in records and records[key]["tag"] != record["tag"]:
            raise ValueError("An index key cannot change its native asset type.")
        additions.add(key)
        records[key] = record
        if len(records) > MAX_INDEX_RECORDS:
            raise ValueError("Too many native index records.")
    updated = sorted(records.values(), key=lambda r: r["key"])
    if updated == originals:
        return data
    payload = _encode_index(updated)
    aux = (section.offset + len(payload) + 4095) & ~4095
    delta = aux - archive.header[6]
    result = bytearray(data[:section.offset])
    result += payload
    result += b"\0" * (aux - len(result))
    result += data[archive.header[6]:]
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("Rebuilt index archive is too large.")
    struct.pack_into("<I", result, 64, len(payload))
    struct.pack_into("<I", result, 76, 0)  # last TOC entry uses the native zero sentinel
    for field in (24, 32, 40, 48, 52):
        struct.pack_into("<I", result, field, archive.header[field // 4] + delta)
    if read_index(result) != updated:
        raise ValueError("Rebuilt index failed its semantic verification.")
    return bytes(result)
