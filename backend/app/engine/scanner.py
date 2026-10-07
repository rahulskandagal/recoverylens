"""Deterministic scanner: cluster classification, fragment detection and header parsing.

Everything in this module is rule-based and reproducible. No ML is used to decide whether
bytes are valid; ML is applied later only to *relationships* between fragments.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import numpy as np

from .common import CLUSTER, HEADER_SIGS, TS_RE, entropy, hexoff, printable_ratio, sha256

PDF_OBJ_RE = re.compile(rb"(?:^|[\r\n])(\d{1,6}) 0 obj\b")
PDF_TOKENS = (b" obj", b"endobj", b"stream", b" Tj", b"xref", b"%PDF", b"/Type")
JPEG_OK_AFTER_FF = set([0x00, 0xD9] + list(range(0xD0, 0xD8)))


@dataclass
class ClusterInfo:
    index: int
    cls: str
    entropy: float
    printable: float
    sig: str | None = None
    rst: list[tuple[int, int]] = field(default_factory=list)  # (pos, n)
    pdf_objs: list[tuple[int, int]] = field(default_factory=list)  # (pos, objnum)
    zip_locals: list[tuple[int, str]] = field(default_factory=list)
    zip_central: bool = False
    zip_eocd: int | None = None
    jpeg_eoi: int | None = None
    pdf_eof: int | None = None
    ts_first: str | None = None
    ts_last: str | None = None
    embedded: list[tuple[int, str]] = field(default_factory=list)
    jpeg_violations: list[int] = field(default_factory=list)
    hash: str = ""


@dataclass
class Fragment:
    id: str
    start_cluster: int
    n_clusters: int
    family: str
    clusters: list[ClusterInfo]
    header: dict[str, Any] | None = None
    duplicate_of: str | None = None
    corruption: list[str] = field(default_factory=list)
    sha256: str = ""

    @property
    def offset(self) -> int:
        return self.start_cluster * CLUSTER

    @property
    def length(self) -> int:
        return self.n_clusters * CLUSTER

    @property
    def end_cluster(self) -> int:
        return self.start_cluster + self.n_clusters

    def rst_seq(self) -> list[tuple[int, int]]:
        """RST markers as (offset within fragment, n)."""
        out = []
        for i, c in enumerate(self.clusters):
            out += [(i * CLUSTER + p, n) for p, n in c.rst]
        return out

    def pdf_objs(self) -> list[tuple[int, int]]:
        out = []
        for i, c in enumerate(self.clusters):
            out += [(i * CLUSTER + p, n) for p, n in c.pdf_objs]
        return out

    def zip_locals(self) -> list[tuple[int, str]]:
        out = []
        for i, c in enumerate(self.clusters):
            out += [(i * CLUSTER + p, n) for p, n in c.zip_locals]
        return out

    def timestamps(self) -> tuple[str | None, str | None]:
        ts = [c.ts_first for c in self.clusters if c.ts_first] + [c.ts_last for c in self.clusters if c.ts_last]
        return (min(ts), max(ts)) if ts else (None, None)

    def summary(self, image: "EvidenceImage | None" = None) -> dict[str, Any]:
        ent = float(np.mean([c.entropy for c in self.clusters]))
        t0, t1 = self.timestamps()
        d: dict[str, Any] = {
            "id": self.id, "offset": self.offset, "offset_hex": hexoff(self.offset),
            "length": self.length, "start_cluster": self.start_cluster, "clusters": self.n_clusters,
            "sector": self.offset // 512, "family": self.family,
            "classes": sorted({c.cls for c in self.clusters}), "entropy": round(ent, 3),
            "sha256": self.sha256, "header": self.header, "duplicate_of": self.duplicate_of,
            "has_footer": any(c.jpeg_eoi is not None or c.pdf_eof is not None or c.zip_eocd is not None for c in self.clusters),
            "rst_markers": len(self.rst_seq()), "pdf_objects": [n for _, n in self.pdf_objs()][:50],
            "zip_members": [n for _, n in self.zip_locals()][:30],
            "ts_first": t0, "ts_last": t1, "corruption": self.corruption,
            "embedded": [e for c in self.clusters for e in c.embedded][:10],
        }
        return d


class EvidenceImage:
    """Read-only accessor for a storage image. The file is never opened for writing."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.size = len(self.data)
        self.n_clusters = (self.size + CLUSTER - 1) // CLUSTER

    def cluster(self, i: int) -> bytes:
        return self.data[i * CLUSTER:(i + 1) * CLUSTER].ljust(CLUSTER, b"\x00")

    def read(self, off: int, n: int) -> bytes:
        return self.data[off:off + n]

    def frag_bytes(self, f: Fragment) -> bytes:
        return self.data[f.offset:f.offset + f.length]


# ---------------------------------------------------------------------------------------
# Cluster classification
# ---------------------------------------------------------------------------------------

def _jpeg_legal(b: bytes) -> tuple[bool, list[tuple[int, int]], int | None, list[int]]:
    """Entropy-coded JPEG data only allows FF00 (stuffing), FFD0-FFD7 (RST) and FFD9 (EOI).

    Returns (looks_like_scan_data, rst_markers, eoi_pos, violation_positions). A couple of
    violations are tolerated (bit-rot); random data produces many.
    """
    rst: list[tuple[int, int]] = []
    viol: list[int] = []
    eoi = None
    i = b.find(b"\xff")
    n_ff = 0
    while i != -1 and i < len(b) - 1:
        nxt = b[i + 1]
        n_ff += 1
        if nxt not in JPEG_OK_AFTER_FF:
            viol.append(i)
            if len(viol) > 2:
                return False, [], None, viol
        elif 0xD0 <= nxt <= 0xD7:
            rst.append((i, nxt - 0xD0))
        elif nxt == 0xD9:
            eoi = i
            if b[i + 2:].strip(b"\x00") == b"":
                break
        i = b.find(b"\xff", i + 2)
    return n_ff >= 3, rst, eoi, viol


def _sqlite_page_ok(b: bytes, hdr_off: int = 0) -> bool:
    t = b[hdr_off]
    if t not in (0x0D, 0x05, 0x0A, 0x02):
        return False
    freeblock, ncell, content, frag = struct.unpack(">HHHB", b[hdr_off + 1:hdr_off + 8])
    content = 65536 if content == 0 else content
    if ncell == 0 or ncell > 1000 or content > CLUSTER or frag > 60 or (freeblock and freeblock >= CLUSTER):
        return False
    hlen = 12 if t in (0x05, 0x02) else 8
    arr = hdr_off + hlen
    if arr + 2 * ncell > content:
        return False
    ptrs = struct.unpack(f">{ncell}H", b[arr:arr + 2 * ncell])
    return all(content <= p < CLUSTER for p in ptrs)


def _fat_entry_ok(e: bytes) -> bool:
    """Strict validity test for one 32-byte FAT directory entry (short or VFAT LFN)."""
    if e[11] == 0x0F:
        return e[12] == 0 and e[26:28] == b"\x00\x00" and (e[0] == 0xE5 or 1 <= (e[0] & 0x1F) <= 20)
    if e[11] not in (0x10, 0x20, 0x01, 0x21, 0x00) or e[12] != 0:
        return False
    if not all(32 <= c < 127 or c == 0xE5 for c in e[:11]) or e[0] == 0x20:
        return False
    dt = struct.unpack("<H", e[24:26])[0]
    return 1 <= (dt >> 5) & 0xF <= 12 and 1 <= dt & 0x1F <= 31


def _fat_dir_ok(b: bytes) -> bool:
    valid = 0
    for i in range(0, 32 * 8, 32):
        e = b[i:i + 32]
        if e == bytes(32):
            break
        if not _fat_entry_ok(e):
            return False
        valid += 1
    return valid >= 2


def classify_cluster(i: int, b: bytes) -> ClusterInfo:
    stripped = b.rstrip(b"\x00")
    if not stripped:
        return ClusterInfo(i, "zero", 0.0, 0.0)
    ent = entropy(b)
    ent_s = entropy(stripped) if len(stripped) >= 256 else ent  # ignore slack padding
    pr = printable_ratio(stripped)
    ci = ClusterInfo(i, "binary", ent, pr, hash=sha256(b))
    for name, sig in HEADER_SIGS.items():
        if name == "mp4":
            if b[4:8] == sig:
                ci.sig = "mp4"
        elif b.startswith(sig):
            ci.sig = name
    # structural markers (collected regardless of class)
    ci.pdf_objs = [(m.start(1), int(m.group(1))) for m in PDF_OBJ_RE.finditer(b)]
    for m in re.finditer(rb"PK\x03\x04", b):
        p = m.start()
        if p + 30 <= len(b):
            nlen = struct.unpack("<H", b[p + 26:p + 28])[0]
            name = b[p + 30:p + 30 + nlen]
            if 0 < nlen < 256 and len(name) == nlen and all(32 <= c < 127 for c in name):
                ci.zip_locals.append((p, name.decode()))
    ci.zip_central = b"PK\x01\x02" in b
    e = b.rfind(b"PK\x05\x06")
    ci.zip_eocd = e if e != -1 else None
    e = b.rfind(b"%%EOF")
    ci.pdf_eof = e if e != -1 else None
    ts = TS_RE.findall(b)
    if ts:
        ci.ts_first, ci.ts_last = ts[0].decode().replace("T", " "), ts[-1].decode().replace("T", " ")
    for m in re.finditer(rb"\xff\xd8\xff[\xe0\xe1\xdb]", b[1:]):
        ci.embedded.append((m.start() + 1, "jpeg"))

    if ci.sig == "jpeg":
        ci.cls = "jpeg_header"
        sos = b.find(b"\xff\xda")
        if sos != -1:
            ok, rst, eoi, viol = _jpeg_legal(b[sos + 2:])
            ci.rst = [(p + sos + 2, n) for p, n in rst]
            ci.jpeg_violations = [p + sos + 2 for p in viol]
            ci.jpeg_eoi = eoi + sos + 2 if eoi is not None else None
        return ci
    if ci.sig == "sqlite":
        ci.cls = "sqlite_page"
        return ci
    if ci.sig == "zip":
        ci.cls = "zip_struct"
        return ci
    if ci.sig in ("png", "gif", "mp4"):
        ci.cls = f"{ci.sig}_header"
        return ci
    # structural page/entry checks run before text heuristics: SQLite pages and FAT
    # directories can be mostly printable
    if _sqlite_page_ok(b):
        ci.cls = "sqlite_page"
        return ci
    if _fat_dir_ok(b):
        ci.cls = "fat_dir"
        return ci
    if pr >= 0.92:
        if ci.sig == "pdf" or any(t in b for t in PDF_TOKENS):
            ci.cls = "pdf_text"
        elif sum(1 for ln in stripped.split(b"\n")[:60] if TS_RE.match(ln)) >= 3:
            ci.cls = "log_text"
        elif stripped.count(b"<") > 20 and b"</" in stripped:
            ci.cls = "xml_text"
        else:
            ci.cls = "text"
        return ci
    if ci.sig == "pdf":
        ci.cls = "pdf_text"
        return ci
    if len(stripped) < 256 and stripped.endswith(b"\xff\xd9"):
        # short JPEG tail: a few scan bytes + EOI followed by slack; too short for an entropy test
        ok, rst, eoi, viol = _jpeg_legal(b)
        if eoi is not None and not viol:
            ci.cls = "jpeg_data"
            ci.rst, ci.jpeg_eoi = rst, eoi
            return ci
    if ent_s > 6.0:
        ok, rst, eoi, viol = _jpeg_legal(b)
        if ok:
            ci.cls = "jpeg_data"
            ci.rst, ci.jpeg_eoi, ci.jpeg_violations = rst, eoi, viol
            return ci
    if ci.zip_locals or ci.zip_central or ci.zip_eocd is not None:
        ci.cls = "zip_struct"
        return ci
    if ent_s > 7.2:
        ci.cls = "high_entropy"
    return ci


def _text_doc_start(b: bytes) -> bool:
    lines = b.split(b"\n", 2)
    return len(lines) > 1 and lines[0][:1].isupper() and 3 <= len(lines[0]) <= 80 and \
        len(lines[1].strip()) >= 3 and set(lines[1].strip()) <= set(b"=-#*")


FAMILY = {
    "jpeg_header": "jpeg", "jpeg_data": "jpeg", "pdf_text": "pdf", "sqlite_page": "sqlite",
    "zip_struct": "compressed", "high_entropy": "compressed", "log_text": "log", "text": "text",
    "xml_text": "xml", "fat_dir": "fat_dir", "binary": "binary", "zero": "zero",
    "png_header": "png", "gif_header": "gif", "mp4_header": "mp4",
}


# ---------------------------------------------------------------------------------------
# Header parsers
# ---------------------------------------------------------------------------------------

def parse_jpeg_header(b: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {"type": "jpeg", "markers": []}
    i = 2
    while i + 4 <= len(b):
        if b[i] != 0xFF:
            info["error"] = f"marker expected at +{i}"
            break
        m = b[i + 1]
        if m == 0xD8 or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", b[i + 2:i + 4])[0]
        seg = b[i + 4:i + 2 + seg_len]
        info["markers"].append(f"FF{m:02X}")
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", seg[1:5])
            nc = seg[5]
            comps = [(seg[6 + 3 * k + 1] >> 4, seg[6 + 3 * k + 1] & 15) for k in range(nc)]
            hmax, vmax = max(c[0] for c in comps), max(c[1] for c in comps)
            info.update(width=w, height=h, components=nc, mcu_w=8 * hmax, mcu_h=8 * vmax,
                        progressive=m == 0xC2)
        elif m == 0xDD:
            info["restart_interval"] = struct.unpack(">H", seg[:2])[0]
        elif m == 0xE1 and seg.startswith(b"Exif\x00\x00"):
            info["exif"] = _parse_exif(seg[6:])
        elif m == 0xDA:
            info["sos_offset"] = i
            info["scan_start"] = i + 2 + seg_len
            break
        i += 2 + seg_len
    if "width" in info:
        mcus = -(-info["width"] // info["mcu_w"]) * -(-info["height"] // info["mcu_h"])
        info["total_mcus"] = mcus
        ri = info.get("restart_interval")
        if ri:
            info["expected_intervals"] = -(-mcus // ri)
            info["expected_rst"] = info["expected_intervals"] - 1
            info["mcu_rows"] = -(-info["height"] // info["mcu_h"])
    return info


def _parse_exif(t: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        le = t[:2] == b"II"
        E = "<" if le else ">"
        off = struct.unpack(E + "I", t[4:8])[0]
        n = struct.unpack(E + "H", t[off:off + 2])[0]
        names = {0x010E: "ImageDescription", 0x0131: "Software", 0x0132: "DateTime", 0x010F: "Make", 0x0110: "Model"}
        for k in range(n):
            e = t[off + 2 + 12 * k: off + 14 + 12 * k]
            tag, typ, cnt = struct.unpack(E + "HHI", e[:8])
            if tag in names and typ == 2:
                vo = struct.unpack(E + "I", e[8:12])[0] if cnt > 4 else None
                raw = t[vo:vo + cnt] if vo is not None else e[8:8 + cnt]
                out[names[tag]] = raw.rstrip(b"\x00").decode("latin-1")
    except Exception:
        pass
    return out


def parse_header(ci: ClusterInfo, b: bytes) -> dict[str, Any] | None:
    try:
        if ci.sig == "jpeg":
            return parse_jpeg_header(b)
        if ci.sig == "pdf":
            return {"type": "pdf", "version": b[5:8].decode("latin-1")}
        if ci.sig == "sqlite":
            page_size = struct.unpack(">H", b[16:18])[0]
            page_size = 65536 if page_size == 1 else page_size
            return {"type": "sqlite", "page_size": page_size,
                    "page_count": struct.unpack(">I", b[28:32])[0],
                    "text_encoding": {1: "UTF-8", 2: "UTF-16le", 3: "UTF-16be"}.get(struct.unpack(">I", b[56:60])[0], "?")}
        if ci.sig == "zip":
            return {"type": "zip", "first_member": ci.zip_locals[0][1] if ci.zip_locals else None}
        if ci.sig == "png":
            w, h = struct.unpack(">II", b[16:24])
            return {"type": "png", "width": w, "height": h}
        if ci.sig in ("gif", "mp4"):
            return {"type": ci.sig}
    except Exception as e:  # malformed header - record, don't guess
        return {"type": ci.sig, "error": str(e)}
    return None


# ---------------------------------------------------------------------------------------
# FAT-style directory remnants
# ---------------------------------------------------------------------------------------

def _lfn_checksum(short: bytes) -> int:
    s = 0
    for c in short:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def parse_fat_dir(b: bytes, cluster_index: int) -> list[dict[str, Any]]:
    entries = []
    lfn_parts: list[tuple[int, str]] = []
    for i in range(0, len(b), 32):
        e = b[i:i + 32]
        if e == bytes(32) or not _fat_entry_ok(e):
            break
        if e[11] == 0x0F:
            chk = e[13]
            raw = e[1:11] + e[14:26] + e[28:32]
            s = raw.decode("utf-16-le", "replace").split("\x00")[0].replace("￿", "")
            lfn_parts.append((chk, s))
            continue
        deleted = e[0] == 0xE5
        short = e[:11]
        attr = e[11]
        tm, dt = struct.unpack("<HH", e[22:26])
        hi, lo = struct.unpack("<H", e[20:22])[0], struct.unpack("<H", e[26:28])[0]
        size = struct.unpack("<I", e[28:32])[0]
        name = None
        name_conf = 0.0
        recovered_first = None
        if lfn_parts:
            chk = lfn_parts[0][0]
            full = "".join(p for _, p in reversed(lfn_parts))
            if not deleted and _lfn_checksum(short) == chk:
                name, name_conf = full, 0.98
            elif deleted:
                # the deleted marker overwrote the first byte of the short name: find the
                # first character that makes the LFN checksum match (classic forensic step)
                for c in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_":
                    if _lfn_checksum(bytes([c]) + short[1:]) == chk:
                        recovered_first = chr(c)
                        break
                name = full
                name_conf = 0.95 if recovered_first else 0.8
        if not name:
            base = short[:8].decode("latin-1").strip()
            ext = short[8:].decode("latin-1").strip()
            name = (("_" + base[1:]) if deleted else base) + (f".{ext}" if ext else "")
            name_conf = 0.6
        try:
            when = datetime(((dt >> 9) & 0x7F) + 1980, (dt >> 5) & 0xF, dt & 0x1F,
                            (tm >> 11) & 0x1F, (tm >> 5) & 0x3F, (tm & 0x1F) * 2).isoformat()
        except ValueError:
            when = None
        fc = (hi << 16) | lo
        entries.append({
            "name": name, "name_confidence": name_conf, "deleted": deleted, "attr": attr,
            "short_name": (recovered_first or "?") + short[1:8].decode("latin-1").rstrip() + "." + short[8:].decode("latin-1").strip()
            if deleted else short[:8].decode("latin-1").rstrip() + "." + short[8:].decode("latin-1").strip(),
            "first_cluster": fc, "image_cluster": fc - 2, "size": size, "modified": when,
            "dir_cluster": cluster_index, "entry_offset": cluster_index * CLUSTER + i,
            "checksum_first_char_recovered": recovered_first,
        })
        lfn_parts = []
    return entries


# ---------------------------------------------------------------------------------------
# Scan driver
# ---------------------------------------------------------------------------------------

@dataclass
class ScanResult:
    clusters: list[ClusterInfo]
    fragments: list[Fragment]
    dir_entries: list[dict[str, Any]]
    stats: dict[str, Any]

    def frag(self, fid: str) -> Fragment:
        return self._by_id[fid]

    def index(self) -> None:
        self._by_id = {f.id: f for f in self.fragments}


def scan(img: EvidenceImage, progress: Callable[[float, str], None] | None = None) -> ScanResult:
    clusters: list[ClusterInfo] = []
    n = img.n_clusters
    for i in range(n):  # chunked scanning, one cluster at a time
        clusters.append(classify_cluster(i, img.cluster(i)))
        if progress and i % 256 == 0:
            progress(i / n, f"Scanned {i}/{n} clusters")

    # ---- group clusters into fragments -------------------------------------------------
    frags: list[Fragment] = []
    cur: list[ClusterInfo] = []

    def flush() -> None:
        if cur:
            fam = FAMILY.get(cur[0].cls, "binary")
            frags.append(Fragment(f"F{len(frags) + 1:04d}", cur[0].index, len(cur), fam, list(cur)))
            cur.clear()

    last_rst: int | None = None
    last_obj: int | None = None
    last_ts: str | None = None
    for c in clusters:
        fam = FAMILY.get(c.cls, "binary")
        if fam == "zero":
            flush()
            last_rst = last_obj = last_ts = None
            continue
        split = False
        if cur and fam == "jpeg" and FAMILY.get(cur[-1].cls) == "jpeg" and not c.sig:
            # a restart marker can straddle the cluster boundary (FF | Dn)
            pb, cb = img.cluster(cur[-1].index), img.cluster(c.index)
            if pb[-1] == 0xFF and 0xD0 <= cb[0] <= 0xD7:
                n_ = cb[0] - 0xD0
                if last_rst is None or n_ == (last_rst + 1) % 8:
                    cur[-1].rst.append((CLUSTER - 1, n_))
                    last_rst = n_
        if cur:
            pfam = FAMILY.get(cur[-1].cls, "binary")
            if fam != pfam or c.sig or pfam in ("fat_dir", "binary"):
                split = True
            elif cur[-1].jpeg_eoi is not None or cur[-1].pdf_eof is not None or cur[-1].zip_eocd is not None:
                split = True  # a footer ends a structure
            elif fam == "jpeg" and c.rst and last_rst is not None and c.rst[0][1] != (last_rst + 1) % 8:
                split = True  # restart-marker sequence discontinuity
            elif fam == "pdf" and c.pdf_objs and last_obj is not None and c.pdf_objs[0][1] < last_obj:
                split = True  # object numbering restarts: different document
            elif fam == "log" and c.ts_first and last_ts and c.ts_first < last_ts:
                split = True  # time goes backwards
            elif fam == "text" and _text_doc_start(img.cluster(c.index)):
                split = True  # plain text has no signature; a title + underline is a soft header
        if split:
            flush()
        cur.append(c)
        if c.rst:
            last_rst = c.rst[-1][1]
        if c.pdf_objs:
            last_obj = c.pdf_objs[-1][1]
        if c.ts_last:
            last_ts = c.ts_last
    flush()

    # ---- per-fragment hashes, headers, duplicates -------------------------------------
    first_seen: dict[str, str] = {}
    dir_entries: list[dict[str, Any]] = []
    for f in frags:
        fb = img.frag_bytes(f)
        f.sha256 = sha256(fb)
        c0 = f.clusters[0]
        if c0.sig:
            f.header = parse_header(c0, fb[:CLUSTER * 2])
        if f.family == "fat_dir":
            for c in f.clusters:
                dir_entries += parse_fat_dir(img.cluster(c.index), c.index)
        key = f.sha256
        if key in first_seen and f.family not in ("zero",):
            f.duplicate_of = first_seen[key]
        else:
            first_seen[key] = f.id
    # fragments that are copies of *part* of another fragment (cluster-level duplication)
    cl_owner: dict[str, str] = {}
    for f in frags:
        for c in f.clusters:
            if c.hash and c.hash not in cl_owner:
                cl_owner[c.hash] = f.id
    for f in frags:
        if f.duplicate_of or f.family in ("zero", "fat_dir"):
            continue
        owners = {cl_owner.get(c.hash) for c in f.clusters}
        if len(owners) == 1 and f.id not in owners and None not in owners:
            f.duplicate_of = owners.pop()

    counts: dict[str, int] = {}
    for c in clusters:
        counts[c.cls] = counts.get(c.cls, 0) + 1
    res = ScanResult(clusters, frags, dir_entries, {
        "clusters": n, "sectors": img.size // 512, "cluster_classes": counts,
        "fragments": len(frags), "header_fragments": sum(1 for f in frags if f.header),
        "duplicate_fragments": sum(1 for f in frags if f.duplicate_of),
        "directory_entries": len(dir_entries),
    })
    res.index()
    return res
