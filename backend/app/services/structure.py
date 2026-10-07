"""Universal File Structure Explorer: plugin-based deterministic parsers.

Each parser walks the reconstructed bytes with a real format grammar and emits a component tree.
Every component is mapped back to the reconstruction's segments to report whether its bytes were
recovered, missing (placeholder) or corrupted, and which evidence fragment they came from.
No LLM or heuristic guesses decide binary validity. Unsupported formats say so.

Add a format:   @structure_parser("ext", "Human name")  def parse(data, f) -> list[node]
"""
from __future__ import annotations

import re
import struct
import zlib
from typing import Any, Callable

from ..engine.common import hexoff
from ..engine.pdfcheck import scan_objects
from ..engine.sqlitefmt import parse_page
from ..engine.validators import member_bytes, parse_central_directory
from .context import range_state

PARSERS: dict[str, tuple[str, Callable[[bytes, dict[str, Any]], list[dict[str, Any]]]]] = {}


def structure_parser(*types: str, name: str):
    def deco(fn):
        for t in types:
            PARSERS[t] = (name, fn)
        return fn
    return deco


def node(f: dict[str, Any], name: str, kind: str, off: int, length: int, valid: str = "pass", detail: str = "",
         children: list[dict[str, Any]] | None = None, confidence: float | None = None) -> dict[str, Any]:
    """A component. valid: pass | partial | fail | na  (deterministic parser verdict on these bytes)."""
    state, frags = range_state(f, off, off + max(1, length))
    if state == "missing":
        valid = "fail" if valid == "pass" else valid
    if confidence is None:
        confidence = {"recovered": 1.0, "corrupted": 0.4, "partial": 0.5, "missing": 0.0}[state]
        if valid == "fail":
            confidence = min(confidence, 0.3)
        elif valid == "partial":
            confidence = min(confidence, 0.7)
    return {"name": name, "kind": kind, "offset": off, "offset_hex": hexoff(off), "length": length, "validation": valid,
            "state": state, "source_fragments": frags, "confidence": round(confidence * 100), "detail": detail,
            "children": children or []}


# ------------------------------------------------------------------------------------------ PDF

@structure_parser("pdf", name="PDF (object grammar)")
def parse_pdf(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    m = re.match(rb"%PDF-(\d\.\d)", data)
    out.append(node(f, f"Header %PDF-{m.group(1).decode() if m else '?'}", "header", 0, 8 if m else 0,
                    "pass" if m else "fail", "version header at offset 0" if m else "no %PDF- signature at offset 0"))
    objs = scan_objects(data)
    pages, streams, others = [], [], []
    for num in sorted(objs):
        o = objs[num]
        tm = re.search(rb"/Type\s*/(\w+)", o.body)
        typ = tm.group(1).decode() if tm else ("stream" if o.stream is not None else "object")
        n = node(f, f"obj {num} {o.gen} – {typ}", "object", o.start, o.end - o.start, "pass" if o.intact else "fail",
                 o.reason or ("stream " + (f"{len(o.stream):,} B" if o.stream is not None else "") if o.stream is not None else "dictionary balanced"))
        if typ == "Page":
            pages.append(n)
        elif o.stream is not None:
            filt = re.search(rb"/Filter\s*/(\w+)", o.body)
            ok = "pass"
            detail = f"stream {len(o.stream):,} B" + (f", /{filt.group(1).decode()}" if filt else "")
            if filt and filt.group(1) == b"FlateDecode":
                try:
                    zlib.decompress(o.stream.rstrip(b"\r\n"))
                    detail += ", inflates cleanly"
                except zlib.error as e:
                    ok, detail = "fail", detail + f", inflate error: {e}"
            n["validation"] = ok if o.intact else "fail"
            n["detail"] = detail
            streams.append(n)
        else:
            others.append(n)
    out.append({**node(f, f"Objects ({len(objs)})", "group", 0, 0, "pass" if objs and all(o.intact for o in objs.values()) else "partial" if objs else "fail",
                       f"{sum(o.intact for o in objs.values())}/{len(objs)} intact"), "children": others, "offset": None, "offset_hex": None})
    out.append({**node(f, f"Pages ({len(pages)})", "group", 0, 0, "pass" if pages else "fail", "page objects (/Type /Page)"),
                "children": pages, "offset": None, "offset_hex": None})
    out.append({**node(f, f"Streams ({len(streams)})", "group", 0, 0, "pass" if all(s["validation"] == "pass" for s in streams) else "partial"),
                "children": streams, "offset": None, "offset_hex": None})
    sx = data.rfind(b"startxref")
    xo = None
    if sx != -1:
        mm = re.match(rb"startxref\s+(\d+)", data[sx:sx + 40])
        xo = int(mm.group(1)) if mm else None
    if xo is not None and 0 <= xo < len(data) and (data[xo:xo + 4] == b"xref" or re.match(rb"\d+ \d+ obj", data[xo:xo + 20])):
        tr = data.find(b"trailer", xo)
        end = tr if tr != -1 else sx
        out.append(node(f, "Cross-reference table" if data[xo:xo + 4] == b"xref" else "Cross-reference stream", "xref", xo, max(1, end - xo),
                        "pass", f"startxref → {xo}"))
    else:
        out.append(node(f, "Cross-reference", "xref", xo or 0, 0, "fail", f"startxref={xo} does not lead to an xref section"))
    tr = data.rfind(b"trailer")
    if tr != -1:
        root = re.search(rb"/Root\s+(\d+)\s+\d+\s+R", data[tr:tr + 400])
        ok = bool(root) and int(root.group(1)) in objs
        out.append(node(f, "Trailer", "trailer", tr, max(1, (sx if sx > tr else tr + 40) - tr), "pass" if ok else "partial",
                        f"/Root {root.group(1).decode()} 0 R" + (" resolves" if ok else " does not resolve") if root else "no /Root entry"))
    eof = data.rfind(b"%%EOF")
    out.append(node(f, "%%EOF marker", "footer", eof if eof != -1 else len(data), 5 if eof != -1 else 0, "pass" if eof != -1 else "fail",
                    "end-of-file marker" if eof != -1 else "missing"))
    return out


# ------------------------------------------------------------------------------------------ JPEG

JPEG_NAMES = {0xD8: "SOI", 0xC0: "SOF0 (baseline)", 0xC2: "SOF2 (progressive)", 0xC4: "DHT (Huffman table)", 0xDB: "DQT (quantization table)",
              0xDD: "DRI (restart interval)", 0xDA: "SOS (start of scan)", 0xFE: "COM (comment)", 0xD9: "EOI"}


@structure_parser("jpeg", name="JPEG (marker segments)")
def parse_jpeg(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if data[:2] != b"\xff\xd8":
        return [node(f, "SOI", "header", 0, 0, "fail", "no SOI marker at offset 0 (headerless data)")]
    out.append(node(f, "SOI", "header", 0, 2, "pass", "start of image"))
    p = 2
    groups: dict[str, list[dict[str, Any]]] = {"APP": [], "DQT": [], "DHT": [], "other": []}
    while p + 4 <= len(data):
        if data[p] != 0xFF:
            out.append(node(f, "Marker chain broken", "error", p, 1, "fail", f"expected 0xFF at {p}"))
            return out
        mk = data[p + 1]
        ln = struct.unpack(">H", data[p + 2:p + 4])[0]
        name = f"APP{mk - 0xE0}" if 0xE0 <= mk <= 0xEF else JPEG_NAMES.get(mk, f"marker 0x{mk:02X}")
        seg = node(f, name, "segment", p, ln + 2, "pass" if p + 2 + ln <= len(data) else "fail", f"length {ln}")
        if 0xE0 <= mk <= 0xEF:
            ident = data[p + 4:p + 9].split(b"\x00")[0].decode("latin-1", "replace")
            seg["detail"] += f", identifier '{ident}'"
            groups["APP"].append(seg)
        elif mk == 0xDB:
            groups["DQT"].append(seg)
        elif mk == 0xC4:
            groups["DHT"].append(seg)
        elif mk in (0xC0, 0xC2):
            h, w = struct.unpack(">HH", data[p + 5:p + 9])
            seg["detail"] += f", {w}×{h}, {data[p + 9]} component(s)"
            out.append(seg)
        elif mk == 0xDA:
            out.extend([{**node(f, f"{k} segments ({len(v)})", "group", 0, 0, "pass" if v else ("fail" if k in ("DQT", "DHT") else "na")),
                         "children": v, "offset": None, "offset_hex": None} for k, v in groups.items() if v or k in ("DQT", "DHT")])
            out.append(seg)
            scan_start = p + 2 + ln
            eoi = data.rfind(b"\xff\xd9")
            end = eoi if eoi > scan_start else len(data)
            rst = len(re.findall(rb"\xff[\xd0-\xd7]", data[scan_start:end]))
            out.append(node(f, "Entropy-coded image data", "data", scan_start, end - scan_start, "pass" if eoi > scan_start else "partial",
                            f"{end - scan_start:,} B, {rst} restart marker(s)"))
            out.append(node(f, "EOI", "footer", eoi if eoi > scan_start else len(data), 2 if eoi > scan_start else 0,
                            "pass" if eoi > scan_start else "fail", "end of image" if eoi > scan_start else "EOI marker not found"))
            return out
        else:
            groups["other"].append(seg)
        p += 2 + ln
    out.append(node(f, "SOS", "segment", p, 0, "fail", "scan header not reached"))
    return out


# ------------------------------------------------------------------------------------------ ZIP / OOXML

def _ooxml_role(name: str) -> str:
    n = name.lower()
    if n.endswith("document.xml") or n.endswith("workbook.xml") or n.endswith("presentation.xml"):
        return "main document"
    if n.endswith("styles.xml"):
        return "styles"
    if "_rels/" in n or n.endswith(".rels"):
        return "relationships"
    if "/media/" in n:
        return "media"
    if n.startswith("docprops/"):
        return "document properties"
    if n == "[content_types].xml":
        return "content types"
    if "vbaproject" in n:
        return "VBA macro project (not executed)"
    return "part"


@structure_parser("zip", "docx", "xlsx", "pptx", name="ZIP / OOXML container")
def parse_zip(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    members, eocd = parse_central_directory(data)
    if eocd is None:
        return [node(f, "End of central directory", "footer", len(data), 0, "fail", "EOCD record not found: container index missing")]
    kids = []
    for m in members:
        lo = m["local_offset"]
        local_ok = data[lo:lo + 4] == b"PK\x03\x04"
        payload, why = member_bytes(data, m) if local_ok else (None, "local header missing")
        st = "pass" if why == "crc ok" else "fail"
        nlen = struct.unpack("<H", data[lo + 26:lo + 28])[0] if local_ok else len(m["name"])
        kids.append(node(f, m["name"], _ooxml_role(m["name"]), lo, 30 + nlen + m["csize"], st,
                         f"{_ooxml_role(m['name'])}; {'deflate' if m['method'] == 8 else 'stored' if m['method'] == 0 else m['method']}, "
                         f"{m['csize']:,} → {m['usize']:,} B; {why}"))
    by_role: dict[str, list[dict[str, Any]]] = {}
    for k in kids:
        by_role.setdefault(k["kind"], []).append(k)
    out = [node(f, "ZIP container", "container", 0, len(data), "pass" if all(k["validation"] == "pass" for k in kids) else "partial",
                f"{len(members)} member(s) listed in the central directory")]
    for role, items in by_role.items():
        out.append({**node(f, f"{role} ({len(items)})", "group", 0, 0, "pass" if all(i["validation"] == "pass" for i in items) else "partial"),
                    "children": items, "offset": None, "offset_hex": None})
    out.append(node(f, "Central directory", "index", eocd["cd_offset"], eocd["cd_size"], "pass" if members else "fail",
                    f"{len(members)}/{eocd['entries']} entries parsed"))
    out.append(node(f, "End of central directory", "footer", eocd["eocd_offset"], 22, "pass", f"declares {eocd['entries']} entries"))
    return out


# ------------------------------------------------------------------------------------------ SQLite

@structure_parser("sqlite", name="SQLite 3 (b-tree pages)")
def parse_sqlite(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    if not data.startswith(b"SQLite format 3\x00"):
        return [node(f, "Database header", "header", 0, 0, "fail", "no 'SQLite format 3' header (headerless pages)")]
    ps = struct.unpack(">H", data[16:18])[0]
    ps = 65536 if ps == 1 else ps
    count = struct.unpack(">I", data[28:32])[0]
    out = [node(f, "Database header (100 B)", "header", 0, 100, "pass", f"page size {ps}, {count} page(s) declared, "
                f"encoding {({1: 'UTF-8', 2: 'UTF-16le', 3: 'UTF-16be'}).get(struct.unpack('>I', data[56:60])[0], '?')}")]
    pages = []
    types: dict[str, int] = {}
    for i in range(count):
        off = i * ps
        b = data[off:off + ps]
        state, _ = range_state(f, off, off + ps)
        if state == "missing" or len(b) < ps:
            pages.append(node(f, f"page {i + 1}", "page", off, ps, "fail", "bytes not recovered (placeholder)"))
            continue
        try:
            info = parse_page(b, ps, is_first=(i == 0))
        except Exception as e:
            info = {"type": "invalid", "error": str(e)}
        t = info["type"]
        types[t] = types.get(t, 0) + 1
        detail = t.replace("_", " ") + (f", {info.get('ncell')} cell(s)" if info.get("ncell") is not None else "")
        if info.get("rows"):
            detail += f", rowids {info.get('min_rowid')}–{info.get('max_rowid')}"
        pages.append(node(f, f"page {i + 1}", "page", off, ps, "fail" if t == "invalid" else "partial" if info.get("errors") else "pass", detail))
    schema = []
    for t in f.get("structure", {}).get("tables", []) or []:
        schema.append({**node(f, f"table {t.get('name')}", "table", 0, 0, "pass",
                              f"root page {t.get('root_page', '?')}; {len(t.get('columns') or [])} column(s); "
                              f"{t.get('rows_recovered', len(t.get('sample', [])))} row(s) recovered"),
                       "offset": None, "offset_hex": None})
    idx = [p for p in pages if "index" in p["detail"]]
    out.append({**node(f, f"Schema ({len(schema)} table(s))", "group", 0, 0, "pass" if schema else "na", "from sqlite_master on page 1"),
                "children": schema, "offset": None, "offset_hex": None})
    if idx:
        out.append({**node(f, f"Indexes ({len(idx)} page(s))", "group", 0, 0, "pass"), "children": idx, "offset": None, "offset_hex": None})
    out.append({**node(f, f"Pages ({count})", "group", 0, 0, "pass" if all(p["validation"] == "pass" for p in pages) else "partial",
                       ", ".join(f"{k.replace('_', ' ')} ×{v}" for k, v in types.items())),
                "children": pages, "offset": None, "offset_hex": None})
    return out


# ------------------------------------------------------------------------------------------ MP4 / ISO-BMFF

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"dinf", b"meta"}
BOX_NAMES = {"ftyp": "file type", "moov": "movie metadata", "mdat": "media data", "trak": "track", "mvhd": "movie header",
             "tkhd": "track header", "udta": "user data / metadata", "meta": "metadata", "free": "free space"}


def _boxes(data: bytes, f: dict[str, Any], start: int, end: int, depth: int = 0) -> tuple[list[dict[str, Any]], int]:
    out, p = [], start
    while p + 8 <= end:
        size, typ = struct.unpack(">I4s", data[p:p + 8])
        hdr = 8
        if size == 1 and p + 16 <= end:
            size, hdr = struct.unpack(">Q", data[p + 8:p + 16])[0], 16
        elif size == 0:
            size = end - p
        if size < hdr or p + size > end or not typ.isalnum():
            out.append(node(f, f"invalid box at {p}", "error", p, end - p, "fail", "box size/type does not parse"))
            return out, p
        t = typ.decode("latin-1")
        kids = []
        if typ in CONTAINERS and depth < 6:
            kids, _ = _boxes(data, f, p + hdr + (4 if typ == b"meta" else 0), p + size, depth + 1)
        out.append(node(f, f"{t} – {BOX_NAMES.get(t, 'box')}", "box", p, size, "pass", f"{size:,} B", kids))
        p += size
    return out, p


@structure_parser("mp4", name="ISO-BMFF / MP4 (box tree)")
def parse_mp4(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    boxes, end = _boxes(data, f, 0, len(data.rstrip(b"\x00")))
    names = [b["name"].split(" ")[0] for b in boxes]
    for need in ("ftyp", "moov", "mdat"):
        if need not in names:
            boxes.append(node(f, f"{need} – {BOX_NAMES[need]}", "box", end, 0, "fail", "required box not found"))
    return boxes


# ------------------------------------------------------------------------------------------ PNG

@structure_parser("png", name="PNG (chunks + CRC)")
def parse_png(data: bytes, f: dict[str, Any]) -> list[dict[str, Any]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return [node(f, "PNG signature", "header", 0, 0, "fail", "missing")]
    out = [node(f, "PNG signature", "header", 0, 8, "pass")]
    p = 8
    while p + 12 <= len(data):
        ln, typ = struct.unpack(">I4s", data[p:p + 8])
        crc_ok = zlib.crc32(data[p + 4:p + 8 + ln]) & 0xFFFFFFFF == struct.unpack(">I", data[p + 8 + ln:p + 12 + ln])[0] if p + 12 + ln <= len(data) else False
        out.append(node(f, typ.decode("latin-1"), "chunk", p, ln + 12, "pass" if crc_ok else "fail", f"{ln:,} B, CRC {'ok' if crc_ok else 'mismatch'}"))
        p += ln + 12
        if typ == b"IEND":
            break
    return out


# ------------------------------------------------------------------------------------------ entry point

def explore(ctx, f: dict[str, Any]) -> dict[str, Any]:
    t = f["file_type"]
    base = {"file_id": f["file_id"], "file_name": f["file_name"], "type": t, "size": f["size_bytes"],
            "registered_parsers": sorted({v[0] for v in PARSERS.values()})}
    if t not in PARSERS or f.get("orphan"):
        why = "headerless orphan data: no format grammar can be anchored" if f.get("orphan") else f"no deterministic parser is registered for '{t}'"
        return {**base, "available": False, "status": "STRUCTURE PARSER UNAVAILABLE", "reason": why, "tree": []}
    data = ctx.reconstruction_bytes(f)
    if data is None:
        return {**base, "available": False, "status": "STRUCTURE PARSER UNAVAILABLE", "reason": "reconstruction bytes are not available on the server", "tree": []}
    name, fn = PARSERS[t]
    try:
        tree = fn(data, f)
    except Exception as e:  # a parser crash is reported, not hidden
        return {**base, "available": False, "status": "STRUCTURE PARSE FAILED", "reason": f"{name}: {type(e).__name__}: {e}", "tree": []}

    counts = {"recovered": 0, "missing": 0, "corrupted": 0, "partial": 0}

    def walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            if n.get("offset") is not None and n["kind"] != "group":
                counts[n["state"]] = counts.get(n["state"], 0) + 1
            walk(n["children"])
    walk(tree)
    return {**base, "available": True, "parser": name, "tree": tree, "counts": counts,
            "note": "Component validity is decided by the deterministic parser; recovered/missing/corrupted comes from the reconstruction map. "
                    "Placeholder (missing) bytes never count as recovered."}
