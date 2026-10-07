"""Security scanning, export decisions and byte identity.

Run from backend/:  .venv\\Scripts\\python -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

from build_fixtures import build  # noqa: E402

from app.engine import security as S  # noqa: E402
from app.engine.analyze import analyze  # noqa: E402
from app.engine.synth import build_dataset, make_suspicious_binary, make_test_malware_artifact  # noqa: E402

FX = build()
TMP = Path(tempfile.mkdtemp(prefix="rl_sec_"))


class _Boom(S.MalwareScanner):
    def scan_bytes(self, data):
        raise RuntimeError("engine crashed")

    def get_scan_engine(self):
        return "Boom"

    def get_signature_version(self):
        return "0"


def run(src: Path, mode: str) -> dict:
    return analyze(str(src), TMP / f"{src.name}_{mode}", lambda s, m: None, lambda p, m: None, mode=mode, source_label=src.name)


class ScannerTests(unittest.TestCase):
    def test_no_engine_is_not_scanned_never_clean(self):
        r = S.scan_artifact(b"hello world", "txt", "upload", scanner=None, use_engine=False)
        self.assertEqual(r["status"], "NOT_SCANNED")
        self.assertFalse(r["executed"])

    def test_demo_clean_carries_caveat_and_simulated_flag(self):
        r = S.scan_artifact(FX["valid.pdf"].read_bytes(), "pdf", "demo", scanner=S.DemoScanner())
        self.assertEqual(r["status"], "CLEAN")
        self.assertTrue(r["simulated"])
        self.assertIn("does not constitute proof", r["explanation"])

    def test_test_signature(self):
        data = make_test_malware_artifact()
        demo = S.scan_artifact(data, "txt", "demo", scanner=S.DemoScanner())
        self.assertEqual(demo["status"], "MALWARE_DETECTED")
        self.assertTrue(demo["is_test"])
        self.assertIn("NOT been executed", demo["explanation"])
        # without an antivirus engine a rule match is only an indicator
        upload = S.scan_artifact(data, "txt", "upload", scanner=None, use_engine=False)
        self.assertEqual(upload["status"], "SUSPICIOUS")
        self.assertIn("not a confirmed infection", upload["explanation"])

    def test_embedded_pe_is_suspicious_random_is_not(self):
        r = S.scan_artifact(make_suspicious_binary(random.Random(1)), "bin", "demo", scanner=S.DemoScanner())
        rules = {m["rule"] for m in r["yara_matches"]}
        self.assertEqual(r["status"], "SUSPICIOUS")
        self.assertIn("RL_Embedded_PE_Executable", rules)
        self.assertTrue(r["static"]["embedded_executables"])
        rnd = S.scan_artifact(random.Random(2).randbytes(8192), "bin", "demo", scanner=S.DemoScanner())
        self.assertEqual(rnd["status"], "CLEAN")

    def test_engine_failure_is_scan_failed_not_clean(self):
        r = S.scan_artifact(b"plain", "txt", "upload", scanner=_Boom())
        self.assertEqual(r["status"], "SCAN_FAILED")
        self.assertIn("not treated as clean", r["explanation"])

    def test_clamav_reply_parsing(self):
        self.assertEqual(S.ClamAVScanner._parse("stream: Eicar-Test-Signature FOUND", "ClamAV 1.3").status, "MALWARE_DETECTED")
        self.assertEqual(S.ClamAVScanner._parse("stream: OK", "ClamAV 1.3").status, "CLEAN")
        self.assertEqual(S.ClamAVScanner._parse("stream: lstat() failed ERROR", None).status, "SCAN_FAILED")

    def test_repository_contains_no_contiguous_eicar_string(self):
        needle = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
        for p in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app").rglob("*.pyc")) + list((ROOT / "app").rglob("*.yar")):
            self.assertNotIn(needle, p.read_bytes(), str(p))


class ExportTests(unittest.TestCase):
    def test_validated_export_is_byte_identical(self):
        res = run(FX["valid.pdf"], "upload")
        e = res["files"][0]["export"]
        self.assertEqual(e["class"], "validated_file")
        self.assertTrue(e["byte_identical"])
        self.assertEqual(e["reconstruction_sha256"], hashlib.sha256(FX["valid.pdf"].read_bytes()).hexdigest())

    def test_invalid_pdf_is_forensic_artifact_not_recovered_file(self):
        res = run(FX["corrupted_demo_report.pdf"], "upload")
        e = res["files"][0]["export"]
        self.assertEqual(e["class"], "forensic_artifact")
        self.assertIn("FORENSIC-ARTIFACT", e["file"])
        self.assertIn("should not be treated as a verified reconstructed file", e["warning"])
        self.assertTrue(e["byte_identical"])

    def test_missing_ranges_are_declared_placeholders(self):
        img, _ = build_dataset("B", TMP / "demo")
        res = run(img, "demo")
        f = next(x for x in res["files"] if x["file_name"].startswith("Project_Report__"))
        e = f["export"]
        self.assertEqual(e["class"], "forensic_artifact")
        self.assertEqual(len(e["placeholders"]), len(f["missing_ranges"]))
        self.assertLess(e["observed_bytes"], f["size_bytes"])

    def test_security_demo_datasets(self):
        expect = {"SEC1": {"CLEAN"}, "SEC2": {"SUSPICIOUS"}, "SEC3": {"MALWARE_DETECTED", "CLEAN"}}
        for key, statuses in expect.items():
            img, _ = build_dataset(key, TMP / "demo")
            res = run(img, "demo")
            got = {f["security"]["status"] for f in res["files"]}
            self.assertEqual(got, statuses, key)


class ApiExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        cls.ctx = TestClient(app)
        cls.c = cls.ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def _case(self, dataset: str) -> str:
        cid = self.c.post("/api/cases", json={"dataset": dataset}).json()["id"]
        for _ in range(120):
            if self.c.get(f"/api/cases/{cid}").json()["status"] in ("complete", "failed"):
                break
            time.sleep(0.25)
        return cid

    def test_download_bytes_match_reconstruction_and_audit(self):
        cid = self._case("SEC1")
        f = self.c.get(f"/api/cases/{cid}/files").json()[0]
        r = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}/download")
        self.assertEqual(r.status_code, 200)
        detail = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}").json()
        self.assertEqual(hashlib.sha256(r.content).hexdigest(), detail["export"]["reconstruction_sha256"])
        self.assertEqual(r.headers["x-recoverylens-byte-identical"], "true")
        v = self.c.post(f"/api/cases/{cid}/files/{f['file_id']}/export-verification",
                        json={"variant": "export", "sha256": hashlib.sha256(r.content).hexdigest(), "size": len(r.content)}).json()
        self.assertTrue(v["byte_identical"])
        audit = " ".join(a["message"] for a in self.c.get(f"/api/cases/{cid}/audit").json())
        self.assertIn("BYTE-IDENTICAL", audit)
        self.assertIn("client re-hash", audit)

    def test_malware_export_requires_authorization(self):
        cid = self._case("SEC3")
        mal = next(f for f in self.c.get(f"/api/cases/{cid}/files").json() if f["security"]["status"] == "MALWARE_DETECTED")
        url = f"/api/cases/{cid}/files/{mal['file_id']}/download"
        self.assertEqual(self.c.get(url).status_code, 403)
        self.assertEqual(self.c.get(url + "?inline=true").status_code, 403)
        ok = self.c.get(url + "?authorized=true")
        self.assertEqual(ok.status_code, 200)
        self.assertIn(".QUARANTINED", ok.headers["content-disposition"])
        self.assertEqual(ok.headers["content-type"], "application/octet-stream")

    def test_segments_zip_contains_only_observed_bytes(self):
        cid = self._case("B")
        f = next(x for x in self.c.get(f"/api/cases/{cid}/files").json() if x["file_name"].startswith("Project_Report__"))
        r = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}/download?variant=segments")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        man = json.loads(z.read("manifest.json"))
        detail = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}").json()
        self.assertEqual(sum(s["length"] for s in man["segments"]), detail["export"]["observed_bytes"])
        self.assertTrue(man["missing_ranges"])

    def test_report_has_security_and_export_sections(self):
        cid = self._case("SEC2")
        rep = self.c.get(f"/api/cases/{cid}/report").json()
        self.assertEqual(rep["report_metadata"]["schema_version"], "1.3")  # 1.3 = 1.2 + advanced_analysis
        self.assertIn("recovery_possibility", rep["advanced_analysis"])
        self.assertIn("security_guardian", rep["advanced_analysis"])
        for f in rep["recovered_files"]:
            self.assertIn(f["security_analysis"]["status"], ("CLEAN", "SUSPICIOUS", "MALWARE_DETECTED", "SCAN_FAILED", "NOT_SCANNED"))
            self.assertFalse(f["security_analysis"]["executed"])
            self.assertIn("byte_identical", f["export_validation"])


if __name__ == "__main__":
    unittest.main()
