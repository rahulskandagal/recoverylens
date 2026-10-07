"""Write a set of REAL files with realistic corruption for testing single-file mode.

    .venv\\Scripts\\python scripts\\make_corrupted_files.py [out_dir]

Each file starts as a valid file (Pillow JPEG, PDF with objects/xref, DOCX zip) and is then damaged the
way real storage damages data. The damage applied is printed and saved in corruption_manifest.json.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.synth import _report_pages, _sentence, make_docx, make_jpeg, make_pdf  # noqa: E402


def main(out: Path) -> None:
    rng = random.Random(4242)
    d = datetime(2025, 9, 24, 10, 0)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []

    def save(name: str, data: bytes, damage: str, expect: str) -> None:
        (out / name).write_bytes(data)
        manifest.append({"file": name, "size": len(data), "damage": damage, "expected_result": expect})

    # 1. PDF: 1.5 KB of an object body overwritten with random bytes (xref still intact)
    pdf = bytearray(make_pdf(rng, "Quarterly Audit", "Finance", d, _report_pages(rng, "Quarterly Audit Report", 6, ["Audit"])))
    at = len(pdf) // 3
    pdf[at:at + 1500] = rng.randbytes(1500)
    save("corrupted_audit_report.pdf", bytes(pdf), f"bytes {at}-{at + 1500} overwritten with random data",
         "FRAGMENT ONLY (integrity ~78): the overwritten object(s) are flagged as corrupted ranges; intact objects inventoried")

    # 2. PDF: truncated (last 30% lost: xref, trailer and %%EOF gone)
    pdf2 = make_pdf(rng, "Project Plan", "PMO", d, _report_pages(rng, "Project Plan", 5, []))
    save("truncated_project_plan.pdf", pdf2[: int(len(pdf2) * 0.7)], "last 30% of the file missing (xref/trailer/%%EOF lost)",
         "PARTIALLY recovered (integrity ~49): surviving objects inventoried; nothing invented for the lost tail")

    # 3. JPEG: bit flips in the scan data + truncated end (EOI lost)
    jpg = bytearray(make_jpeg(rng, "site_photo", 1024, 768, d, 2))
    jpg = jpg[: int(len(jpg) * 0.85)]
    flips = sorted(rng.randrange(len(jpg) // 3, len(jpg) - 2000) for _ in range(6))
    for p in flips:
        jpg[p] ^= 0x10
    save("damaged_site_photo.jpg", bytes(jpg), f"6 bit flips at {flips}; last 15% truncated (no EOI marker)",
         "PARTIALLY recovered (integrity ~69): decodable part shown, missing rows hatched, suspect rows boxed")

    # 4. DOCX: CRC-breaking corruption inside word/document.xml's compressed data
    paras = [_sentence(rng, 16) for _ in range(150)]
    docx = bytearray(make_docx(rng, "Incident Notes", "Analyst", d, paras, None))
    i = docx.find(b"word/document.xml") + 200
    docx[i:i + 40] = rng.randbytes(40)
    save("corrupted_incident_notes.docx", bytes(docx), f"40 bytes overwritten inside word/document.xml data at offset {i}",
         "MOSTLY recovered (integrity ~80): other members verified by CRC-32; document.xml reported as corrupted")

    # 5. PDF: header wiped (first 64 bytes zeroed) - file no longer starts with %PDF
    pdf3 = bytearray(make_pdf(rng, "Contract", "Legal", d, _report_pages(rng, "Service Contract", 3, [])))
    pdf3[:64] = bytes(64)
    save("header_wiped_contract.pdf", bytes(pdf3), "first 64 bytes (the %PDF header) zeroed",
         "FRAGMENT ONLY (integrity ~52): no signature at offset 0, so it is treated as raw data; the PDF is still found "
         "by carving and named from its /Title")

    (out / "corruption_manifest.json").write_text(json.dumps(manifest, indent=2))
    for m in manifest:
        print(f"{m['file']:34} {m['size']:>9,} B  {m['damage']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "test_images")
