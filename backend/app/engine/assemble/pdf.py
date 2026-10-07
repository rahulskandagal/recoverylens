"""PDF reconstruction anchored on the cross-reference (xref) table.

The xref table lists the exact byte offset of every object. A fragment containing
"N 0 obj" at local position p therefore belongs at logical offset xref[N] - p. That is a
deterministic, verifiable placement, much stronger than similarity. Fragments without an
object header are placed only if validation proves it: the placement must make a stream
end exactly where its /Length says it does.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..common import CLUSTER, Candidate, Edge, Segment, fill_gaps, hexoff, split_corrupted
from ..scanner import Fragment
from ..validators import pdf_objects
from .base import Ctx, edge_from, generic_feats, materialize, token_sim
from .naming import resolve_name


def parse_split_xref(fb: bytes) -> dict[str, Any] | None:
    """An xref table whose 'startxref ... %%EOF' tail spilled into the next cluster (another fragment).

    Returned with startxref=None; its logical position is later derived from the fragment's own object
    headers (xref offsets), and the spilled tail is attached only if its startxref value matches exactly.
    """
    if fb.find(b"startxref") != -1:
        return None
    x = fb.rfind(b"\nxref")
    if x == -1:
        return None
    x += 1
    entries: dict[int, int] = {}
    pos = x + 5
    while True:
        hm = re.match(rb"(\d+) (\d+)\s*\n", fb[pos:])
        if not hm:
            break
        first, cnt = int(hm.group(1)), int(hm.group(2))
        pos += hm.end()
        for k in range(cnt):
            ln = fb[pos:pos + 20]
            if len(ln) < 18:
                break
            if ln[17:18] == b"n":
                entries[first + k] = int(ln[:10])
            pos += 20
    if len(entries) < 2:
        return None
    tr = re.search(rb"trailer\s*<<(.*?)>>", fb[x:], re.S)
    return {"startxref": None, "xref_pos": x, "entries": entries, "end": len(fb.rstrip(b"\x00")), "partial": False,
            "split_tail": True, "trailer": tr.group(1).decode("latin-1").strip() if tr else None}


def parse_tail(fb: bytes) -> dict[str, Any] | None:
    s = fb.rfind(b"startxref")
    if s == -1:
        return parse_split_xref(fb)
    m = re.match(rb"startxref\s+(\d+)", fb[s:])
    if not m:
        return None
    x = fb.rfind(b"xref", 0, s)
    while x != -1 and fb[x - 5:x] == b"start":
        x = fb.rfind(b"xref", 0, x)
    entries: dict[int, int] = {}
    partial = x == -1
    if x != -1:
        pos = x + 5
        while True:
            hm = re.match(rb"(\d+) (\d+)\s*\n", fb[pos:])
            if not hm:
                break
            first, cnt = int(hm.group(1)), int(hm.group(2))
            pos += hm.end()
            for k in range(cnt):
                ln = fb[pos:pos + 20]
                if len(ln) < 18:
                    break
                if ln[17:18] == b"n":
                    entries[first + k] = int(ln[:10])
                pos += 20
    eof = fb.find(b"%%EOF", s)
    end = eof + 5 + (1 if fb[eof + 5:eof + 6] == b"\n" else 0) if eof != -1 else len(fb.rstrip(b"\x00"))
    tr = re.search(rb"trailer\s*<<(.*?)>>", fb[s - 400 if s > 400 else 0:s], re.S)
    return {"startxref": int(m.group(1)), "xref_pos": x, "entries": entries, "end": end,
            "partial": partial, "trailer": tr.group(1).decode("latin-1").strip() if tr else None}


def _votes(f: Fragment, xref: dict[int, int], total: int | None = None) -> Counter:
    """Implied logical start per matching object. Files begin on cluster boundaries, so a
    genuine placement is cluster-aligned; a coincidental match almost never is (1/4096)."""
    c: Counter = Counter()
    for p, n in f.pdf_objs():
        if n in xref:
            s = xref[n] - p
            if s >= 0 and s % CLUSTER == 0 and (total is None or s < total):
                c[s] += 1
    return c


def assemble_pdf(ctx: Ctx) -> None:
    frags = [f for f in ctx.pool("pdf")]
    tails = []
    for f in frags:
        t = parse_tail(ctx.img.frag_bytes(f))
        if t and t["entries"]:
            t["frag"] = f
            tails.append(t)
    headers = [f for f in frags if f.header and f.header.get("type") == "pdf"]
    used_tails: set[str] = set()
    jobs: list[tuple[Fragment | None, dict[str, Any] | None]] = []
    for H in headers:
        best, bscore = None, 0.0
        for t in tails:
            if t["frag"].id in used_tails:
                continue
            if t["frag"] is H:
                best, bscore = t, 1.0
                break
            objs = H.pdf_objs()
            ok = sum(1 for p, n in objs if t["entries"].get(n) == p)
            score = ok / max(1, len(objs))
            if score > bscore:
                best, bscore = t, score
        if best and bscore >= 0.5:
            used_tails.add(best["frag"].id)
            jobs.append((H, best))
        else:
            jobs.append((H, None))
    for t in tails:
        if t["frag"].id not in used_tails:
            jobs.append((None, t))
    for H, T in jobs:
        _build(ctx, H, T)


def _build(ctx: Ctx, H: Fragment | None, T: dict[str, Any] | None) -> None:
    cid = ctx.next_id()
    placed: list[tuple[int, Fragment, int, str]] = []  # (logical start, frag, anchors, method)
    edges: list[Edge] = []
    ops: list[str] = []
    conflicts: list[str] = []
    if H:
        placed.append((0, H, 99, "header"))
        ops.append(f"Header fragment {H.id} @ {hexoff(H.offset)} placed at logical 0 (%PDF signature)")
    xref: dict[int, int] = {}
    total = None
    if T and T.get("split_tail"):
        xref = T["entries"]
        tf = T["frag"]
        v = _votes(tf, xref)
        if tf is H:
            t_start = 0
        elif v and v.most_common(1)[0][1] >= 2:
            t_start = v.most_common(1)[0][0]
            placed.append((t_start, tf, 99, "xref"))
            ops.append(f"Cross-reference fragment {tf.id} @ {hexoff(tf.offset)} placed at logical {t_start}: "
                       f"{v.most_common(1)[0][1]} of its own object headers sit exactly at their xref offsets")
        else:
            t_start = None
        total = None
        if t_start is not None:
            want = t_start + T["xref_pos"]  # the value a genuine 'startxref' line must carry
            nxt = t_start + tf.length
            for f in ctx.pool("pdf", "text", "binary", "compressed"):
                if f is tf or f is H or any(f is p[1] for p in placed):
                    continue
                fb = ctx.img.read(f.offset, min(f.length, CLUSTER))
                m = re.search(rb"startxref\s+(\d+)\s+%%EOF", fb)
                if m and int(m.group(1)) == want and fb.find(b"xref", 0, m.start()) == -1:
                    placed.append((nxt, f, 99, "startxref"))
                    total = nxt + m.end() + (1 if fb[m.end():m.end() + 1] == b"\n" else 0)
                    ops.append(f"Tail fragment {f.id} @ {hexoff(f.offset)} placed at logical {nxt}: its startxref={want} "
                               f"points exactly at the xref table in {tf.id}; %%EOF found")
                    break
            if total is None:
                total = nxt
                ops.append("startxref/%%EOF tail not located; file end bounded by the xref fragment")
            ops.append(f"Cross-reference table parsed: {len(xref)} object offsets; expected size {total} bytes")
    elif T:
        xref = T["entries"]
        tf: Fragment = T["frag"]
        t_start = T["startxref"] - T["xref_pos"] if T["xref_pos"] != -1 else None
        if tf is not H and t_start is not None:
            placed.append((t_start, tf, 99, "xref"))
            ops.append(f"Trailer fragment {tf.id} @ {hexoff(tf.offset)} placed at logical {t_start} using startxref={T['startxref']}")
        total = (t_start or 0) + T["end"] if tf is not H else T["end"]
        ops.append(f"Cross-reference table parsed: {len(xref)} object offsets; expected size {total} bytes")
    # ---- deterministic placement by xref anchors ---------------------------------------
    if xref:
        for f in ctx.pool("pdf"):
            if any(f is p[1] for p in placed) or (H is None and f.header):
                continue
            v = _votes(f, xref, total)
            if not v:
                continue
            start, n = v.most_common(1)[0]
            n_objs = len(f.pdf_objs())
            # a fragment of a *different* PDF can match one object by coincidence; require
            # the majority of its object headers to agree before trusting the placement
            if n / max(1, n_objs) < 0.6 or (n < 2 and n_objs > 1):
                continue
            others = n_objs - n
            if others:
                conflicts.append(f"{f.id}: {others} object header(s) disagree with the majority xref placement")
            placed.append((start, f, n, "xref"))
            ops.append(f"{f.id} placed at logical {start}: {n} object header(s) match xref offsets exactly")
    else:
        # no xref: order by object-number continuity (weaker, model-scored)
        cur = H
        while cur:
            last_obj = max((n for _, n in cur.pdf_objs()), default=None)
            best = None
            for f in ctx.pool("pdf"):
                if any(f is p[1] for p in placed) or f.header:
                    continue
                objs = f.pdf_objs()
                feats = generic_feats(ctx, cur, f)
                feats.update(struct_order=1.0 if objs and last_obj is not None and objs[0][1] == last_obj + 1 else 0.2,
                             index_verified=0.0, boundary_smooth=0.5,
                             content_sim=token_sim(ctx.img.cluster(cur.end_cluster - 1), ctx.img.cluster(f.start_cluster)),
                             time_consistent=0.5)
                e = edge_from(ctx, cur.id, f.id, feats, [f"Object numbering {last_obj} -> {objs[0][1] if objs else 'none'}"])
                if not best or e.confidence > best[0].confidence:
                    best = (e, f)
            if not best or best[0].confidence < 0.6:
                break
            pos = placed[-1][0] + placed[-1][1].length
            placed.append((pos, best[1], 0, "sequence"))
            edges.append(best[0])
            cur = best[1]
    # overlapping claims: stronger anchors win cluster by cluster; report the collisions
    for i, p in enumerate(placed):
        for q in placed[:i]:
            if p[0] < q[0] + q[1].length and q[0] < p[0] + p[1].length:
                weaker = p if p[2] < q[2] else q
                conflicts.append(f"{p[1].id} and {q[1].id} claim overlapping logical ranges; {weaker[1].id} yields the overlap")
    final = sorted(placed, key=lambda t: t[0])
    total = total or (max(s + f.length for s, f, _, _ in final) if final else 0)

    def materialize_(pl: list[tuple[int, Fragment, int, str]]) -> tuple[bytes, list[Segment]]:
        d, s, _ = materialize(ctx, pl, total)
        return d, s

    data, segs = materialize_(final)
    # ---- validation-driven gap filling for fragments without object headers ------------
    if xref:
        gaps = [s for s in segs if s.kind == "missing"]
        spare = [f for f in ctx.pool("pdf") if not any(f is q[1] for q in final) and not f.pdf_objs() and not f.header]
        for g in gaps:
            before = pdf_objects(data, xref, [(s.start, s.start + s.length) for s in segs if s.kind == "missing"])
            ok_before = sum(1 for o in before.values() if o["status"] == "ok")
            for f in spare:
                if f.length > g.length:
                    continue
                for start in {g.start, g.start + g.length - f.length}:
                    trial = final + [(start, f, 0, "validated gap fill")]
                    d2, s2 = materialize_(sorted(trial, key=lambda t: t[0]))
                    after = pdf_objects(d2, xref, [(s.start, s.start + s.length) for s in s2 if s.kind == "missing"])
                    ok_after = sum(1 for o in after.values() if o["status"] == "ok")
                    if ok_after > ok_before:
                        final = sorted(trial, key=lambda t: t[0])
                        data, segs = d2, s2
                        spare.remove(f)
                        ops.append(f"{f.id} placed in gap at {start}: placement makes {ok_after - ok_before} more object(s) validate (stream /Length agreement)")
                        break
                else:
                    continue
                break
    # ---- edges between consecutive placed fragments ------------------------------------
    for (sa, fa, na, ha), (sb, fb_, nb, hb) in zip(final, final[1:]):
        if any(e.src == fa.id and e.dst == fb_.id for e in edges):
            continue
        adjacent = sa + fa.length >= sb
        a_objs, b_objs = fa.pdf_objs(), fb_.pdf_objs()
        seq_ok = (not b_objs) or (a_objs and b_objs[0][1] == max(n for _, n in a_objs) + 1) or (not a_objs)
        feats = generic_feats(ctx, fa, fb_)
        feats.update(struct_order=1.0 if seq_ok else 0.2,
                     index_verified=1.0 if (xref and hb != "sequence") else 0.0,
                     boundary_smooth=0.8 if adjacent else 0.5,
                     content_sim=token_sim(ctx.img.cluster(fa.end_cluster - 1), ctx.img.cluster(fb_.start_cluster)),
                     time_consistent=0.5)
        ev = [f"{fb_.id} placed at logical offset {sb} by {hb} ({nb if nb != 99 else 'structural'} anchor(s))",
              "Fragments are logically adjacent" if adjacent else f"Gap of {sb - (sa + fa.length)} bytes between them (missing data)"]
        if a_objs and b_objs:
            ev.append(f"Object numbering {max(n for _, n in a_objs)} -> {b_objs[0][1]}")
        edges.append(edge_from(ctx, fa.id, fb_.id, feats, ev, "continuation" if adjacent else "structural"))
    # ---- corruption from object validation ----------------------------------------------
    missing = [(s.start, s.start + s.length) for s in segs if s.kind == "missing"]
    objs = pdf_objects(data, xref, missing) if xref else {}
    bad = []
    for o in objs.values():
        if o["status"] == "corrupted":
            if o.get("bad_offsets"):
                for b in o["bad_offsets"]:
                    bad.append((b, b + 1, f"obj {o['num']}: {o.get('reason')}"))
            else:
                bad.append((o["offset"], o["end"], f"obj {o['num']}: {o.get('reason')}"))
    segs = split_corrupted(segs, bad)
    used = [q[1] for q in final]
    ctx.claim(cid, used)
    title = None
    m = re.search(rb"/Title \((.*?)\)", data)
    if m:
        title = m.group(1).decode("latin-1")
    first = H or used[0]
    fat, name, src, medges = resolve_name(ctx, cid, first, "pdf", title, "PDF /Info Title")
    cand = Candidate(
        id=cid, type="pdf", name=name, name_source=src, fragments=[f.id for f in used], segments=segs,
        expected_size=total if T else None,
        expected_size_source="startxref + trailer length (xref table)" if T else "unknown (no xref recovered)",
        data=data, edges=edges + medges, conflicts=conflicts, header_found=H is not None,
        structure={"xref": {str(k): v for k, v in xref.items()}, "trailer": T["trailer"] if T else None,
                   "xref_recovered": bool(T), "placement": [{"fragment": f.id, "logical": s, "anchors": n if n != 99 else "structural", "method": h} for s, f, n, h in final]},
        metadata={}, operations=ops,
    )
    if fat:
        cand.metadata["directory_entry"] = fat
    ctx.candidates.append(cand)
    ctx.log("reconstruct", f"PDF {name}: {len(used)} fragment(s) placed, xref {'recovered' if T else 'missing'}")
