"""JPEG reconstruction.

Strategy (baseline JPEG with restart markers, common in camera firmware):
  1. Start from a header fragment (SOI..SOS).
  2. Candidate successors must continue the RST0..RST7 sequence (deterministic filter).
  3. Each candidate is scored by the edge model using, among others, the *pixel seam*
     between MCU rows decoded across the join (a wrong fragment produces a visible tear).
  4. Chain growth stops at EOI, or when no candidate is convincing: the rest is reported
     as missing. Missing rows are never synthesized.
"""
from __future__ import annotations

import math
import re

import numpy as np

from ..common import Candidate, Edge, Segment, fill_gaps, hexoff, split_corrupted
from ..scanner import Fragment
from ..validators import decode_gray, row_seams
from .base import Ctx, edge_from, generic_feats, safe_name
from .naming import resolve_name


ACCEPT = 0.70  # forensic bias: prefer a reported gap over a wrong join; 50-70% joins are surfaced for review
SEAM_GATE = 6.0  # safety net: above every correct join observed in calibration (max 5.1x); the model scores the rest
RST_RE = re.compile(rb"\xff[\xd0-\xd7]")


def _rst_logical(data: bytes, scan_start: int) -> list[tuple[int, int]]:
    """Restart markers in the assembled byte stream (catches markers split across joins)."""
    return [(m.start(), m.group(0)[1] - 0xD0) for m in RST_RE.finditer(data, scan_start)]


def _continues(data: bytes, last_n: int, b_first: bytes, B: Fragment) -> bool:
    """Deterministic restart-marker continuity test across a join (handles FF|Dn straddling)."""
    seq = B.rst_seq()
    want = (last_n + 1) % 8
    if data[-1:] == b"\xff":
        # the join itself must complete the marker: FF | D(want), then B continues with want+1
        if not b_first or b_first[0] != 0xD0 + want:
            return False
        return not seq or seq[0][1] == (want + 1) % 8
    return bool(seq) and seq[0][1] == want


def _seam_score(data: bytes, mh: int, join_row: int) -> tuple[float, float]:
    """Return (smoothness 0..1, ratio) for the rows around the join."""
    g = decode_gray(data + b"\xff\xd9")
    if g is None:
        return 0.0, 99.0
    rows = g.shape[0] // mh
    s = row_seams(g, mh, rows)
    ref = s[1:join_row]
    ref = ref[~np.isnan(ref)]
    base = float(np.median(ref)) if len(ref) >= 2 else float(np.abs(np.diff(g[: join_row * mh or mh], axis=0)).mean() or 1.0)
    base = max(base, 1.0)
    vals = [s[k] for k in (join_row, join_row + 1) if 0 < k < rows and not np.isnan(s[k])]
    if not vals:
        return 0.5, 1.0
    ratio = max(vals) / base
    return 1.0 / (1.0 + max(0.0, ratio - 1.3)), ratio


def _propose(ctx: Ctx, st: dict, taken: set[str]) -> list[tuple[float, Fragment, Edge]]:
    """Scored continuation candidates for one chain (best first). Empty list => chain stops (reason set)."""
    hdr, chain, data, restart = st["hdr"], st["chain"], st["data"], st["restart"]
    eoi = data.rfind(b"\xff\xd9")
    if eoi != -1 and data[eoi + 2:].strip(b"\x00") == b"":
        st["stop_reason"] = "End-of-image marker reached"
        return []
    pool = [f for f in ctx.pool("jpeg") if f.header is None and f not in chain and f.id not in taken]
    if st["size_cap"]:
        pool = [f for f in pool if len(data) + f.length <= st["size_cap"] + 4096 * (f.n_clusters - 1) and len(data) < st["size_cap"]]
    last = chain[-1]
    skipped = False
    if restart:
        rst = _rst_logical(data, hdr.get("scan_start", 0))
        last_n = rst[-1][1] if rst else -1
        cands = [f for f in pool if _continues(data, last_n, ctx.img.frag_bytes(f)[:1], f)]
        if not cands:
            # tolerate ONE destroyed/damaged restart marker at the join (bit-rot); the
            # pixel-seam gate below still has to accept the result
            cands = [f for f in pool if _continues(data, last_n + 1, ctx.img.frag_bytes(f)[:1], f)]
            skipped = bool(cands)
    else:
        cands = [f for f in pool if 0 <= f.start_cluster - last.end_cluster <= 64][:8]
    scored = []
    for B in cands:
        feats = generic_feats(ctx, last, B)
        ratio = 0.0
        if restart:
            rst = _rst_logical(data, hdr.get("scan_start", 0))
            lens = np.diff([p for p, _ in rst]) if len(rst) > 2 else np.array([2000])
            L = float(np.median(lens))
            joined = _rst_logical(data + ctx.img.frag_bytes(B)[:8192], hdr.get("scan_start", 0))
            nxt = joined[len(rst)] if len(joined) > len(rst) else (len(data) + B.length, (last_n + 1) % 8)
            span = nxt[0] - (rst[-1][0] if rst else hdr.get("scan_start", 0))
            z = abs(span - L) / max(0.35 * L, 256)
            feats["struct_order"] = math.exp(-z / 2) * (0.5 if skipped else 1.0)
            smooth, ratio = _seam_score(data + ctx.img.frag_bytes(B), hdr.get("mcu_h", 16), len(rst))
            feats["boundary_smooth"] = smooth
            ev = [(f"Restart marker sequence continues across the join: RST{last_n % 8} -> RST{nxt[1]}" if not skipped else
                   f"One restart marker missing or damaged at the join (RST{last_n % 8} -> RST{nxt[1]}); treated as corruption"),
                  f"Spanning MCU row length {span} B vs typical {int(L)} B",
                  f"Pixel seam across the join is {ratio:.2f}x the typical row boundary"]
        else:
            feats["struct_order"] = 0.5
            g = decode_gray(data + ctx.img.frag_bytes(B))
            feats["boundary_smooth"] = 0.6 if g is not None else 0.1
            ev = ["No restart markers: continuity judged by decodability and proximity only"]
        feats.update(index_verified=0.0, content_sim=0.5, time_consistent=0.5)
        e = edge_from(ctx, last.id, B.id, feats, ev, truth_ctx={"a": last.id, "b": B.id, "chain": [f.id for f in chain]})
        if restart and ratio > SEAM_GATE:
            # hard validation gate: a visible tear across the join is decisive evidence
            e.evidence.append(f"Rejected by seam gate: tear ratio {ratio:.2f} > {SEAM_GATE}")
            e.confidence = min(e.confidence, 0.45)
        scored.append((e.confidence, B, e))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        st["stop_reason"] = "No fragment continues the restart-marker sequence" if restart else "No adjacent decodable continuation"
    return scored


def assemble_jpeg(ctx: Ctx) -> None:
    """All header chains grow together: each round the globally best (chain, fragment) join is accepted.

    Growing chains one after another is order-dependent: an image whose own tail was overwritten could
    take the continuation that belongs to another photo before that photo is processed. Letting chains
    compete for each contested fragment gives it to the chain whose evidence is strongest.
    """
    headers = [f for f in ctx.scan.fragments if f.header and f.header.get("type") == "jpeg" and ctx.free(f)]
    states: list[dict] = []
    for H in headers:
        hdr = H.header or {}
        fat0 = ctx.fat_entry_for(H.start_cluster)
        st = {"H": H, "hdr": hdr, "cid": ctx.next_id(), "chain": [H], "edges": [], "data": ctx.img.frag_bytes(H),
              "restart": bool(hdr.get("restart_interval")) and not hdr.get("progressive"),
              "size_cap": fat0["size"] + 4096 if fat0 else None,  # file-size constraint from directory metadata
              "stop_reason": "", "active": "width" in hdr, "cache": None}
        if "width" not in hdr:
            st["stop_reason"] = "Header lacks a frame (SOF) segment; image geometry unknown"
        states.append(st)
    taken: set[str] = set()
    while True:
        best = None
        for st in states:
            if not st["active"]:
                continue
            if st["cache"] is None:
                st["cache"] = _propose(ctx, st, taken)
            st["cache"] = [t for t in st["cache"] if t[1].id not in taken]
            if not st["cache"]:
                if not st["stop_reason"]:
                    st["stop_reason"] = "Every continuation candidate was assigned to another image with stronger evidence"
                st["active"] = False
                continue
            p = st["cache"][0][0]
            if p < ACCEPT:
                _, B, e = st["cache"][0]
                e.chosen = False
                st["edges"].append(e)
                st["stop_reason"] = f"Best continuation candidate {B.id} scored only {p:.0%}; not accepted"
                st["active"] = False
                continue
            if best is None or p > best[0]:
                best = (p, st)
        if best is None:
            break
        _, st = best
        p, B, e = st["cache"][0]
        for q, Bq, eq in st["cache"][1:3]:
            eq.chosen = False
            st["edges"].append(eq)
        for other in states:  # record the contest as evidence on every chain that also wanted B
            if other is not st and other["active"] and other["cache"] and other["cache"][0][1] is B:
                other["cache"][0][2].evidence.append(
                    f"{B.id} was also the best candidate for image {st['H'].id}, which scored higher ({p:.0%} vs {other['cache'][0][0]:.0%})")
        st["edges"].append(e)
        st["chain"].append(B)
        st["data"] += ctx.img.frag_bytes(B)
        st["cache"] = None
        taken.add(B.id)
    for st in states:
        _finish_jpeg(ctx, st)


def _finish_jpeg(ctx: Ctx, st: dict) -> None:
    H, hdr, cid, chain, edges, data, restart, stop_reason = (st["H"], st["hdr"], st["cid"], st["chain"], st["edges"],
                                                             st["data"], st["restart"], st["stop_reason"])
    ctx.claim(cid, chain)
    # ---- build logical map ------------------------------------------------------
    segs: list[Segment] = []
    pos = 0
    for f in chain:
        segs.append(Segment(pos, f.length, "recovered", f.id, f.offset))
        pos += f.length
    eoi = data.rfind(b"\xff\xd9")
    complete = eoi != -1 and data[eoi + 2:].strip(b"\x00") == b""
    rst = _rst_logical(data, hdr.get("scan_start", 0))
    rows_total = hdr.get("mcu_rows")
    rows_have = min(rows_total or 0, len(rst) + (1 if complete else 0)) if restart else None
    fat, name, name_src, meta_edges = resolve_name(ctx, cid, H, "jpg",
                                                   (hdr.get("exif") or {}).get("ImageDescription"), "EXIF ImageDescription")
    exp_src = ""
    if complete:
        expected = eoi + 2
        exp_src = "EOI marker position"
        data = data[:expected]
    elif fat and fat["size"] > len(data.rstrip(b"\x00")) // 2:
        expected = fat["size"]
        exp_src = "size field of linked directory entry"
    elif restart and rows_total and rst:
        per_row = rst[-1][0] / max(1, len(rst))
        expected = int(hdr.get("scan_start", 0) + per_row * rows_total)
        exp_src = "estimated from average MCU-row size x total rows (estimate)"
    else:
        expected = None
    if not complete and rst:
        # bytes after the last RST belong to an incomplete row: keep them but trim slack
        data = data.rstrip(b"\x00") if len(chain) == 1 else data
    segs = fill_gaps(segs, max(expected or 0, sum(s.length for s in segs)))
    if expected and expected < sum(f.length for f in chain):
        segs = [s for s in segs if s.start < expected]
        if segs:
            last_s = segs[-1]
            last_s.length = min(last_s.length, expected - last_s.start)
    # deterministic corruption: illegal marker bytes (logical offsets)
    bad = []
    base = 0
    for f in chain:
        for i, c in enumerate(f.clusters):
            for v in c.jpeg_violations:
                lo = base + i * 4096 + v
                bad.append((lo, lo + 2, "Illegal marker sequence in entropy-coded data (byte corruption)"))
        base += f.length
    segs = split_corrupted(segs, bad)
    cand = Candidate(
        id=cid, type="jpeg", name=name, name_source=name_src, fragments=[f.id for f in chain],
        segments=segs, expected_size=expected, expected_size_source=exp_src, data=data,
        edges=edges + meta_edges,
        structure={"header": {k: v for k, v in hdr.items() if k != "markers"}, "markers": hdr.get("markers", []),
                   "restart_mode": restart, "rows_total": rows_total, "rows_recovered": rows_have,
                   "stop_reason": stop_reason, "complete": complete},
        metadata={"exif": hdr.get("exif", {}), "dimensions": f"{hdr.get('width')}x{hdr.get('height')}"},
        operations=[f"Header {H.id} @ {hexoff(H.offset)} parsed (SOF/DQT/DHT/SOS)",
                    f"Chain grown by restart-marker continuity + pixel-seam scoring: {' -> '.join(f.id for f in chain)}",
                    f"Chain terminated: {stop_reason}"],
    )
    if fat:
        cand.metadata["directory_entry"] = fat
    ctx.candidates.append(cand)
    ctx.log("reconstruct", f"JPEG {name}: {len(chain)} fragment(s), {'complete' if complete else 'incomplete'} ({stop_reason})")
