"""Orphan fragments: structured data that could not be attached to any file header.

Orphans are reported, never silently dropped. JPEG scan data without its header cannot be
decoded on its own. If other JPEG headers exist on the same medium, their decoding tables
can be *borrowed* to render a preview. That preview is explicitly labelled unverified,
because colours and geometry may be wrong.
"""
from __future__ import annotations

import re

from ..common import CLUSTER, Candidate, Edge, Segment, fill_gaps, hexoff
from ..scanner import Fragment
from ..validators import pdf_text
from .base import Ctx, edge_from, generic_feats

EXT_TYPE = {"jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf", "docx": "docx", "db": "sqlite", "log": "log",
            "txt": "txt", "bin": None}


def _unmatched_dir_entries(ctx: Ctx) -> list[dict]:
    starts = set()
    for c in ctx.candidates:
        if c.metadata.get("directory_entry"):
            starts.add(c.metadata["directory_entry"]["entry_offset"])
    return [e for e in ctx.scan.dir_entries if e["entry_offset"] not in starts]


def _link_dir(ctx: Ctx, cid: str, group: list[Fragment], ftype: str) -> tuple[dict | None, list[Edge]]:
    """Weak metadata link: an unmatched directory entry whose start cluster was overwritten
    and whose extent (first cluster + size) plausibly covers this orphan."""
    best, edges = None, []
    for e in _unmatched_dir_entries(ctx):
        ext = e["name"].rsplit(".", 1)[-1].lower()
        if EXT_TYPE.get(ext, "x") not in (ftype, None):
            continue
        span = -(-e["size"] // CLUSTER)
        d = abs(group[0].start_cluster - e["image_cluster"])
        if 0 < d <= span * 40:
            first_cls = ctx.scan.clusters[e["image_cluster"]].cls if 0 <= e["image_cluster"] < len(ctx.scan.clusters) else "?"
            feats = {"type_compat": 1.0 if EXT_TYPE.get(ext) == ftype else 0.5, "struct_order": 0.4, "index_verified": 0.0,
                     "boundary_smooth": 0.5, "entropy_sim": 0.5, "hist_sim": 0.5,
                     "proximity": max(0.0, 1 - d / (span * 40)), "content_sim": 0.5, "time_consistent": 0.5}
            ed = edge_from(ctx, f"D{e['entry_offset']:06X}", cid, feats, [
                f"Deleted entry '{e['name']}' points to cluster {e['image_cluster']}, which now holds '{first_cls}' data: its start was overwritten",
                f"This orphan lies {d} clusters from that start, within a plausible extent for a {e['size']}-byte file",
                "Proximity is weak evidence: the link is a possibility, not a finding"], "metadata")
            ed.confidence = min(ed.confidence, 0.55)
            if not best or ed.confidence > best[1].confidence:
                best = (e, ed)
    if best:
        edges.append(best[1])
        return best[0], edges
    return None, edges


def _transplant_preview(ctx: Ctx, data: bytes) -> tuple[bytes | None, str | None]:
    donors = [c for c in ctx.candidates if c.type == "jpeg" and c.header_found and c.structure.get("restart_mode")]
    m = re.search(rb"\xff[\xd0-\xd7]", data)
    if not donors or not m:
        return None, None
    d = donors[0]
    hdr = d.structure["header"]
    body = data[m.end():]
    eoi = body.find(b"\xff\xd9")
    body = body[:eoi] if eoi != -1 else body.rstrip(b"\x00")
    return d.data[:hdr["scan_start"]] + body + b"\xff\xd9", d.name


def assemble_orphans(ctx: Ctx) -> None:
    # ---- orphan JPEG scan data: group by restart-marker continuity -------------------------
    pool = [f for f in ctx.pool("jpeg") if f.header is None]
    pool.sort(key=lambda f: f.start_cluster)
    used: set[str] = set()
    groups: list[tuple[list[Fragment], list[Edge]]] = []
    for f in pool:
        if f.id in used:
            continue
        chain, edges = [f], []
        used.add(f.id)
        while True:
            last = chain[-1]
            seq = last.rst_seq()
            if not seq:
                break
            want = (seq[-1][1] + 1) % 8
            best = None
            for g in pool:
                if g.id in used or not g.rst_seq() or g.rst_seq()[0][1] != want:
                    continue
                feats = generic_feats(ctx, last, g)
                feats.update(struct_order=0.5, index_verified=0.0, boundary_smooth=0.5, content_sim=0.5, time_consistent=0.5)
                e = edge_from(ctx, last.id, g.id, feats, [f"Restart markers continue RST{seq[-1][1]} -> RST{want}",
                                                          "No header available: pixel-seam check impossible"])
                if not best or e.confidence > best.confidence:
                    best = e
            if not best or best.confidence < 0.8:  # RST continuity alone matches 1 in 8 by chance
                break
            nxt = next(g for g in pool if g.id == best.dst)
            chain.append(nxt)
            edges.append(best)
            used.add(nxt.id)
        groups.append((chain, edges))
    for chain, edges in groups:
        _emit(ctx, chain, edges, "jpeg")
    # ---- orphan PDF text fragments ---------------------------------------------------------------
    pdfs = ctx.pool("pdf")
    for f in pdfs:
        _emit(ctx, [f], [], "pdf")
    # ---- orphan SQLite pages -----------------------------------------------------------------------
    sq = ctx.pool("sqlite")
    if sq:
        _emit(ctx, sq, [], "sqlite")


def _emit(ctx: Ctx, chain: list[Fragment], edges: list[Edge], ftype: str) -> None:
    cid = ctx.next_id()
    data = b"".join(ctx.img.frag_bytes(f) for f in chain)
    segs, pos = [], 0
    for f in chain:
        segs.append(Segment(pos, f.length, "recovered", f.id, f.offset, "orphan"))
        pos += f.length
    fat, medges = _link_dir(ctx, cid, chain, ftype)
    # an orphan's logical position inside its original file is unknown, so no missing
    # ranges are asserted; a weakly linked size only bounds the completeness estimate
    exp = fat["size"] if fat else None
    ext = {"jpeg": "bin", "pdf": "pdf.fragment", "sqlite": "db.fragment"}[ftype]
    name = f"unknown_fragment_{chain[0].offset:08X}.{ext}"
    structure: dict = {"orphan": True, "reason": {
        "jpeg": "JPEG entropy-coded data without SOI/DQT/DHT/SOF/SOS header; cannot be decoded with its own tables",
        "pdf": "PDF body text that could not be placed: no matching header/xref recovered",
        "sqlite": "SQLite pages whose database header or numbering evidence was not recovered"}[ftype]}
    preview_src = None
    if ftype == "jpeg":
        prev, donor = _transplant_preview(ctx, data)
        structure["rst_markers"] = sum(len(f.rst_seq()) for f in chain)
        if prev:
            structure["transplant_donor"] = donor
            structure["transplant_bytes"] = prev
            preview_src = donor
    if ftype == "pdf":
        structure["text"] = pdf_text(data)[:2000]
    ctx.claim(cid, chain)
    cand = Candidate(
        id=cid, type=ftype, name=name, name_source="generated (orphan fragment, no name evidence)" if not fat else
        f"generated; possible match to deleted entry '{fat['name']}' (weak)", fragments=[f.id for f in chain],
        segments=segs, expected_size=exp, expected_size_source="size field of weakly-linked directory entry" if exp else "unknown",
        data=data, edges=edges + medges, structure=structure, orphan=True, header_found=False,
        metadata={"possible_directory_entry": fat} if fat else {},
        operations=[f"Orphan {ftype} fragment(s) {', '.join(f.id for f in chain)} grouped"] +
                   ([f"Preview rendered with decoding tables borrowed from {preview_src} (UNVERIFIED)"] if preview_src else []),
        remaining_compatible=len(ctx.pool("jpeg")) if ftype == "jpeg" else 0,
    )
    ctx.candidates.append(cand)
    ctx.log("reconstruct", f"Orphan {ftype} group {name}: {len(chain)} fragment(s)" + (f", possible link to {fat['name']}" if fat else ""))
