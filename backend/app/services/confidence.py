"""Confidence Calibration Engine.

Per artifact, six measures are kept separate and each carries the evidence that produced it:
detection, relationship, reconstruction, classification, integrity, recovery possibility.

Calibration compares predicted confidence with OBSERVED ground-truth outcomes. Labels exist only for
synthetic images with ground truth (demo datasets and Benchmark Lab runs). Identical images are
counted once. When too few labelled predictions exist, calibration is reported as unavailable;
statistics are never manufactured.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .. import db
from ..engine.evaluate import evaluate
from .context import CaseCtx

MIN_SAMPLES = 30
MIN_BIN = 5
DISCLAIMER = ("Confidence is the system's evidence-based assessment. It is not proof. Calibration shows how often similar "
              "confidence values were confirmed on SYNTHETIC TEST DATA with known ground truth.")
SIGNED_TYPES = {"jpeg", "png", "gif", "pdf", "zip", "docx", "xlsx", "pptx", "sqlite", "mp4"}
FAMILY_OF = {"jpeg": {"jpeg"}, "pdf": {"pdf"}, "sqlite": {"sqlite"}, "docx": {"compressed", "xml"}, "xlsx": {"compressed", "xml"},
             "zip": {"compressed"}, "log": {"log", "text"}, "txt": {"text", "log"}}


# ------------------------------------------------------------------------------------------ per-artifact profile

def profile(ctx: CaseCtx, f: dict[str, Any], possibility: dict[str, Any] | None = None) -> dict[str, Any]:
    fams = FAMILY_OF.get(f["file_type"], set())
    frs = [ctx.fragments.get(x) for x in f.get("fragments", [])]
    frs = [x for x in frs if x]
    match = sum(1 for x in frs if x["family"] in fams)
    share = match / len(frs) if frs else 0
    signed = f["file_type"] in SIGNED_TYPES
    sig = signed and bool(f.get("header_found")) and not f.get("orphan")
    det_ev = [("file signature found at the reconstruction's first byte" if sig else
               "no file signature: type inferred from content statistics" if signed else
               f"{f['file_type'].upper()} has no magic signature; detection rests on content statistics only"),
              f"{match}/{len(frs)} member fragment(s) have a content family consistent with {f['file_type'].upper()}"]
    det = round(60 * sig + 40 * share, 1) if signed else round(100 * share * 0.7, 1)
    rc = f.get("relationship_components") or {}
    rel_ev = [x for x in [rc.get("basis"), rc.get("summary")] if x] or [f"{sum(1 for e in f.get('fragment_relationships', []) if e.get('chosen'))} accepted fragment link(s)"]
    ints = sorted(f.get("integrity_factors", []), key=lambda x: -(abs(x.get("points") or 0)))[:4]
    poss = possibility or {}
    return {"file_id": f["file_id"], "file_name": f["file_name"], "disclaimer": DISCLAIMER, "measures": [
        {"key": "detection", "name": "Detection confidence", "value": det, "unit": "%",
         "meaning": "how sure we are that these bytes are this kind of file data", "evidence": det_ev,
         "basis": ("60 × signature present + 40 × share of fragments with a matching content family" if signed else
                   "70 × share of fragments with a matching content family (capped: no signature can confirm the type)")},
        {"key": "relationship", "name": "Relationship confidence", "value": f.get("relationship_confidence"), "unit": "%",
         "meaning": "how strongly evidence supports that the fragments belong together", "evidence": rel_ev,
         "basis": "calibrated edge model over structural/continuity features (see relationship components)"},
        {"key": "reconstruction", "name": "Reconstruction confidence", "value": f.get("reconstruction_percentage"), "unit": "%",
         "meaning": "share of the expected structure that was located", "evidence": [f.get("recon_confidence_reason") or f.get("reconstruction_basis") or "—"],
         "basis": "bytes/rows located ÷ bytes/rows the file's own structure declares"},
        {"key": "classification", "name": "Classification confidence", "value": f.get("content_confidence"), "unit": "%",
         "meaning": "how sure we are about the technical type", "evidence": list(f.get("content_confidence_factors") or [])[:4],
         "basis": "itemised content-classification factors"},
        {"key": "integrity", "name": "Integrity score", "value": f.get("integrity_score"), "unit": "/100",
         "meaning": "structural validity of the reconstructed bytes", "evidence": [f"{x.get('factor')}: {x.get('points')}/{x.get('max')} – {str(x.get('detail'))[:120]}" for x in ints],
         "basis": "sum of itemised validator factors"},
        {"key": "possibility", "name": "Recovery possibility", "value": poss.get("level"), "unit": "level",
         "meaning": "whether further recovery is realistically possible", "evidence": (poss.get("plus") or []) + [f"− {m}" for m in poss.get("minus") or []],
         "basis": "explicit evidence rules (HIGH/MEDIUM/LOW/UNKNOWN), not a score"},
    ]}


# ------------------------------------------------------------------------------------------ labelled samples

def _truth_path(dataset: str) -> Path | None:
    from ..engine.synth import SEEDS
    from ..pipeline import DEMO
    if dataset not in SEEDS:
        return None
    p = DEMO / f"demo_{dataset}_{SEEDS[dataset]}.truth.json"
    return p if p.exists() else None


def _file_records(files: list[dict[str, Any]], cand_truth: dict[str, str | None], truth: dict[str, Any], recon_bytes) -> list[dict[str, Any]]:
    tf = {t["name"]: t for t in truth["files"]}
    out = []
    for f in files:
        name = cand_truth.get(f["file_id"])
        if not name or f.get("orphan") or name not in tf:
            continue
        data = recon_bytes(f)
        if data is None:
            continue
        exact = hashlib.sha256(data[:tf[name]["size"]]).hexdigest() == tf[name]["sha256"] and len(data) >= tf[name]["size"]
        out.append({"integrity": f["integrity_score"], "predicted_valid": (f.get("export") or {}).get("class") == "validated_file",
                    "exact": exact, "type_ok": f["file_type"] == tf[name]["type"] or (f["file_type"], tf[name]["type"]) in (("zip", "docx"), ("docx", "zip")),
                    "file": name})
    return out


def collect_samples() -> dict[str, Any]:
    edges: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in db.query("SELECT id, dataset, image_sha256 FROM cases WHERE status='complete' AND mode='demo' AND dataset IS NOT NULL ORDER BY created_at DESC"):
        # one (latest) case per demo dataset: re-running the same dataset must not inflate the sample
        if c["image_sha256"] in seen or f"dataset:{c['dataset']}" in seen:
            continue
        seen.add(f"dataset:{c['dataset']}")
        tp = _truth_path(c["dataset"])
        if not tp:
            continue
        seen.add(c["image_sha256"])
        ctx = CaseCtx.load(c["id"])
        truth = json.loads(tp.read_text())
        ev = evaluate({"files": ctx.files}, truth)
        edges += ev.get("edge_records", [])
        files += _file_records(ctx.files, ev.get("candidate_truth", {}), truth, ctx.reconstruction_bytes)
        sources.append({"kind": "demo case", "id": c["id"], "dataset": c["dataset"], "edges": len(ev.get("edge_records", []))})
    for r in db.query("SELECT id, data FROM benchmarks WHERE status='complete' ORDER BY created DESC") if _has_bench() else []:
        d = json.loads(r["data"])
        for sc in d.get("scenarios", []):
            if sc.get("image_sha256") in seen:
                continue
            seen.add(sc.get("image_sha256"))
            edges += sc.get("edge_records", [])
            files += sc.get("file_records", [])
            sources.append({"kind": "benchmark", "id": r["id"], "dataset": sc["scenario"], "edges": len(sc.get("edge_records", []))})
    return {"edges": edges, "files": files, "sources": sources}


def _has_bench() -> bool:
    return bool(db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='benchmarks'"))


# ------------------------------------------------------------------------------------------ metrics (NumPy; scikit-learn optional)

def _binary_metrics(p: np.ndarray, y: np.ndarray, pred: np.ndarray) -> dict[str, Any]:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    brier = float(np.mean((p - y) ** 2))
    bins = []
    for lo in range(0, 100, 10):
        m = (p * 100 >= lo) & ((p * 100 < lo + 10) if lo < 90 else (p * 100 <= 100))
        n = int(m.sum())
        bins.append({"range": f"{lo}–{lo + 10}%", "lo": lo, "n": n, "mean_predicted": round(float(p[m].mean()) * 100, 1) if n else None,
                     "observed_rate": round(float(y[m].mean()) * 100, 1) if n else None, "low_sample": 0 < n < MIN_BIN})
    ece = float(sum(b["n"] * abs((b["mean_predicted"] or 0) - (b["observed_rate"] or 0)) for b in bins if b["n"]) / max(1, len(p)) / 100)
    return {"n": int(len(p)), "positives": int(y.sum()), "brier": round(brier, 4), "ece": round(ece, 4),
            "precision": None if prec is None else round(prec, 3), "recall": None if rec is None else round(rec, 3),
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "bins": bins}


def calibration() -> dict[str, Any]:
    s = collect_samples()
    out: dict[str, Any] = {"disclaimer": DISCLAIMER, "sources": s["sources"], "min_samples": MIN_SAMPLES,
                           "label": "SYNTHETIC TEST DATA", "engine": "NumPy (Brier score, reliability bins, confusion matrix)"}
    E = s["edges"]
    if len(E) >= MIN_SAMPLES:
        p = np.array([e["confidence"] / 100 for e in E])
        y = np.array([1 if e["correct"] else 0 for e in E])
        pred = np.array([1 if e["chosen"] else 0 for e in E])
        out["relationship"] = {"available": True, "title": "Relationship confidence vs. confirmed fragment links",
                               "prediction": "link accepted by the engine", **_binary_metrics(p, y, pred)}
    else:
        out["relationship"] = {"available": False, "n": len(E),
                               "status": "CONFIDENCE CALIBRATION UNAVAILABLE — INSUFFICIENT VALIDATION DATA",
                               "reason": f"{len(E)} labelled link prediction(s); at least {MIN_SAMPLES} are required. "
                                         "Run demo datasets or the Benchmark Lab to add ground-truth-labelled samples."}
    F = s["files"]
    if len(F) >= MIN_SAMPLES:
        p = np.array([x["integrity"] / 100 for x in F])
        y = np.array([1 if x["exact"] else 0 for x in F])
        pred = np.array([1 if x["predicted_valid"] else 0 for x in F])
        out["integrity"] = {"available": True, "title": "Integrity score vs. byte-exact reconstruction",
                            "prediction": "export classed VALIDATED RECOVERED FILE", **_binary_metrics(p, y, pred),
                            "classification_accuracy": round(float(np.mean([x["type_ok"] for x in F])), 3)}
    else:
        out["integrity"] = {"available": False, "n": len(F),
                            "status": "CONFIDENCE CALIBRATION UNAVAILABLE — INSUFFICIENT VALIDATION DATA",
                            "reason": f"{len(F)} labelled artifact(s); at least {MIN_SAMPLES} are required. "
                                      "Run the Benchmark Lab to add more ground-truth comparisons."}
    return out
