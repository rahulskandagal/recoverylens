"""PDF structural validation, render test and xref-rebuild repair.

Every check is recorded with a weight, a status (pass / partial / fail / na), a score in
[0, 1] and the evidence it was based on. PDF integrity is the weighted mean of the scored
checks (checks that do not apply are excluded from the denominator):

    header 10 · %%EOF 5 · startxref 5 · xref offsets 15 · trailer /Root+/Size 10 ·
    page tree 15 · indirect references resolve 15 · streams (/Length + decode) 10 · render 15

The renderer (PyMuPDF / MuPDF) silently repairs damaged files, so "render" is reported
together with whether MuPDF had to repair the structure.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Any

try:  # optional dependency: real render test and page previews
    import pymupdf  # type: ignore
except Exception:  # pragma: no cover
    pymupdf = None

WEIGHTS = {
    "header": 10, "eof": 5, "startxref": 5, "xref_offsets": 15, "trailer": 10,
    "page_tree": 15, "references": 15, "streams": 10, "render": 15,
}
CATEGORY = {
    "header": "structural", "eof": "structural", "startxref": "structural", "xref_offsets": "checksum",
    "trailer": "structural", "page_tree": "parser", "references": "checksum", "streams": "checksum", "render": "parser",
}
NAMES = {
    "header": "%PDF-x.y header", "eof": "%%EOF trailer marker", "startxref": "startxref points to an xref section",
    "xref_offsets": "xref offsets land on real 'obj' boundaries", "trailer": "Trailer /Root and /Size valid",
    "page_tree": "Page tree resolves (page count > 0)", "references": "Indirect references resolve (Links)",
    "streams": "Streams: /Length matches and filters decode", "render": "Pages render cleanly (MuPDF)",
}

OBJ_RE = re.compile(rb"(?<![0-9])(\d{1,7})\s+(\d{1,5})\s+obj\b")
REF_RE = re.compile(rb"(?<![0-9])(\d{1,7})\s+(\d{1,5})\s+R\b")


@dataclass
class PdfObj:
    num: int
    gen: int
    start: int
    end: int  # offset just after "endobj" (or where the object was cut off)
    body: bytes
    intact: bool
    reason: str = ""
    stream: bytes | None = None
    in_objstm: int | None = None


@dataclass
class PdfReport:
    checks: list[dict[str, Any]] = field(default_factory=list)
    objects: dict[int, PdfObj] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    damaged_ranges: list[tuple[int, int, str]] = field(default_factory=list)
    previews: list[bytes] = field(default_factory=list)

    @property
    def integrity(self) -> float:
        num = sum(c["weight"] * c["score"] for c in self.checks if c["status"] != "na")
        den = sum(c["weight"] for c in self.checks if c["status"] != "na")
        return round(100.0 * num / den, 1) if den else 0.0

    def check(self, cid: str) -> dict[str, Any]:
        return next(c for c in self.checks if c["id"] == cid)


def _add(r: PdfReport, cid: str, status: str, evidence: str, score: float | None = None) -> None:
    sc = {"pass": 1.0, "fail": 0.0, "na": 0.0}.get(status, 0.5) if score is None else max(0.0, min(1.0, score))
    r.checks.append({"id": cid, "name": NAMES[cid], "status": status, "weight": WEIGHTS[cid],
                     "score": round(sc, 3), "points": round(WEIGHTS[cid] * sc, 1) if status != "na" else None,
                     "detail": evidence, "evidence": evidence, "category": CATEGORY[cid]})


def _ratio_status(ok: int, total: int) -> str:
    if total == 0:
        return "na"
    return "pass" if ok == total else "fail" if ok == 0 else "partial"


# ------------------------------------------------------------------------------------------
# object scanning (independent of the xref table, so it also works on broken files)
# ------------------------------------------------------------------------------------------

def _dict_ok(body: bytes) -> bool:
    head = body.split(b"stream", 1)[0]
    return head.count(b"<<") == head.count(b">>")


def garbage_runs(data: bytes, min_run: int = 24) -> list[tuple[int, int]]:
    """Byte runs that cannot be PDF syntax: long stretches of non-text outside any stream.
    Used to pinpoint overwritten regions (e.g. a FF00 fill pattern)."""
    runs, i, n = [], 0, len(data)
    in_stream = False
    while i < n:
        if data.startswith(b"stream", i) and not data.startswith(b"endstream", i - 3):
            in_stream = True
        elif data.startswith(b"endstream", i):
            in_stream = False
        c = data[i]
        if not in_stream and not (32 <= c < 127 or c in (9, 10, 13)):
            j = i
            bad = 0
            while j < n and bad * 1.0 >= (j - i) * 0.6:
                cj = data[j]
                if not (32 <= cj < 127 or cj in (9, 10, 13)):
                    bad += 1
                j += 1
                if j - i > 8 and data[j - 8:j].isascii() and all(32 <= x < 127 or x in (9, 10, 13) for x in data[j - 8:j]):
                    j -= 8
                    break
            if j - i >= min_run and i > 16:  # the binary comment on line 2 is legal
                runs.append((i, j))
            i = max(j, i + 1)
            continue
        i += 1
    return runs


def scan_objects(data: bytes) -> dict[int, PdfObj]:
    starts = [(m.start(), int(m.group(1)), int(m.group(2)), m.end()) for m in OBJ_RE.finditer(data)]
    objs: dict[int, PdfObj] = {}
    for i, (s, num, gen, hdr_end) in enumerate(starts):
        nxt = starts[i + 1][0] if i + 1 < len(starts) else len(data)
        e = data.find(b"endobj", hdr_end)
        stream = None
        if e != -1 and e < nxt:
            body = data[hdr_end:e]
            ok = _dict_ok(body)
            reason = "" if ok else "unbalanced dictionary (bytes damaged)"
            if ok and b"stream" in body:
                m = re.search(rb"stream\r?\n", body)
                if m:
                    se = body.rfind(b"endstream")
                    stream = body[m.end():se] if se != -1 else None
                    if se == -1:
                        ok, reason = False, "stream not terminated"
            objs[num] = PdfObj(num, gen, s, e + 6, body, ok, reason, stream)
        else:
            objs[num] = PdfObj(num, gen, s, nxt, data[hdr_end:nxt], False, "object cut off: no endobj before the next object/end of data")
    return objs


def _resolve_int(v: bytes, objs: dict[int, PdfObj]) -> int | None:
    m = re.fullmatch(rb"\s*(\d+)\s+(\d+)\s+R\s*", v)
    if m:
        o = objs.get(int(m.group(1)))
        if o and o.intact:
            n = re.search(rb"^\s*(\d+)\s*$", o.body)
            return int(n.group(1)) if n else None
        return None
    m = re.fullmatch(rb"\s*(\d+)\s*", v)
    return int(m.group(1)) if m else None


def _dict_value(body: bytes, key: bytes) -> bytes | None:
    m = re.search(rb"/" + key + rb"\s*((?:\d+\s+\d+\s+R)|\d+|/\w+|\[[^\]]*\])", body)
    return m.group(1) if m else None


def _decode(raw: bytes, body: bytes) -> tuple[bytes | None, str]:
    filt = _dict_value(body, b"Filter") or b""
    if not filt:
        return raw, "unfiltered"
    if b"FlateDecode" in filt:
        for wbits in (15, -15):
            try:
                out = zlib.decompressobj(wbits).decompress(raw)
                return _png_unpredict(out, body), "FlateDecode ok"
            except zlib.error:
                continue
        return None, "FlateDecode error"
    return None, f"filter {filt.decode('latin-1', 'replace')} not decoded (not checked)"


def _png_unpredict(data: bytes, body: bytes) -> bytes:
    pm = re.search(rb"/Predictor\s+(\d+)", body)
    if not pm or int(pm.group(1)) < 10:
        return data
    cols = int((re.search(rb"/Columns\s+(\d+)", body) or [0, b"1"])[1])
    out, prev = bytearray(), bytearray(cols)
    for i in range(0, len(data), cols + 1):
        ft, row = data[i], bytearray(data[i + 1:i + 1 + cols])
        for j in range(len(row)):
            a = row[j - 1] if j else 0
            b = prev[j] if j < len(prev) else 0
            c = prev[j - 1] if j and j - 1 < len(prev) else 0
            if ft == 1:
                row[j] = (row[j] + a) & 255
            elif ft == 2:
                row[j] = (row[j] + b) & 255
            elif ft == 3:
                row[j] = (row[j] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[j] = (row[j] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += row
        prev = row
    return bytes(out)


def _objstm_members(objs: dict[int, PdfObj]) -> dict[int, int]:
    """Objects stored inside object streams (PDF 1.5+): member number -> container number."""
    out = {}
    for o in list(objs.values()):
        if o.intact and o.stream is not None and b"/ObjStm" in o.body:
            dec, _ = _decode(o.stream, o.body)
            n = _resolve_int(_dict_value(o.body, b"N") or b"", objs)
            if dec and n:
                nums = re.findall(rb"\d+", dec[:max(1, _resolve_int(_dict_value(o.body, b"First") or b"0", objs) or 0)])
                for k in range(0, min(len(nums), 2 * n), 2):
                    out[int(nums[k])] = o.num
    return out


# ------------------------------------------------------------------------------------------
# xref parsing
# ------------------------------------------------------------------------------------------

def _parse_classic_xref(data: bytes, pos: int) -> tuple[dict[int, int] | None, bytes | None]:
    m = re.match(rb"\s*xref\s*", data[pos:])
    if not m:
        return None, None
    p = pos + m.end()
    entries: dict[int, int] = {}
    while True:
        hm = re.match(rb"(\d+)\s+(\d+)\s*\r?\n", data[p:])
        if not hm:
            break
        first, cnt = int(hm.group(1)), int(hm.group(2))
        p += hm.end()
        for k in range(cnt):
            ln = data[p:p + 20]
            em = re.match(rb"(\d{10}) (\d{5}) ([nf])", ln)
            if not em:
                return (entries or None), None
            if em.group(3) == b"n":
                entries[first + k] = int(em.group(1))
            p += 20
    tm = re.search(rb"trailer\s*<<(.*?)>>\s*startxref", data[p:], re.S)
    return entries, (tm.group(1) if tm else None)


def _parse_xref_stream(data: bytes, pos: int, objs: dict[int, PdfObj]) -> tuple[dict[int, int] | None, bytes | None]:
    m = OBJ_RE.match(data, pos) or OBJ_RE.match(data, pos + len(data[pos:]) - len(data[pos:].lstrip()))
    if not m:
        return None, None
    o = objs.get(int(m.group(1)))
    if not o or not o.intact or b"/XRef" not in o.body or o.stream is None:
        return None, None
    dec, _ = _decode(o.stream, o.body)
    w = [int(x) for x in re.findall(rb"\d+", _dict_value(o.body, b"W") or b"")]
    if not dec or len(w) != 3:
        return None, o.body
    size = _resolve_int(_dict_value(o.body, b"Size") or b"0", objs) or 0
    idx = [int(x) for x in re.findall(rb"\d+", _dict_value(o.body, b"Index") or b"")] or [0, size]
    rec = sum(w)
    entries: dict[int, int] = {}
    p = 0
    for s, n in zip(idx[0::2], idx[1::2]):
        for k in range(n):
            row = dec[p:p + rec]
            p += rec
            if len(row) < rec:
                break
            t = int.from_bytes(row[:w[0]], "big") if w[0] else 1
            f2 = int.from_bytes(row[w[0]:w[0] + w[1]], "big")
            if t == 1:
                entries[s + k] = f2
    return entries, o.body


# ------------------------------------------------------------------------------------------
# main check
# ------------------------------------------------------------------------------------------

def check_pdf(data: bytes, render: bool = True, max_previews: int = 6) -> PdfReport:
    r = PdfReport()
    body = data.rstrip(b"\x00")
    objs = scan_objects(data)
    members = _objstm_members(objs)
    r.objects = objs
    intact = {n for n, o in objs.items() if o.intact} | set(members)
    runs = garbage_runs(data)
    for a, b in runs:
        r.damaged_ranges.append((a, b, f"overwritten region: {b - a} bytes of non-PDF data"))
    for o in objs.values():
        if not o.intact:
            cut = next((a for a, _ in runs if o.start < a < o.end), None)
            r.damaged_ranges.append((o.start, cut or o.end, f"obj {o.num}: {o.reason}"))

    # 1 header
    hm = re.search(rb"%PDF-(\d\.\d)", data[:1024])
    _add(r, "header", "pass" if hm else "fail",
         f"PDF-{hm.group(1).decode()} at offset {hm.start()}" if hm else "No %PDF-x.y signature in the first 1024 bytes")
    # 2 %%EOF
    ep = body.rfind(b"%%EOF")
    _add(r, "eof", "pass" if ep != -1 and ep >= len(body) - 1024 else "fail",
         f"%%EOF at offset {ep} of {len(body)}" if ep != -1 else "No %%EOF marker: file is truncated or its end was overwritten")
    # 3 startxref
    sp = data.rfind(b"startxref")
    sx = None
    if sp != -1:
        mm = re.match(rb"startxref\s+(\d+)", data[sp:])
        sx = int(mm.group(1)) if mm else None
    xref: dict[int, int] | None = None
    trailer: bytes | None = None
    kind = None
    if sx is not None and 0 < sx < len(data):
        xref, trailer = _parse_classic_xref(data, sx)
        kind = "classic xref table" if xref is not None else None
        if xref is None:
            xref, trailer = _parse_xref_stream(data, sx, objs)
            kind = "xref stream" if xref is not None else None
    salvaged = False
    if xref is None:
        # the xref header may be overwritten while entry lines survive just before 'trailer'
        tpos = data.rfind(b"trailer")
        if tpos != -1:
            lines = re.findall(rb"(\d{10}) (\d{5}) ([nf])[ \r\n]", data[max(0, tpos - 20 * 4096):tpos])
            zm = re.search(rb"/Size\s+(\d+)", data[tpos:])
            if lines and zm:
                first = int(zm.group(1)) - len(lines)
                xref = {first + k: int(off) for k, (off, _g, t) in enumerate(lines) if t == b"n" and first + k > 0}
                salvaged = bool(xref)
    if sx is None:
        _add(r, "startxref", "fail", "No startxref keyword found")
    elif kind is None:
        _add(r, "startxref", "fail", f"startxref={sx}, but no parseable xref table or xref stream at that offset (bytes overwritten or offset wrong)")
    else:
        _add(r, "startxref", "pass", f"startxref={sx} → {kind}")
    if trailer is None:  # fall back to any surviving trailer dictionary
        tm = list(re.finditer(rb"trailer\s*<<(.*?)>>", data, re.S))
        trailer = tm[-1].group(1) if tm else None
        if trailer is None:  # xref-stream style trailer found by scanning
            xs = next((o for o in objs.values() if o.intact and b"/XRef" in o.body), None)
            trailer = xs.body if xs else None
    # 4 xref offsets
    if xref:
        bad = []
        for num, off in sorted(xref.items()):
            m = OBJ_RE.match(data, off) if off < len(data) else None
            if not m or int(m.group(1)) != num:
                bad.append(num)
        ok = len(xref) - len(bad)
        _add(r, "xref_offsets", _ratio_status(ok, len(xref)),
             ("xref header overwritten; " + f"{len(xref)} surviving entry lines salvaged (numbered back from /Size); " if salvaged else "")
             + f"{ok}/{len(xref)} in-use entries point to the matching 'N G obj'" + (f"; invalid for objects {bad[:10]}" if bad else ""),
             ok / len(xref))
    else:
        _add(r, "xref_offsets", "fail", "No xref entries could be read, so no offsets could be verified"
             + (f"; {len(objs)} objects found by scanning instead" if objs else ""))
    # 5 trailer /Root /Size
    root_num = size = None
    if trailer:
        rm = re.search(rb"/Root\s+(\d+)\s+(\d+)\s+R", trailer)
        root_num = int(rm.group(1)) if rm else None
        zm = re.search(rb"/Size\s+(\d+)", trailer)
        size = int(zm.group(1)) if zm else None
    maxnum = max(objs) if objs else 0
    root_ok = root_num is not None and root_num in objs and objs[root_num].intact and b"/Catalog" in objs[root_num].body
    size_ok = size is not None and size >= maxnum + 1
    ev = []
    ev.append(f"/Root {root_num} 0 R → " + ("intact /Catalog" if root_ok else "missing or damaged object" if root_num is not None else "absent"))
    ev.append(f"/Size {size} " + ("≥" if size_ok else "<" if size is not None else "absent;") + (f" max object {maxnum}+1" if size is not None else ""))
    _add(r, "trailer", "pass" if root_ok and size_ok else "partial" if root_ok or size_ok else "fail",
         ("No trailer dictionary found; " if not trailer else "") + "; ".join(ev))
    # 6 page tree
    pages: list[int] = []
    declared = None
    tree_ok = False
    cat_num, cat_note = (root_num, "") if root_ok else (None, "")
    if not root_ok:  # trailer lost: locate the catalog by scanning, as repair tools do
        cat_num = next((n for n, o in objs.items() if o.intact and re.search(rb"/Type\s*/Catalog\b", o.body)), None)
        cat_note = f" (catalog obj {cat_num} located by scanning; the trailer's /Root is unusable)" if cat_num else ""
    if cat_num is not None:
        pm = re.search(rb"/Pages\s+(\d+)\s+\d+\s+R", objs[cat_num].body)
        seen: set[int] = set()

        def walk(n: int, depth: int = 0) -> bool:
            if n in seen or depth > 30:
                return False
            seen.add(n)
            o = objs.get(n)
            if not o or not o.intact:
                return False
            if re.search(rb"/Type\s*/Pages\b", o.body):
                kids = re.search(rb"/Kids\s*\[([^\]]*)\]", o.body)
                okk = True
                for km in REF_RE.finditer(kids.group(1) if kids else b""):
                    okk = walk(int(km.group(1)), depth + 1) and okk
                return okk
            if re.search(rb"/Type\s*/Page\b", o.body):
                pages.append(n)
                return True
            return False

        if pm:
            tree_ok = walk(int(pm.group(1)))
            cm = re.search(rb"/Count\s+(\d+)", objs[int(pm.group(1))].body) if int(pm.group(1)) in objs else None
            declared = int(cm.group(1)) if cm else None
    orphan_pages = [n for n, o in objs.items() if o.intact and re.search(rb"/Type\s*/Page\b", o.body) and n not in pages]
    if tree_ok and pages and (declared is None or declared == len(pages)):
        _add(r, "page_tree", "pass", f"Catalog → Pages tree resolves to {len(pages)} page(s)" + (f" (/Count {declared})" if declared else "") + cat_note)
    elif pages:
        _add(r, "page_tree", "partial", f"{len(pages)} page(s) reachable, /Count {declared}; some Kids unresolved" + cat_note, len(pages) / max(1, declared or len(pages)))
    else:
        _add(r, "page_tree", "fail", ("Catalog missing or damaged, so the page tree cannot be walked" if cat_num is None else "Page tree does not reach any page")
             + (f"; {len(orphan_pages)} unreachable /Page object(s) survive: {orphan_pages[:6]}" if orphan_pages else "; no /Page objects survive"))
    # 7 indirect references
    refs: list[tuple[int, int]] = []
    for o in objs.values():
        if o.intact:
            head = o.body.split(b"stream", 1)[0]
            refs += [(o.num, int(m.group(1))) for m in REF_RE.finditer(head)]
    if trailer:
        refs += [(-1, int(m.group(1))) for m in REF_RE.finditer(trailer)]
    unresolved = [(a, b) for a, b in refs if b not in intact]
    ok = len(refs) - len(unresolved)
    _add(r, "references", _ratio_status(ok, len(refs)),
         f"{ok}/{len(refs)} indirect references resolve to intact objects"
         + (f"; unresolved: {', '.join(f'{b} (from {'trailer' if a < 0 else f'obj {a}'})' for a, b in unresolved[:6])}" if unresolved else ""),
         ok / len(refs) if refs else 0.0)
    # 8 streams
    s_total = s_ok = 0
    s_notes: list[str] = []
    stream_objs = [o for o in objs.values() if b"stream" in o.body.split(b"endstream", 1)[0][:4096] and re.search(rb"stream\r?\n", o.body)]
    for o in stream_objs:
        s_total += 1
        if not o.intact or o.stream is None:
            s_notes.append(f"obj {o.num}: stream damaged/cut off")
            continue
        declared_len = _resolve_int(_dict_value(o.body, b"Length") or b"", objs)
        actual = len(o.stream.rstrip(b"\r\n"))
        len_ok = declared_len is not None and abs(declared_len - actual) <= 2
        dec, how = _decode(o.stream[:declared_len] if declared_len and len_ok else o.stream, o.body)
        dec_ok = dec is not None or "not decoded" in how
        if len_ok and dec_ok:
            s_ok += 1
        else:
            s_notes.append(f"obj {o.num}: " + ("; ".join(x for x in [
                None if len_ok else f"/Length {declared_len} vs actual {actual}", None if dec_ok else how] if x)))
            r.damaged_ranges.append((o.start, o.end, f"obj {o.num}: stream " + ("length mismatch" if not len_ok else how)))
    _add(r, "streams", _ratio_status(s_ok, s_total),
         f"{s_ok}/{s_total} streams have a matching /Length and decode" + (f"; {'; '.join(s_notes[:4])}" if s_notes else ""),
         s_ok / s_total if s_total else 0.0)
    # 9 render test
    rendered = 0
    page_count = 0
    repaired = None
    if render and pymupdf is not None:
        try:
            pymupdf.TOOLS.mupdf_display_errors(False)
            pymupdf.TOOLS.mupdf_warnings(reset=True)
            doc = pymupdf.open(stream=data, filetype="pdf")
            repaired = bool(doc.is_repaired)
            page_count = doc.page_count
            pymupdf.TOOLS.mupdf_warnings(reset=True)  # open-time repair warnings are reported via `repaired`
            for i in range(page_count):
                try:
                    pix = doc[i].get_pixmap(dpi=60 if i >= max_previews else 90)
                    warn = pymupdf.TOOLS.mupdf_warnings(reset=True)
                    if not warn:
                        rendered += 1
                    if i < max_previews:
                        r.previews.append(pix.tobytes("png"))
                except Exception:
                    pass
            doc.close()
        except Exception as e:
            repaired = True
            s_notes.append(f"renderer could not open file: {e}")
        ev = f"{rendered}/{page_count} page(s) rendered without errors"
        if repaired:
            ev += "; MuPDF had to repair the file structure before rendering"
        _add(r, "render", "na" if False else _ratio_status(rendered, page_count) if page_count else "fail",
             ev if page_count else "MuPDF found 0 renderable pages" + (f" ({len(orphan_pages)} unreachable page object(s) survive)" if orphan_pages else "")
             + ("; structure required repair" if repaired else ""),
             rendered / page_count if page_count else 0.0)
    else:
        _add(r, "render", "na", "PyMuPDF not installed: render test skipped (not counted)")
    if not stream_objs:
        r.checks[-2]["evidence"] = r.checks[-2]["detail"] = "No stream objects survive to be checked (not counted)"
    r.stats = {
        "objects_found": len(objs), "objects_intact": sum(1 for o in objs.values() if o.intact),
        "objects_in_objstm": len(members), "xref_kind": kind, "xref_entries": len(xref or {}),
        "root": root_num, "catalog": cat_num, "size": size, "pages_reachable": len(pages), "pages_declared": declared,
        "orphan_page_objects": orphan_pages, "references_total": len(refs), "references_resolved": ok,
        "streams_total": s_total, "streams_ok": s_ok, "render_pages": page_count, "render_clean": rendered,
        "render_repaired": repaired, "renderer": "PyMuPDF " + pymupdf.__version__ if pymupdf else None,
        "startxref": sx,
    }
    r.stats["object_inventory"] = [{"num": o.num, "offset": o.start, "end": o.end, "intact": o.intact, "reason": o.reason,
                                    "type": (re.search(rb"/Type\s*/(\w+)", o.body) or [None, b""])[1].decode("latin-1") if o.intact else None}
                                   for o in sorted(objs.values(), key=lambda x: x.num)]
    return r


def pdf_status(r: PdfReport, complete_bytes: bool) -> tuple[str, str]:
    """Status decided by named checks; the reason lists the deciding evidence."""
    c = {x["id"]: x for x in r.checks}
    st = r.stats
    render_known = c["render"]["status"] != "na"
    pages_ok = st["render_clean"] if render_known else st["pages_reachable"]
    pages_total = st["render_pages"] if render_known else (st["pages_declared"] or st["pages_reachable"])
    failing = [x for x in r.checks if x["status"] in ("fail", "partial")]

    def summary() -> str:
        bits = [short_phrase(x, st) for x in failing if x["id"] != "render"]
        if c["streams"]["status"] == "pass":
            bits.append("streams OK")
        if pages_total:
            bits.append(f"{pages_ok}/{pages_total} page(s) {'rendered' if render_known else 'reachable'}")
        else:
            orphans = len(st.get("orphan_page_objects") or [])
            bits.append("0 renderable pages" + (f" ({orphans} page object(s) survive, unreachable)" if orphans else ""))
        ok_n = sum(1 for x in r.checks if x["status"] == "pass")
        scored = sum(1 for x in r.checks if x["status"] != "na")
        bits.append(f"{ok_n}/{scored} checks pass")
        return "; ".join(bits)

    if not failing and complete_bytes and pages_total and pages_ok == pages_total:
        return "FULLY_RECOVERED", "Fully recovered: " + summary()
    structural = all(c[k]["status"] == "pass" for k in ("header", "trailer", "page_tree"))
    if structural and pages_total and pages_ok == pages_total and c["references"]["score"] >= 0.9:
        return "MOSTLY_RECOVERED", "Mostly recovered: " + summary()
    if pages_ok:
        return "PARTIALLY_RECOVERED", "Partially recovered: " + summary()
    if st["objects_intact"]:
        return "FRAGMENT_ONLY", (f"Fragment only: {st['objects_intact']}/{st['objects_found']} objects intact but no page renders; " + summary())
    return "UNRECOVERABLE", "Unrecoverable: no intact PDF objects; " + summary()


SHORT_OK = {"header": "header OK", "eof": "%%EOF OK", "startxref": "startxref OK", "xref_offsets": "xref offsets OK",
            "trailer": "trailer OK", "page_tree": "page tree OK", "references": "all references resolve",
            "streams": "streams OK", "render": "all pages render"}


def short_phrase(x: dict[str, Any], st: dict[str, Any]) -> str:
    """One compact clause per check, used in status reasons."""
    cid, ev = x["id"], x["evidence"]
    if x["status"] == "pass":
        return SHORT_OK[cid]
    if x["status"] == "na":
        return f"{cid}: n/a"
    m = re.search(r"(\d+)/(\d+)", ev)
    if cid == "header":
        return "no %PDF header"
    if cid == "eof":
        return "%%EOF missing (truncated end)"
    if cid == "startxref":
        return "startxref does not lead to a readable xref"
    if cid == "xref_offsets":
        return f"xref offsets invalid for {int(m.group(2)) - int(m.group(1))}/{m.group(2)} objects" if m else "xref entries unreadable"
    if cid == "trailer":
        return f"trailer /Root (obj {st.get('root')}) missing or damaged" if "missing or damaged" in ev else "trailer /Root or /Size invalid"
    if cid == "page_tree":
        return "page tree unresolvable" if x["status"] == "fail" else "page tree partially resolvable"
    if cid == "references":
        return f"{int(m.group(2)) - int(m.group(1))}/{m.group(2)} references unresolved" if m else "references unresolved"
    if cid == "streams":
        return f"{int(m.group(2)) - int(m.group(1))}/{m.group(2)} streams fail /Length or decode" if m else "streams damaged"
    if cid == "render":
        return f"{st.get('render_clean', 0)}/{st.get('render_pages', 0)} pages render" if st.get("render_pages") else "0 renderable pages"
    return ev.split(";")[0]


def repair_pdf(data: bytes, r: PdfReport) -> tuple[bytes, dict[str, Any]] | None:
    """Rebuild the cross-reference table from scanned intact objects.

    Only structure is derived: every object body is copied byte-for-byte from the input.
    If the catalog or page tree is lost, a minimal Catalog/Pages pair is synthesized that
    points at the surviving /Page objects. It is listed in `synthesized_objects`, and the
    content of damaged objects is never recreated.
    """
    intact = [o for o in sorted(r.objects.values(), key=lambda x: x.num) if o.intact and b"/XRef" not in o.body]
    if not intact:
        return None
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for o in intact:
        offsets[o.num] = len(out)
        out += data[o.start:o.end] + b"\n"
    synthesized: list[str] = []
    root = r.stats.get("root")
    root_ok = root in offsets and b"/Catalog" in r.objects[root].body
    if not root_ok and r.stats.get("catalog") in offsets:  # trailer lost but the catalog survives
        root, root_ok = r.stats["catalog"], True
    if not root_ok:
        page_objs = [o.num for o in intact if re.search(rb"/Type\s*/Page\b", o.body)]
        nxt = max(r.objects) + 1
        pages_num, cat_num = nxt, nxt + 1
        kids = " ".join(f"{n} 0 R" for n in page_objs)
        offsets[pages_num] = len(out)
        out += f"{pages_num} 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_objs)} >>\nendobj\n".encode()
        offsets[cat_num] = len(out)
        out += f"{cat_num} 0 obj\n<< /Type /Catalog /Pages {pages_num} 0 R >>\nendobj\n".encode()
        synthesized = [f"{pages_num} 0 obj (/Pages over {len(page_objs)} surviving page object(s))", f"{cat_num} 0 obj (/Catalog)"]
        root = cat_num
    size = max(offsets) + 1
    xref_at = len(out)
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for n in range(1, size):
        out += (f"{offsets[n]:010d} 00000 n \n" if n in offsets else "0000000000 65535 f \n").encode()
    out += f"trailer\n<< /Size {size} /Root {root} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out), {
        "method": "xref rebuilt by scanning intact objects; object bodies copied byte-for-byte",
        "objects_kept": [o.num for o in intact],
        "objects_dropped": sorted(n for n, o in r.objects.items() if not o.intact),
        "synthesized_objects": synthesized,
    }
