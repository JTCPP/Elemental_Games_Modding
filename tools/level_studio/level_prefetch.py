"""Update the game's native text prefetch tags for custom level archives.

PAL 0xB0520 reads ``prefetch-lists.txt`` using tag=/file=/neighbor= commands.
Level loading selects the final segment of a level key (0x53897..0x538AF).
A missing tag is optional, but an existing file command must not refer to
an archive removed from the ISO.  Keep deleted tag names for old level-key
aliases and redirect their file dependencies to the retained replacement.

This parser accepts human indentation and comments, then emits native-safe
commands: the original game does not skip nonempty comment lines.  All
unrelated command values and their ordering are preserved.  File I/O and
checking that replacement archives exist belong to the ISO builder.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping


MAX_PREFETCH_BYTES = 1024 * 1024
MAX_OPERATIONS = 1024
MAX_TAGS = 4096
MAX_LINE_BYTES = 259  # the native reader owns a 0x104-byte line buffer
MAX_LEVEL_ID_BYTES = 63  # native tag constructor 0xAF7E1 copies at most 0x3F
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9_.%-]+\Z")
_COMMAND = re.compile(r"([A-Za-z]+)\s*=\s*(.*?)\s*\Z")
_COMMENT = re.compile(r"\s+(?:[#;]|//)")
_RESERVED = {"always", "default", "con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


@dataclass
class _Tag:
    name: str
    commands: list[tuple[str, str]]


def _bytes(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("Prefetch data must be bytes.")
    if len(data) > MAX_PREFETCH_BYTES:
        raise ValueError("Prefetch manifest exceeds its size limit.")
    return bytes(data)


def _id(value: str, label: str = "level ID", *, reserved: bool = False) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"Invalid {label}: use 1-63 ASCII letters, digits, underscores or hyphens.")
    if not reserved and value.lower() in _RESERVED:
        raise ValueError(f"Reserved {label}.")
    return value


def _file(value: str) -> str:
    if not value or len(value.encode("ascii")) + 5 > MAX_LINE_BYTES:
        raise ValueError("Invalid prefetch filename length.")
    components = re.split(r"[/\\]", value)
    for component in components:
        if (not _COMPONENT.fullmatch(component) or component in (".", "..")
                or component.split(".")[0].lower() in _RESERVED - {"always", "default"}):
            raise ValueError("Prefetch filenames must be safe relative paths.")
        if "%" in component and component != "%LANGUAGE%.xbr":
            raise ValueError("Unknown prefetch filename substitution.")
    return value


def _parse(data: bytes) -> list[_Tag]:
    try:
        source = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Prefetch manifest must use ASCII.") from exc
    if any(ord(c) < 32 and c not in "\t\r\n" for c in source) or "\x7f" in source:
        raise ValueError("Invalid control character in prefetch manifest.")
    tags = []
    names = set()
    current = None
    for raw in source.splitlines():
        if len(raw) > MAX_LINE_BYTES:
            raise ValueError("Prefetch line exceeds the native reader's size limit.")
        line = raw.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        comment = _COMMENT.search(line)
        if comment:
            line = line[:comment.start()].rstrip()
        match = _COMMAND.fullmatch(line)
        if match is None:
            raise ValueError("Unknown or malformed native prefetch command.")
        command, value = match.group(1).lower(), match.group(2)
        if command == "tag":
            _id(value, "prefetch tag", reserved=True)
            if value.lower() in names:
                raise ValueError("Duplicate or ambiguous native prefetch tag.")
            names.add(value.lower())
            current = _Tag(value, [])
            tags.append(current)
            if len(tags) > MAX_TAGS:
                raise ValueError("Too many native prefetch tags.")
        elif command in ("file", "neighbor"):
            if current is None:
                raise ValueError("Native prefetch command appears before a tag.")
            if command == "file":
                _file(value)
            else:
                _id(value, "neighbor tag", reserved=True)
            current.commands.append((command, value))
        else:
            raise ValueError("Unknown native prefetch command.")
    return tags


def read_prefetch(data: bytes) -> list[dict]:
    """Return native tags, file dependencies and neighbor tags in source order."""
    return [{"tag": tag.name,
             "files": [v for k, v in tag.commands if k == "file"],
             "neighbors": [v for k, v in tag.commands if k == "neighbor"]}
            for tag in _parse(_bytes(data))]


def _operations(values: Iterable[Mapping], field: str) -> list[dict]:
    if isinstance(values, (str, bytes, Mapping)):
        raise ValueError("Level operations must be an iterable of mappings.")
    result, seen = [], set()
    for value in values:
        if len(result) >= MAX_OPERATIONS:
            raise ValueError("Too many native prefetch level operations.")
        if not isinstance(value, Mapping) or "id" not in value or field not in value:
            raise ValueError(f"Level operation requires id and {field}.")
        level_id = _id(value["id"])
        target = _id(value[field], field)
        if level_id.lower() in seen or level_id.lower() == target.lower():
            raise ValueError("Duplicate or self-referencing native prefetch level operation.")
        seen.add(level_id.lower())
        result.append({"id": level_id, field: target})
    return result


def _stem(path: str) -> str | None:
    basename = re.split(r"[/\\]", path)[-1]
    return basename[:-4].lower() if basename.lower().endswith(".xbr") else None


def _replace_file(path: str, source: str, target: str) -> str:
    if _stem(path) != source.lower():
        return path
    start = max(path.rfind("/"), path.rfind("\\")) + 1
    return path[:start] + target + ".xbr"


def _serialize(tags: list[_Tag], newline: str) -> bytes:
    if len(tags) > MAX_TAGS:
        raise ValueError("Too many native prefetch tags.")
    blocks = []
    for tag in tags:
        lines = ["tag=" + tag.name] + [command + "=" + value for command, value in tag.commands]
        if any(len(line) > MAX_LINE_BYTES for line in lines):
            raise ValueError("Generated prefetch line exceeds the native reader's size limit.")
        blocks.append(newline.join(lines))
    result = ((newline + newline).join(blocks) + (newline if blocks else "")).encode("ascii")
    if len(result) > MAX_PREFETCH_BYTES:
        raise ValueError("Generated prefetch manifest exceeds its size limit.")
    return result


def update_prefetch(data: bytes, created: Iterable[Mapping] = (),
                    deleted: Iterable[Mapping] = ()) -> bytes:
    """Clone template tags and redirect file references for removed archives.

    ``created`` rows contain ``id`` and the template archive's basename,
    ``template``.  ``deleted`` rows contain ``id`` and ``replacement``.
    Replacement chains resolve to their retained final destination; cycles
    are rejected.  The original tag name remains usable for level aliases.

    Templates without a dedicated tag get a direct file-only custom tag:
    absence of an optional native prefetch tag does not prevent level loading.
    This function does not modify the source dump, index or config archive.
    """
    data = _bytes(data)
    additions = _operations(created, "template")
    removals = _operations(deleted, "replacement")
    if not additions and not removals:
        return data
    tags = _parse(data)
    by_name = {tag.name.lower(): tag for tag in tags}
    pending = {row["id"].lower(): row for row in additions}
    while pending:
        progressed = False
        for key, row in list(pending.items()):
            template = row["template"].lower()
            if template in pending:
                continue
            original = by_name.get(template)
            commands = ([(command, _replace_file(value, template, row["id"]))
                         if command == "file" else (command, value)
                         for command, value in original.commands] if original else [])
            # A custom archive must be included even if its template's tag
            # exists only as an empty/neighbor-only preload group.
            if not any(command == "file" and _stem(value) == key for command, value in commands):
                commands.insert(0, ("file", row["id"] + ".xbr"))
            new = _Tag(row["id"], commands)
            if key in by_name:
                if by_name[key].commands != new.commands:
                    raise ValueError("Custom level ID conflicts with an existing native prefetch tag.")
            else:
                tags.append(new)
                by_name[key] = new
            del pending[key]
            progressed = True
        if not progressed:
            raise ValueError("Cyclic custom level template dependencies.")
    redirects = {row["id"].lower(): row["replacement"] for row in removals}
    resolved = {}
    for key in redirects:
        seen = {key}
        value = redirects[key]
        while value.lower() in redirects:
            if value.lower() in seen:
                raise ValueError("Cyclic deleted level replacements.")
            seen.add(value.lower())
            value = redirects[value.lower()]
        resolved[key] = value
    for tag in tags:
        commands = []
        for command, value in tag.commands:
            basename = _stem(value) if command == "file" else None
            if basename in resolved:
                value = _replace_file(value, basename, resolved[basename])
            commands.append((command, value))
        tag.commands = commands
    newline = "\r\n" if b"\r\n" in data else "\n"
    result = _serialize(tags, newline)
    # Decode the generated text as a final native-format/size check.
    _parse(result)
    return result
