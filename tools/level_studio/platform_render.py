"""Verified initial platform opacity and render-distance bias.

Retail FE240 / table 197BF0 declares 37 platform channels. gameVisible
is channel 10; MeshOffset is channel 24. 79510 -> C5F20 -> C3BBC and
D6D3B multiply gameVisible into the material opacity. MeshOffset follows
7948B -> 78FC0 -> C1FA0: it biases sorting distance, never vertex positions.
Keep this instance state separate from the shared GSHD material.
"""
from __future__ import annotations

import math
import struct


def read_platform_render_state(data, node, sections):
    if node.get("type") != "platform":
        return None
    record = node.get("recordOffset")
    if isinstance(record, bool) or not isinstance(record, int):
        raise ValueError("Offset de plateforme absent.")
    owners = [section for section in sections if section.tag == "node"
              and section.offset <= record <= section.offset + section.size - 28]
    if len(owners) != 1:
        raise ValueError("Paramètres de plateforme hors de leur graphe.")
    section = owners[0]
    count = struct.unpack_from("<I", data, record + 8)[0]
    # Other serialized layouts remain undecoded rather than guessing that
    # a short parameter table carries the same render channels.
    if count != 37:
        return None
    field = record + 12
    start = field + struct.unpack_from("<i", data, field)[0]
    if not section.offset <= start <= section.offset + section.size - count * 4:
        raise ValueError("Table de paramètres de plateforme hors limites.")
    visibility, game_visible, mesh_offset = [struct.unpack_from("<f", data, start + index * 4)[0]
                                           for index in (0, 10, 24)]
    if not all(math.isfinite(value) for value in (visibility, game_visible, mesh_offset)):
        raise ValueError("Paramètres de rendu de plateforme non finis.")
    if not 0 <= visibility <= 1 or not 0 <= game_visible <= 1:
        raise ValueError("Opacité de plateforme hors de l’intervalle 0..1.")
    return {"visibility": visibility, "gameVisible": game_visible, "meshOffset": mesh_offset,
            "sourceVerified": True, "visibilityOffset": start,
            "gameVisibleOffset": start + 40, "meshOffsetOffset": start + 96}
