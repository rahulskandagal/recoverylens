"""Signature-stub detection and end-to-end recovery on a realistic damaged image (scripts/make_test_image.py)."""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine.analyze import analyze  # noqa: E402
from app.engine.evaluate import evaluate  # noqa: E402
from app.engine.postproc import signature_stub  # noqa: E402
from app.engine.synth import _report_pages, make_docx, make_jpeg, make_pdf  # noqa: E402
from datetime import datetime  # noqa: E402

RNG = random.Random(5)


class StubTests(unittest.TestCase):
    def test_signature_followed_by_random_bytes_is_a_stub(self):
        noise = os.urandom(20000)
        for sig, t in [(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00", "jpeg"), (b"%PDF-1.7\n", "pdf"),
                       (b"PK\x03\x04\x14\x00\x06\x00" + os.urandom(22), "zip"), (b"\x89PNG\r\n\x1a\n", "png")]:
            self.assertIsNotNone(signature_stub(sig + noise, t), t)

    def test_real_files_are_never_stubs(self):
        d = datetime(2025, 1, 1)
        self.assertIsNone(signature_stub(make_jpeg(RNG, "x", 640, 480, d, 1), "jpeg"))
        self.assertIsNone(signature_stub(make_pdf(RNG, "t", "a", d, _report_pages(RNG, "t", 3, [])), "pdf"))
        self.assertIsNone(signature_stub(make_docx(RNG, "t", "a", d, ["para " * 40] * 80, None), "docx"))


class RealisticImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from make_test_image import build
        cls.tmp = Path(tempfile.mkdtemp(prefix="rl_real_"))
        r = build(18, 2025, cls.tmp / "img.img")
        cls.truth = json.loads(Path(r["truth"]).read_text())
        cls.res = analyze(r["image"], cls.tmp / "out", lambda s, m: None, lambda p, m: None, None, "upload", "img.img")
        cls.by = {f["file_name"].split("__")[0]: f for f in cls.res["files"]}

    def test_intact_fragmented_files_are_fully_recovered(self):
        for n in ("site_inspection_photo", "Meeting_Minutes", "storage_gateway"):
            self.assertEqual(self.by[n]["recovery_status"], "FULLY_RECOVERED", n)
            self.assertEqual(self.by[n]["export"]["class"], "validated_file", n)

    def test_split_pdf_tail_is_placed_by_startxref(self):
        ops = " ".join(self.by["Meeting_Minutes"]["provenance"]["operations"])
        self.assertIn("points exactly at the xref table", ops)

    def test_docx_fully_located_and_bitrot_reported_not_hidden(self):
        f = self.by["Recovery_Test_Proposal"]
        self.assertEqual(f["reconstruction_percentage"], 100.0)
        self.assertTrue(f["corrupted_ranges"])  # the injected bit flip is reported
        self.assertNotEqual(f["export"]["class"], "validated_file")

    def test_overwritten_data_is_never_claimed(self):
        for n in ("Incident_Report", "team_photo", "evidence_index"):
            self.assertTrue(self.by[n]["missing_ranges"], n)

    def test_ground_truth_precision(self):
        ev = evaluate(self.res, self.truth)
        self.assertEqual(ev["cluster_placement_precision"], 100.0)
        self.assertEqual(ev["edge_accuracy"], 100.0)
        self.assertLessEqual(ev["cluster_recall"], 100.0)


class LayoutRobustnessTests(unittest.TestCase):
    """The same damage in different random layouts must give the same verdicts (no layout luck)."""

    def test_three_random_layouts(self):
        from make_test_image import build
        for seed in (7, 99, 31337):
            tmp = Path(tempfile.mkdtemp(prefix=f"rl_seed{seed}_"))
            r = build(18, seed, tmp / "img.img")
            res = analyze(r["image"], tmp / "out", lambda s, m: None, lambda p, m: None, None, "upload", "img.img")
            by = {f["file_name"].split("__")[0]: f for f in res["files"]}
            for n in ("site_inspection_photo", "Meeting_Minutes", "storage_gateway"):
                self.assertEqual(by[n]["recovery_status"], "FULLY_RECOVERED", f"seed {seed}: {n}")
            # layouts differ in how many pieces a gap needs; whatever is located must be right, the rest reported missing
            ev = evaluate(res, json.loads(Path(r["truth"]).read_text()))
            self.assertEqual(ev["cluster_placement_precision"], 100.0, f"seed {seed}")
            self.assertEqual(ev["edge_accuracy"], 100.0, f"seed {seed}")
            docx = by["Recovery_Test_Proposal"]
            if docx["reconstruction_percentage"] < 100:
                self.assertTrue(docx["missing_ranges"], f"seed {seed}: gap must be reported, not hidden")
                self.assertNotEqual(docx["export"]["class"], "validated_file")


if __name__ == "__main__":
    unittest.main()
