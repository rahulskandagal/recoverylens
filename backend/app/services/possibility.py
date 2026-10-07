"""Recovery Possibility Map: is further recovery realistically possible for an incomplete artifact?

The level is decided by explicit evidence rules, not by an arbitrary score:

  HIGH     a structurally matching candidate exists (e.g. unassigned fragment carrying the exact PDF
           object numbers / ZIP member / JPEG restart sequence the gap needs)
  MEDIUM   the structure says what is missing AND same-family unassigned fragments of a fitting size
           exist, but nothing structurally pins them to the gap
  LOW      the missing extent is known but no compatible unassigned data exists in the image
           (most likely overwritten); or corrupted bytes with no alternative copy
  UNKNOWN  the format does not declare what is missing, or the artifact is an orphan

Every level lists its supporting (+) and opposing (-) evidence.
"""
from __future__ import annotations

from typing import Any

from ..engine.common import CLUSTER, hexoff
from .context import CaseCtx

FAMILY_FOR = {"jpeg": {"jpeg"}, "pdf": {"pdf"}, "sqlite": {"sqlite"}, "docx": {"compressed", "xml"}, "xlsx": {"compressed", "xml"},
              "zip": {"compressed"}, "log": {"log", "text"}, "txt": {"text", "log"}, "png": {"compressed", "binary"}}


def _expected_content(f: dict[str, Any], a: int, b: int) -> tuple[str, list[str], dict[str, Any]]:
    """What the file's own structure says should be in logical range [a, b). (text, evidence, structural keys)"""
    st = f.get("structure", {})
    t = f["file_type"]
    if t == "pdf" and st.get("xref"):
        objs = sorted(int(k) for k, off in st["xref"].items() if a <= int(off) < b)
        if objs:
            return (f"PDF object(s) {', '.join(map(str, objs[:12]))}{'…' if len(objs) > 12 else ''}",
                    [f"cross-reference table places object(s) {', '.join(map(str, objs[:6]))} inside this range"], {"pdf_objects": objs})
        return "PDF object data (no xref entry starts inside this range)", ["range lies between xref-indexed object offsets"], {}
    if t in ("docx", "xlsx", "zip") and st.get("members"):
        names = [m["name"] for m in st["members"] if m.get("local_offset") is not None and a <= m["local_offset"] < b]
        spans = [m["name"] for m in st["members"] if m.get("local_offset") is not None and m["local_offset"] < a
                 and m["local_offset"] + 30 + len(m["name"]) + m.get("csize", 0) > a]
        names = names or spans
        if names:
            return (f"ZIP member data: {', '.join(names[:5])}", ["central directory offsets/sizes place these members in the range"], {"zip_members": names})
        return "ZIP member data", ["range lies inside the container body declared by the central directory"], {}
    if t == "sqlite" and st.get("page_size"):
        ps = st["page_size"]
        pages = list(range(a // ps + 1, -(-b // ps) + 1))
        return (f"SQLite page(s) {pages[0]}–{pages[-1]}" if len(pages) > 1 else f"SQLite page {pages[0]}",
                [f"header declares page size {ps} and {st.get('page_count')} pages"], {"sqlite_pages": pages})
    if t == "jpeg":
        rows = st.get("rows_total")
        return ("JPEG entropy-coded scan data" + (f" (image has {rows} MCU rows)" if rows else ""),
                ["the SOS/EOI structure places scan data here"], {"jpeg": True})
    if f.get("expected_size"):
        return "file content (format does not index this range)", [f"expected size {f['expected_size']:,} B from {f.get('expected_size_source')}"], {}
    return "unknown", [], {}


def _candidates(ctx: CaseCtx, f: dict[str, Any], length: int, keys: dict[str, Any]) -> list[dict[str, Any]]:
    fams = FAMILY_FOR.get(f["file_type"], set())
    out = []
    own = set(f.get("fragments", []))
    for fr in ctx.fragments.values():
        if fr["id"] in own or fr["family"] not in fams or fr.get("family") == "zero":
            continue
        free = fr.get("assigned_to") in (None, "") or (fr.get("assigned_to") or "").startswith("R") and ctx_file_is_orphan(ctx, fr.get("assigned_to"))
        if not free:
            continue
        ev, conflicts, strength = [f"content family '{fr['family']}' matches a {f['file_type'].upper()} gap"], [], "family"
        if fr.get("header"):
            conflicts.append("carries a file header: it starts another file, not a continuation")
        if fr["length"] > length + CLUSTER:
            ev.append(f"fragment ({fr['length']:,} B) is larger than the gap ({length:,} B): only a prefix could fit")
        else:
            ev.append(f"fragment size {fr['length']:,} B fits the {length:,} B gap")
        if keys.get("pdf_objects") and fr.get("pdf_objects"):
            hit = sorted(set(keys["pdf_objects"]) & set(fr["pdf_objects"]))
            if hit:
                ev.append(f"contains PDF object header(s) {', '.join(map(str, hit[:6]))} that the xref expects in this gap")
                strength = "structural"
            else:
                conflicts.append(f"its PDF objects ({min(fr['pdf_objects'])}–{max(fr['pdf_objects'])}) are not the ones missing here")
        if keys.get("zip_members") and fr.get("zip_members"):
            if set(keys["zip_members"]) & set(fr["zip_members"]):
                ev.append("carries a local header for a member expected in this gap")
                strength = "structural"
        if fr.get("duplicate_of"):
            conflicts.append(f"duplicate of {fr['duplicate_of']}")
        out.append({"id": fr["id"], "offset_hex": hexoff(fr["offset"]), "length": fr["length"], "family": fr["family"],
                    "assigned_to": fr.get("assigned_to"), "strength": strength, "evidence": ev, "conflicts": conflicts,
                    "compatible": strength == "structural" or not conflicts})
    out.sort(key=lambda c: (c["strength"] != "structural", bool(c["conflicts"]), abs(c["length"] - length)))
    return out[:8]


_orphans: dict[tuple[str, str], bool] = {}


def ctx_file_is_orphan(ctx: CaseCtx, fid: str | None) -> bool:
    if not fid:
        return False
    k = (ctx.id, fid)
    if k not in _orphans:
        try:
            _orphans[k] = bool(ctx.file(fid).get("orphan"))
        except KeyError:
            _orphans[k] = False
    return _orphans[k]


def assess_file(ctx: CaseCtx, f: dict[str, Any]) -> dict[str, Any]:
    miss = f.get("missing_ranges", [])
    corr = f.get("corrupted_ranges", [])
    regions = []
    for i, m in enumerate(miss):
        text, ev, keys = _expected_content(f, m["start"], m["end"])
        cands = _candidates(ctx, f, m["length"], keys)
        structural = [c for c in cands if c["strength"] == "structural" and not c["conflicts"]]
        plausible = [c for c in cands if c["compatible"]]
        if text == "unknown":
            lvl, why = "UNKNOWN", "the format does not declare what belongs in this range"
        elif structural:
            lvl, why = "HIGH", f"{len(structural)} unassigned fragment(s) structurally match what the file expects here"
        elif plausible:
            lvl, why = "MEDIUM", f"{len(plausible)} same-family unassigned fragment(s) could fit; nothing structurally pins them to this gap"
        else:
            lvl, why = "LOW", "no compatible unassigned data exists in the image; the bytes were most likely overwritten"
        regions.append({"index": i, "kind": "missing", "start": m["start"], "end": m["end"], "length": m["length"],
                        "offset_hex": hexoff(m["start"]), "expected_content": text, "known_evidence": ev, "candidates": cands,
                        "level": lvl, "reason": why})
    for j, c in enumerate(corr):
        frag = c.get("fragment_id")
        dup = [fr["id"] for fr in ctx.fragments.values() if frag and (fr.get("duplicate_of") == frag or ctx.fragments.get(frag, {}).get("duplicate_of") == fr["id"])]
        lvl = "MEDIUM" if dup else "LOW"
        regions.append({"index": len(miss) + j, "kind": "corrupted", "start": c["start"], "end": c["end"], "length": c["length"],
                        "offset_hex": hexoff(c["start"]), "expected_content": c.get("note") or "bytes present but fail validation",
                        "known_evidence": [f"source {c.get('source_offset_hex')} in {frag}"] if frag else [],
                        "candidates": [{"id": d, "strength": "duplicate", "evidence": ["byte-identical copy of the source fragment exists"],
                                        "conflicts": [], "compatible": True, "offset_hex": hexoff(ctx.fragments[d]["offset"]),
                                        "length": ctx.fragments[d]["length"], "family": ctx.fragments[d]["family"], "assigned_to": ctx.fragments[d].get("assigned_to")} for d in dup],
                        "level": lvl, "reason": ("a duplicate copy exists and could be compared" if dup else
                                                 "no alternative copy exists; repairing without one would mean inventing bytes")})
    plus, minus = [], []
    if f.get("signature_stub"):
        level = "LOW"
        minus.append("signature stub: " + f["signature_stub"].split("; ", 1)[-1])
    elif f.get("orphan"):
        level = "UNKNOWN"
        minus.append("orphan data: the original file's size and layout are unknown")
    elif not regions:
        level = "N/A"
        plus.append("no missing or corrupted ranges: nothing left to recover")
    else:
        order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
        level = max((r["level"] for r in regions), key=lambda x: order[x])
        n_miss = len(miss)
        if n_miss:
            plus.append(f"{n_miss} missing range(s) ({sum(m['length'] for m in miss):,} B) are declared by the file's structure") if \
                any(r["expected_content"] != "unknown" for r in regions if r["kind"] == "missing") else \
                minus.append(f"{n_miss} missing range(s) whose content the format does not declare")
        n_struct = sum(1 for r in regions for c in r["candidates"] if c["strength"] == "structural" and not c["conflicts"])
        n_fam = sum(1 for r in regions for c in r["candidates"] if c["compatible"] and c["strength"] != "structural")
        if n_struct:
            plus.append(f"{n_struct} unassigned fragment(s) structurally match expected content")
        if n_fam:
            plus.append(f"{n_fam} unassigned fragment(s) are family-compatible")
        conf = sum(1 for r in regions for c in r["candidates"] if c["conflicts"])
        if conf:
            minus.append(f"{conf} candidate(s) conflict with the expected structure")
        if corr:
            minus.append(f"{len(corr)} corrupted range(s): bytes exist but fail validation")
        if not n_struct and not n_fam and miss:
            minus.append("no compatible unassigned fragment remains in the image")
    return {"file_id": f["file_id"], "file_name": f["file_name"], "file_type": f["file_type"], "status": f["recovery_status"],
            "level": level, "plus": plus, "minus": minus, "regions": regions, "size": f["size_bytes"],
            "segments": [{k: s[k] for k in ("start", "end", "length", "kind", "fragment_id")} for s in f.get("segments", [])],
            "data_class": "DERIVED (evidence rules over the reconstruction map and unassigned fragments)"}


def assess_case(ctx: CaseCtx) -> dict[str, Any]:
    items = [assess_file(ctx, f) for f in ctx.files]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["level"]] = counts.get(it["level"], 0) + 1
    return {"files": items, "counts": counts, "heatmap": heatmap(ctx, items),
            "legend": {"recovered": "GREEN – bytes used by a recovered file", "candidate": "YELLOW – unassigned fragment that is a candidate for a gap",
                       "uncertain": "ORANGE – bytes in an orphan/uncertain reconstruction", "corrupted": "RED – bytes that fail validation",
                       "not_analyzed": "GRAY – empty or unlinked data (scanned, not part of any recovery target)"},
            "rules": __doc__.split("\n\n")[1].strip()}


def heatmap(ctx: CaseCtx, items: list[dict[str, Any]]) -> dict[str, Any]:
    dm = ctx.diskmap or {}
    unit = dm.get("unit", CLUSTER)
    size = dm.get("size") or (ctx.row.get("image_size") or 0)
    cells = -(-size // unit) if size else 0
    state = ["not_analyzed"] * cells
    owner = [None] * cells
    status = {f["file_id"]: f for f in ctx.files}
    for f in ctx.files:
        unc = f.get("orphan") or f["recovery_status"] in ("UNCERTAIN", "FRAGMENT_ONLY")
        for s in f.get("segments", []):
            if s["source_offset"] is None:
                continue
            for k in range(s["source_offset"] // unit, -(-(s["source_offset"] + s["length"]) // unit)):
                if 0 <= k < cells:
                    owner[k] = f["file_id"]
                    state[k] = "corrupted" if s["kind"] == "corrupted" else ("uncertain" if unc else "recovered")
    cand = {c["id"] for it in items for r in it["regions"] for c in r["candidates"]}
    for cid in cand:
        fr = ctx.fragments.get(cid)
        if not fr:
            continue
        for k in range(fr["offset"] // unit, -(-(fr["offset"] + fr["length"]) // unit)):
            if 0 <= k < cells and state[k] == "not_analyzed":
                state[k] = "candidate"
                owner[k] = cid
    code = {"not_analyzed": 0, "recovered": 1, "candidate": 2, "uncertain": 3, "corrupted": 4}
    # cap payload: aggregate into at most 6000 cells (worst state wins)
    agg = max(1, -(-cells // 6000))
    out, own = [], []
    for i in range(0, cells, agg):
        block = state[i:i + agg]
        worst = max(block, key=lambda s: code[s])
        out.append(code[worst])
        own.append(next((owner[i + j] for j, s in enumerate(block) if s == worst), None))
    return {"unit": unit * agg, "cells": len(out), "codes": code, "state": out, "owner": own,
            "names": {k: v["file_name"] for k, v in status.items()}}
