"""Single-file mode, PDF validator checks, repair, input-type detection, status/priority.

Run from backend/:  .venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from build_fixtures import build  # noqa: E402

from app.engine.analyze import analyze  # noqa: E402
from app.engine.intake import detect  # noqa: E402
from app.engine.pdfcheck import WEIGHTS, check_pdf, pdf_status, repair_pdf  # noqa: E402
from app.engine.priority import DEFAULT_CRITERIA, prioritize  # noqa: E402

FX = build()
TMP = Path(tempfile.mkdtemp(prefix="rl_sf_"))


def pdf(name: str):
    data = FX[name].read_bytes()
    r = check_pdf(data)
    return data, r, {c["id"]: c for c in r.checks}


def run(name: str) -> tuple[dict, list[tuple[str, str]]]:
    log: list[tuple[str, str]] = []
    res = analyze(str(FX[name]), TMP / name, lambda s, m: log.append((s, m)), lambda p, m: None, source_label=name)
    return res, log


class PdfCheckTests(unittest.TestCase):
    def test_valid_pdf_passes_every_check(self):
        _, r, c = pdf("valid.pdf")
        self.assertEqual({k: v["status"] for k, v in c.items()}, {k: "pass" for k in WEIGHTS})
        self.assertEqual(r.integrity, 100.0)
        self.assertEqual(r.stats["render_clean"], 2)

    def test_header_check_fails_without_signature(self):
        _, r, _ = pdf("valid.pdf")
        r2 = check_pdf(b"garbage" + FX["valid.pdf"].read_bytes()[20:], render=False)
        self.assertEqual(next(x for x in r2.checks if x["id"] == "header")["status"], "fail")

    def test_truncated_trailer(self):
        _, _, c = pdf("truncated_trailer.pdf")
        self.assertEqual(c["eof"]["status"], "fail")
        self.assertEqual(c["startxref"]["status"], "fail")
        self.assertIn("located by scanning", c["page_tree"]["evidence"])

    def test_corrupted_xref_offsets_counted(self):
        _, _, c = pdf("corrupted_xref.pdf")
        self.assertEqual(c["xref_offsets"]["status"], "partial")
        self.assertIn("5/8", c["xref_offsets"]["evidence"])
        self.assertIn("[4, 5, 6]", c["xref_offsets"]["evidence"])
        self.assertEqual(c["references"]["status"], "pass")  # objects still exist; only the index is wrong

    def test_corrupted_stream_detected(self):
        _, _, c = pdf("corrupted_stream.pdf")
        self.assertEqual(c["streams"]["status"], "partial")
        self.assertIn("FlateDecode error", c["streams"]["evidence"])

    def test_real_sample_pinpoints_overwritten_region(self):
        _, r, c = pdf("corrupted_demo_report.pdf")
        self.assertIn((579, 1379, "overwritten region: 800 bytes of non-PDF data"), r.damaged_ranges)
        self.assertIn("4/7", c["xref_offsets"]["evidence"])  # salvaged xref lines
        self.assertIn("3/7 indirect references", c["references"]["evidence"])
        self.assertEqual(c["page_tree"]["status"], "fail")

    def test_integrity_is_documented_weighted_mean(self):
        _, r, _ = pdf("corrupted_stream.pdf")
        num = sum(x["weight"] * x["score"] for x in r.checks if x["status"] != "na")
        den = sum(x["weight"] for x in r.checks if x["status"] != "na")
        self.assertAlmostEqual(r.integrity, round(100 * num / den, 1))


class StatusTests(unittest.TestCase):
    def test_status_classification(self):
        expect = {"valid.pdf": "FULLY_RECOVERED", "corrupted_xref.pdf": "MOSTLY_RECOVERED",
                  "corrupted_stream.pdf": "PARTIALLY_RECOVERED", "truncated_trailer.pdf": "PARTIALLY_RECOVERED",
                  "corrupted_demo_report.pdf": "FRAGMENT_ONLY"}
        for name, st in expect.items():
            _, r, _ = pdf(name)
            got, why = pdf_status(r, complete_bytes=True)
            self.assertEqual(got, st, name)
            self.assertTrue(why and "checks pass" in why, name)

    def test_incomplete_bytes_never_fully(self):
        _, r, _ = pdf("valid.pdf")
        self.assertNotEqual(pdf_status(r, complete_bytes=False)[0], "FULLY_RECOVERED")


class RepairTests(unittest.TestCase):
    def test_xref_rebuild_restores_valid_pdf(self):
        for name in ("corrupted_xref.pdf", "truncated_trailer.pdf"):
            data, r, _ = pdf(name)
            out, meta = repair_pdf(data, r)
            r2 = check_pdf(out)
            self.assertEqual(pdf_status(r2, True)[0], "FULLY_RECOVERED", name)
            self.assertEqual(meta["synthesized_objects"], [], name)

    def test_lost_catalog_is_synthesized_and_declared(self):
        data, r, _ = pdf("corrupted_demo_report.pdf")
        out, meta = repair_pdf(data, r)
        self.assertEqual(meta["objects_dropped"], [5])
        self.assertEqual(len(meta["synthesized_objects"]), 2)
        for num in meta["objects_kept"]:  # object bodies are byte-identical copies
            o = r.objects[num]
            self.assertIn(data[o.start:o.end], out)


class IntakeTests(unittest.TestCase):
    def test_detection(self):
        self.assertEqual(detect(FX["valid.pdf"].read_bytes(), "valid.pdf")["label"], "UPLOADED FILE – PDF")
        self.assertEqual(detect(FX["photo.jpg"].read_bytes(), "photo.jpg")["format"], "jpeg")
        d = detect(FX["fat12_disk.img"].read_bytes(), "fat12_disk.img")
        self.assertEqual((d["kind"], d["label"]), ("disk_image", "DISK IMAGE"))
        self.assertIn("FAT12", d["filesystem"])
        self.assertEqual(detect(b"\x00" * 8192 + b"\x13" * 100)["kind"], "raw_image")

    def test_plain_text_with_no_signature_is_a_single_file_not_dropped(self):
        """Regression: plain text/log/csv/etc. have no magic-byte signature, so before this fix they
        fell through to 'raw_image' carving, which found zero fragments and produced zero recovered
        candidates -- a genuinely recovered real file silently vanished with no error and no entry
        anywhere in the UI. Found via live testing a real Recycle Bin recovery of a .txt file."""
        body = b"This is a real, valid test document created for RecoveryLens verification.\r\nLine two.\r\n"
        d = detect(body, "note.txt")
        self.assertEqual(d["kind"], "single_file")
        self.assertEqual(d["format"], "txt")
        d2 = detect(body, "app.log")
        self.assertEqual(d2["format"], "log")

    def test_empty_file_still_falls_back_to_raw_image(self):
        self.assertEqual(detect(b"")["kind"], "raw_image")

    def test_high_entropy_data_is_not_misclassified_as_text(self):
        import random
        rng = random.Random(1)
        noise = bytes(rng.randrange(256) for _ in range(4096))
        self.assertEqual(detect(noise)["kind"], "raw_image")


class SingleFilePipelineTests(unittest.TestCase):
    def test_corrupted_demo_report_acceptance(self):
        res, log = run("corrupted_demo_report.pdf")
        f, s = res["files"][0], res["summary"]
        self.assertEqual(s["input_type"]["label"], "UPLOADED FILE – PDF")
        self.assertEqual(f["file_name"], "corrupted_demo_report__rec0000.pdf")
        self.assertEqual(f["recovery_status"], "FRAGMENT_ONLY")
        self.assertIn("0 renderable pages", f["status_reason"])
        self.assertEqual(f["reconstruction_percentage"], 100.0)
        self.assertTrue(f["recon_confidence_reason"].startswith("Contiguous"))
        self.assertIsNone(s["overall_recovery_coverage"])
        self.assertTrue(s["coverage_reason"].startswith("N/A – no file-system metadata"))
        self.assertEqual(f["links_metric"]["kind"], "internal_references")
        self.assertEqual(len(f["validator_checks"]), 9)
        self.assertTrue(res["evidence_unchanged"])
        # repaired copy recorded with both hashes; carved copy untouched
        rc = f["repaired_copy"]
        self.assertEqual(rc["carved_sha256"], hashlib.sha256(FX["corrupted_demo_report.pdf"].read_bytes()).hexdigest())
        self.assertTrue(any(stage == "repair" and rc["sha256"] in m and rc["carved_sha256"] in m for stage, m in log))
        self.assertTrue(any(stage == "validate" and "SHA-256" in m for stage, m in log))

    def test_intact_pdf_is_fully_recovered(self):
        res, _ = run("valid.pdf")
        f = res["files"][0]
        self.assertEqual(f["recovery_status"], "FULLY_RECOVERED")
        self.assertEqual(f["integrity_score"], 100.0)
        self.assertIsNone(f["repaired_copy"])
        self.assertEqual(f["file_name"], "valid__rec0000.pdf")
        self.assertFalse([x for x in res["summary"]["findings"] if x["level"] == "warning"])

    def test_single_jpeg(self):
        res, _ = run("photo.jpg")
        f = res["files"][0]
        self.assertEqual(res["summary"]["input_type"]["kind"], "single_file")
        self.assertEqual(f["recovery_status"], "FULLY_RECOVERED")
        self.assertEqual(f["links_metric"]["kind"], "not_applicable")

    def test_disk_image_carves_deleted_file(self):
        res, _ = run("fat12_disk.img")
        s = res["summary"]
        self.assertEqual(s["input_type"]["kind"], "disk_image")
        f = next(f for f in res["files"] if f["file_type"] == "jpeg" and not f["orphan"])
        self.assertEqual(f["file_name"], "deleted_notes__rec0000.jpg")
        self.assertEqual(f["recovery_status"], "FULLY_RECOVERED")

    def test_zero_counts_are_not_warnings(self):
        for name in ("valid.pdf", "fat12_disk.img"):
            res, _ = run(name)
            for x in res["summary"]["findings"]:
                self.assertFalse(x["level"] == "warning" and x["text"].startswith("0 "), x)

    def test_diskmap_uses_sectors_for_small_inputs(self):
        res, _ = run("corrupted_demo_report.pdf")
        d = res["diskmap"]
        self.assertEqual((d["unit"], d["clusters"]), (512, 4))
        self.assertEqual(d["damaged"], [0, 1, 1, 0])


class PriorityTests(unittest.TestCase):
    def test_unrecoverable_is_demoted_and_keywords_count(self):
        res, _ = run("corrupted_demo_report.pdf")
        files = json.loads(json.dumps(res["files"]))
        files[0]["recovery_status"] = "UNRECOVERABLE"
        crit = json.loads(json.dumps(DEFAULT_CRITERIA))
        prioritize(files, crit)
        self.assertIn(files[0]["priority_level"], ("INFORMATIONAL", "HIGH", "CRITICAL"))
        crit["keywords"] = ["corrupted_demo"]
        prioritize(files, crit)
        self.assertGreater(files[0]["relevance_indicators"][0]["points"], 0)


if __name__ == "__main__":
    unittest.main()
