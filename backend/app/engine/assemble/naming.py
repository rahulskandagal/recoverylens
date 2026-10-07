"""Name resolution with provenance: directory metadata > internal metadata > generated."""
from __future__ import annotations

from typing import Any

from ..common import Edge, hexoff
from ..scanner import Fragment
from .base import Ctx, safe_name


def dir_node_id(e: dict[str, Any]) -> str:
    return f"D{e['entry_offset']:06X}"


def resolve_name(ctx: Ctx, cid: str, first: Fragment, ext: str, internal: str | None,
                 internal_src: str) -> tuple[dict[str, Any] | None, str, str, list[Edge]]:
    fat = ctx.fat_entry_for(first.start_cluster)
    edges: list[Edge] = []
    if fat:
        conf = 0.93 * fat["name_confidence"] + 0.05
        ev = [f"Deleted directory entry {fat['short_name']} (at {hexoff(fat['entry_offset'])}) records first cluster "
              f"{fat['first_cluster']}, which maps to this header at {hexoff(first.offset)}",
              f"Long-file-name entries give '{fat['name']}'" +
              (f"; first short-name character '{fat['checksum_first_char_recovered']}' recovered via LFN checksum"
               if fat.get("checksum_first_char_recovered") else "")]
        edges.append(Edge(dir_node_id(fat), cid, "metadata", conf, ev))
        return fat, fat["name"], "FAT directory entry (deleted, long file name)", edges
    if internal:
        return None, safe_name(internal, ext), internal_src, edges
    return None, f"recovered_{first.offset:08X}.{ext}", "generated from source offset (no name evidence)", edges
