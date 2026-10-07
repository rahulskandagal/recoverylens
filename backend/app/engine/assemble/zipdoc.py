"""ZIP / DOCX / XLSX reconstruction anchored on the central directory.

The central directory (CD) at the end of a ZIP lists each member's name, local-header
offset, sizes and CRC-32. That gives:
  * exact placement of any fragment containing a local header (name + offset must agree),
  * exact file size (EOCD position),
  * a cryptographic-strength-ish acceptance test for gap filling: a candidate run of
    clusters is accepted only if the members it completes decompress AND match CRC-32.
"""
from __future__ import annotations

import re
import struct
import zlib
from collections import Counter
from typing import Any

import numpy as np

from ..common import CLUSTER, Candidate, Edge, Segment, hexoff, split_corrupted
from ..scanner import Fragment, _jpeg_legal
from ..validators import decode_gray, member_bytes, member_data_range
from .base import Ctx, edge_from, generic_feats, materialize
from .naming import resolve_name

ZIP_FAMILIES = ("compressed", "jpeg", "xml", "text", "binary")


def parse_zip_tail(fb: bytes) -> dict[str, Any] | None:
    e = fb.rfind(b"PK\x05\x06")
    if e == -1 or e + 22 > len(fb):
        return None
    n, cd_size, cd_off, clen = struct.unpack("<HIIH", fb[e + 10:e + 22])
    start = cd_off + cd_size - e  # logical position of this fragment
    if start < 0:
        return None
    members = []
    p = cd_off - start
    if p < 0:
        return {"start": start, "total": start + e + 22 + clen, "members": [], "entries": n, "partial_cd": True, "eocd": e}
    for _ in range(n):
        if fb[p:p + 4] != b"PK\x01\x02":
            break
        method, _t, _d, crc, csize, usize, nlen, xlen, cmlen = struct.unpack("<HHHIIIHHH", fb[p + 10:p + 34])
        lho = struct.unpack("<I", fb[p + 42:p + 46])[0]
        members.append({"name": fb[p + 46:p + 46 + nlen].decode("utf-8", "replace"), "method": method, "crc": crc,
                        "csize": csize, "usize": usize, "local_offset": lho})
        p += 46 + nlen + xlen + cmlen
    return {"start": start, "total": start + e + 22 + clen, "members": members, "entries": n,
            "partial_cd": len(members) < n, "eocd": e}


def _join_split_cd(ctx: Ctx, t: dict[str, Any], frags: list[Fragment]) -> None:
    """The central directory started in an earlier fragment than the EOCD record.

    The EOCD gives the exact logical offset and size of the directory. A fragment holding a directory entry
    ("PK\\x01\\x02") at local position q can only be its start if cd_offset - q lands on a cluster boundary;
    the joined bytes must then parse as exactly the number of entries the EOCD declares.
    """
    tf = t["frag"]
    fb = ctx.img.frag_bytes(tf)
    e = t["eocd"]
    n, cd_size, cd_off = struct.unpack("<HII", fb[e + 10:e + 20])
    for f in frags:
        if f is tf:
            continue
        b = ctx.img.frag_bytes(f)
        q = b.find(b"PK\x01\x02")
        while q != -1:
            start_f = cd_off - q
            if start_f >= 0 and start_f % CLUSTER == 0 and start_f < t["start"] <= start_f + f.length:
                joined = b[: t["start"] - start_f] + fb
                p = cd_off - start_f
                members = []
                for _ in range(n):
                    if joined[p:p + 4] != b"PK\x01\x02":
                        break
                    method, _t, _d, crc, csize, usize, nlen, xlen, cmlen = struct.unpack("<HHHIIIHHH", joined[p + 10:p + 34])
                    lho = struct.unpack("<I", joined[p + 42:p + 46])[0]
                    members.append({"name": joined[p + 46:p + 46 + nlen].decode("utf-8", "replace"), "method": method, "crc": crc,
                                    "csize": csize, "usize": usize, "local_offset": lho})
                    p += 46 + nlen + xlen + cmlen
                if len(members) == n and p == cd_off - start_f + cd_size:
                    t.update(members=members, partial_cd=False, cd_frag=f, cd_frag_start=start_f, cd_q_cluster=q // CLUSTER)
                    return
            q = b.find(b"PK\x01\x02", q + 4)


def _crc_status(data: bytes, members: list[dict[str, Any]], missing: list[tuple[int, int]]) -> dict[str, str]:
    out = {}
    for m in members:
        a, b = member_data_range(data, m)
        if any(m["local_offset"] < y and b > x for x, y in missing):
            out[m["name"]] = "missing"
            continue
        _, why = member_bytes(data, m)
        out[m["name"]] = "ok" if why == "crc ok" else "bad"
    return out


def _rst_run(b: bytes) -> tuple[int, int]:
    """(restart markers, sequence breaks) in JPEG scan data: RSTn must be followed by RST(n+1 mod 8)."""
    seq = [m.group(0)[1] - 0xD0 for m in re.finditer(rb"\xff[\xd0-\xd7]", b)]
    breaks = sum(1 for a, c in zip(seq, seq[1:]) if c != (a + 1) % 8)
    return len(seq), breaks


def _is_jpeg_bytes(b: bytes) -> bool:
    """Legal entropy-coded scan data, or JPEG marker segments (SOI/DQT/DHT/SOS) such as an image's header tables."""
    ok, _, _, _ = _jpeg_legal(b)
    return ok or any(mk in b for mk in (b"\xff\xd8\xff", b"\xff\xdb\x00", b"\xff\xc4\x00", b"\xff\xda\x00"))


def _stored_jpeg_continuity(data: bytes, m: dict[str, Any], lo: int, hi: int) -> str | None:
    """Evidence that bytes [lo, hi) belong inside a STORED JPEG member: its restart markers continue in
    sequence across both joins. Returns an evidence sentence, or None."""
    if m["method"] != 0 or not m["name"].lower().endswith((".jpg", ".jpeg")):
        return None
    a, b = member_data_range(data, m)
    body = data[a:b]
    sos = body.find(b"\xff\xda")
    if sos == -1:
        return None
    n_all, br_all = _rst_run(body[sos:])
    n_in, _ = _rst_run(data[max(a, lo):min(b, hi)])
    left, br_left = _rst_run(data[max(a, lo - 8192):min(b, lo + 8192)])
    right, br_right = _rst_run(data[max(a, hi - 8192):min(b, hi + 8192)])
    need_in = min(4, max(1, (hi - lo) // 2048))  # small pieces hold fewer restart intervals
    if not (n_all >= 16 and br_all <= 1 and n_in >= need_in and left >= 4 and right >= 4 and br_left == 0 and br_right == 0):
        return None
    # Marker order alone matches by chance (1 in 8 per join), and photos saved with the standard Huffman
    # tables decode each other's data. Image geometry does not match by chance: the bytes per restart
    # interval (one MCU row) follow the image width, so the inserted run must have this member's row size.
    pos = [a + mm.start() for mm in re.finditer(rb"\xff[\xd0-\xd7]", body[sos:])]
    pos = [p + sos for p in pos]
    outside = [q - p for p, q in zip(pos, pos[1:]) if q <= lo or p >= hi]
    inside = [q - p for p, q in zip(pos, pos[1:]) if p >= lo and q <= hi]
    if len(outside) < 3:
        return None
    L = float(np.median(outside))
    if len(inside) < 1 or (len(inside) < 3 and hi - lo >= 4 * L):
        return None
    Li = float(np.median(inside))
    span_lo = next((q - p for p, q in zip(pos, pos[1:]) if p < lo <= q), None)
    span_hi = next((q - p for p, q in zip(pos, pos[1:]) if p < hi <= q), None)
    if span_lo is None and pos and pos[0] >= lo:
        # the join precedes the first restart marker: measure from where the entropy-coded data starts
        seglen = int.from_bytes(body[sos + 2:sos + 4], "big")
        span_lo = pos[0] - (a + sos + 2 + seglen)
    ok = abs(Li - L) <= 0.25 * L and all(s is not None and abs(s - L) <= 0.35 * L for s in (span_lo, span_hi))
    if not ok or decode_gray(body + (b"" if body.rstrip(b"\x00").endswith(b"\xff\xd9") else b"\xff\xd9")) is None:
        return None
    return (f"{m['name']} (stored JPEG): restart markers continue in sequence across both joins and the inserted data "
            f"has this image's row size ({Li:.0f} B vs {L:.0f} B per restart interval); the member decodes")


def _release_unproven_tail(data: bytes, segs: list[Segment], prev: Segment, gs: int, members: list[dict[str, Any]],
                           anchored: set[int], ops: list[str]) -> tuple[bytes, list[Segment]]:
    """Before an unfillable gap: release trailing clusters of the preceding fragment that the data itself rejects.

    A deflate member that inflates cleanly up to a cluster boundary and fails within the next 512 bytes shows that
    the cluster after the boundary does not continue it (the scanner had merged unrelated data onto the fragment).
    Released clusters are reported as missing - never kept as file content and never replaced by guesses.
    """
    for k in (2, 1):
        b = gs - k * CLUSTER
        if b <= prev.start or any(i in anchored for i in range(b // CLUSTER, gs // CLUSTER)):
            continue
        for m in members:
            a, e = member_data_range(data, m)
            if not (a < b < e):
                continue
            if m["method"] == 0 and m["name"].lower().endswith((".jpg", ".jpeg")):
                # stored picture: every cluster inside it must be JPEG data; random/text/zero clusters are not
                rejected = not any(_is_jpeg_bytes(data[x:x + CLUSTER]) for x in range(b, gs, CLUSTER))
                fail_at = b
            elif m["method"] == 8:
                prog = _inflate_progress(data, a, min(e, gs))
                fail_at = a + prog
                rejected = b <= fail_at < b + 512 and prog < min(e, gs) - a
            else:
                continue
            if rejected:
                buf = bytearray(data)
                buf[b:gs] = bytes(gs - b)
                keep = []
                for s in segs:
                    if s is prev:
                        keep.append(Segment(s.start, b - s.start, s.kind, s.fragment_id, s.source_offset, s.note))
                    else:
                        keep.append(s)
                why = ("the cluster is not JPEG data" if m["method"] == 0 else
                       f"it inflates cleanly to logical {b} and fails {fail_at - b} bytes after it")
                keep.append(Segment(b, gs - b, "missing", note=f"cluster(s) released: the bytes do not continue {m['name']} ({why})"))
                ops.append(f"{prev.fragment_id}: last {k} cluster(s) released and reported missing: {m['name']}: {why} "
                           "(unrelated data merged onto the fragment)")
                return bytes(buf), sorted(keep, key=lambda s: s.start)
    return data, segs


def _replace_slot(segs: list[Segment], lo: int, n: int, frag_id: str | None, src: int, note: str) -> list[Segment]:
    keep = [s for s in segs if s.start + s.length <= lo or s.start >= lo + n]
    for s in segs:
        if s.start + s.length <= lo or s.start >= lo + n:
            continue
        if s.start < lo:
            keep.append(Segment(s.start, lo - s.start, s.kind, s.fragment_id, s.source_offset, s.note))
        if s.start + s.length > lo + n:
            d = lo + n - s.start
            keep.append(Segment(lo + n, s.start + s.length - lo - n, s.kind, s.fragment_id,
                                (s.source_offset + d) if s.source_offset is not None else None, s.note))
    keep.append(Segment(lo, n, "recovered", frag_id, src, note))
    return sorted(keep, key=lambda s: s.start)


def _resolve_contested(ctx: Ctx, data: bytes, segs: list[Segment], placements: list, members: list[dict[str, Any]],
                       total: int, ops: list[str]) -> tuple[bytes, list[Segment]]:
    """A logical cluster claimed by two placed fragments goes to the one that makes more members verify by CRC-32.

    Typical case: the scanner merged an unrelated cluster onto the end of the header fragment, and that cluster
    competes with the true one (from a fragment anchored by a local header). Distance-to-anchor ties cannot decide
    it; the container checksum can.
    """
    claims: dict[int, list[tuple[Fragment, int]]] = {}
    for start, f, _, _ in placements:
        if start % CLUSTER:
            continue
        for i in range(f.n_clusters):
            lo = start + i * CLUSTER
            if 0 <= lo < total:
                claims.setdefault(lo // CLUSTER, []).append((f, i))
    missing = [(s.start, s.start + s.length) for s in segs if s.kind == "missing"]

    def score(buf: bytes) -> int:
        return sum(1 for v in _crc_status(buf, members, missing).values() if v == "ok")

    for slot, alts in sorted(claims.items()):
        if len({f.id for f, _ in alts}) < 2:
            continue
        lo = slot * CLUSTER
        n = min(CLUSTER, total - lo)
        cur = next((s for s in segs if s.start <= lo < s.start + s.length), None)
        base = score(data)
        best = None
        for f, i in alts:
            src = f.offset + i * CLUSTER
            if cur is not None and cur.fragment_id == f.id and cur.source_offset is not None and cur.source_offset + (lo - cur.start) == src:
                continue  # already the chosen alternative
            trial = bytearray(data)
            trial[lo:lo + n] = ctx.img.read(src, n)
            sc = score(bytes(trial))
            if sc > base and (best is None or sc > best[0]):
                best = (sc, f, src, bytes(trial))
        if best:
            sc, f, src, trial = best
            ops.append(f"Logical cluster {slot} was claimed by {cur.fragment_id if cur else '?'} and {f.id}: "
                       f"{f.id} chosen because {sc - base} more member(s) then verify by CRC-32")
            data = trial
            segs = _replace_slot(segs, lo, n, f.id, src, "CRC-resolved contested slot")
    return data, segs


def _two_piece_fill(ctx: Ctx, data: bytes, members: list[dict[str, Any]], missing: list[tuple[int, int]], gs: int, ge: int,
                    placements: list, cl: list) -> tuple | None:
    """Fill a gap inside a STORED JPEG member from two separate free fragments (the member was split twice).

    Only fragment pairs whose sizes add up exactly to the gap are tried, in both orders. A pair is accepted
    only if every join (gap start, the internal join and gap end) passes the restart-marker and row-size
    checks and the member decodes. Returns the same tuple shape as the single-run search, or None.
    """
    if gs % CLUSTER:
        return None
    n_cl = -(-(ge - gs) // CLUSTER)
    touched = [m for m in members if m["local_offset"] < ge and member_data_range(data, m)[1] > gs
               and m["method"] == 0 and m["name"].lower().endswith((".jpg", ".jpeg"))]
    if not touched or n_cl < 2:
        return None
    placed = {p[1].id for p in placements}
    frs = [f for f in ctx.scan.fragments if ctx.free(f) and f.id not in placed and f.family != "zero" and f.header is None
           and f.n_clusters < n_cl and any(c.rst for c in f.clusters)
           and not any(i in ctx.assigned_clusters for i in range(f.start_cluster, f.end_cluster))][:60]
    for A in frs:
        for B in frs:
            if A is B or A.n_clusters + B.n_clusters != n_cl:
                continue
            mid = gs + A.n_clusters * CLUSTER
            trial = bytearray(data)
            trial[gs:mid] = ctx.img.read(A.offset, mid - gs)
            trial[mid:ge] = ctx.img.read(B.offset, ge - mid)
            t = bytes(trial)
            ev = []
            for m in touched:
                e1 = _stored_jpeg_continuity(t, m, gs, mid)
                e2 = _stored_jpeg_continuity(t, m, mid, ge)
                if e1 and e2:
                    ev.append(f"{m['name']} (stored JPEG): two pieces {A.id} + {B.id}; restart markers and row size match across "
                              "all three joins and the member decodes")
            if ev:
                return ([(A.start_cluster, A.n_clusters), (B.start_cluster, B.n_clusters)], ev, gs, n_cl, 0, t)
    return None


def _inflate_progress(data: bytes, a: int, upto: int) -> int:
    """How many compressed bytes of a raw-deflate stream starting at `a` inflate without error."""
    d = zlib.decompressobj(-15)
    pos = a
    step = 512
    while pos < upto:
        try:
            d.decompress(data[pos:min(upto, pos + step)])
        except zlib.error:
            return pos - a
        if d.eof:
            return upto - a
        pos += step
    return upto - a


def _refine_deflate(ctx: Ctx, data: bytes, segs: list[Segment], m: dict[str, Any], protected: set[int],
                    placements: list) -> tuple[bytes, list[tuple[int, int]], bool]:
    """Re-select cluster slots of a failing/missing deflate member by inflate progress, then
    accept the result only if the member's CRC-32 matches. Returns (data, swaps, verified)."""
    a, b = member_data_range(data, m)
    slots = [s for s in range(a // CLUSTER, -(-b // CLUSTER)) if s not in protected]
    if not slots or len(slots) > 6:
        return data, [], False
    in_use = {s.source_offset // CLUSTER + k for s in segs if s.source_offset is not None
              for k in range(-(-s.length // CLUSTER))}
    pool = [c.index for c in ctx.scan.clusters
            if c.cls in ("high_entropy", "zip_struct", "binary") and c.index not in ctx.assigned_clusters
            and c.index not in in_use]
    trial = bytearray(data)
    swaps = []
    for s in slots:
        lo, hi = s * CLUSTER, min(len(trial), (s + 1) * CLUSTER)
        need = min(b, hi)
        if _inflate_progress(bytes(trial), a, need) >= need - a:
            continue  # current content already inflates cleanly through this slot
        best = None
        for c in pool:
            if any(c == x for _, x in swaps):
                continue
            trial[lo:hi] = ctx.img.read(c * CLUSTER, hi - lo)
            if _inflate_progress(bytes(trial), a, need) >= need - a:
                best = c
                break
        if best is None:
            return data, [], False
        trial[lo:hi] = ctx.img.read(best * CLUSTER, hi - lo)
        swaps.append((s, best))
    _, why = member_bytes(bytes(trial), m)
    if why == "crc ok" and swaps:
        return bytes(trial), swaps, True
    return data, [], False


def assemble_zip(ctx: Ctx) -> None:
    frags = [f for f in ctx.scan.fragments if f.family in ZIP_FAMILIES and ctx.free(f)]
    tails = []
    for f in frags:
        if any(c.zip_eocd is not None for c in f.clusters):
            t = parse_zip_tail(ctx.img.frag_bytes(f))
            if t:
                t["frag"] = f
                tails.append(t)
    for t in tails:
        if t.get("partial_cd"):
            _join_split_cd(ctx, t, frags)
    headers = [f for f in frags if f.header and f.header.get("type") == "zip"]
    used: set[str] = set()
    for H in headers:
        best, bs = None, 0.0
        locs = H.zip_locals()
        for t in tails:
            if t["frag"].id in used:
                continue
            if t["frag"] is H:
                best, bs = t, 1.0
                break
            by_off = {m["local_offset"]: m["name"] for m in t["members"]}
            ok = sum(1 for p, n in locs if by_off.get(p) == n)
            s = ok / max(1, len(locs))
            if s > bs:
                best, bs = t, s
        if best and bs >= 0.5:
            used.add(best["frag"].id)
        else:
            best = None
        _build(ctx, H, best)
    for t in tails:
        if t["frag"].id not in used and t["members"]:
            _build(ctx, None, t)


def _build(ctx: Ctx, H: Fragment | None, T: dict[str, Any] | None) -> None:
    cid = ctx.next_id()
    ops: list[str] = []
    conflicts: list[str] = []
    placements: list[tuple[int, Fragment, int, str]] = []
    if H:
        placements.append((0, H, 99, "header"))
        ops.append(f"Header fragment {H.id} @ {hexoff(H.offset)} placed at logical 0 (PK\\x03\\x04)")
    members = T["members"] if T else []
    if T:
        tf = T["frag"]
        if tf is not H:
            placements.append((T["start"], tf, 99, "central directory"))
        ops.append(f"End-of-central-directory in {tf.id}: {T['entries']} members, expected size {T['total']} bytes; "
                   f"trailer fragment placed at logical {T['start']}")
        if T.get("cd_frag") is not None and T["cd_frag"] is not H:
            cf = T["cd_frag"]
            placements.append((T["cd_frag_start"], cf, 99, "central directory"))
            ops.append(f"Central directory split across fragments: its start is in {cf.id} @ {hexoff(cf.offset)}, placed at logical "
                       f"{T['cd_frag_start']} (EOCD directory offset; the joined directory parses as exactly {T['entries']} entries)")
        total = T["total"]
    else:
        total = sum(f.length for f in ([H] if H else []))
        ops.append("No central directory recovered: size and member offsets unknown")
    by_off = {m["local_offset"]: m["name"] for m in members}
    anchors: dict[str, list[int]] = {}
    if T and T.get("cd_frag") is not None and T["cd_frag"] is not H:
        anchors[T["cd_frag"].id] = [T["cd_q_cluster"]]  # the cluster holding the directory's first entry
    # ---- anchor placement: local headers at the offsets the CD promises -------------------
    if members:
        for f in ctx.scan.fragments:
            if f.family not in ZIP_FAMILIES or not ctx.free(f) or any(f is p[1] for p in placements):
                continue
            if H is None and f.header:
                continue
            v: Counter = Counter()
            hits: dict[int, list[int]] = {}
            for p, n in f.zip_locals():
                for off, nm in by_off.items():
                    if nm == n and (off - p) % CLUSTER == 0 and -f.length < off - p < total:
                        v[off - p] += 1
                        hits.setdefault(off - p, []).append(p // CLUSTER)
            if v:
                start, n = v.most_common(1)[0]
                placements.append((start, f, n, "local header"))
                anchors[f.id] = hits[start]
                ops.append(f"{f.id} placed at logical {start}: {n} local header(s) match central-directory offsets and names")
    data, segs, used = materialize(ctx, placements, total, anchors)
    if members:
        data, segs = _resolve_contested(ctx, data, segs, placements, members, total, ops)
    # ---- CRC-verified gap filling ---------------------------------------------------------
    if members:
        missing = [(s.start, s.start + s.length) for s in segs if s.kind == "missing"]
        # clusters that hold a verified local header are anchors and are never given up
        anchored = {(p[0] + off) // CLUSTER for p in placements for off, _ in p[1].zip_locals()}
        for gs0, ge in missing:
            if gs0 % CLUSTER:
                continue
            prev = next((s for s in segs if s.kind == "recovered" and s.start + s.length == gs0 and s.fragment_id), None)
            found = None
            cl = ctx.scan.clusters
            # k = 0: the gap exactly. k > 0: the scanner may have merged unrelated trailing clusters onto the
            # preceding fragment (same content class); those clusters are released only if CRC-32 then proves
            # the replacement run is the file's own data.
            for k in range(0, 3):
                gs = gs0 - k * CLUSTER
                if k and (prev is None or gs < prev.start or any(i in anchored for i in range(gs // CLUSTER, gs0 // CLUSTER))):
                    break
                n_cl = -(-(ge - gs) // CLUSTER)
                others = [(x, y) for x, y in missing if (x, y) != (gs0, ge)]
                base_status = _crc_status(data, members, [(x, y) for x, y in missing])
                touched = [m for m in members if m["local_offset"] < ge and member_data_range(data, m)[1] > gs]
                if not touched:
                    break
                for c in range(ctx.img.n_clusters - n_cl + 1):
                    run = range(c, c + n_cl)
                    if any(i in ctx.assigned_clusters or cl[i].cls == "zero" for i in run):
                        continue
                    if any(p[1].start_cluster <= c < p[1].end_cluster for p in placements):
                        continue
                    trial = bytearray(data)
                    trial[gs:ge] = ctx.img.read(c * CLUSTER, ge - gs)
                    st = _crc_status(bytes(trial), touched, others)
                    newly = [m_ for m_, v in st.items() if v == "ok" and base_status.get(m_) != "ok"]
                    if newly and all(v != "bad" for v in st.values()):
                        found = (c, newly, gs, n_cl, k, bytes(trial))
                        break
                    # stored JPEG members: CRC fails if the member carries genuine bit-rot, but restart-marker
                    # continuity across both joins still proves where the bytes belong (the CRC failure is then
                    # reported as a corrupted range, never hidden)
                    if any(cl[i].rst for i in run):
                        ev = [e for e in (_stored_jpeg_continuity(bytes(trial), m_, gs, ge) for m_ in touched) if e]
                        others_bad = [m_["name"] for m_ in touched if st.get(m_["name"]) == "bad"
                                      and not _stored_jpeg_continuity(bytes(trial), m_, gs, ge)]
                        if ev and not others_bad:
                            found = (c, ev, gs, n_cl, k, bytes(trial))
                            break
                if found:
                    break
            if not found:
                found = _two_piece_fill(ctx, data, members, missing, gs0, ge, placements, cl)
            if not found and prev is not None:
                data, segs = _release_unproven_tail(data, segs, prev, gs0, members, anchored, ops)
            if found:
                c, newly, gs, n_cl, k, trial_b = found
                data = trial_b
                structural = any("restart markers" in x for x in newly)
                segs = [s for s in segs if not (s.kind == "missing" and s.start == gs0)]
                if k:
                    segs = [Segment(s.start, s.length - k * CLUSTER, s.kind, s.fragment_id, s.source_offset, s.note) if s is prev else s
                            for s in segs]
                    ops.append(f"{prev.fragment_id}: last {k} cluster(s) released: "
                               f"{'restart-marker continuity' if structural else 'CRC-32'} shows they are not part of this file "
                               "(the scanner had merged adjacent unrelated data onto the fragment)")
                pieces = c if isinstance(c, list) else [(c, n_cl)]  # [(first cluster, cluster count)] in logical order
                note = "structure-verified gap fill" if structural else "CRC-verified gap fill"
                buf = bytearray(data)
                foreign = []
                lo_piece = gs
                for pc, pn in pieces:
                    hi_piece = min(ge, lo_piece + pn * CLUSTER)
                    f = next((fr for fr in ctx.scan.fragments if fr.start_cluster <= pc < fr.end_cluster), None)
                    bad_here = []
                    if structural:
                        # geometry proves where the run belongs, not that every cluster in it survived: a cluster
                        # that is not JPEG scan data (random, text, zeros) was overwritten and is reported missing
                        for j in range(pn):
                            ci = cl[pc + j]
                            if ci.cls not in ("jpeg_data", "jpeg_header", "zip_struct") and not _is_jpeg_bytes(ctx.img.cluster(pc + j)):
                                lo_ = lo_piece + j * CLUSTER
                                hi_ = min(hi_piece, lo_ + CLUSTER)
                                buf[lo_:hi_] = bytes(hi_ - lo_)
                                bad_here.append((lo_, hi_, pc + j, ci.cls))
                    pos_ = lo_piece
                    for lo_, hi_, cc, cls_ in bad_here + [(hi_piece, hi_piece, None, None)]:
                        if lo_ > pos_:
                            segs.append(Segment(pos_, lo_ - pos_, "recovered", f.id if f else None, pc * CLUSTER + (pos_ - lo_piece), note))
                        if hi_ > lo_:
                            segs.append(Segment(lo_, hi_ - lo_, "missing",
                                                note=f"cluster {hexoff(cc * CLUSTER)} inside the run is {cls_} data, not JPEG scan data (overwritten)"))
                        pos_ = hi_
                    ctx.claim_clusters([x for x in range(pc, pc + pn) if x not in {y[2] for y in bad_here}])
                    if f and f not in used:
                        used.append(f)
                    foreign += bad_here
                    lo_piece = hi_piece
                data = bytes(buf)
                if foreign:
                    ops.append(f"{len(foreign)} cluster(s) inside the placed run are not JPEG scan data "
                               f"({', '.join(hexoff(x[2] * CLUSTER) for x in foreign)}): reported missing, never used as file content")
                segs.sort(key=lambda s: s.start)
                src = " + ".join(f"{pn} cluster(s) at {hexoff(pc * CLUSTER)}" for pc, pn in pieces)
                ops.append(f"Gap [{gs},{ge}) filled from {src}: " +
                           ("; ".join(newly) if structural else f"members {', '.join(newly)} now pass CRC-32"))
    # ---- deflate-aware refinement of failing members (CRC must confirm) -------------------
    if members:
        protected = {s.start // CLUSTER for s in segs if s.note in ("header", "central directory")
                     and s.start % CLUSTER == 0 and s.length <= CLUSTER}
        protected |= {0} if H else set()
        for p in placements:  # clusters holding a verified local header are anchors too
            for off, _ in p[1].zip_locals():
                protected.add((p[0] + off) // CLUSTER)
        missing = [(s.start, s.start + s.length) for s in segs if s.kind == "missing"]
        st0 = _crc_status(data, members, [])
        for m in members:
            if m["method"] != 8 or st0.get(m["name"]) == "ok":
                continue
            data2, swaps, ok = _refine_deflate(ctx, data, segs, m, protected, placements)
            if ok:
                data = data2
                for slot, c in swaps:
                    lo = slot * CLUSTER
                    n = min(CLUSTER, total - lo)
                    f = next((fr for fr in ctx.scan.fragments if fr.start_cluster <= c < fr.end_cluster), None)
                    keep = [s for s in segs if s.start + s.length <= lo or s.start >= lo + n]
                    cut = [s for s in segs if not (s.start + s.length <= lo or s.start >= lo + n)]
                    for s in cut:  # split the overlapped segment around the replaced slot
                        if s.start < lo:
                            keep.append(Segment(s.start, lo - s.start, s.kind, s.fragment_id, s.source_offset, s.note))
                        if s.start + s.length > lo + n:
                            d = lo + n - s.start
                            keep.append(Segment(lo + n, s.start + s.length - lo - n, s.kind, s.fragment_id,
                                                (s.source_offset + d) if s.source_offset is not None else None, s.note))
                    keep.append(Segment(lo, n, "recovered", f.id if f else None, c * CLUSTER, "inflate-guided + CRC-verified"))
                    segs = sorted(keep, key=lambda s: s.start)
                    ctx.claim_clusters([c])
                    if f and f not in used:
                        used.append(f)
                ops.append(f"{m['name']}: slot(s) {[s for s, _ in swaps]} re-selected by deflate-stream continuity; "
                           f"member now matches CRC-32 ({', '.join(hexoff(c * CLUSTER) for _, c in swaps)})")
    # ---- member-level validation -> corrupted ranges -------------------------------------
    missing = [(s.start, s.start + s.length) for s in segs if s.kind == "missing"]
    st = _crc_status(data, members, missing)
    bad = []
    for m in members:
        if st.get(m["name"]) == "bad":
            a, b = member_data_range(data, m)
            bad.append((a, b, f"{m['name']}: data present but CRC-32 mismatch (corrupted or mis-placed bytes)"))
    segs = split_corrupted(segs, bad)
    names = {m["name"] for m in members}
    kind = "docx" if "word/document.xml" in names else "xlsx" if "xl/workbook.xml" in names else "zip"
    # ---- edges between consecutive placed fragments ----------------------------------------
    edges: list[Edge] = []
    order = sorted([(s.start, s.fragment_id) for s in segs if s.fragment_id], key=lambda t: t[0])
    seen: list[str] = []
    for _, fid in order:
        if fid not in seen:
            seen.append(fid)
    for a_id, b_id in zip(seen, seen[1:]):
        fa, fb_ = ctx.scan.frag(a_id), ctx.scan.frag(b_id)
        sa = min(s.start for s in segs if s.fragment_id == a_id)
        ea = max(s.start + s.length for s in segs if s.fragment_id == a_id)
        sb = min(s.start for s in segs if s.fragment_id == b_id)
        adjacent = ea >= sb
        how = next(s.note for s in segs if s.fragment_id == b_id)
        member_ok = [m["name"] for m in members if st.get(m["name"]) == "ok"
                     and m["local_offset"] < sb + CLUSTER and member_data_range(data, m)[1] > sb]
        feats = generic_feats(ctx, fa, fb_)
        feats.update(struct_order=1.0, index_verified=1.0 if how in ("local header", "central directory", "CRC-verified gap fill",
                                                                     "structure-verified gap fill") else 0.0,
                     boundary_smooth=1.0 if member_ok else 0.5, content_sim=0.5, time_consistent=0.5)
        ev = [f"{b_id} placed at logical {sb} by {how}",
              f"Member(s) spanning the join verify by CRC-32: {', '.join(member_ok)}" if member_ok else "No member spanning the join could be CRC-verified",
              "Logically adjacent" if adjacent else f"{sb - ea} bytes missing between them"]
        edges.append(edge_from(ctx, a_id, b_id, feats, ev, "continuation" if adjacent else "structural"))
    for f in used:
        ctx.assigned[f.id] = cid
    ctx.claim(cid, [f for f in used if all(s.fragment_id != f.id or True for s in segs)])
    title = None
    core = next((m for m in members if m["name"] == "docProps/core.xml"), None)
    if core and st.get(core["name"]) == "ok":
        raw, _ = member_bytes(data, core)
        import re
        mt = re.search(rb"<dc:title>([^<]+)</dc:title>", raw or b"")
        title = mt.group(1).decode("utf-8", "replace") if mt else None
    first = H or (used[0] if used else None)
    if first is None:
        return
    fat, name, src, medges = resolve_name(ctx, cid, first, kind, title, "docProps/core.xml dc:title")
    cand = Candidate(
        id=cid, type=kind, name=name, name_source=src, fragments=[f.id for f in used], segments=segs,
        expected_size=total if T else None,
        expected_size_source="end-of-central-directory position" if T else "unknown (central directory missing)",
        data=data, edges=edges + medges, conflicts=conflicts, header_found=H is not None,
        structure={"members": [{**m, "status": st.get(m["name"], "?")} for m in members], "cd_recovered": bool(T)},
        metadata={}, operations=ops,
    )
    if fat:
        cand.metadata["directory_entry"] = fat
    ctx.candidates.append(cand)
    ctx.log("reconstruct", f"{kind.upper()} {name}: {len(used)} fragment(s); "
                           f"{sum(1 for v in st.values() if v == 'ok')}/{len(members)} members CRC-verified")
