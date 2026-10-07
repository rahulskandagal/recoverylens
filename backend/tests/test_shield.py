"""Recovery Shield: baselines, snapshots, change detection, incidents, restore.

Run from backend/:  .venv\\Scripts\\python -m pytest -q tests/test_shield.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, shield  # noqa: E402


def _wait_snapshot(snap_id: str, timeout: float = 20.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = shield.get_snapshot(snap_id)
        if s["status"] in ("protected", "failed"):
            return s
        time.sleep(0.1)
    raise TimeoutError(f"snapshot {snap_id} did not finish: {shield.get_snapshot(snap_id)}")


class ShieldTests(unittest.TestCase):
    def setUp(self):
        shield.init()
        self.tmp = Path(tempfile.mkdtemp(prefix="rl_shield_src_"))
        (self.tmp / "docs").mkdir()
        (self.tmp / "College_Project.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 500)
        (self.tmp / "Resume.pdf").write_bytes(b"%PDF-1.4\n" + b"y" * 300)
        (self.tmp / "docs" / "notes.txt").write_text("hello world\n" * 20)
        self.src = shield.register_source("Test laptop", str(self.tmp))
        self.sid = self.src["id"]

    def tearDown(self):
        shield.stop_monitor(self.sid)
        try:
            shield.delete_source(self.sid)
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_rejects_bad_path(self):
        with self.assertRaises(ValueError):
            shield.register_source("bad", str(self.tmp / "does_not_exist"))

    def test_snapshot_is_a_real_byte_copy(self):
        snap_id = shield.create_snapshot(self.sid)
        snap = _wait_snapshot(snap_id)
        self.assertEqual(snap["status"], "protected")
        self.assertEqual(snap["file_count"], 3)
        v = shield.verify_snapshot(snap_id)
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["mismatches"], [])
        stored = shield.SNAPSHOTS / snap_id / "College_Project.pdf"
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_bytes(), (self.tmp / "College_Project.pdf").read_bytes())

    def test_scan_before_baseline_is_refused(self):
        with self.assertRaises(ValueError):
            shield.scan_and_diff(self.sid)

    def test_deletion_detected_and_recoverable_from_snapshot(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        (self.tmp / "Resume.pdf").unlink()
        diff = shield.scan_and_diff(self.sid)
        self.assertEqual([d["rel_path"] for d in diff["deleted"]], ["Resume.pdf"])
        self.assertTrue(diff["deleted"][0]["recoverable"])
        self.assertIsNotNone(diff["incident_id"])
        inc = shield.get_incident(diff["incident_id"])
        self.assertIn("unauthorized deletion", inc["summary"])
        self.assertEqual(len(inc["details"]["deleted"]), 1)

    def test_incident_not_repeated_for_the_same_still_deleted_file(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        (self.tmp / "Resume.pdf").unlink()
        d1 = shield.scan_and_diff(self.sid)
        self.assertIsNotNone(d1["incident_id"])
        d2 = shield.scan_and_diff(self.sid)  # nothing new happened
        self.assertIsNone(d2["incident_id"])
        self.assertEqual([d["rel_path"] for d in d2["deleted"]], ["Resume.pdf"])  # still reported as recoverable
        self.assertEqual(len(shield.list_incidents(self.sid)), 1)

    def test_mass_deletion_is_high_severity(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        for i in range(6):
            p = self.tmp / f"extra_{i}.txt"
            p.write_text("x")
        snap2 = shield.create_snapshot(self.sid)
        _wait_snapshot(snap2)
        for i in range(6):
            (self.tmp / f"extra_{i}.txt").unlink()
        diff = shield.scan_and_diff(self.sid)
        self.assertEqual(len(diff["deleted"]), 6)
        inc = shield.get_incident(diff["incident_id"])
        self.assertEqual(inc["severity"], "HIGH")
        self.assertIn("mass deletion", inc["summary"])

    def test_modification_and_corruption_are_distinguished(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        (self.tmp / "docs" / "notes.txt").write_text("changed content\n")  # plain text: "modified", not "corrupted"
        pdf = self.tmp / "College_Project.pdf"
        data = bytearray(pdf.read_bytes())
        data[20:60] = b"\x00" * 40  # break the PDF's internal structure -> a real validator must reject it
        pdf.write_bytes(bytes(data))
        diff = shield.scan_and_diff(self.sid)
        self.assertIn("docs/notes.txt", [m["rel_path"] for m in diff["modified"]])
        self.assertIn("College_Project.pdf", [c["rel_path"] for c in diff["corrupted"]])
        self.assertTrue(diff["corrupted"][0]["reason"])

    def test_rename_detected_as_rename_not_delete_plus_new(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        (self.tmp / "Resume.pdf").rename(self.tmp / "Resume_2025.pdf")
        diff = shield.scan_and_diff(self.sid)
        self.assertEqual(diff["deleted"], [])
        self.assertEqual(diff["new_files"], [])
        self.assertEqual(diff["renamed"], [{"from": "Resume.pdf", "to": "Resume_2025.pdf"}])

    def test_restore_requires_explicit_authorization(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        with self.assertRaises(ValueError):
            shield.restore(self.sid, snap_id, ["Resume.pdf"], "recovery", None, authorized=False)

    def test_restore_to_recovery_directory_is_byte_identical_and_never_touches_original(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        original_pdf_bytes = (self.tmp / "College_Project.pdf").read_bytes()
        (self.tmp / "Resume.pdf").unlink()
        r = shield.restore(self.sid, snap_id, ["Resume.pdf"], "recovery", None, authorized=True)
        self.assertEqual(len(r["restored"]), 1)
        self.assertTrue(r["restored"][0]["byte_identical"])
        dest = Path(r["restored"][0]["destination"])
        self.assertTrue(dest.is_file())
        self.assertFalse((self.tmp / "Resume.pdf").exists())  # original location untouched by a 'recovery' restore
        self.assertEqual((self.tmp / "College_Project.pdf").read_bytes(), original_pdf_bytes)  # other files untouched

    def test_restore_to_original_location_recreates_the_file(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        original = (self.tmp / "Resume.pdf").read_bytes()
        (self.tmp / "Resume.pdf").unlink()
        shield.restore(self.sid, snap_id, ["Resume.pdf"], "original", None, authorized=True)
        self.assertTrue((self.tmp / "Resume.pdf").is_file())
        self.assertEqual((self.tmp / "Resume.pdf").read_bytes(), original)

    def test_monitor_start_stop(self):
        r = shield.start_monitor(self.sid)
        self.assertTrue(r["monitoring"])
        self.assertEqual(shield.get_source(self.sid)["protection"], "active")
        r2 = shield.stop_monitor(self.sid)
        self.assertFalse(r2["monitoring"])
        self.assertEqual(shield.get_source(self.sid)["protection"], "paused")

    def test_audit_trail_records_key_operations(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        (self.tmp / "Resume.pdf").unlink()
        shield.scan_and_diff(self.sid)
        log = " ".join(a["message"] for a in shield.get_audit(self.sid))
        self.assertIn("registered", log)
        self.assertIn("Protected recovery point", log)
        self.assertIn("Scan complete", log)

    def test_overview_aggregates_across_sources(self):
        snap_id = shield.create_snapshot(self.sid)
        _wait_snapshot(snap_id)
        ov = shield.overview()
        self.assertGreaterEqual(ov["sources"], 1)
        self.assertGreaterEqual(ov["protected_files"], 3)
        self.assertEqual(ov["unrecoverable"], 0)


if __name__ == "__main__":
    unittest.main()
