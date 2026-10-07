"""Recovery Benchmark Lab: controlled evaluation on SYNTHETIC damaged images with known ground truth.

Each scenario builds clean files (kept as GROUND TRUTH hashes + cluster maps), applies one damage
type, runs the unmodified recovery engine on the damaged image, and compares the result with the
ground truth. All numbers are measured, never estimated, and are labelled SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .. import db
from ..engine.analyze import analyze
from ..engine.common import CLUSTER
from ..engine.evaluate import evaluate
from ..engine.synth import FileSpec, _report_pages, _sentence, build_image, make_docx, make_jpeg, make_pdf

BENCH = db.DATA / "benchmarks"
POOL = ThreadPoolExecutor(max_workers=1)
LABEL = "SYNTHETIC TEST DATA"
STAGES = ["Dataset generated", "Damage applied", "Fragments detected", "Relationships calculated", "Reconstruction attempted",
          "Validation", "Ground truth comparison", "Metrics"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS benchmarks (
  id TEXT PRIMARY KEY, created TEXT, finished TEXT, status TEXT, stage TEXT, progress REAL, seed INTEGER, data TEXT, error TEXT
);
"""

SCENARIOS: dict[str, dict[str, str]] = {
    "deleted_ranges": {"title": "Deleted ranges", "damage": "one whole fragment of a PDF and of a JPEG overwritten"},
    "random_corruption": {"title": "Random corruption", "damage": "bit flips inside JPEG and DOCX fragments"},
    "fragmentation": {"title": "Fragmentation", "damage": "JPEG split into 6 and PDF into 5 scattered fragments (no loss)"},
    "missing_header": {"title": "Missing header", "damage": "first cluster (file header) of a JPEG overwritten"},
    "missing_footer": {"title": "Missing footer", "damage": "last cluster (trailer) of a PDF overwritten"},
    "reordered_fragments": {"title": "Reordered fragments", "damage": "fragments stored in reverse physical order"},
    "partial_truncation": {"title": "Partial truncation", "damage": "final 30% of a PDF's clusters overwritten"},
    "mixed_fragments": {"title": "Mixed fragments", "damage": "three JPEGs and two PDFs interleaved across the image"},
    "duplicate_fragments": {"title": "Duplicate fragments", "damage": "byte-identical copies of fragments elsewhere on the image"},
}


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _specs(key: str, rng: random.Random) -> tuple[list[FileSpec], int]:
    d = datetime(2025, 9, 1, 9, 0)
    jpg = lambda name, i: make_jpeg(rng, name, 640, 480, d, i)  # noqa: E731
    pdf = lambda title, pages=6: make_pdf(rng, title, "Benchmark", d, _report_pages(rng, title, pages, ["Benchmark"]))  # noqa: E731
    ncl = lambda b: (len(b) + CLUSTER - 1) // CLUSTER  # noqa: E731
    if key == "deleted_ranges":
        return [FileSpec("report.pdf", pdf("Report"), "pdf", 4, drop=[1]), FileSpec("photo.jpg", jpg("photo", 1), "jpeg", 4, drop=[2])], 700
    if key == "random_corruption":
        paras = [_sentence(rng, 16) for _ in range(160)]
        return [FileSpec("photo.jpg", jpg("photo", 2), "jpeg", 3, flips=[(0, 3), (2, 4)]),
                FileSpec("notes.docx", make_docx(rng, "Notes", "Benchmark", d, paras, None), "docx", 3, flips=[(1, 2)])], 700
    if key == "fragmentation":
        return [FileSpec("photo.jpg", jpg("photo", 3), "jpeg", 6), FileSpec("report.pdf", pdf("Report", 8), "pdf", 5)], 900
    if key == "missing_header":
        return [FileSpec("photo.jpg", jpg("photo", 4), "jpeg", 3, drop_clusters=[0]), FileSpec("other.jpg", jpg("other", 5), "jpeg", 2)], 700
    if key == "missing_footer":
        p = pdf("Report")
        return [FileSpec("report.pdf", p, "pdf", 3, drop_clusters=[ncl(p) - 1])], 600
    if key == "reordered_fragments":
        p, j = pdf("Report", 6), jpg("photo", 6)
        return [FileSpec("report.pdf", p, "pdf", 3, split_at=[ncl(p) // 3, 2 * ncl(p) // 3], reverse_layout=True),
                FileSpec("photo.jpg", j, "jpeg", 3, split_at=[ncl(j) // 3, 2 * ncl(j) // 3], reverse_layout=True)], 800
    if key == "partial_truncation":
        p = pdf("Report", 10)
        n = ncl(p)
        return [FileSpec("report.pdf", p, "pdf", 2, drop_clusters=list(range(int(n * 0.7), n)))], 700
    if key == "mixed_fragments":
        return [FileSpec(f"photo_{i}.jpg", jpg(f"photo_{i}", 7 + i), "jpeg", 4) for i in range(3)] + \
               [FileSpec(f"doc_{i}.pdf", pdf(f"Document {i}"), "pdf", 3) for i in range(2)], 1200
    if key == "duplicate_fragments":
        return [FileSpec("report.pdf", pdf("Report"), "pdf", 3, dup=[0, 2]), FileSpec("photo.jpg", jpg("photo", 11), "jpeg", 3, dup=[1])], 800
    raise KeyError(key)


# ------------------------------------------------------------------------------------------ metrics

def _metrics(result: dict[str, Any], truth: dict[str, Any], ev: dict[str, Any]) -> dict[str, Any]:
    owner: dict[int, tuple[str, int]] = {}
    for tf in truth["files"]:
        for lc, pc in enumerate(tf["cluster_map"]):
            if pc is not None:
                owner[pc] = (tf["name"], lc)
    for dup in truth.get("duplicates", []):
        for k in range(dup["clusters"]):
            if dup["copy_of_cluster"] + k in owner:
                owner[dup["at_cluster"] + k] = owner[dup["copy_of_cluster"] + k]
    cand = ev.get("candidate_truth", {})
    # fragment-linking (junction level): truth junctions = fragment boundaries between surviving clusters
    truth_j = set()  # (file, logical cluster before the boundary): a duplicate copy may legitimately satisfy it
    for tf in truth["files"]:
        m = tf["cluster_map"]
        for lc in range(len(m) - 1):
            if m[lc] is not None and m[lc + 1] is not None and m[lc + 1] != m[lc] + 1:
                truth_j.add((tf["name"], lc))
    eng_j, ok_j = 0, 0
    found = set()
    for f in result["files"]:
        if f["orphan"]:
            continue
        segs = [s for s in f["segments"] if s["kind"] != "missing" and s["source_offset"] is not None]
        for a, b in zip(segs, segs[1:]):
            if b["start"] != a["end"]:
                continue
            la, fb = (a["source_offset"] + a["length"] - 1) // CLUSTER, b["source_offset"] // CLUSTER
            if fb == la + 1 or fb == la:
                continue  # physically contiguous: not a join decision
            eng_j += 1
            oa, ob = owner.get(la), owner.get(fb)
            if oa and ob and oa[0] == ob[0] and ob[1] == oa[1] + 1:
                ok_j += 1
                found.add((oa[0], oa[1]))
    # false positives: non-file clusters placed into a non-orphan reconstruction; false negatives: surviving clusters missed
    placed: dict[int, bool] = {}
    for f in result["files"]:
        if f["orphan"]:
            continue
        for s in f["segments"]:
            if s["source_offset"] is None or s["kind"] == "missing":
                continue
            for k in range(s["source_offset"] // CLUSTER, -(-(s["source_offset"] + s["length"]) // CLUSTER)):
                placed[k] = True
    noise = [k for fr in result["fragments"] if fr["family"] != "zero"
             for k in range(fr["start_cluster"], fr["start_cluster"] + fr["clusters"]) if k not in owner]
    fp = sum(1 for k in noise if placed.get(k))
    surviving = sum(1 for tf in truth["files"] for x in tf["cluster_map"] if x is not None)
    total = sum(tf["clusters"] for tf in truth["files"])
    correct = sum(x["correctly_recovered_clusters"] for x in ev["files"])
    matched = [f for f in result["files"] if cand.get(f["file_id"]) and not f["orphan"]]
    tmap = {tf["name"]: tf for tf in truth["files"]}
    type_ok = [f["file_type"] == tmap[cand[f["file_id"]]]["type"] for f in matched]
    return {
        "byte_recovery_rate": round(100 * correct / total, 1) if total else None,
        "byte_recovery_rate_of_surviving": round(100 * correct / surviving, 1) if surviving else None,
        "linking_precision": round(100 * ok_j / eng_j, 1) if eng_j else None,
        "linking_recall": round(100 * len(found & truth_j) / len(truth_j), 1) if truth_j else None,
        "junctions": {"truth": len(truth_j), "engine": eng_j, "correct": ok_j},
        "classification_accuracy": round(100 * float(np.mean(type_ok)), 1) if type_ok else None,
        "reconstruction_accuracy": ev.get("cluster_placement_precision"),
        "false_positive_rate": round(100 * fp / len(noise), 2) if noise else None,
        "false_negative_rate": round(100 * (surviving - correct) / surviving, 1) if surviving else None,
        "noise_clusters": len(noise), "surviving_clusters": surviving, "total_clusters": total, "correct_clusters": correct,
    }


def _file_records(result: dict[str, Any], truth: dict[str, Any], ev: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    tmap = {tf["name"]: tf for tf in truth["files"]}
    recs = []
    for f in result["files"]:
        name = ev.get("candidate_truth", {}).get(f["file_id"])
        if not name or f["orphan"] or not (f.get("export") or {}).get("file"):
            continue
        p = out_dir / "reconstructed" / f["export"]["file"]
        if not p.is_file():
            continue
        data = p.read_bytes()
        t = tmap[name]
        exact = len(data) >= t["size"] and hashlib.sha256(data[:t["size"]]).hexdigest() == t["sha256"]
        recs.append({"file": name, "integrity": f["integrity_score"], "predicted_valid": f["export"]["class"] == "validated_file",
                     "exact": exact, "type_ok": f["file_type"] == t["type"], "status": f["recovery_status"]})
    return recs


# ------------------------------------------------------------------------------------------ runner

def _update(bid: str, **kw: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in kw)
    db.execute(f"UPDATE benchmarks SET {sets} WHERE id=?", tuple(kw.values()) + (bid,))


def start(scenarios: list[str] | None, seed: int | None) -> str:
    init()
    keys = [k for k in (scenarios or list(SCENARIOS)) if k in SCENARIOS]
    if not keys:
        raise ValueError("no known scenario selected")
    bid = datetime.now().strftime("B%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    seed = int(seed) if seed is not None else random.randrange(1, 10 ** 6)
    db.execute("INSERT INTO benchmarks(id, created, status, stage, progress, seed, data) VALUES (?,?,?,?,?,?,?)",
               (bid, _now(), "queued", "queued", 0.0, seed, json.dumps({"label": LABEL, "scenarios": [], "planned": keys, "log": []})))
    POOL.submit(_run, bid, keys, seed)
    return bid


def _run(bid: str, keys: list[str], seed: int) -> None:
    data: dict[str, Any] = {"label": LABEL, "seed": seed, "planned": keys, "scenarios": [], "log": [],
                            "note": "Synthetic images with simulated damage. Results measure the engine on SYNTHETIC TEST DATA only "
                                    "and are not real-world forensic findings."}
    root = BENCH / bid
    root.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        data["log"].append({"ts": _now(), "message": msg})
        _update(bid, data=json.dumps(data))

    try:
        _update(bid, status="running")
        for i, key in enumerate(keys):
            base = i / len(keys)
            span = 1 / len(keys)

            def stage(k: int, frac: float = 0.0) -> None:
                _update(bid, stage=f"{SCENARIOS[key]['title']}: {STAGES[k]}", progress=round(base + span * (k + frac) / len(STAGES), 3))

            rng = random.Random(seed * 100 + i)
            stage(0)
            specs, n_clusters = _specs(key, rng)
            log(f"[{key}] {STAGES[0]}: {len(specs)} clean ground-truth file(s): " + ", ".join(f"{s.name} ({len(s.data):,} B)" for s in specs))
            stage(1)
            img, tp = root / f"{key}.img", root / f"{key}.truth.json"
            truth = build_image(specs, n_clusters, seed * 100 + i, img, tp, f"BENCHMARK {key}: {SCENARIOS[key]['damage']}")
            log(f"[{key}] {STAGES[1]}: {SCENARIOS[key]['damage']}; image SHA-256 {truth['image_sha256'][:16]}…")
            out = root / key
            t0 = time.time()

            def progress(p: float, msg: str) -> None:
                stage(2 if p < 0.3 else 3 if p < 0.45 else 4 if p < 0.62 else 5, 0.5)

            res = analyze(str(img), out, lambda s, m: None, progress, None, "demo", f"benchmark_{key}.img")
            log(f"[{key}] {STAGES[2]}–{STAGES[5]}: {len(res['fragments'])} fragments, {len(res['files'])} candidate file(s) in {time.time() - t0:.1f}s")
            stage(6)
            ev = evaluate(res, truth)
            stage(7)
            m = _metrics(res, truth, ev)
            frecs = _file_records(res, truth, ev, out)
            if frecs:
                m["integrity_prediction_accuracy"] = round(100 * float(np.mean([r["predicted_valid"] == r["exact"] for r in frecs])), 1)
            else:
                m["integrity_prediction_accuracy"] = None
            data["scenarios"].append({
                "scenario": key, "title": SCENARIOS[key]["title"], "damage": SCENARIOS[key]["damage"], "image_sha256": truth["image_sha256"],
                "ground_truth": [{"name": t["name"], "type": t["type"], "size": t["size"], "sha256": t["sha256"],
                                  "surviving_pct": round(100 * sum(1 for x in t["cluster_map"] if x is not None) / t["clusters"], 1)} for t in truth["files"]],
                "recovered": [{"name": f["file_name"], "type": f["file_type"], "status": f["recovery_status"], "integrity": f["integrity_score"],
                               "reconstruction": f["reconstruction_percentage"], "matched_truth": ev.get("candidate_truth", {}).get(f["file_id"])}
                              for f in res["files"]],
                "metrics": m, "edge_records": ev.get("edge_records", []), "file_records": frecs,
                "evaluation": {k: ev[k] for k in ("cluster_placement_precision", "cluster_recall", "edge_accuracy", "edges_evaluated",
                                                  "corruption_bytes_injected", "corruption_bytes_detected")},
            })
            log(f"[{key}] {STAGES[6]} + {STAGES[7]}: byte recovery {m['byte_recovery_rate']}%, linking P/R {m['linking_precision']}/{m['linking_recall']}")
        data["aggregate"] = _aggregate(data["scenarios"])
        _update(bid, status="complete", stage="complete", progress=1.0, finished=_now(), data=json.dumps(data))
    except Exception as e:  # surfaced, never hidden
        data["log"].append({"ts": _now(), "message": f"BENCHMARK FAILED: {type(e).__name__}: {e}"})
        _update(bid, status="failed", error=traceback.format_exc()[-2000:], data=json.dumps(data))


def _aggregate(sc: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ["byte_recovery_rate", "linking_precision", "linking_recall", "classification_accuracy", "reconstruction_accuracy",
            "integrity_prediction_accuracy", "false_positive_rate", "false_negative_rate"]
    out = {}
    for k in keys:
        vals = [s["metrics"][k] for s in sc if s["metrics"].get(k) is not None]
        out[k] = {"mean": round(float(np.mean(vals)), 1) if vals else None, "n": len(vals)}
    return out


def get(bid: str) -> dict[str, Any] | None:
    init()
    r = db.one("SELECT * FROM benchmarks WHERE id=?", (bid,))
    if not r:
        return None
    r["data"] = json.loads(r["data"]) if r.get("data") else {}
    for s in r["data"].get("scenarios", []):  # large per-sample records are for calibration only
        s["edge_records"] = len(s.get("edge_records", []))
        s["file_records"] = s.get("file_records", [])
    return r


def list_runs() -> list[dict[str, Any]]:
    init()
    rows = db.query("SELECT id, created, finished, status, stage, progress, seed, data FROM benchmarks ORDER BY created DESC LIMIT 30")
    out = []
    for r in rows:
        d = json.loads(r.pop("data") or "{}")
        r["scenarios"] = len(d.get("scenarios", []))
        r["aggregate"] = d.get("aggregate")
        out.append(r)
    return out
