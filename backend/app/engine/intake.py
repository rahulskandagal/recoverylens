"""Input-type detection: single file vs. disk/partition image vs. raw storage dump.

A single document (PDF, JPEG, DOCX, ...) is validated/repaired as one file; carving is
reserved for storage images. Detection is deterministic and the evidence is recorded.
"""
from __future__ import annotations

import struct
from typing import Any

from .common import printable_ratio

FILE_SIGS = [  # (format, signature, offset)
    ("pdf", b"%PDF-", 0), ("jpeg", b"\xff\xd8\xff", 0), ("png", b"\x89PNG\r\n\x1a\n", 0),
    ("gif", b"GIF8", 0), ("sqlite", b"SQLite format 3\x00", 0), ("zip", b"PK\x03\x04", 0),
    ("mp4", b"ftyp", 4),
]
LABEL = {"pdf": "PDF", "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "sqlite": "SQLite", "docx": "DOCX",
         "xlsx": "XLSX", "pptx": "PPTX", "zip": "ZIP", "mp4": "MP4", "log": "log/text", "txt": "text"}
TEXT_PRINTABLE_THRESHOLD = 0.85  # plain text has no magic-byte signature; this is the only honest way to tell it apart


def _zip_kind(data: bytes) -> str:
    head = data[:65536]
    if b"word/" in head or b"word/document.xml" in data[-65536:]:
        return "docx"
    if b"xl/" in head:
        return "xlsx"
    if b"ppt/" in head:
        return "pptx"
    return "zip"


def detect(data: bytes, filename: str = "") -> dict[str, Any]:
    ev: list[str] = []
    n = len(data)
    # ---- forensic containers & file systems -------------------------------------------------
    if data.startswith(b"EVF\x09\x0d\x0a\xff\x00") or data.startswith(b"EVF2\r\n\x81\x00"):
        return {"kind": "disk_image", "format": "e01", "label": "DISK IMAGE – EnCase E01",
                "evidence": ["EWF/E01 signature at offset 0"], "supported": False,
                "note": "E01 containers are compressed; this prototype analyses raw (dd) images. Convert with ewfexport first."}
    fs = None
    if n >= 512 and data[510:512] == b"\x55\xaa":
        if data[3:11] == b"NTFS    ":
            fs = "NTFS boot sector"
        elif data[54:62] in (b"FAT12   ", b"FAT16   ") or data[82:90] == b"FAT32   ":
            fs = f"{(data[54:62] if data[54:58] == b"FAT1" else data[82:90]).decode().strip()} boot sector"
        elif data[3:11] == b"EXFAT   ":
            fs = "exFAT boot sector"
        else:
            parts = [data[446 + 16 * i:462 + 16 * i] for i in range(4)]
            if any(p[4] != 0 and struct.unpack("<I", p[12:16])[0] > 0 for p in parts):
                fs = "MBR partition table"
    if n >= 520 and data[512:520] == b"EFI PART":
        fs = "GPT partition table"
    if n >= 1082 and data[1080:1082] == b"\x53\xef":
        fs = "ext2/3/4 superblock"
    if n >= 32774 and data[32769:32774] == b"CD001":
        fs = "ISO-9660 volume descriptor"
    if fs:
        ev.append(f"{fs} detected")
        return {"kind": "disk_image", "format": "raw", "label": "DISK IMAGE", "filesystem": fs, "evidence": ev, "supported": True}
    # ---- single files -------------------------------------------------------------------------
    for fmt, sig, off in FILE_SIGS:
        if data[off:off + len(sig)] == sig:
            if fmt == "zip":
                fmt = _zip_kind(data)
            ev.append(f"{LABEL[fmt]} signature at offset {off}")
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext:
                ev.append(f"file extension '.{ext}' " + ("agrees" if ext in (fmt, "jpg" if fmt == "jpeg" else fmt, "db" if fmt == "sqlite" else fmt) else "does not match the signature"))
            ev.append(f"{n:,} bytes; no partition table or file-system structures")
            return {"kind": "single_file", "format": fmt, "label": f"UPLOADED FILE – {LABEL[fmt]}", "evidence": ev, "supported": True}
    # ---- plain text/log, as a last resort: no magic bytes exist for these formats, so a high
    # printable-byte ratio is the only signal -- without this, a genuinely recovered .txt/.log/.csv/
    # .json/etc. file (no header signature at all) would silently produce zero recovered candidates.
    pr = printable_ratio(data)
    if pr >= TEXT_PRINTABLE_THRESHOLD:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        fmt = "log" if ext == "log" else "txt"
        ev.append(f"{pr:.0%} printable bytes, no binary file signature at offset 0")
        ev.append(f"{n:,} bytes; no partition table or file-system structures")
        return {"kind": "single_file", "format": fmt, "label": f"UPLOADED FILE – {LABEL[fmt].upper()}",
                "evidence": ev, "supported": True}
    ev.append("no file-system signature and no single-file signature at offset 0")
    return {"kind": "raw_image", "format": "raw", "label": "RAW STORAGE IMAGE", "evidence": ev, "supported": True,
            "note": "Treated as a raw dump of unallocated space; files are carved by content."}
