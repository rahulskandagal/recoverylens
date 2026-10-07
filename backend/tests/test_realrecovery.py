"""Real Recovery Mode: Windows Recycle Bin ($I/$R) parsing, recovery-into-case, and restore-to-folder.

The real system Recycle Bin is not touched by these tests: a fake `$Recycle.Bin\\<SID>\\` tree is
built by hand in a temp directory, with $I records constructed byte-for-byte to the documented
format, so parsing correctness is verified deterministically without depending on what happens to be
in the machine's actual Recycle Bin. `scan_recycle_bin(drive, root=...)` accepts that temp directory
as an explicit override for exactly this purpose; production callers never pass `root`.

Run from backend/:  .venv\\Scripts\\python -m pytest -q tests/test_realrecovery.py
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import struct
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from app import db, pipeline, realrecovery  # noqa: E402
from app import recyclebin as rb  # noqa: E402
from app.engine.synth import _report_pages, make_jpeg, make_pdf  # noqa: E402
from build_fixtures import fat12_image  # noqa: E402

_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime(dt: datetime) -> int:
    return int((dt - _FILETIME_EPOCH).total_seconds() * 10_000_000)


def _index_v2(size: int, when: datetime, original_path: str) -> bytes:
    return struct.pack("<qqqi", 2, size, _filetime(when), len(original_path)) + original_path.encode("utf-16-le") + b"\x00\x00"


def _index_v1(size: int, when: datetime, original_path: str) -> bytes:
    return struct.pack("<qqq", 1, size, _filetime(when)) + original_path.encode("utf-16-le") + b"\x00\x00"


def _wait_case(cid: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        c = db.one("SELECT * FROM cases WHERE id=?", (cid,))
        if c and c["status"] in ("complete", "failed"):
            return c
        time.sleep(0.15)
    raise TimeoutError(f"case {cid} did not finish")


class RecycleBinParserTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rl_rbin_"))
        self.sid_dir = self.root / "$Recycle.Bin" / "S-1-5-21-TEST-1001"
        self.sid_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _plant(self, suffix: str, size: int, when: datetime, orig_path: str, version: int = 2,
              data: bytes | None = None, with_data: bool = True) -> None:
        idx = (_index_v2 if version == 2 else _index_v1)(size, when, orig_path)
        ext = "." + orig_path.rsplit(".", 1)[-1] if "." in orig_path else ""
        (self.sid_dir / f"$I{suffix}{ext}").write_bytes(idx)
        if with_data:
            (self.sid_dir / f"$R{suffix}{ext}").write_bytes(data if data is not None else b"\xff" * size)

    def _scan(self):
        return rb.scan_recycle_bin("Z:", root=self.root)

    def test_version2_index_parsed_correctly(self):
        when = datetime(2026, 9, 26, 10, 30, 0, tzinfo=timezone.utc)
        self._plant("3K2J8F", 1234, when, "C:\\Users\\me\\Documents\\report.pdf", version=2)
        items = self._scan()["_raw"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.original_name, "report.pdf")
        self.assertEqual(item.original_path, "C:\\Users\\me\\Documents\\report.pdf")
        self.assertEqual(item.size, 1234)
        self.assertTrue(item.has_data)
        self.assertEqual(item.deleted_at[:19], "2026-09-26T10:30:00")

    def test_version2_index_with_null_terminator_counted_strips_it(self):
        """Real Windows $I records count the trailing NUL terminator in nchars (unlike our other
        synthetic helper above, which doesn't). Bug found via live testing against a real Recycle
        Bin: left un-stripped, this NUL ends up embedded in original_name and crashes os.open()
        as soon as that name is used to build an evidence-store path."""
        when = datetime(2026, 9, 26, tzinfo=timezone.utc)
        path = "C:\\Users\\me\\RecoveryTest\\test.pdf"
        encoded = path.encode("utf-16-le") + b"\x00\x00"  # nchars includes the terminator, as Windows writes it
        idx = struct.pack("<qqqi", 2, 77, _filetime(when), len(path) + 1) + encoded
        (self.sid_dir / "$IREALWIN.pdf").write_bytes(idx)
        (self.sid_dir / "$RREALWIN.pdf").write_bytes(b"%PDF-1.4 real")
        item = self._scan()["_raw"][0]
        self.assertEqual(item.original_name, "test.pdf")
        self.assertNotIn("\x00", item.original_name)
        self.assertNotIn("\x00", item.original_path)

    def test_version1_index_parsed_correctly(self):
        when = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._plant("ABCDEF", 42, when, "D:\\notes.txt", version=1)
        item = self._scan()["_raw"][0]
        self.assertEqual(item.original_name, "notes.txt")
        self.assertEqual(item.size, 42)

    def test_purged_item_has_no_data(self):
        """Recycle Bin emptied: if $R is gone the item must be reported as NOT recoverable -- never guessed present."""
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._plant("GONE01", 999, when, "C:\\Users\\me\\Pictures\\photo.jpg", with_data=False)
        item = self._scan()["_raw"][0]
        self.assertFalse(item.has_data)
        self.assertIsNone(item.data_path)

    def test_read_item_bytes_matches_planted_data(self):
        payload = b"%PDF-1.4\nreal recoverable bytes\n"
        when = datetime(2026, 2, 2, tzinfo=timezone.utc)
        self._plant("REAL01", len(payload), when, "C:\\deleted\\keep.pdf", data=payload)
        item = self._scan()["_raw"][0]
        self.assertEqual(rb.read_item_bytes(item), payload)

    def test_read_item_bytes_raises_when_purged(self):
        when = datetime(2026, 3, 3, tzinfo=timezone.utc)
        self._plant("NONE01", 10, when, "C:\\deleted\\gone.bin", with_data=False)
        item = self._scan()["_raw"][0]
        with self.assertRaises(FileNotFoundError):
            rb.read_item_bytes(item)

    def test_multiple_sid_folders_all_scanned(self):
        (self.root / "$Recycle.Bin" / "S-1-5-21-TEST-2002").mkdir(parents=True)
        when = datetime(2026, 4, 4, tzinfo=timezone.utc)
        self._plant("ONE01", 5, when, "C:\\a.txt")
        idx2_dir = self.root / "$Recycle.Bin" / "S-1-5-21-TEST-2002"
        (idx2_dir / "$ITWO01.txt").write_bytes(_index_v2(6, when, "C:\\b.txt"))
        (idx2_dir / "$RTWO01.txt").write_bytes(b"\x00" * 6)
        items = self._scan()["_raw"]
        self.assertEqual({i.original_name for i in items}, {"a.txt", "b.txt"})

    def test_no_recycle_bin_folder_reports_honestly(self):
        empty = Path(tempfile.mkdtemp(prefix="rl_norbin_"))
        try:
            result = rb.scan_recycle_bin("Q:", root=empty)
            self.assertEqual(result["items"], [])
            self.assertTrue(result["errors"])
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class RecoverIntoCaseTests(unittest.TestCase):
    """Recovering from the Recycle Bin must go through the SAME real engine as an upload: no demo
    data, real validation, real hashes, and it must show up under Recovered Files unmodified."""

    @classmethod
    def setUpClass(cls):
        realrecovery.init()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rl_rbin_case_"))
        self.sid_dir = self.root / "$Recycle.Bin" / "S-1-5-21-TEST-3003"
        self.sid_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _scan_with_item(self, payload: bytes, name: str) -> dict:
        when = datetime(2026, 4, 4, tzinfo=timezone.utc)
        (self.sid_dir / f"$I{name}").write_bytes(_index_v2(len(payload), when, f"C:\\Users\\me\\{name}"))
        (self.sid_dir / f"$R{name}").write_bytes(payload)
        return realrecovery.start_scan("Y:", _root=self.root)

    def test_recovered_pdf_runs_through_real_engine_and_validates(self):
        pdf = make_pdf(random.Random(1), "Recovered", "me", datetime(2026, 1, 1), _report_pages(random.Random(1), "Recovered", 2, []))
        scan = self._scan_with_item(pdf, "keep.pdf")
        item = scan["items"][0]
        self.assertTrue(item["has_data"])
        cid = realrecovery.recover_item(scan["id"], item["item_id"], _root=self.root)
        case = _wait_case(cid)
        self.assertEqual(case["status"], "complete", case.get("error"))
        self.assertEqual(case["mode"], "recyclebin")
        self.assertIsNone(case["dataset"])  # never tagged with a demo dataset
        files = db.query("SELECT data FROM files WHERE case_id=?", (cid,))
        self.assertEqual(len(files), 1)
        f = json.loads(files[0]["data"])
        self.assertEqual(f["file_type"], "pdf")
        self.assertEqual(f["recovery_status"], "FULLY_RECOVERED")  # a real, intact, valid PDF must validate as such
        self.assertEqual(f["size_bytes"], len(pdf))  # scanned size is the real file's size, not a hard-coded number

    def test_purged_item_refuses_to_recover(self):
        when = datetime(2026, 5, 5, tzinfo=timezone.utc)
        (self.sid_dir / "$Igone.bin").write_bytes(_index_v2(10, when, "C:\\Users\\me\\gone.bin"))  # no $R planted: purged
        scan = realrecovery.start_scan("Y:", _root=self.root)
        item = scan["items"][0]
        self.assertFalse(item["has_data"])
        with self.assertRaises(ValueError):
            realrecovery.recover_item(scan["id"], item["item_id"], _root=self.root)


class RestoreFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        realrecovery.init()
        from fastapi.testclient import TestClient
        from app.main import app
        cls.ctx = TestClient(app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def setUp(self):
        cid = self.c.post("/api/cases", json={"dataset": "B"}).json()["id"]
        for _ in range(120):
            if self.c.get(f"/api/cases/{cid}").json()["status"] in ("complete", "failed"):
                break
            time.sleep(0.25)
        self.cid = cid
        self.fid = self.c.get(f"/api/cases/{cid}/files").json()[0]["file_id"]
        self.dest = Path(tempfile.mkdtemp(prefix="rl_restore_"))

    def tearDown(self):
        shutil.rmtree(self.dest, ignore_errors=True)

    def test_restore_requires_authorization(self):
        r = self.c.post(f"/api/cases/{self.cid}/files/{self.fid}/restore", json={"destination_path": str(self.dest), "authorized": False})
        self.assertEqual(r.status_code, 400)

    def test_restore_writes_byte_identical_copy_to_custom_folder(self):
        r = self.c.post(f"/api/cases/{self.cid}/files/{self.fid}/restore", json={"destination_path": str(self.dest), "authorized": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["byte_identical"])
        self.assertTrue(Path(body["destination"]).is_file())
        audit = " ".join(a["message"] for a in self.c.get(f"/api/cases/{self.cid}/audit").json())
        self.assertIn("restored to", audit)
        self.assertIn("BYTE-IDENTICAL", audit)

    def test_restore_never_writes_into_evidence_store(self):
        before = db.one("SELECT image_path FROM cases WHERE id=?", (self.cid,))["image_path"]
        before_hash = hashlib.sha256(Path(before).read_bytes()).hexdigest()
        self.c.post(f"/api/cases/{self.cid}/files/{self.fid}/restore", json={"destination_path": str(self.dest), "authorized": True})
        after_hash = hashlib.sha256(Path(before).read_bytes()).hexdigest()
        self.assertEqual(before_hash, after_hash)

    def test_restore_default_destination_is_recovered_folder(self):
        r = self.c.post(f"/api/cases/{self.cid}/files/{self.fid}/restore", json={"authorized": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("RecoveryLens_Recovered", r.json()["destination"])


class CarvePurgedItemTests(unittest.TestCase):
    """A Recycle Bin item whose $R data is already purged has nothing left to copy byte-for-byte;
    the only honest path left is searching an already-analysed disk image for a same-type,
    similar-size candidate -- never claiming a match the evidence doesn't support."""

    @classmethod
    def setUpClass(cls):
        realrecovery.init()

    def setUp(self):
        self._case_ids: list[str] = []

    def tearDown(self):
        for cid in self._case_ids:
            db.execute("DELETE FROM cases WHERE id=?", (cid,))
            db.execute("DELETE FROM files WHERE case_id=?", (cid,))
            db.execute("DELETE FROM audit WHERE case_id=?", (cid,))
            shutil.rmtree(pipeline.CASES / cid, ignore_errors=True)
            shutil.rmtree(Path("data/evidence") / cid, ignore_errors=True)

    def _disk_image_case(self, payload: bytes, name: str) -> str:
        img_bytes = fat12_image(payload, name)
        fd = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
        with fd:
            fd.write(img_bytes)
        cid = pipeline.create_case("Carve test image", "upload", None, Path(fd.name), "carve_test.img", None, move=True)
        self._case_ids.append(cid)
        case = _wait_case(cid)
        self.assertEqual(case["status"], "complete", case.get("error"))
        return cid

    def _single_file_case(self) -> str:
        pdf = make_pdf(random.Random(2), "Single", "me", datetime(2026, 1, 1), _report_pages(random.Random(2), "Single", 1, []))
        fd = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        with fd:
            fd.write(pdf)
        cid = pipeline.create_case("Carve single-file case", "upload", None, Path(fd.name), "single.pdf", None, move=True)
        self._case_ids.append(cid)
        case = _wait_case(cid)
        self.assertEqual(case["status"], "complete", case.get("error"))
        return cid

    def _demo_case(self) -> str:
        cid = "FAKE-DEMO-" + uuid.uuid4().hex[:8]
        self._case_ids.append(cid)
        db.execute("INSERT INTO cases(id,name,mode,dataset,status,stage,progress,created_at,image_name,image_path,"
                  "image_sha256,image_size,criteria) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (cid, "fake demo", "demo", "B", "complete", "done", 1.0, "2026-01-01T00:00:00", "x.img", "x.img", "abc", 0, "{}"))
        db.execute("INSERT INTO files(case_id, id, name, type, status, priority_score, data) VALUES (?,?,?,?,?,?,?)",
                  (cid, "R001", "x.pdf", "pdf", "FULLY_RECOVERED", 50.0,
                   json.dumps({"file_id": "R001", "file_type": "pdf", "size_bytes": 100, "recovery_status": "FULLY_RECOVERED",
                               "status_reason": "ok", "input_type": "disk_image", "file_name": "x.pdf"})))
        return cid

    def _plant_purged_item(self, original_name: str, size: int) -> tuple[str, str]:
        scan_id = "RBSCAN-TEST-" + uuid.uuid4().hex[:6]
        item_id = "$ITEST" + uuid.uuid4().hex[:6]
        db.execute("INSERT INTO recyclebin_scans(id, drive, created_at, item_count, recoverable_count, errors) VALUES (?,?,?,?,?,?)",
                  (scan_id, "Z:", "2026-01-01T00:00:00", 1, 0, "[]"))
        db.execute("INSERT INTO recyclebin_items VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (scan_id, item_id, "Z:", "S-1-5-21-TEST", original_name, f"Z:\\{original_name}",
                   "2026-01-01T00:00:00", size, 0, None))
        return scan_id, item_id

    def test_recovered_when_matching_type_and_close_size(self):
        jpg = make_jpeg(random.Random(3), "fixture", 320, 240, datetime(2025, 1, 1))
        cid = self._disk_image_case(jpg, "notes.jpg")
        scan_id, item_id = self._plant_purged_item("Meeting_Photo.jpg", len(jpg))
        result = realrecovery.carve_purged_item(scan_id, item_id, cid)
        self.assertEqual(result["status"], "RECOVERED", result["reason"])
        self.assertIsNotNone(result["file_id"])
        row = db.one("SELECT case_id FROM recyclebin_items WHERE scan_id=? AND item_id=?", (scan_id, item_id))
        self.assertEqual(row["case_id"], cid)

    def test_insufficient_evidence_when_type_not_present(self):
        jpg = make_jpeg(random.Random(4), "fixture", 320, 240, datetime(2025, 1, 1))
        cid = self._disk_image_case(jpg, "notes.jpg")
        scan_id, item_id = self._plant_purged_item("report.pdf", 5000)
        result = realrecovery.carve_purged_item(scan_id, item_id, cid)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(result["file_id"])
        row = db.one("SELECT case_id FROM recyclebin_items WHERE scan_id=? AND item_id=?", (scan_id, item_id))
        self.assertIsNone(row["case_id"])  # no case is linked when nothing was actually found

    def test_still_present_item_rejects_carve(self):
        jpg = make_jpeg(random.Random(5), "fixture", 320, 240, datetime(2025, 1, 1))
        cid = self._disk_image_case(jpg, "notes.jpg")
        scan_id, item_id = self._plant_purged_item("still_here.jpg", len(jpg))
        db.execute("UPDATE recyclebin_items SET has_data=1 WHERE scan_id=? AND item_id=?", (scan_id, item_id))
        with self.assertRaises(ValueError):
            realrecovery.carve_purged_item(scan_id, item_id, cid)

    def test_demo_case_rejected(self):
        """Real and simulated evidence must never mix: a demo case can never be used to 'find' a real file."""
        cid = self._demo_case()
        scan_id, item_id = self._plant_purged_item("x.pdf", 100)
        with self.assertRaises(ValueError):
            realrecovery.carve_purged_item(scan_id, item_id, cid)

    def test_single_file_case_rejected(self):
        """A single-file upload has nothing to carve -- it is one contiguous candidate, not a volume."""
        cid = self._single_file_case()
        scan_id, item_id = self._plant_purged_item("something.pdf", 100)
        with self.assertRaises(ValueError):
            realrecovery.carve_purged_item(scan_id, item_id, cid)

    def test_demo_case_excluded_from_carve_image_list(self):
        self._demo_case()
        images = realrecovery.list_carve_images()
        self.assertTrue(all(im["mode"] != "demo" for im in images))

    def test_disk_image_case_appears_in_carve_image_list(self):
        jpg = make_jpeg(random.Random(6), "fixture", 320, 240, datetime(2025, 1, 1))
        cid = self._disk_image_case(jpg, "notes.jpg")
        images = realrecovery.list_carve_images()
        self.assertIn(cid, {im["id"] for im in images})


if __name__ == "__main__":
    unittest.main()
