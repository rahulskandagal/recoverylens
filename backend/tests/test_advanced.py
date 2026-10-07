"""Advanced services: Fragment DNA, Structure Explorer, Digital Twin, Recovery Possibility, Counterfactual
Simulator, Cross-Artifact Intelligence, Safety Guardian, Confidence Calibration, Benchmark Lab, AI Investigator.

Run from backend/:  .venv\\Scripts\\python -m unittest discover -s tests -v   (pytest also collects these)
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import security as S  # noqa: E402
from app.services import benchmark  # noqa: E402

STATUSES = {"HIGH", "MEDIUM", "LOW", "UNKNOWN", "N/A"}


def _sha_file(p: str) -> str:
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class AdvancedApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        cls.ctx = TestClient(app)
        cls.c = cls.ctx.__enter__()
        cls.cases = {k: cls._case(k) for k in ("B", "SEC3", "USB")}

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    @classmethod
    def _case(cls, dataset: str) -> str:
        cid = cls.c.post("/api/cases", json={"dataset": dataset}).json()["id"]
        for _ in range(240):
            if cls.c.get(f"/api/cases/{cid}").json()["status"] in ("complete", "failed"):
                break
            time.sleep(0.25)
        assert cls.c.get(f"/api/cases/{cid}").json()["status"] == "complete"
        return cid

    def _files(self, cid):
        return self.c.get(f"/api/cases/{cid}/files").json()

    def _gap_file(self, cid):
        for f in self._files(cid):
            if f["missing_ranges"] and not f["orphan"]:
                return f
        self.fail("no file with missing ranges")

    def _evidence(self, cid):
        from app import db
        return db.one("SELECT image_path, image_sha256 FROM cases WHERE id=?", (cid,))

    # ---------------------------------------------------------------- Fragment DNA
    def test_fragment_dna_card_and_similarity_caveat(self):
        cid = self.cases["B"]
        page = self.c.get(f"/api/cases/{cid}/dna", params={"family": "pdf"}).json()
        self.assertGreater(page["total"], 0)
        card = page["items"][0]
        for k in ("sha256", "entropy", "byte_histogram", "signature", "mime", "header", "footer", "structure", "compression",
                  "encoding", "alignment", "parser_compatibility", "metadata_indicators", "anomalies", "potential_relationships"):
            self.assertIn(k, card)
        self.assertEqual(len(card["byte_histogram"]), 32)
        d = self.c.get(f"/api/cases/{cid}/dna/{card['id']}").json()
        self.assertIn("does NOT establish", d["similar"]["caveat"])
        for it in d["similar"]["items"]:
            self.assertIn(it["label"], ("STRONG SIMILARITY", "POSSIBLE SIMILARITY", "WEAK SIMILARITY", "NO SIGNIFICANT SIMILARITY"))
            self.assertTrue(it["evidence"])
        m = self.c.get(f"/api/cases/{cid}/dna/map").json()
        self.assertTrue(m["points"])

    # ---------------------------------------------------------------- Structure Explorer
    def test_structure_explorer_pdf_and_unsupported(self):
        cid = self.cases["B"]
        pdf = next(f for f in self._files(cid) if f["file_type"] == "pdf" and not f["orphan"])
        s = self.c.get(f"/api/cases/{cid}/files/{pdf['file_id']}/structure").json()
        self.assertTrue(s["available"])
        names = [n["name"] for n in s["tree"]]
        self.assertTrue(any(n.startswith("Header %PDF") for n in names))
        self.assertTrue(any("Trailer" in n for n in names) or any("Cross-reference" in n for n in names))
        sec = self._files(self.cases["SEC3"])
        txt = next(f for f in sec if f["file_type"] == "txt")
        u = self.c.get(f"/api/cases/{self.cases['SEC3']}/files/{txt['file_id']}/structure").json()
        self.assertFalse(u["available"])
        self.assertEqual(u["status"], "STRUCTURE PARSER UNAVAILABLE")

    # ---------------------------------------------------------------- Digital Twin
    def test_digital_twin_has_provenance_chain_and_gap_nodes(self):
        cid = self.cases["B"]
        f = self._gap_file(cid)
        t = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}/twin").json()
        kinds = {n["kind"] for n in t["nodes"]}
        self.assertTrue({"image", "fragment", "missing", "operation", "validation", "security", "artifact"} <= kinds)
        self.assertTrue(all(d["text"] for d in t["decisions"]))
        self.assertTrue(any("placed after" in d["text"] for d in t["decisions"]))

    # ---------------------------------------------------------------- Recovery Possibility
    def test_possibility_levels_have_evidence(self):
        cid = self.cases["B"]
        p = self.c.get(f"/api/cases/{cid}/possibility").json()
        for it in p["files"]:
            self.assertIn(it["level"], STATUSES)
            for r in it["regions"]:
                self.assertIn(r["level"], STATUSES - {"N/A"})
                self.assertTrue(r["reason"])
        gap = next(it for it in p["files"] if any(r["kind"] == "missing" for r in it["regions"]))
        self.assertTrue(gap["plus"] or gap["minus"])
        self.assertIn(p["heatmap"]["codes"]["candidate"], (2,))

    # ---------------------------------------------------------------- Counterfactual Simulator
    def test_simulation_never_touches_evidence_or_export(self):
        cid = self.cases["USB"]
        ev = self._evidence(cid)
        before_ev = _sha_file(ev["image_path"])
        pos = self.c.get(f"/api/cases/{cid}/possibility").json()
        target = next(((it, r, c) for it in pos["files"] for r in it["regions"] if r["kind"] == "missing" for c in r["candidates"]), None)
        self.assertIsNotNone(target)
        it, r, cand = target
        detail = self.c.get(f"/api/cases/{cid}/files/{it['file_id']}").json()
        export_path = Path(ev["image_path"]).parents[2] / "cases" / cid / "reconstructed" / detail["export"]["file"]
        before_exp = _sha_file(str(export_path))
        res = self.c.post(f"/api/cases/{cid}/files/{it['file_id']}/simulate", json={"fragment_id": cand["id"], "range_index": r["index"]}).json()
        self.assertEqual(res["label"], "SIMULATION — NOT EVIDENCE")
        self.assertFalse(res["evidence_modified"])
        self.assertFalse(res["persisted"])
        self.assertIn(res["verdict"], ("CANDIDATE REJECTED BY SIMULATION", "CANDIDATE IMPROVES RECONSTRUCTION (SIMULATED)",
                                       "NO VALIDATOR CHANGE — FILL UNCONFIRMED"))
        self.assertEqual(_sha_file(ev["image_path"]), before_ev)
        self.assertEqual(before_ev, ev["image_sha256"])
        self.assertEqual(_sha_file(str(export_path)), before_exp)
        after = self.c.get(f"/api/cases/{cid}/files/{it['file_id']}").json()
        self.assertEqual(after["integrity_score"], detail["integrity_score"])  # nothing persisted
        bad = self.c.post(f"/api/cases/{cid}/files/{it['file_id']}/simulate", json={"fragment_id": cand["id"], "range_index": 99})
        self.assertEqual(bad.status_code, 400)
        audit = " ".join(a["message"] for a in self.c.get(f"/api/cases/{cid}/audit").json())
        self.assertIn("SIMULATION result (not evidence)", audit)

    def test_simulator_positive_and_negative_controls(self):
        """Blank one segment of a complete PDF (in memory only); the true fragment must be confirmed by the
        validators and a wrong fragment must not be."""
        import copy
        from app.services import simulator
        from app.services.context import CaseCtx
        ctx = CaseCtx.load(self.cases["USB"])
        f = next(x for x in ctx.files if x["file_type"] == "pdf" and x["recovery_status"] == "FULLY_RECOVERED"
                 and len([s for s in x["segments"] if s["fragment_id"]]) >= 3)
        target = [s for s in f["segments"] if s["fragment_id"]][1]
        g = copy.deepcopy(f)
        for s in g["segments"]:
            if s["start"] == target["start"]:
                s.update(kind="missing", fragment_id=None, source_offset=None, source_offset_hex=None)
        data = bytearray(ctx.reconstruction_bytes(f))
        data[target["start"]:target["end"]] = bytes(target["length"])
        ctx.files = [g if x["file_id"] == f["file_id"] else x for x in ctx.files]
        ctx.reconstruction_bytes = lambda _f: bytes(data)  # in-memory baseline; nothing on disk is touched
        good = simulator.simulate(ctx, f["file_id"], target["fragment_id"], 0,
                                  source_offset=target["source_offset"] - ctx.fragments[target["fragment_id"]]["offset"])
        self.assertEqual(good["verdict"], "CANDIDATE IMPROVES RECONSTRUCTION (SIMULATED)", good["verdict_reason"])
        self.assertGreater(good["delta"]["integrity_from_validators"], 0)
        wrong = next(fr["id"] for fr in ctx.fragments.values() if fr["family"] == "text" and not fr.get("assigned_to"))
        bad = simulator.simulate(ctx, f["file_id"], wrong, 0)
        self.assertNotEqual(bad["verdict"], "CANDIDATE IMPROVES RECONSTRUCTION (SIMULATED)", bad["verdict_reason"])

    # ---------------------------------------------------------------- Cross-Artifact Intelligence
    def test_cross_artifact_relationships_are_evidence_backed(self):
        x = self.c.get(f"/api/cases/{self.cases['USB']}/cross-artifact").json()
        self.assertTrue(x["relationships"])
        for r in x["relationships"]:
            for k in ("type", "evidence", "confidence", "source", "target"):
                self.assertIn(k, r)
            self.assertTrue(r["evidence"])
            self.assertEqual(r["data_class"], "INFERRED RELATIONSHIP")

    # ---------------------------------------------------------------- Safety Guardian
    def test_guardian_wording_and_isolation(self):
        cid = self.cases["SEC3"]
        g = self.c.get(f"/api/cases/{cid}/guardian").json()
        by = {i["raw_status"]: i for i in g["items"]}
        mal = by["MALWARE_DETECTED"]
        self.assertEqual(mal["status"], "MALWARE DETECTED")
        self.assertTrue(mal["detections"])
        self.assertFalse(mal["executed"])
        self.assertTrue(mal["isolation"]["read_only"])
        iso = Path(self._evidence(cid)["image_path"]).parents[2] / "cases" / cid / mal["isolation"]["path"]
        self.assertTrue(iso.is_file())
        self.assertFalse(os.access(iso, os.W_OK))
        clean = by.get("CLEAN")
        if clean:
            self.assertEqual(clean["headline"], "NO MALWARE DETECTED BY AVAILABLE SCANNERS")
            self.assertIn("does not guarantee", clean["absence_note"])
            self.assertNotIn("safe", clean["headline"].lower())

    def test_polyglot_is_suspicious(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w") as z:
            z.writestr("payload.txt", "harmless test member")
        r = S.scan_artifact(jpeg + mem.getvalue(), "jpeg", "upload", use_engine=False)
        self.assertEqual(r["status"], "SUSPICIOUS")
        self.assertTrue(any("polyglot" in i["text"] for i in r["static"]["indicators"]))

    # ---------------------------------------------------------------- Confidence
    def test_confidence_profile_separate_measures_and_calibration_honesty(self):
        cid = self.cases["B"]
        f = self._files(cid)[0]
        p = self.c.get(f"/api/cases/{cid}/files/{f['file_id']}/confidence").json()
        keys = [m["key"] for m in p["measures"]]
        self.assertEqual(keys, ["detection", "relationship", "reconstruction", "classification", "integrity", "possibility"])
        self.assertIn("not proof", p["disclaimer"])
        cal = self.c.get("/api/calibration").json()
        for part in ("relationship", "integrity"):
            c = cal[part]
            if c["available"]:
                self.assertGreaterEqual(c["n"], cal["min_samples"])
                self.assertIsNotNone(c["brier"])
            else:
                self.assertEqual(c["status"], "CONFIDENCE CALIBRATION UNAVAILABLE — INSUFFICIENT VALIDATION DATA")

    # ---------------------------------------------------------------- AI Investigator
    def test_investigator_requires_approval_and_logs(self):
        cid = self.cases["USB"]
        ev = self._evidence(cid)
        r = self.c.post(f"/api/cases/{cid}/investigator/plan", json={"focus": ""}).json()
        recs = [x for x in r["recommendations"] if x["status"] == "pending"]
        self.assertTrue(recs)
        for x in recs:
            for k in ("action", "reason", "evidence", "expected_benefit", "risk", "confidence"):
                self.assertTrue(x[k])
        self.assertIn(r["llm"]["status"], ("LLM CONFIGURED", "LLM NOT CONFIGURED"))
        a, b = recs[0], recs[-1]
        res = self.c.post(f"/api/cases/{cid}/investigator/{a['id']}/decision", json={"decision": "approve"}).json()
        self.assertIn(res["status"], ("executed", "failed"))
        again = self.c.post(f"/api/cases/{cid}/investigator/{a['id']}/decision", json={"decision": "approve"})
        self.assertEqual(again.status_code, 409)
        if b["id"] != a["id"]:
            rj = self.c.post(f"/api/cases/{cid}/investigator/{b['id']}/decision", json={"decision": "reject"}).json()
            self.assertEqual(rj["status"], "rejected")
        self.assertEqual(_sha_file(ev["image_path"]), ev["image_sha256"])
        audit = " ".join(x["message"] for x in self.c.get(f"/api/cases/{cid}/audit").json())
        self.assertIn("AI recommendation generated", audit)
        self.assertIn("User APPROVED", audit)
        prov = self.c.get(f"/api/cases/{cid}/files/{self._files(cid)[0]['file_id']}/provenance").json()
        self.assertEqual(prov["question"], "Where did this recovered artifact come from?")

    def test_pdf_report_renders_from_same_records(self):
        import pymupdf
        cid = self.cases["B"]
        r = self.c.get(f"/api/cases/{cid}/report.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        doc = pymupdf.open(stream=r.content, filetype="pdf")
        text = "".join(p.get_text() for p in doc)
        self.assertIn("Forensic Recovery Report", text)
        self.assertIn(self._evidence(cid)["image_sha256"], text)  # evidence hash printed verbatim
        self.assertIn("DEMO / SYNTHETIC DATA", text)
        audit = " ".join(a["message"] for a in self.c.get(f"/api/cases/{cid}/audit").json())
        self.assertIn("Forensic PDF report generated", audit)

    def test_report_contains_advanced_analysis(self):
        rep = self.c.get(f"/api/cases/{self.cases['B']}/report").json()
        adv = rep["advanced_analysis"]
        for k in ("recovery_possibility", "cross_artifact_relationships", "security_guardian", "confidence_profiles", "ai_investigator"):
            self.assertIn(k, adv)


class BenchmarkTests(unittest.TestCase):
    def test_fragmentation_scenario_metrics_are_measured(self):
        from app import db
        benchmark.init()
        bid = f"Btest-{int(time.time() * 1000)}"
        db.execute("INSERT INTO benchmarks(id, created, status, stage, progress, seed, data) VALUES (?,?,?,?,?,?,?)",
                   (bid, "test", "queued", "queued", 0, 3, "{}"))
        benchmark._run(bid, ["fragmentation", "deleted_ranges"], 3)
        r = benchmark.get(bid)
        self.assertEqual(r["status"], "complete", r.get("error"))
        self.assertEqual(r["data"]["label"], "SYNTHETIC TEST DATA")
        frag = next(s for s in r["data"]["scenarios"] if s["scenario"] == "fragmentation")
        self.assertEqual(frag["metrics"]["false_negative_rate"], 0.0)
        self.assertGreaterEqual(frag["metrics"]["byte_recovery_rate"], 99.0)
        dele = next(s for s in r["data"]["scenarios"] if s["scenario"] == "deleted_ranges")
        self.assertLess(dele["metrics"]["byte_recovery_rate"], 100.0)  # overwritten data is never "recovered"
        for s in r["data"]["scenarios"]:
            for g in s["ground_truth"]:
                self.assertEqual(len(g["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
