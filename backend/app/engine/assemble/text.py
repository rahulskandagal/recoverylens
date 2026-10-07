"""Log and plain-text reconstruction.

Text formats have no container index or checksum, so chaining relies on softer evidence
(timestamp order, line joins, vocabulary, proximity) scored by the edge model. The results
are reported as inferred; completeness cannot be estimated without a size reference.
"""
from __future__ import annotations

import math
import re
import statistics
from datetime import datetime

from ..common import TS_RE, Candidate, Edge, Segment, fill_gaps, hexoff
from ..scanner import Fragment
from .base import Ctx, edge_from, generic_feats, token_sim
from .naming import resolve_name

LINE_TS = re.compile(rb"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("T", " "))


def _interval(fb: bytes) -> float:
    ts = [_ts(m.decode()) for m in TS_RE.findall(fb)]
    d = [(b - a).total_seconds() for a, b in zip(ts, ts[1:]) if b >= a]
    return statistics.median(d) if d else 60.0


def _log_feats(ctx: Ctx, a: Fragment, b: Fragment) -> tuple[dict[str, float], list[str]]:
    fa, fb = ctx.img.frag_bytes(a).rstrip(b"\x00"), ctx.img.frag_bytes(b)
    feats = generic_feats(ctx, a, b)
    ev = []
    ta, tb = a.timestamps()[1], b.timestamps()[0]
    if ta and tb:
        gap = (_ts(tb) - _ts(ta)).total_seconds()
        med = _interval(fa)
        if gap < 0:
            feats["time_consistent"] = 0.0
            ev.append(f"Time goes backwards across the join ({ta} -> {tb})")
        else:
            feats["time_consistent"] = math.exp(-gap / (8 * med + 1))
            ev.append(f"Timestamps ordered: {ta} -> {tb} (gap {int(gap)} s; typical line interval {int(med)} s)")
    else:
        feats["time_consistent"] = 0.5
    a_mid = not fa.endswith(b"\n")
    first_line = fb.split(b"\n", 1)[0]
    b_mid = not LINE_TS.match(first_line)
    if a_mid and b_mid:
        joined = fa.rsplit(b"\n", 1)[-1] + first_line
        feats["struct_order"] = 1.0 if LINE_TS.match(joined) else 0.3
        ev.append("Split line re-joins into a well-formed log line" if feats["struct_order"] == 1.0
                  else "Split line does not re-join into a well-formed line")
    elif not a_mid and not b_mid:
        feats["struct_order"] = 0.7
        ev.append("Join falls on a line boundary")
    else:
        feats["struct_order"] = 0.1
        ev.append("Line boundary mismatch at the join")
    feats["content_sim"] = token_sim(fa[-4096:], fb[:4096])
    feats.update(index_verified=0.0, boundary_smooth=feats["struct_order"])
    return feats, ev


def _text_feats(ctx: Ctx, a: Fragment, b: Fragment) -> tuple[dict[str, float], list[str]]:
    fa, fb = ctx.img.frag_bytes(a).rstrip(b"\x00"), ctx.img.frag_bytes(b)
    feats = generic_feats(ctx, a, b)
    a_mid = bool(fa) and fa[-1:] not in (b"\n", b".")
    b_mid = bool(fb) and not fb[:1].isupper()
    feats.update(struct_order=0.6 if a_mid == b_mid else 0.3, index_verified=0.0, boundary_smooth=0.5,
                 content_sim=token_sim(fa[-4096:], fb[:4096]), time_consistent=0.5)
    return feats, ["Plain text: continuity judged by line/sentence joins, vocabulary and proximity only"]


def _looks_like_doc_start(b: bytes) -> bool:
    lines = b.split(b"\n", 2)
    return bool(lines[0][:1].isupper()) and len(lines[0]) <= 80 and len(lines) > 1 and \
        (lines[1].strip() == b"" or set(lines[1].strip()) <= set(b"=-#*"))


def _chain(ctx: Ctx, frags: list[Fragment], featfn, starts: list[Fragment], threshold: float) -> list[tuple[list[Fragment], list[Edge]]]:
    scored: list[Edge] = []
    for a in frags:
        for b in frags:
            if a is b or b in starts and b is not a and featfn is _text_feats:
                continue
            feats, ev = featfn(ctx, a, b)
            if feats.get("time_consistent", 0.5) == 0.0:
                continue
            scored.append(edge_from(ctx, a.id, b.id, feats, ev))
    scored.sort(key=lambda e: -e.confidence)
    nxt: dict[str, Edge] = {}
    prv: dict[str, str] = {}
    for e in scored:
        if e.confidence < threshold or e.src in nxt or e.dst in prv:
            continue
        # avoid cycles
        x = e.dst
        cyc = False
        while x in nxt:
            x = nxt[x].dst
            if x == e.src:
                cyc = True
                break
        if cyc:
            continue
        nxt[e.src] = e
        prv[e.dst] = e.src
    byid = {f.id: f for f in frags}
    out = []
    heads = [f for f in frags if f.id not in prv and (not starts or f in starts)]
    for h in heads:
        chain, edges = [h], []
        while chain[-1].id in nxt:
            e = nxt[chain[-1].id]
            chain.append(byid[e.dst])
            edges.append(e)
        alts = [e for e in scored if e.src in {c.id for c in chain} and e not in edges and e.confidence >= 0.35][:3]
        for e in alts:
            e.chosen = False
        out.append((chain, edges + alts))
    return out


def _emit(ctx: Ctx, chain: list[Fragment], edges: list[Edge], ftype: str) -> None:
    fat0 = ctx.fat_entry_for(chain[0].start_cluster)
    if fat0:  # file-size constraint: a chain may not grow beyond the recorded size
        keep, total = [], 0
        for f in chain:
            if total >= fat0["size"]:
                break
            keep.append(f)
            total += f.length
        ids = {f.id for f in keep}
        edges = [e for e in edges if e.src in ids and (e.dst in ids or not e.chosen)]
        chain = keep
    cid = ctx.next_id()
    data = b"".join(ctx.img.frag_bytes(f) for f in chain).rstrip(b"\x00")
    segs, pos = [], 0
    for f in chain:
        n = min(f.length, len(data) - pos)
        if n > 0:
            segs.append(Segment(pos, n, "recovered", f.id, f.offset, "inferred order"))
        pos += f.length
    fat, name, src, medges = resolve_name(ctx, cid, chain[0], ftype, None, "")
    exp = fat["size"] if fat else None
    if exp and exp > len(data):
        segs = fill_gaps(segs, exp)
    ctx.claim(cid, chain)
    t0, t1 = chain[0].timestamps()[0], chain[-1].timestamps()[1]
    cand = Candidate(
        id=cid, type=ftype, name=name, name_source=src, fragments=[f.id for f in chain], segments=segs,
        expected_size=exp, expected_size_source="size field of linked directory entry" if exp else
        "unknown (text formats carry no size or index)", data=data, edges=edges + medges,
        structure={"order_method": "edge-model chain (inferred)", "time_range": [t0, t1] if t0 else None},
        metadata={"time_range": [t0, t1] if t0 else None}, operations=[
            f"Fragments ordered by inferred continuity: {' -> '.join(f.id for f in chain)}"],
        remaining_compatible=len(ctx.pool("log" if ftype == "log" else "text")),
    )
    if fat:
        cand.metadata["directory_entry"] = fat
    ctx.candidates.append(cand)
    ctx.log("reconstruct", f"{ftype.upper()} {name}: {len(chain)} fragment(s) chained ({hexoff(chain[0].offset)})")


def assemble_text(ctx: Ctx) -> None:
    logs = ctx.pool("log")
    if logs:
        for chain, edges in _chain(ctx, logs, _log_feats, [], 0.5):
            _emit(ctx, chain, edges, "log")
    texts = ctx.pool("text")
    starts = [f for f in texts if _looks_like_doc_start(ctx.img.frag_bytes(f))]
    # plain text: only documents with a recognisable beginning become candidates; the rest
    # stays in the fragment explorer as unclassified text remnants
    if starts:
        near = [f for f in texts if f in starts or any(0 <= f.start_cluster - s.end_cluster <= 256 for s in starts)]
        for chain, edges in _chain(ctx, near, _text_feats, starts, 0.75):
            _emit(ctx, chain, edges, "txt")
