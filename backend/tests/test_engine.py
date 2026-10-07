"""Engine tests. Run from backend/:  .venv\\Scripts\\python -m unittest discover -s tests -v"""
from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.analyze import analyze  # noqa: E402
from app.engine.common import CLUSTER, Segment, fill_gaps, split_corrupted  # noqa: E402
from app.engine.evaluate import evaluate  # noqa: E402
from app.engine.priority import DEFAULT_CRITERIA, prioritize  # noqa: E402
from app.engine.scanner import EvidenceImage, classify_cluster, scan  # noqa: E402
from app.engine.synth import build_dataset, make_docx, make_jpeg, make_sqlite  # noqa: E402
from app.engine.validators import validate  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rl_test_"))
_cache: dict[str, tuple[Path, dict, dict]] = {}


def run(key: str) -> tuple[Path, dict, dict]:
    if key not in _cache:
        img, truth = build_dataset(key, TMP / "demo")
        res = analyze(str(img), TMP / f"out_{key}", lambda s, m: None, lambda p, m: None)
        _cache[key] = (img, res, json.loads(truth.read_text()))
    return _cache[key]


class ScannerTests(unittest.TestCase):
    def test_random_data_is_not_jpeg(self):
        rng = random.Random(1)
        for i in range(50):
            self.assertNotEqual(classify_cluster(i, rng.randbytes(CLUSTER)).cls, "jpeg_data")

    def test_directory_remnant_names_recovered(self):
        img, _, truth = run("B")
        s = scan(EvidenceImage(str(img)))
        names = {e["name"] for e in s.dir_entries}
        self.assertTrue({"Project_Report.pdf", "Invoice_2025-117.pdf"} <= names)
        self.assertTrue(all(e["deleted"] for e in s.dir_entries))


class ReconstructionTests(unittest.TestCase):
    def test_pdf_placement_matches_ground_truth(self):
        _, res, truth = run("B")
        ev = evaluate(res, truth)
        self.assertEqual(ev["cluster_placement_precision"], 100.0)
        rep = next(f for f in res["files"] if f["file_name"].startswith("Project_Report__"))
        self.assertEqual(len(rep["missing_ranges"]), 1)  # exactly the overwritten fragment

    def test_sqlite_pages_numbered_correctly(self):
        _, res, truth = run("D")
        ev = evaluate(res, truth)
        self.assertEqual(ev["cluster_placement_precision"], 100.0)
        self.assertEqual(ev["corruption_bytes_detected"], ev["corruption_bytes_injected"])

    def test_unrecoverable_dataset_is_reported_as_such(self):
        _, res, _ = run("E")
        self.assertTrue(all(f["recovery_status"] in ("UNRECOVERABLE", "FRAGMENT_ONLY") for f in res["files"]))

    def test_missing_ranges_are_zero_filled_never_synthesized(self):
        _, res, _ = run("USB")
        for f in res["files"]:
            data = (TMP / "out_USB" / "reconstructed" / f["output_file"]).read_bytes()
            for m in f["missing_ranges"]:
                self.assertEqual(data[m["start"]:m["end"]].strip(b"\x00"), b"", f["file_name"])

    def test_every_recovered_byte_traces_to_source(self):
        img, res, _ = run("USB")
        raw = img.read_bytes()
        for f in res["files"]:
            data = (TMP / "out_USB" / "reconstructed" / f["output_file"]).read_bytes()
            for s in f["segments"]:
                if s["kind"] != "missing" and s["source_offset"] is not None and not f["orphan"]:
                    self.assertEqual(data[s["start"]:s["end"]], raw[s["source_offset"]:s["source_offset"] + s["length"]])

    def test_evidence_not_modified(self):
        img, res, _ = run("A")
        self.assertTrue(res["evidence_unchanged"])
        self.assertEqual(hashlib.sha256(img.read_bytes()).hexdigest(), res["summary"]["image_sha256"])


class ValidatorTests(unittest.TestCase):
    def test_jpeg_intact(self):
        from app.engine.scanner import parse_jpeg_header
        d = make_jpeg(random.Random(2), "t", 320, 240, datetime(2025, 1, 1))
        v = validate("jpeg", d, header=parse_jpeg_header(d[:8192]))
        self.assertEqual(v["parser"], "pass")
        self.assertEqual(v["structural"], "pass")

    def test_docx_crc_detects_tampering(self):
        d = bytearray(make_docx(random.Random(3), "T", "x", datetime(2025, 1, 1), ["hello world"] * 200, None))
        z = zipfile.ZipFile(io.BytesIO(bytes(d)))
        info = z.getinfo("word/document.xml")
        pos = info.header_offset + 30 + len(info.filename) + 10
        d[pos] ^= 0xFF
        v = validate("docx", bytes(d))
        st = {m["name"]: m["status"] for m in v["stats"]["members"]}
        self.assertEqual(st["word/document.xml"], "corrupted")
        self.assertEqual(st["[Content_Types].xml"], "ok")

    def test_sqlite_valid(self):
        v = validate("sqlite", make_sqlite(random.Random(4), 50, 50))
        self.assertEqual(v["parser"], "pass")


class ScoringTests(unittest.TestCase):
    def test_integrity_is_sum_of_factors(self):
        _, res, _ = run("USB")
        for f in res["files"]:
            total = max(0.0, min(100.0, sum(x["points"] for x in f["integrity_factors"])))
            self.assertAlmostEqual(total, f["integrity_score"], delta=0.2)

    def test_scores_are_kept_separate(self):
        _, res, _ = run("USB")
        f = next(f for f in res["files"] if f["file_name"].startswith("Project_Proposal_v3__"))
        self.assertNotEqual(f["integrity_score"], f["reconstruction_percentage"])
        self.assertIn("relationship_components", f)

    def test_keywords_change_priority(self):
        _, res, _ = run("USB")
        files = json.loads(json.dumps(res["files"]))
        crit = json.loads(json.dumps(DEFAULT_CRITERIA))
        crit["keywords"] = ["zzz-no-match"]
        prioritize(files, crit)
        self.assertTrue(all(f["relevance_indicators"][0]["points"] == 0 for f in files))


class SegmentTests(unittest.TestCase):
    def test_fill_gaps_and_corruption(self):
        segs = fill_gaps([Segment(0, 10, "recovered", "F1", 100), Segment(20, 10, "recovered", "F2", 500)], 40)
        self.assertEqual([s.kind for s in segs], ["recovered", "missing", "recovered", "missing"])
        segs = split_corrupted(segs, [(22, 24, "bad")])
        self.assertEqual(next(s for s in segs if s.kind == "corrupted").source_offset, 502)


if __name__ == "__main__":
    unittest.main()
