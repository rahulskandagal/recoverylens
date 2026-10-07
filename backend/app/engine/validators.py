"""Deterministic, format-specific validators.

Each validator receives reconstructed bytes (+ the reconstruction's segment map) and returns
a list of checks. Validators use real parsers (Pillow, zipfile, sqlite3, xml) or explicit
structural walks. They never consult the ML model.

Register new formats with @register("ext").
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import struct
import tempfile
import zipfile
import zlib
from typing import Any, Callable
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

Check = dict[str, Any]
VALIDATORS: dict[str, Callable[..., dict[str, Any]]] = {}


def register(*types: str):
    def deco(fn):
        for t in types:
            VALIDATORS[t] = fn
        return fn
    return deco


def check(name: str, status: str, detail: str, category: str = "structural") -> Check:
    # status: pass | fail | partial | na
    return {"name": name, "status": status, "detail": detail, "category": category}


def validate(ftype: str, data: bytes, **kw) -> dict[str, Any]:
    fn = VALIDATORS.get(ftype)
    if not fn:
        return {"checks": [check("Validator", "na", f"No validator registered for type '{ftype}'")],
                "structural": "not_validated", "parser": "not_validated", "checksum": "not_available"}
    try:
        return fn(data, **kw)
    except Exception as e:  # a validator crash is itself evidence of damage, never hidden
        return {"checks": [check("Validator execution", "fail", f"Parser raised {type(e).__name__}: {e}", "parser")],
                "structural": "fail", "parser": "fail", "checksum": "not_available"}


def summarize(checks: list[Check], category: str) -> str:
    st = [c["status"] for c in checks if c["category"] == category and c["status"] != "na"]
    if not st:
        return "not_available"
    if all(s == "pass" for s in st):
        return "pass"
    if all(s == "fail" for s in st):
        return "fail"
    return "partial"


# ---------------------------------------------------------------------------------------
# JPEG
# ---------------------------------------------------------------------------------------

def decode_gray(data: bytes) -> np.ndarray | None:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return np.asarray(im.convert("L"), dtype=np.float32)
    except Exception:
        return None


def row_seams(g: np.ndarray, mh: int, rows: int) -> np.ndarray:
    """Mean absolute pixel difference across each MCU-row boundary (index k = boundary above row k)."""
    out = np.full(rows, np.nan)
    for k in range(1, rows):
        y = k * mh
        if y >= g.shape[0]:
            break
        out[k] = float(np.abs(g[y] - g[y - 1]).mean())
    return out


@register("jpeg")
def validate_jpeg(data: bytes, header: dict[str, Any] | None = None, **_) -> dict[str, Any]:
    checks: list[Check] = []
    stats: dict[str, Any] = {}
    checks.append(check("SOI marker", "pass" if data[:3] == b"\xff\xd8\xff" else "fail", "Start-of-image FFD8 at offset 0"))
    hdr = header or {}
    need = {"FFDB": "quantization tables", "FFC4": "Huffman tables", "FFDA": "start of scan"}
    have = set(hdr.get("markers", []))
    for m, what in need.items():
        checks.append(check(f"{m} {what}", "pass" if m in have else "fail", f"Marker {m} {'present' if m in have else 'missing'} in header"))
    if "width" in hdr:
        checks.append(check("Frame header (SOF)", "pass", f"{hdr['width']}x{hdr['height']}, {hdr['components']} components, MCU {hdr['mcu_w']}x{hdr['mcu_h']}"))
    else:
        checks.append(check("Frame header (SOF)", "fail", "No frame header: dimensions unknown"))
    eoi = data.rfind(b"\xff\xd9")
    has_eoi = eoi != -1 and data[eoi + 2:].strip(b"\x00") == b""
    checks.append(check("EOI marker", "pass" if has_eoi else "fail",
                        "End-of-image FFD9 terminates the stream" if has_eoi else "Stream ends without FFD9 (truncated)"))
    scan_start = hdr.get("scan_start", 0)
    rst = [(m.start(), m.group(0)[1] - 0xD0) for m in re.finditer(rb"\xff[\xd0-\xd7]", data[scan_start:])]
    breaks = sum(1 for i in range(1, len(rst)) if rst[i][1] != (rst[i - 1][1] + 1) % 8)
    stats["rst_found"] = len(rst)
    stats["rst_breaks"] = breaks
    if hdr.get("restart_interval"):
        exp = hdr.get("expected_rst", 0)
        st = "pass" if len(rst) >= exp and breaks == 0 else ("partial" if rst else "fail")
        checks.append(check("Restart-marker sequence", st,
                            f"{len(rst)}/{exp} restart markers, {breaks} sequence break(s)", "checksum"))
    viol = []
    body = data[scan_start:eoi if has_eoi else len(data)]
    for m in re.finditer(rb"\xff([^\x00\xd0-\xd7\xd9])", body):
        viol.append(scan_start + m.start())
    stats["marker_violations"] = viol
    checks.append(check("Entropy-coded segment legality", "pass" if not viol else "fail",
                        "No illegal marker bytes in scan data" if not viol else f"{len(viol)} illegal FFxx sequence(s) - byte corruption"))
    g = decode_gray(data)
    if g is None:
        checks.append(check("Decoder (Pillow/libjpeg)", "fail", "Image could not be decoded", "parser"))
        return {"checks": checks, "stats": stats, "structural": summarize(checks, "structural"), "parser": "fail",
                "checksum": summarize(checks, "checksum")}
    mh = hdr.get("mcu_h", 16)
    rows_total = hdr.get("mcu_rows") or -(-g.shape[0] // mh)
    rows_ok = min(rows_total, len(rst) + (1 if has_eoi else 0)) if hdr.get("restart_interval") else (rows_total if has_eoi else None)
    stats.update(width=int(g.shape[1]), height=int(g.shape[0]), rows_total=rows_total, rows_decoded=rows_ok)
    full = has_eoi and not viol and (rows_ok == rows_total)
    checks.append(check("Decoder (Pillow/libjpeg)", "pass" if full else "partial",
                        f"Decoded {g.shape[1]}x{g.shape[0]}; {rows_ok if rows_ok is not None else '?'} of {rows_total} MCU rows backed by recovered data", "parser"))
    # statistical anomaly detection: MCU rows whose boundaries are far rougher than typical
    suspect: list[int] = []
    if rows_ok and rows_ok > 4:
        s = row_seams(g, mh, rows_ok)
        vals = s[1:rows_ok][~np.isnan(s[1:rows_ok])]
        if len(vals) > 3:
            med = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med))) or 1.0
            for k in range(1, rows_ok):
                if not np.isnan(s[k]) and (s[k] - med) / (1.4826 * mad) > 6.0:
                    suspect.append(k)
    stats["suspect_rows"] = suspect
    stats["seam_profile"] = [None if np.isnan(x) else round(float(x), 2) for x in row_seams(g, mh, rows_total)] if rows_total else []
    if suspect:
        checks.append(check("Row-continuity anomaly scan", "partial",
                            f"MCU rows {suspect[:8]} show statistically abnormal boundaries: suspected corruption OR genuine image edges "
                            "(advisory only; not counted in validation)", "advisory"))
    else:
        checks.append(check("Row-continuity anomaly scan", "pass", "No abnormal MCU-row boundaries detected (advisory)", "advisory"))
    return {"checks": checks, "stats": stats, "structural": summarize(checks, "structural"),
            "parser": summarize(checks, "parser"), "checksum": summarize(checks, "checksum")}


# ---------------------------------------------------------------------------------------
# PNG (chunk CRC walk)
# ---------------------------------------------------------------------------------------

@register("png")
def validate_png(data: bytes, **_) -> dict[str, Any]:
    checks = [check("PNG signature", "pass" if data.startswith(b"\x89PNG\r\n\x1a\n") else "fail", "8-byte signature")]
    pos, ok, bad, iend = 8, 0, 0, False
    while pos + 12 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        if pos + 12 + ln > len(data):
            break
        crc = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])[0]
        if zlib.crc32(data[pos + 4:pos + 8 + ln]) & 0xFFFFFFFF == crc:
            ok += 1
        else:
            bad += 1
        pos += 12 + ln
        if typ == b"IEND":
            iend = True
            break
    checks.append(check("Chunk CRC32", "pass" if bad == 0 and ok else "partial" if ok else "fail", f"{ok} valid, {bad} failing chunk CRCs", "checksum"))
    checks.append(check("IEND chunk", "pass" if iend else "fail", "Terminating chunk present" if iend else "Missing IEND"))
    try:
        Image.open(io.BytesIO(data)).load()
        checks.append(check("Decoder", "pass", "Pillow decoded the image", "parser"))
    except Exception as e:
        checks.append(check("Decoder", "fail", str(e), "parser"))
    return {"checks": checks, "stats": {"size": pos}, "structural": summarize(checks, "structural"),
            "parser": summarize(checks, "parser"), "checksum": summarize(checks, "checksum")}


# ---------------------------------------------------------------------------------------
# MP4 / ISO-BMFF (box walk - container structure only, no decoding)
# ---------------------------------------------------------------------------------------

@register("mp4")
def validate_mp4(data: bytes, **_) -> dict[str, Any]:
    pos, boxes = 0, []
    while pos + 8 <= len(data):
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        if size == 1 and pos + 16 <= len(data):
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        if size < 8 or pos + size > len(data) or not typ.isalnum():
            break
        boxes.append(typ.decode("latin-1"))
        pos += size
    checks = [
        check("ftyp box", "pass" if boxes[:1] == ["ftyp"] else "fail", "File-type box first"),
        check("moov (metadata) box", "pass" if "moov" in boxes else "fail", "Stream metadata atoms"),
        check("mdat (media) box", "pass" if "mdat" in boxes else "fail", "Media payload box"),
        check("Box chain", "pass" if pos == len(data.rstrip(b"\x00")) else "partial", f"Walked {len(boxes)} top-level boxes ({pos} bytes)"),
        check("Decoder", "na", "Container-structure validation only; stream decoding not implemented", "parser"),
    ]
    return {"checks": checks, "stats": {"boxes": boxes}, "structural": summarize(checks, "structural"),
            "parser": "not_validated", "checksum": "not_available"}


# ---------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------

OBJ_AT = re.compile(rb"(\d+) 0 obj\b")


def pdf_objects(data: bytes, xref: dict[int, int], missing: list[tuple[int, int]]) -> dict[int, dict[str, Any]]:
    """Check every object the xref table promises."""
    def in_missing(a: int, b: int) -> bool:
        return any(a < m1 and b > m0 for m0, m1 in missing)

    offs = sorted(xref.items(), key=lambda kv: kv[1])
    out: dict[int, dict[str, Any]] = {}
    for i, (num, off) in enumerate(offs):
        end = offs[i + 1][1] if i + 1 < len(offs) else data.find(b"xref", off)
        end = end if end and end > off else len(data)
        o: dict[str, Any] = {"num": num, "offset": off, "end": end}
        if in_missing(off, end):
            full = all(m0 <= off and m1 >= end for m0, m1 in missing if off < m1 and end > m0)
            o["status"] = "missing" if full else "partial"
            out[num] = o
            continue
        body = data[off:end]
        m = OBJ_AT.match(body)
        if not m or int(m.group(1)) != num:
            o["status"] = "corrupted"
            o["reason"] = "object header not at xref offset"
        elif b"endobj" not in body:
            o["status"] = "corrupted"
            o["reason"] = "endobj missing"
        else:
            o["status"] = "ok"
            lm = re.search(rb"/Length (\d+)", body)
            s = body.find(b"stream\n")
            if lm and s != -1:
                ln = int(lm.group(1))
                if body[s + 7 + ln:s + 7 + ln + 10] != b"\nendstream":
                    o["status"] = "corrupted"
                    o["reason"] = "stream /Length does not match endstream position"
                else:
                    stream = body[s + 7:s + 7 + ln]
                    bad = [s + 7 + j for j, c in enumerate(stream) if c < 9 or (13 < c < 32) or c > 126]
                    if bad and b"/Filter" not in body:
                        o["status"] = "corrupted"
                        o["reason"] = f"{len(bad)} non-text byte(s) inside an uncompressed text content stream"
                        o["bad_offsets"] = [off + j for j in bad]
                    o["stream"] = stream
            if b"/Type /Page " in body or b"/Type /Page>" in body:
                c = re.search(rb"/Contents (\d+) 0 R", body)
                o["page_contents"] = int(c.group(1)) if c else None
            ti = re.search(rb"/Title \((.*?)\)", body)
            if ti:
                o["info"] = {k.decode(): v.decode("latin-1") for k, v in re.findall(rb"/(\w+) \((.*?)\)", body)}
        out[num] = o
    return out


def pdf_text(stream: bytes) -> str:
    parts = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", stream)
    return "\n".join(p.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\").decode("latin-1") for p in parts)


@register("pdf")
def validate_pdf(data: bytes, missing: list[tuple[int, int]] | None = None, render: bool = True, **_) -> dict[str, Any]:
    """Nine weighted structural checks + MuPDF render test (see pdfcheck.py for weights)."""
    from .pdfcheck import check_pdf, pdf_status
    r = check_pdf(data, render=render)
    stats: dict[str, Any] = dict(r.stats)
    # per-page text from reachable content streams (for preview / keyword relevance)
    text_pages, page_status = [], []
    page_objs = [o for o in sorted(r.objects.values(), key=lambda x: x.num)
                 if o.intact and re.search(rb"/Type\s*/Page\b", o.body)]
    for i, pg in enumerate(page_objs):
        cm = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", pg.body)
        co = r.objects.get(int(cm.group(1))) if cm else None
        st = "ok" if co and co.intact else "missing"
        page_status.append({"page": i + 1, "status": st})
        if co and co.intact and co.stream is not None:
            from .pdfcheck import _decode
            dec, _how = _decode(co.stream, co.body)
            if dec is not None:
                text_pages.append({"page": i + 1, "status": st, "text": pdf_text(dec)[:3000]})
    stats.update(text_pages=text_pages, page_status=page_status, pages_ok=sum(1 for p in page_status if p["status"] == "ok"),
                 pages_declared=r.stats.get("pages_declared"), objects=r.stats["objects_found"])
    info = None
    for o in r.objects.values():
        if o.intact and re.search(rb"/(Title|Author|Producer)\s*\(", o.body) and b"/Type" not in o.body.split(b"stream")[0][:40]:
            info = {k.decode(): v.decode("latin-1") for k, v in re.findall(rb"/(\w+)\s*\((.*?)\)", o.body)}
            break
    stats["info"] = info
    status, reason = pdf_status(r, complete_bytes=not missing)
    return {"checks": r.checks, "stats": stats, "structural": summarize(r.checks, "structural"),
            "parser": summarize(r.checks, "parser"), "checksum": summarize(r.checks, "checksum"),
            "weighted": True, "integrity": r.integrity, "status_hint": (status, reason),
            "damaged_ranges": r.damaged_ranges, "previews": r.previews, "pdf_report": r}


# ---------------------------------------------------------------------------------------
# ZIP / DOCX / XLSX
# ---------------------------------------------------------------------------------------

def parse_central_directory(data: bytes) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    e = data.rfind(b"PK\x05\x06")
    if e == -1:
        return [], None
    n, cd_size, cd_off = struct.unpack("<HII", data[e + 10:e + 20])
    eocd = {"entries": n, "cd_size": cd_size, "cd_offset": cd_off, "eocd_offset": e}
    out = []
    p = cd_off
    for _ in range(n):
        if data[p:p + 4] != b"PK\x01\x02":
            break
        (method, _t, _d, crc, csize, usize, nlen, xlen, clen) = struct.unpack("<HHHIIIHHH", data[p + 10:p + 34])
        lho = struct.unpack("<I", data[p + 42:p + 46])[0]
        name = data[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        out.append({"name": name, "method": method, "crc": crc, "csize": csize, "usize": usize, "local_offset": lho})
        p += 46 + nlen + xlen + clen
    return out, eocd


def member_data_range(data: bytes, m: dict[str, Any]) -> tuple[int, int]:
    lo = m["local_offset"]
    nlen, xlen = struct.unpack("<HH", data[lo + 26:lo + 30]) if data[lo:lo + 4] == b"PK\x03\x04" else (len(m["name"]), 0)
    start = lo + 30 + nlen + xlen
    return start, start + m["csize"]


def member_bytes(data: bytes, m: dict[str, Any]) -> tuple[bytes | None, str]:
    a, b = member_data_range(data, m)
    raw = data[a:b]
    try:
        if m["method"] == 8:
            out = zlib.decompressobj(-15).decompress(raw)
        elif m["method"] == 0:
            out = raw
        else:
            return None, "unsupported compression"
    except zlib.error as e:
        return None, f"inflate error: {e}"
    if len(out) != m["usize"]:
        return out, "size mismatch"
    return out, "crc ok" if (zlib.crc32(out) & 0xFFFFFFFF) == m["crc"] else "crc mismatch"


def partial_inflate(data: bytes, m: dict[str, Any]) -> bytes:
    a, b = member_data_range(data, m)
    d = zlib.decompressobj(-15)
    out = b""
    for i in range(a, b, 256):
        try:
            out += d.decompress(data[i:min(b, i + 256)])
        except zlib.error:
            break
    return out


def docx_text(xml: bytes) -> str:
    return "\n".join(t.decode("utf-8", "replace") for t in re.findall(rb"<w:t[^>]*>(.*?)</w:t>", xml))


@register("zip", "docx", "xlsx")
def validate_zip(data: bytes, missing: list[tuple[int, int]] | None = None, **_) -> dict[str, Any]:
    missing = missing or []
    checks = [check("Local file header", "pass" if data.startswith(b"PK\x03\x04") else "fail", "ZIP signature at offset 0")]
    members, eocd = parse_central_directory(data)
    checks.append(check("End of central directory", "pass" if eocd else "fail", f"{eocd['entries']} entries declared" if eocd else "EOCD record not found"))
    checks.append(check("Central directory", "pass" if eocd and len(members) == eocd["entries"] else "fail",
                        f"{len(members)} directory entries parsed"))
    stats: dict[str, Any] = {"members": []}
    ok = 0
    for m in members:
        a, b = member_data_range(data, m)
        in_gap = any(a < m1 and b > m0 for m0, m1 in missing) or any(m["local_offset"] < m1 and a > m0 for m0, m1 in missing)
        if in_gap:
            st = "missing"
            raw, why = None, "overlaps an unrecovered range"
        else:
            raw, why = member_bytes(data, m)
            st = "ok" if why == "crc ok" else "corrupted"
        ok += st == "ok"
        stats["members"].append({"name": m["name"], "status": st, "detail": why, "usize": m["usize"],
                                 "csize": m["csize"], "offset": m["local_offset"], "data_start": a, "data_end": b,
                                 "method": "deflate" if m["method"] == 8 else "stored"})
    if members:
        checks.append(check("Member CRC-32 verification", "pass" if ok == len(members) else "partial" if ok else "fail",
                            f"{ok}/{len(members)} members decompress and match their CRC-32", "checksum"))
    names = {m["name"] for m in members}
    kind = "docx" if "word/document.xml" in names else "xlsx" if "xl/workbook.xml" in names else "zip"
    stats["kind"] = kind
    if kind == "docx":
        req = ["[Content_Types].xml", "_rels/.rels", "word/document.xml"]
        st = {m["name"]: m["status"] for m in stats["members"]}
        good = [r for r in req if st.get(r) == "ok"]
        checks.append(check("OOXML required parts", "pass" if len(good) == len(req) else "partial" if good else "fail",
                            f"{len(good)}/{len(req)} required parts intact ({', '.join(r for r in req if r not in good) or 'all present'} {'missing/damaged' if len(good) < len(req) else ''})", "parser"))
        xml_ok = 0
        xml_total = 0
        for m in members:
            if m["name"].endswith((".xml", ".rels")):
                xml_total += 1
                raw, why = member_bytes(data, m) if st.get(m["name"]) != "missing" else (None, "")
                if raw is not None and why == "crc ok":
                    try:
                        ET.fromstring(raw)
                        xml_ok += 1
                    except ET.ParseError:
                        pass
        checks.append(check("XML well-formedness", "pass" if xml_ok == xml_total else "partial" if xml_ok else "fail",
                            f"{xml_ok}/{xml_total} XML parts parse", "parser"))
        doc = next((m for m in members if m["name"] == "word/document.xml"), None)
        if doc:
            raw, why = member_bytes(data, doc) if st.get(doc["name"]) != "missing" else (None, "missing")
            if raw is not None and why == "crc ok":
                stats["text"] = docx_text(raw)[:6000]
                stats["text_verified"] = True
            elif st.get(doc["name"]) != "missing":
                stats["text"] = docx_text(partial_inflate(data, doc))[:6000]
                stats["text_verified"] = False
        core = next((m for m in members if m["name"] == "docProps/core.xml"), None)
        if core and st.get(core["name"]) == "ok":
            raw, _ = member_bytes(data, core)
            stats["core"] = {k.decode(): v.decode("utf-8", "replace") for k, v in re.findall(rb"<(?:dc|dcterms|cp):(\w+)[^>]*>([^<]*)<", raw or b"")}
    try:
        zipfile.ZipFile(io.BytesIO(data))
        checks.append(check("zipfile module open", "pass", "Python zipfile accepts the archive", "parser"))
    except Exception as e:
        checks.append(check("zipfile module open", "fail", str(e), "parser"))
    return {"checks": checks, "stats": stats, "structural": summarize(checks, "structural"),
            "parser": summarize(checks, "parser"), "checksum": summarize(checks, "checksum")}


# ---------------------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------------------

@register("sqlite")
def validate_sqlite(data: bytes, page_status: dict[int, str] | None = None, **_) -> dict[str, Any]:
    checks = [check("SQLite header string", "pass" if data.startswith(b"SQLite format 3\x00") else "fail", "16-byte magic")]
    ps = struct.unpack(">H", data[16:18])[0]
    ps = 65536 if ps == 1 else ps
    pc = struct.unpack(">I", data[28:32])[0]
    checks.append(check("Header page size / count", "pass" if ps in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536) and pc * ps == len(data) else "partial",
                        f"page_size={ps}, page_count={pc}, reconstructed={len(data) // max(ps, 1)} pages"))
    stats: dict[str, Any] = {"page_size": ps, "page_count": pc}
    if page_status:
        okp = sum(1 for v in page_status.values() if v == "ok")
        checks.append(check("B-tree page headers", "pass" if okp == pc else "partial",
                            f"{okp}/{pc} pages present with a valid b-tree page header", "checksum"))
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            msgs = [r[0] for r in con.execute("PRAGMA integrity_check(20)").fetchall()]
        except sqlite3.DatabaseError as e:
            msgs = [f"integrity_check aborted: {e}"]
        stats["integrity_messages"] = msgs
        checks.append(check("PRAGMA integrity_check", "pass" if msgs == ["ok"] else "fail",
                            "ok" if msgs == ["ok"] else f"{len(msgs)} problem(s), e.g. {msgs[0]}", "parser"))
        try:
            tables = con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
            stats["schema"] = [{"name": t, "sql": s} for t, s in tables]
            checks.append(check("Schema readable", "pass", f"{len(tables)} table(s): {', '.join(t for t, _ in tables)}", "parser"))
        except sqlite3.DatabaseError as e:
            checks.append(check("Schema readable", "fail", str(e), "parser"))
        con.close()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"checks": checks, "stats": stats, "structural": summarize(checks, "structural"),
            "parser": summarize(checks, "parser"), "checksum": summarize(checks, "checksum")}


# ---------------------------------------------------------------------------------------
# Text / logs
# ---------------------------------------------------------------------------------------

@register("log", "txt")
def validate_text(data: bytes, **_) -> dict[str, Any]:
    body = data.rstrip(b"\x00")
    try:
        body.decode("utf-8")
        enc = "pass"
    except UnicodeDecodeError:
        enc = "partial"
    lines = body.split(b"\n")
    ts = [re.match(rb"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", ln) for ln in lines]
    tsv = [m.group(1) for m in ts if m]
    mono = all(tsv[i] <= tsv[i + 1] for i in range(len(tsv) - 1)) if tsv else None
    checks = [check("Character encoding", enc, "UTF-8 decodable" if enc == "pass" else "Contains undecodable bytes"),
              check("Line structure", "pass", f"{len(lines)} lines", "parser")]
    if tsv:
        checks.append(check("Timestamp monotonicity", "pass" if mono else "fail",
                            f"{len(tsv)} timestamped lines, {'ordered' if mono else 'out of order - possible mis-ordering'}", "checksum"))
    return {"checks": checks, "stats": {"lines": len(lines), "timestamps": len(tsv),
                                        "first_ts": tsv[0].decode() if tsv else None, "last_ts": tsv[-1].decode() if tsv else None},
            "structural": summarize(checks, "structural"), "parser": summarize(checks, "parser"),
            "checksum": summarize(checks, "checksum")}
