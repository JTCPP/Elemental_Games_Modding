"""Explicit RDMS/PBR bindings in Azurik's named graphics libraries.

The 72-byte type-5 descriptor is also used by fx.xbr and characters.xbr.
Their first auxiliary array is empty: its relative-pointer field contains
the value 1, which is not a pointer to dereference. A nonempty bounds array
at +48/+52 instead establishes the end of the descriptor header table.
The render pairs are still the explicit 16-byte records at +64/+68.

This reader never scans neighbouring resources or invents triangle links.
The existing level reader remains authoritative for a populated first
array. The extension accepts only the observed empty-array layout and
validates every nonempty table, its extent, and its resource tags.
"""
from __future__ import annotations

import math
import struct

from renderer_parser import Section, read_platform_bindings


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _relative(data: bytes, offset: int) -> int:
    return offset + struct.unpack_from("<i", data, offset)[0]


def read_library_platform_bindings(data: bytes, node_section: Section,
                                   record_offset: int,
                                   sections: list[Section]) -> list[dict]:
    """Read level descriptors and the verified empty-first-array variant.

    Return the same binding dictionaries as ``read_platform_bindings``.
    Empty arrays' pointer fields are intentionally ignored. An unknown
    nonempty auxiliary array, mixed variant, or ambiguous table boundary
    raises ValueError instead of guessing its layout.
    """
    base, end = node_section.offset, node_section.offset + node_section.size
    if node_section.tag != "node" or not 0 <= base <= end <= len(data):
        raise ValueError("section node de bibliothèque non valide")
    if not base <= record_offset <= end - 36:
        raise ValueError("nœud de rendu hors de sa section")
    count = _u32(data, record_offset + 28)
    if count == 0:
        return []
    if count > 1024:
        raise ValueError("nombre de descripteurs de rendu non valide")
    target = _relative(data, record_offset + 32)
    header_end = target + count * 72
    if not base <= target <= header_end <= end:
        raise ValueError("enveloppe de rendu hors de sa section")
    if _u32(data, target + 32):
        return read_platform_bindings(data, node_section, record_offset, sections)

    tables: list[tuple[int, int]] = []
    bindings: list[dict] = []
    for descriptor_index in range(count):
        descriptor = target + descriptor_index * 72
        if _u32(data, descriptor + 28) != 5:
            raise ValueError("descripteur de rendu de bibliothèque non décodé")
        if _u32(data, descriptor + 32) != 0:
            raise ValueError("variante mixte de tableaux auxiliaires non validée")
        # The two scalar fields are 0 and 1 in every measured named-library
        # descriptor. Their semantics are not inferred by this reader.
        if struct.unpack_from("<II", data, descriptor + 40) != (0, 1):
            raise ValueError("paramètres du descripteur de bibliothèque non validés")
        if _u32(data, descriptor + 56) != 0:
            raise ValueError("tableau auxiliaire de bibliothèque non décodé")

        bounds_count = _u32(data, descriptor + 48)
        if not 0 < bounds_count <= 20000:
            raise ValueError("nombre de bornes de bibliothèque non valide")
        bounds_start = _relative(data, descriptor + 52)
        bounds_end = bounds_start + bounds_count * 24
        if not header_end <= bounds_start < bounds_end <= end:
            raise ValueError("table de bornes de bibliothèque tronquée")
        tables.append((bounds_start, bounds_end))
        for offset in range(bounds_start, bounds_end, 24):
            if not all(math.isfinite(value) for value in struct.unpack_from("<6f", data, offset)):
                raise ValueError("bornes de bibliothèque non finies")

        pair_count = _u32(data, descriptor + 64)
        if pair_count > 20000:
            raise ValueError("nombre de liaisons de bibliothèque non valide")
        if pair_count == 0:
            continue
        pair_start = _relative(data, descriptor + 68)
        pair_end = pair_start + pair_count * 16
        if not header_end <= pair_start < pair_end <= end:
            raise ValueError("table de liaisons de rendu tronquée")
        tables.append((pair_start, pair_end))
        for pair_index in range(pair_count):
            offset = pair_start + pair_index * 16
            mesh, primitive, tag, distance = struct.unpack_from("<II4sf", data, offset)
            if (mesh >= len(sections) or sections[mesh].index != mesh or sections[mesh].tag != "rdms"
                    or primitive >= len(sections) or sections[primitive].index != primitive
                    or tag not in (b"pbrc", b"pbrw")
                    or sections[primitive].tag.encode("ascii") != tag
                    or not math.isfinite(distance)):
                raise ValueError("liaison de rendu ne référence pas ses ressources")
            bindings.append({"meshResource": mesh, "primitiveResource": primitive,
                             "primitiveTag": tag.decode("ascii"), "maxDistance": distance,
                             "bindingOffset": offset, "descriptorIndex": descriptor_index})

    tables.sort()
    if not tables or tables[0][0] != header_end:
        raise ValueError("limite des descripteurs de bibliothèque non validée")
    if any(previous_end > start for (_, previous_end), (start, _) in zip(tables, tables[1:])):
        raise ValueError("tables de bibliothèque superposées")
    return bindings
