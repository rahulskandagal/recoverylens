"""Synthetic evidence generator (DEMO MODE ONLY).

Builds byte-level storage images that contain *real* files (JPEG, PDF, DOCX, SQLite, logs,
text) which are then fragmented across 4 KiB clusters and damaged (overwritten clusters,
bit-rot, duplicated fragments). FAT-style deleted directory entries are written as a
metadata remnant.

Ground truth is written to a separate JSON file. The analysis engine never reads it; it is
only used after analysis to measure engine accuracy against a known answer.
"""
from __future__ import annotations

import io
import json
import os
import random
import sqlite3
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .common import CLUSTER, sha256

# ---------------------------------------------------------------------------------------
# File makers (produce genuine, parser-valid files)
# ---------------------------------------------------------------------------------------

WORDS = ("analysis system storage timeline review design data team client "
         "delivery risk milestone quality network server report module testing deployment "
         "security backup recovery archive schedule vendor contract invoice hardware sensor "
         "prototype research metrics customer support release audit policy training").split()


def _sentence(rng: random.Random, n: int = 12) -> str:
    w = [rng.choice(WORDS) for _ in range(n)]
    w[0] = w[0].capitalize()
    return " ".join(w) + "."


def make_jpeg(rng: random.Random, desc: str, w: int = 800, h: int = 600,
              when: datetime | None = None, palette: int = 0) -> bytes:
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    skies = [((40, 90, 170), (250, 180, 120)), ((20, 30, 70), (120, 160, 220)),
             ((90, 150, 200), (230, 240, 250)), ((60, 20, 80), (240, 120, 90))]
    top, bot = skies[palette % len(skies)]
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    d.ellipse([w * 0.65, h * 0.12, w * 0.65 + 90, h * 0.12 + 90], fill=(255, 230, 150))
    for k in range(3):
        base = int(h * (0.55 + 0.12 * k))
        pts = [(0, h)]
        x = 0
        while x <= w:
            pts.append((x, base - rng.randint(0, int(h * 0.18))))
            x += rng.randint(40, 120)
        pts += [(w, h)]
        shade = 40 + 35 * k
        d.polygon(pts, fill=(shade // 2, shade, shade // 2 + 20))
    for _ in range(25):
        x, y = rng.randint(0, w), rng.randint(int(h * 0.7), h)
        d.ellipse([x, y, x + rng.randint(4, 14), y + rng.randint(4, 14)], fill=(30, 90 + rng.randint(0, 80), 40))
    d.text((20, h - 30), desc.upper(), fill=(255, 255, 255))
    # sensor noise: real photos are textured, which also makes their size realistic
    nrng = np.random.default_rng(rng.randrange(2**32))
    noise = np.clip(nrng.normal(128, 40, (h, w)), 0, 255).astype(np.uint8)
    img = Image.blend(img, Image.fromarray(noise, "L").convert("RGB"), 0.10)
    exif = img.getexif()
    exif[0x010E] = desc  # ImageDescription
    exif[0x0131] = "RecoveryLens synthetic camera"
    exif[0x0132] = (when or datetime(2025, 8, 14, 10, 22, 31)).strftime("%Y:%m:%d %H:%M:%S")
    buf = io.BytesIO()
    # restart markers every MCU row (common in camera firmware); gives the engine a
    # structural sequence (RST0..RST7) to test continuity between fragments.
    img.save(buf, "JPEG", quality=90, restart_marker_rows=1, exif=exif.tobytes())
    return buf.getvalue()


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(rng: random.Random, title: str, author: str, when: datetime,
             pages: list[list[str]], catalog_extra: str = "", extra_objs: list[bytes] | None = None) -> bytes:
    objs: list[bytes] = []
    n_pages = len(pages)
    page_ids = [5 + 2 * i for i in range(n_pages)]
    objs.append(f"<< /Type /Catalog /Pages 2 0 R {catalog_extra}>>".encode())
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    d = when.strftime("D:%Y%m%d%H%M%S")
    objs.append(f"<< /Title ({_pdf_escape(title)}) /Author ({_pdf_escape(author)}) "
                f"/CreationDate ({d}) /ModDate ({d}) /Producer (RecoveryLens synthetic) >>".encode())
    for i, lines in enumerate(pages):
        content = ["BT", "/F1 10 Tf", "13 TL", "56 780 Td"]
        for ln in lines:
            content.append(f"({_pdf_escape(ln)}) Tj T*")
        content.append("ET")
        stream = ("\n".join(content)).encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs += extra_objs or []
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(rng: random.Random, title: str, creator: str, when: datetime,
              paragraphs: list[str], image: bytes | None) -> bytes:
    dt = (when.year, when.month, when.day, when.hour, when.minute, when.second)
    iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    parts: list[tuple[str, bytes, int]] = [
        ("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '</Types>').encode(), zipfile.ZIP_DEFLATED),
        ("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '</Relationships>').encode(), zipfile.ZIP_DEFLATED),
        ("docProps/core.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{iso}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{iso}</dcterms:modified>'
            '</cp:coreProperties>').encode(), zipfile.ZIP_DEFLATED),
        ("word/document.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>'
            f'{body}</w:body></w:document>').encode(), zipfile.ZIP_DEFLATED),
        ("word/styles.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
            + "".join(f'<w:style w:type="paragraph" w:styleId="S{i}"><w:name w:val="Style {i}"/></w:style>'
                      for i in range(60))
            + '</w:styles>').encode(), zipfile.ZIP_DEFLATED),
    ]
    if image:
        parts.append(("word/media/image1.jpeg", image, zipfile.ZIP_STORED))
    parts.append(("word/_rels/document.xml.rels", (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + ('<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.jpeg"/>' if image else "")
        + '</Relationships>').encode(), zipfile.ZIP_DEFLATED))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data, method in parts:
            zi = zipfile.ZipInfo(name, date_time=dt)
            zi.compress_type = method
            z.writestr(zi, data)
    return buf.getvalue()


def make_sqlite(rng: random.Random, n_emp: int = 600, n_tx: int = 1500) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(path)
        con.execute("PRAGMA page_size=4096")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, email TEXT, salary INTEGER, joined TEXT)")
        con.execute("CREATE TABLE transactions(id INTEGER PRIMARY KEY, emp_id INTEGER, amount REAL, memo TEXT, ts TEXT)")
        first = "Asha Ravi Meera Karan Divya Arjun Neha Vikram Priya Rohan Sneha Aditya".split()
        last = "Rao Iyer Shetty Kumar Nair Patil Reddy Joshi Hegde Gowda".split()
        depts = "Engineering Finance Operations Research Legal Sales".split()
        for i in range(1, n_emp + 1):
            fn, ln = rng.choice(first), rng.choice(last)
            con.execute("INSERT INTO employees VALUES(?,?,?,?,?,?)",
                        (i, f"{fn} {ln}", rng.choice(depts), f"{fn.lower()}.{ln.lower()}{i}@example.org",
                         rng.randint(40, 180) * 1000, f"20{rng.randint(15, 25)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))
        t0 = datetime(2025, 1, 1)
        for i in range(1, n_tx + 1):
            con.execute("INSERT INTO transactions VALUES(?,?,?,?,?)",
                        (i, rng.randint(1, n_emp), round(rng.uniform(5, 5000), 2),
                         f"{rng.choice(['travel', 'hardware', 'license', 'consulting', 'training'])} {rng.choice(WORDS)}",
                         (t0 + timedelta(minutes=317 * i)).strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        con.execute("VACUUM")
        con.close()
        return Path(path).read_bytes()
    finally:
        os.remove(path)


def make_log(rng: random.Random, host: str, start: datetime, n: int) -> bytes:
    users = ["alice", "bob", "carol", "svc_backup", "root"]
    msgs = [
        "sshd[{p}]: Accepted password for {u} from 10.0.{a}.{b} port {port} ssh2",
        "sshd[{p}]: Failed password for {u} from 192.168.{a}.{b} port {port} ssh2",
        "kernel: usb 1-{a}: new high-speed USB device number {b} using xhci_hcd",
        "CRON[{p}]: ({u}) CMD (/usr/local/bin/backup.sh --target /mnt/archive)",
        "systemd[1]: Started Session {p} of user {u}.",
        "sudo: {u} : TTY=pts/{a} ; PWD=/home/{u} ; USER=root ; COMMAND=/usr/bin/rsync -a /srv/project /mnt/usb",
    ]
    t = start
    out = []
    for _ in range(n):
        t += timedelta(seconds=rng.randint(3, 240))
        m = rng.choice(msgs).format(p=rng.randint(1000, 60000), u=rng.choice(users), a=rng.randint(0, 9),
                                   b=rng.randint(1, 254), port=rng.randint(40000, 65000))
        out.append(f"{t:%Y-%m-%d %H:%M:%S} {host} {m}")
    return ("\n".join(out) + "\n").encode()


def make_text(rng: random.Random, title: str, n_par: int, extra: list[str] | None = None) -> bytes:
    pars = [title, "=" * len(title), ""]
    for i in range(n_par):
        pars.append(" ".join(_sentence(rng, rng.randint(8, 18)) for _ in range(rng.randint(3, 6))))
        if extra and i % 4 == 0:
            pars.append(rng.choice(extra))
        pars.append("")
    return ("\n".join(pars)).encode()


# ---------------------------------------------------------------------------------------
# Security-demo payloads. None of these contain executable code or real malware.
# ---------------------------------------------------------------------------------------

def make_suspicious_binary(rng: random.Random) -> bytes:
    """An 'unknown binary' carrying a PE-style header stub (MZ/PE signatures and a DOS-stub
    message, but no sections and no code), high-entropy filler and dropper-like strings.
    It is inert data built to exercise static indicators."""
    hdr = bytearray(0x200)
    hdr[0:2] = b"MZ"
    hdr[0x3C:0x40] = (0x80).to_bytes(4, "little")
    msg = b"This program cannot be run in DOS mode.\r\r\n$"
    hdr[0x4E:0x4E + len(msg)] = msg
    hdr[0x80:0x84] = b"PE\x00\x00"
    hdr[0x84:0x86] = (0x14C).to_bytes(2, "little")  # machine field; NumberOfSections stays 0
    strings = (b"\x00cmd.exe /c start update.bat\x00powershell -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoA"
               b"\x00http://update-cdn.example.invalid/payload\x00")
    return bytes(hdr) + rng.randbytes(9000) + strings + rng.randbytes(2600)


def make_test_malware_artifact() -> bytes:
    """Harmless antivirus-test artifact (EICAR-equivalent). It contains a RecoveryLens test
    signature string that only RecoveryLens' simulated engine and test rule recognise."""
    from .security import TEST_SIGNATURE
    lines = ["Antivirus test artifact", "=======================", "",
             "This file is a HARMLESS test artifact used to demonstrate malware-detection handling.",
             "It contains no executable code. The line below is a test signature, not malware:", ""]
    body = ("\n".join(lines) + "\n").encode() + TEST_SIGNATURE + b"\n\n"
    return body + b"Padding line for a realistic size.\n" * 60


# ---------------------------------------------------------------------------------------
# FAT-style directory remnant (short entries + VFAT long-file-name entries)
# ---------------------------------------------------------------------------------------

def _fat_dt(t: datetime) -> tuple[int, int]:
    return ((t.hour << 11) | (t.minute << 5) | (t.second // 2),
            ((t.year - 1980) << 9) | (t.month << 5) | t.day)


def _short_name(name: str, idx: int) -> bytes:
    base, _, ext = name.rpartition(".")
    base = "".join(c for c in base.upper() if c.isalnum())[:6] + f"~{idx % 10}"
    return base.ljust(8).encode()[:8] + ext.upper().ljust(3).encode()[:3]


def _lfn_checksum(short: bytes) -> int:
    s = 0
    for c in short:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def fat_entries(name: str, idx: int, first_cluster: int, size: int, when: datetime, deleted: bool) -> bytes:
    short = _short_name(name, idx)
    chk = _lfn_checksum(short)
    u = name.encode("utf-16-le")
    chars = [u[i:i + 2] for i in range(0, len(u), 2)]
    chars.append(b"\x00\x00")
    while len(chars) % 13:
        chars.append(b"\xff\xff")
    groups = [chars[i:i + 13] for i in range(0, len(chars), 13)]
    out = b""
    for seq in range(len(groups), 0, -1):
        g = groups[seq - 1]
        ordv = seq | (0x40 if seq == len(groups) else 0)
        if deleted:
            ordv = 0xE5
        out += (bytes([ordv]) + b"".join(g[0:5]) + bytes([0x0F, 0, chk]) + b"".join(g[5:11])
                + b"\x00\x00" + b"".join(g[11:13]))
    tm, dt = _fat_dt(when)
    sn = (b"\xe5" + short[1:]) if deleted else short
    out += sn + struct.pack("<BBBHHHHHHHI", 0x20, 0, 0, tm, dt, dt, (first_cluster >> 16) & 0xFFFF,
                            tm, dt, first_cluster & 0xFFFF, size)
    return out


# ---------------------------------------------------------------------------------------
# Image builder
# ---------------------------------------------------------------------------------------

@dataclass
class FileSpec:
    name: str
    data: bytes
    type: str
    fragments: int = 1
    drop: list[int] = field(default_factory=list)        # fragment indexes overwritten
    flips: list[tuple[int, int]] = field(default_factory=list)  # (fragment index, bytes flipped)
    dup: list[int] = field(default_factory=list)         # fragment indexes duplicated elsewhere
    drop_clusters: list[int] = field(default_factory=list)  # individual logical clusters overwritten
    when: datetime = datetime(2025, 8, 14, 10, 0, 0)
    dir_entry: bool = True
    deleted: bool = True
    split_at: list[int] | None = None  # explicit logical cluster split points
    reverse_layout: bool = False  # benchmark: later logical fragments get LOWER physical addresses


class ImageBuilder:
    def __init__(self, n_clusters: int, rng: random.Random):
        self.n = n_clusters
        self.rng = rng
        self.buf = bytearray(n_clusters * CLUSTER)
        self.used = [False] * n_clusters
        self.truth: dict[str, Any] = {"files": [], "cluster_size": CLUSTER, "duplicates": [], "notes": []}

    def _free_run(self, k: int, gap: int = 1) -> int:
        for _ in range(4000):
            s = self.rng.randint(gap, self.n - k - gap)
            if not any(self.used[s - gap:s + k + gap]):
                return s
        raise RuntimeError("image too small for layout")

    def _write(self, cl: int, data: bytes) -> None:
        self.buf[cl * CLUSTER:cl * CLUSTER + len(data)] = data
        self.used[cl] = True

    def reserve(self, k: int) -> int:
        s = self._free_run(k)
        for c in range(s, s + k):
            self.used[c] = True
        return s

    def place(self, spec: FileSpec, first_cluster_out: dict[str, int]) -> None:
        data = spec.data
        n = (len(data) + CLUSTER - 1) // CLUSTER
        padded = data + b"\x00" * (n * CLUSTER - len(data))
        k = min(spec.fragments, n)
        if spec.split_at:
            cuts = sorted(spec.split_at)
        else:
            cuts = sorted(self.rng.sample(range(1, n), k - 1)) if k > 1 else []
        bounds = [0] + cuts + [n]
        frags = []
        if spec.reverse_layout:
            # reserve one run per fragment, then hand the highest address to the first fragment
            runs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
            width = max(b - a for a, b in runs)
            starts = sorted((self.reserve(width) for _ in runs), reverse=True)
            for (a, b), s in zip(runs, starts):
                for j in range(b - a):
                    self._write(s + j, padded[(a + j) * CLUSTER:(a + j + 1) * CLUSTER])
                frags.append({"logical_cluster": a, "clusters": b - a, "physical_cluster": s})
        for i in range(len(bounds) - 1) if not spec.reverse_layout else ():
            a, b = bounds[i], bounds[i + 1]
            s = self._free_run(b - a, gap=self.rng.randint(1, 6))
            for j in range(b - a):
                self._write(s + j, padded[(a + j) * CLUSTER:(a + j + 1) * CLUSTER])
            frags.append({"logical_cluster": a, "clusters": b - a, "physical_cluster": s})
        mapping: list[int | None] = [None] * n
        for f in frags:
            for j in range(f["clusters"]):
                mapping[f["logical_cluster"] + j] = f["physical_cluster"] + j
        flips = []
        for fi, nbytes in spec.flips:
            f = frags[fi]
            c = f["physical_cluster"] + self.rng.randrange(f["clusters"])
            base = self.rng.randrange(64, CLUSTER - 64 - nbytes)
            for j in range(nbytes):
                pos = c * CLUSTER + base + j
                self.buf[pos] ^= self.rng.choice([0x01, 0x04, 0x10, 0x40, 0x5A])
                flips.append(pos)
        overwritten: list[int] = []
        for fi in spec.drop:
            f = frags[fi]
            overwritten += [f["physical_cluster"] + j for j in range(f["clusters"])]
        for lc in spec.drop_clusters:
            if mapping[lc] is not None:
                overwritten.append(mapping[lc])
        for c in overwritten:
            # overwritten by later content: zeros, random data or unrelated text
            kind = self.rng.random()
            if kind < 0.4:
                blob = bytes(CLUSTER)
            elif kind < 0.75:
                blob = self.rng.randbytes(CLUSTER)
            else:
                blob = make_text(self.rng, "cache", 20)[:CLUSTER].ljust(CLUSTER, b" ")
            self.buf[c * CLUSTER:(c + 1) * CLUSTER] = blob
            for lc, pc in enumerate(mapping):
                if pc == c:
                    mapping[lc] = None
        for fi in spec.dup:
            f = frags[fi]
            s = self._free_run(f["clusters"], gap=2)
            for j in range(f["clusters"]):
                src = f["physical_cluster"] + j
                self._write(s + j, bytes(self.buf[src * CLUSTER:(src + 1) * CLUSTER]))
            self.truth["duplicates"].append({"file": spec.name, "copy_of_cluster": f["physical_cluster"],
                                             "at_cluster": s, "clusters": f["clusters"]})
        first_cluster_out[spec.name] = frags[0]["physical_cluster"]
        self.truth["files"].append({
            "name": spec.name, "type": spec.type, "size": len(data), "sha256": sha256(data),
            "clusters": n, "fragments": frags, "cluster_map": mapping,
            "overwritten_clusters": sorted(set(overwritten)), "flipped_offsets": flips,
            "header_lost": mapping[0] is None,
        })

    def fill(self) -> None:
        pool_text = make_text(self.rng, "tmp", 400)
        for c in range(self.n):
            if self.used[c]:
                continue
            r = self.rng.random()
            if r < 0.62:
                continue  # never-written / zeroed space
            if r < 0.86:
                blob = self.rng.randbytes(CLUSTER)  # encrypted / compressed remnants
            else:
                o = self.rng.randrange(0, len(pool_text) - CLUSTER)
                blob = pool_text[o:o + CLUSTER]
            self.buf[c * CLUSTER:(c + 1) * CLUSTER] = blob


def realistic_specs(rng: random.Random) -> list[FileSpec]:
    """Real files with the damage real media shows: fragmentation, overwritten clusters, bit-rot."""
    d = datetime
    photo = make_jpeg(rng, "site_inspection_photo", 1024, 768, d(2025, 9, 20, 10, 15), 1)
    photo2 = make_jpeg(rng, "team_photo", 800, 600, d(2025, 9, 21, 16, 40), 2)
    diagram = make_jpeg(rng, "network_diagram", 480, 360, d(2025, 9, 18, 9, 0), 3)
    report = make_pdf(rng, "Incident Report", "Security Team", d(2025, 9, 22, 14, 30),
                      _report_pages(rng, "Incident Report - Server Room Access", 8, ["Incident", "Access log", "Evidence"]))
    minutes = make_pdf(rng, "Meeting Minutes", "PMO", d(2025, 9, 19, 17, 0), _report_pages(rng, "Weekly meeting minutes", 3, []))
    paras = [_sentence(rng, rng.randint(12, 22)) for _ in range(220)]
    paras[0] = "Recovery Test Proposal - storage forensics exercise"
    docx = make_docx(rng, "Recovery Test Proposal", "Analyst", d(2025, 9, 23, 11, 0), paras, diagram)
    db = make_sqlite(rng, 300, 800)
    log = make_log(rng, "storage-gw", d(2025, 9, 23, 8, 0), 380)
    n = lambda b: (len(b) + CLUSTER - 1) // CLUSTER  # noqa: E731
    return [
        FileSpec("site_inspection_photo.jpg", photo, "jpeg", 4, when=d(2025, 9, 20, 10, 15)),
        FileSpec("Meeting_Minutes.pdf", minutes, "pdf", 3, when=d(2025, 9, 19, 17, 0)),
        FileSpec("storage_gateway.log", log, "log", 3, when=d(2025, 9, 23, 18, 0)),
        FileSpec("Incident_Report.pdf", report, "pdf", 5, drop=[2], flips=[(3, 2)], when=d(2025, 9, 22, 14, 30)),
        FileSpec("Recovery_Test_Proposal.docx", docx, "docx", 4, flips=[(2, 1)], when=d(2025, 9, 23, 11, 0)),
        FileSpec("team_photo.jpg", photo2, "jpeg", 3, drop_clusters=[n(photo2) - 1], when=d(2025, 9, 21, 16, 40)),
        FileSpec("evidence_index.db", db, "sqlite", 4, drop_clusters=sorted(rng.sample(range(3, n(db)), max(2, n(db) // 6))),
                 when=d(2025, 9, 23, 9, 30)),
    ]


def build_image(specs: list[FileSpec], n_clusters: int, seed: int, out_img: Path, out_truth: Path,
                description: str, extra_noise_jpeg: int = 0, wipe_clusters: int = 0) -> dict[str, Any]:
    rng = random.Random(seed)
    ib = ImageBuilder(n_clusters, rng)
    dir_cluster = ib.reserve(1)
    firsts: dict[str, int] = {}
    for s in specs:
        ib.place(s, firsts)
    if wipe_clusters:  # a securely-wiped region: one repeating fill pattern, as erase tools leave behind
        w = ib.reserve(wipe_clusters)
        ib.buf[w * CLUSTER:(w + wipe_clusters) * CLUSTER] = b"\xff\x00\x00\x00" * (wipe_clusters * CLUSTER // 4)
        ib.truth["wiped_clusters"] = [w, w + wipe_clusters]
    # FAT-style directory remnant (cluster numbers are image cluster index + 2, as in FAT)
    entries = b""
    for i, s in enumerate(specs):
        if s.dir_entry:
            entries += fat_entries(s.name, i + 1, firsts[s.name] + 2, len(s.data), s.when, s.deleted)
    ib.buf[dir_cluster * CLUSTER:dir_cluster * CLUSTER + len(entries)] = entries
    ib.truth["directory_cluster"] = dir_cluster
    ib.fill()
    out_img.write_bytes(bytes(ib.buf))
    ib.truth.update({"seed": seed, "description": description, "image_sha256": sha256(bytes(ib.buf)),
                     "image_size": len(ib.buf), "WARNING": "Ground truth for evaluation only. Never read by the analysis engine."})
    out_truth.write_text(json.dumps(ib.truth, indent=1))
    return ib.truth


# ---------------------------------------------------------------------------------------
# Demo datasets
# ---------------------------------------------------------------------------------------

def _report_pages(rng: random.Random, title: str, n: int, keywords: list[str]) -> list[list[str]]:
    pages = []
    for p in range(n):
        lines = [f"{title} - page {p + 1}", ""]
        for i in range(52):
            s = _sentence(rng, rng.randint(9, 13))
            if i % 9 == 0 and keywords:
                s = f"{rng.choice(keywords)}: " + s
            lines.append(s)
        pages.append(lines)
    return pages


def dataset_specs(key: str, rng: random.Random) -> tuple[list[FileSpec], int, str]:
    d = datetime
    if key == "A":
        photo = make_jpeg(rng, "vacation_photo", 800, 600, d(2025, 8, 14, 10, 22, 31), 0)
        other = make_jpeg(rng, "sunset_beach", 640, 480, d(2025, 8, 15, 18, 40, 0), 3)
        n = (len(photo) + CLUSTER - 1) // CLUSTER
        return ([FileSpec("vacation_photo.jpg", photo, "jpeg", 4, flips=[(1, 2)], drop_clusters=[n - 1],
                          when=d(2025, 8, 14, 10, 22, 31)),
                 FileSpec("sunset_beach.jpg", other, "jpeg", 2, when=d(2025, 8, 15, 18, 40))],
                600, "Dataset A - simple JPEG recovery (4-way fragmented photo, final cluster overwritten, minor bit-rot)")
    if key == "B":
        kw = ["Project Aurora", "Milestone", "Budget"]
        pdf = make_pdf(rng, "Project Report", "R. Kandagal", d(2025, 9, 2, 16, 5),
                       _report_pages(rng, "Project Aurora - Status Report", 10, kw))
        inv = make_pdf(rng, "Invoice 2025-117", "Accounts", d(2025, 6, 1, 9, 0),
                       _report_pages(rng, "Invoice 2025-117", 3, []))
        return ([FileSpec("Project_Report.pdf", pdf, "pdf", 7, drop=[3], flips=[(5, 3)],
                          when=d(2025, 9, 2, 16, 5)),
                 FileSpec("Invoice_2025-117.pdf", inv, "pdf", 2, when=d(2025, 6, 1, 9, 0))],
                700, "Dataset B - fragmented PDF (7 fragments, one overwritten, one with bit-rot)")
    if key == "C":
        img = make_jpeg(rng, "architecture_diagram", 480, 360, d(2025, 9, 10, 11, 0), 2)
        paras = [_sentence(rng, rng.randint(12, 24)) for _ in range(260)]
        paras[0] = "Project Proposal v3 - Aurora data platform"
        docx = make_docx(rng, "Project Proposal v3", "R. Kandagal", d(2025, 9, 10, 11, 30), paras, img)
        return ([FileSpec("Project_Proposal.docx", docx, "docx", 5, drop=[2], flips=[(3, 2), (1, 1)],
                          when=d(2025, 9, 10, 11, 30))],
                600, "Dataset C - corrupted DOCX (5 fragments, one overwritten, CRC-breaking bit-rot)")
    if key == "D":
        db = make_sqlite(rng, 500, 1400)
        n = (len(db) + CLUSTER - 1) // CLUSTER
        return ([FileSpec("records.db", db, "sqlite", 6,
                          drop_clusters=sorted(rng.sample(range(3, n), max(3, n // 5))),
                          flips=[(2, 2)], when=d(2025, 7, 30, 20, 15))],
                700, "Dataset D - damaged SQLite database (about 20% of pages overwritten)")
    if key == "E":
        pic = make_jpeg(rng, "sensor_capture", 800, 600, d(2025, 5, 3, 7, 12), 1)
        return ([FileSpec("unknown_fragment.bin", pic, "jpeg", 8, drop=[0, 1, 2, 4, 5, 6, 7],
                          when=d(2025, 5, 3, 7, 12))],
                400, "Dataset E - largely overwritten data (header and ~88% of clusters destroyed)")
    if key == "USB":
        kw = ["Project Aurora", "Milestone", "Budget", "Client deliverable"]
        diag = make_jpeg(rng, "aurora_architecture", 480, 360, d(2025, 9, 10, 11, 0), 2)
        paras = [_sentence(rng, rng.randint(12, 24)) for _ in range(300)]
        paras[0] = "Project Proposal v3 - Aurora data platform"
        for i in range(5, 300, 23):
            paras[i] = f"Project Aurora milestone {i // 23}: " + paras[i]
        docx = make_docx(rng, "Project Proposal v3", "R. Kandagal", d(2025, 9, 10, 11, 30), paras, diag)
        budget = make_docx(rng, "Budget Notes", "Finance", d(2025, 8, 1, 15, 0),
                           [_sentence(rng, 14) for _ in range(120)], None)
        report = make_pdf(rng, "Project Report", "R. Kandagal", d(2025, 9, 2, 16, 5),
                          _report_pages(rng, "Project Aurora - Status Report", 10, kw))
        minutes = make_pdf(rng, "Meeting Minutes", "PMO", d(2025, 9, 9, 17, 0),
                           _report_pages(rng, "Aurora steering meeting minutes", 4, ["Project Aurora"]))
        invoice = make_pdf(rng, "Invoice 2025-117", "Accounts", d(2025, 6, 1, 9, 0),
                           _report_pages(rng, "Invoice 2025-117", 3, []))
        db = make_sqlite(rng, 400, 1100)
        ndb = (len(db) + CLUSTER - 1) // CLUSTER
        photos = [make_jpeg(rng, n, 800, 600, d(2025, 8, 14, 10, 22 + i), i) for i, n in
                  enumerate(["vacation_photo", "team_offsite", "whiteboard_notes", "server_rack"])]
        lost = make_jpeg(rng, "sensor_capture", 800, 600, d(2025, 5, 3, 7, 12), 1)
        log = make_log(rng, "aurora-gw", d(2025, 9, 9, 8, 0), 420)
        notes = make_text(rng, "Aurora field notes", 30, ["Project Aurora risk register updated", "Backup to USB completed"])
        n_ph = (len(photos[0]) + CLUSTER - 1) // CLUSTER
        specs = [
            FileSpec("Project_Proposal_v3.docx", docx, "docx", 4, flips=[(2, 1)], drop_clusters=[5],
                     when=d(2025, 9, 10, 11, 30)),
            FileSpec("Project_Report.pdf", report, "pdf", 6, drop=[3], flips=[(4, 2)], when=d(2025, 9, 2, 16, 5)),
            FileSpec("Meeting_Minutes.pdf", minutes, "pdf", 3, dup=[1], when=d(2025, 9, 9, 17, 0)),
            FileSpec("Invoice_2025-117.pdf", invoice, "pdf", 1, when=d(2025, 6, 1, 9, 0)),
            FileSpec("Budget_Notes.docx", budget, "docx", 2, when=d(2025, 8, 1, 15, 0)),
            FileSpec("records.db", db, "sqlite", 5, drop_clusters=sorted(rng.sample(range(3, ndb), ndb // 5)),
                     when=d(2025, 7, 30, 20, 15)),
            FileSpec("vacation_photo.jpg", photos[0], "jpeg", 3, drop_clusters=[n_ph - 1], when=d(2025, 8, 14, 10, 22)),
            FileSpec("team_offsite.jpg", photos[1], "jpeg", 4, flips=[(2, 3)], when=d(2025, 8, 14, 10, 23)),
            FileSpec("whiteboard_notes.jpg", photos[2], "jpeg", 3, when=d(2025, 9, 10, 10, 58)),
            FileSpec("server_rack.jpg", photos[3], "jpeg", 5, drop=[2], when=d(2025, 7, 2, 14, 0)),
            FileSpec("unknown_fragment.bin", lost, "jpeg", 8, drop=[0, 1, 2, 4, 5, 6, 7], when=d(2025, 5, 3, 7, 12)),
            FileSpec("gateway.log", log, "log", 3, dir_entry=True, when=d(2025, 9, 9, 23, 0)),
            FileSpec("field_notes.txt", notes, "txt", 2, when=d(2025, 9, 11, 9, 0)),
        ]
        return specs, 3600, "Scenario - corrupted USB storage image (13 deleted files, mixed damage)"
    if key == "REAL":
        return (realistic_specs(rng), 4608,
                "Realistic damaged USB drive (18 MB): 7 real deleted files - fragmented, partly overwritten, bit-rot, a wiped region")
    if key == "SEC1":
        pdf = make_pdf(rng, "Clean Quarterly Report", "Finance", d(2025, 9, 1, 9, 0),
                       _report_pages(rng, "Clean Quarterly Report", 4, ["Project Aurora"]))
        pic = make_jpeg(rng, "site_photo", 640, 480, d(2025, 9, 1, 10, 0), 2)
        return ([FileSpec("clean_report.pdf", pdf, "pdf", 2, when=d(2025, 9, 1, 9, 0)),
                 FileSpec("site_photo.jpg", pic, "jpeg", 2, when=d(2025, 9, 1, 10, 0))],
                400, "Security demo 1 - clean PDF (expected: CLEAN under the configured scanner)")
    if key == "SEC2":
        js_pdf = make_pdf(rng, "Invoice Viewer", "unknown", d(2025, 9, 3, 12, 0),
                          _report_pages(rng, "Invoice INV-2291", 2, []),
                          catalog_extra="/OpenAction 9 0 R ",
                          extra_objs=[b"<< /Type /Action /S /JavaScript /JS (app.alert\\('RecoveryLens demo: harmless alert'\\);) >>"])
        blob = make_suspicious_binary(rng)
        return ([FileSpec("invoice_viewer.pdf", js_pdf, "pdf", 1, when=d(2025, 9, 3, 12, 0)),
                 FileSpec("update_helper.bin", blob, "bin", 1, when=d(2025, 9, 3, 12, 5))],
                400, "Security demo 2 - suspicious content: PDF with auto-run JavaScript + unknown binary with an embedded PE header stub")
    if key == "SEC3":
        art = make_test_malware_artifact()
        pdf = make_pdf(rng, "Meeting Notes", "PMO", d(2025, 9, 4, 15, 0), _report_pages(rng, "Meeting notes", 2, []))
        return ([FileSpec("av_test_artifact.txt", art, "txt", 1, when=d(2025, 9, 4, 14, 0)),
                 FileSpec("meeting_notes.pdf", pdf, "pdf", 1, when=d(2025, 9, 4, 15, 0))],
                400, "Security demo 3 - SIMULATED / TEST malware detection (harmless test signature, no real malware)")
    raise KeyError(key)


DATASETS: dict[str, dict[str, str]] = {
    "REAL": {"title": "Damaged USB drive (realistic, 18 MB)", "summary":
             "7 real deleted files: photos, PDFs, a Word document, a database and a log - scattered, partly overwritten, "
             "with bit-rot and a wiped region. Best dataset for a live demo.", "featured": "true"},
    "USB": {"title": "Corrupted USB image (full scenario)", "summary":
            "13 deleted files - documents, photos, database, logs - fragmented, partially overwritten and bit-rotted."},
    "A": {"title": "A - Simple JPEG recovery", "summary": "4-way fragmented photo; last cluster overwritten; minor bit-rot."},
    "B": {"title": "B - Fragmented PDF", "summary": "7-fragment report; one fragment overwritten; one corrupted object."},
    "C": {"title": "C - Corrupted DOCX", "summary": "5-fragment Word document with an embedded image; CRC failures."},
    "D": {"title": "D - Damaged database", "summary": "SQLite database with ~20% of pages overwritten."},
    "E": {"title": "E - Unrecoverable data", "summary": "Header and most clusters overwritten; only orphan fragments remain."},
    "SEC1": {"title": "Security 1 - Clean PDF", "summary": "Clean report and photo. Expected security result: CLEAN (with the clean-caveat).", "group": "security"},
    "SEC2": {"title": "Security 2 - Suspicious content", "summary": "PDF with auto-run JavaScript and an unknown binary carrying a PE header stub (inert). Expected: SUSPICIOUS.", "group": "security"},
    "SEC3": {"title": "Security 3 - Test malware detection", "summary": "Harmless test-signature artifact (EICAR-equivalent). Expected: SIMULATED / TEST MALWARE DETECTION.", "group": "security"},
}

SEEDS = {"REAL": 2025, "USB": 7, "A": 11, "B": 12, "C": 13, "D": 14, "E": 15, "SEC1": 21, "SEC2": 22, "SEC3": 23}


def build_dataset(key: str, out_dir: Path, seed: int | None = None) -> tuple[Path, Path]:
    seed = SEEDS.get(key, 99) if seed is None else seed
    rng = random.Random(seed)
    specs, n_clusters, desc = dataset_specs(key, rng)
    out_dir.mkdir(parents=True, exist_ok=True)
    img = out_dir / f"demo_{key}_{seed}.img"
    truth = out_dir / f"demo_{key}_{seed}.truth.json"
    build_image(specs, n_clusters, seed, img, truth, desc, wipe_clusters=64 if key == "REAL" else 0)
    return img, truth
