"""Deterministic test fixtures.  python tests/fixtures/build_fixtures.py

valid.pdf            2 pages, FlateDecode content streams, /Info /Title
truncated_trailer.pdf  last 150 bytes cut (no startxref / %%EOF, trailer damaged)
corrupted_xref.pdf     xref offsets for objects 4-6 point to wrong byte positions
corrupted_stream.pdf   bytes flipped inside page 1's compressed content stream
photo.jpg              baseline JPEG
fat12_disk.img         FAT12 boot sector + deleted directory entry + the deleted file's data
corrupted_demo_report.pdf  (checked in) real-world sample: 800-byte region overwritten
"""
from __future__ import annotations

import io
import random
import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from app.engine.synth import fat_entries, make_jpeg  # noqa: E402


def build_pdf(pages: list[str], title: str = "Fixture Report") -> bytes:
    objs: list[bytes] = []
    n = len(pages)
    page_ids = [5 + 2 * i for i in range(n)]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(f"<< /Title ({title}) /Producer (RecoveryLens fixtures) >>".encode())
    for i, text in enumerate(pages):
        content = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET\n".encode() * 40
        comp = zlib.compress(content, 9)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(comp)).encode() + b" /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, body in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    x = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 4 0 R >>\nstartxref\n{x}\n%%EOF\n".encode())
    return out.getvalue()


def fat12_image(payload: bytes, name: str = "deleted_notes.jpg") -> bytes:
    size = 64 * 4096
    img = bytearray(size)
    bs = bytearray(512)
    bs[0:3] = b"\xeb\x3c\x90"
    bs[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHII", bs, 11, 512, 8, 1, 2, 64, size // 512, 0xF8, 1, 32, 2, 0, 0)
    bs[54:62] = b"FAT12   "
    bs[510:512] = b"\x55\xaa"
    img[0:512] = bs
    ent = fat_entries(name, 1, 16 + 2, len(payload), datetime(2025, 9, 1, 12, 0), deleted=True)
    img[8 * 4096:8 * 4096 + len(ent)] = ent
    img[16 * 4096:16 * 4096 + len(payload)] = payload
    return bytes(img)


def build(out: Path = HERE) -> dict[str, Path]:
    rng = random.Random(42)
    valid = build_pdf(["Quarterly recovery report - page one", "Findings and appendix - page two"])
    files: dict[str, bytes] = {"valid.pdf": valid}
    files["truncated_trailer.pdf"] = valid[:-150]
    x = valid.rfind(b"\nxref\n") + 1  # the table itself, not 'startxref'
    lines = valid[x:].split(b"\n")
    for i in (6, 7, 8):  # entries for objects 4, 5, 6
        lines[i] = b"%010d 00000 n " % (int(lines[i][:10]) + 37)
    files["corrupted_xref.pdf"] = valid[:x] + b"\n".join(lines)
    s = bytearray(valid)
    st = valid.find(b"stream\n", valid.find(b"6 0 obj")) + 7
    for k in range(20, 40):
        s[st + k] ^= 0xA5
    files["corrupted_stream.pdf"] = bytes(s)
    jpg = make_jpeg(rng, "fixture", 320, 240, datetime(2025, 1, 1))
    files["photo.jpg"] = jpg
    files["fat12_disk.img"] = fat12_image(jpg)
    paths = {}
    for k, v in files.items():
        (out / k).write_bytes(v)
        paths[k] = out / k
    paths["corrupted_demo_report.pdf"] = out / "corrupted_demo_report.pdf"
    return paths


if __name__ == "__main__":
    for k, p in build().items():
        print(k, p.stat().st_size if p.exists() else "missing")
