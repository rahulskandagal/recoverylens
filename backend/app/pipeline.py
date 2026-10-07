"""Case lifecycle: evidence intake (read-only copy + hash), background analysis, persistence."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .engine.analyze import analyze
from .engine.evaluate import evaluate
from .engine.explain import explain
from .engine.priority import DEFAULT_CRITERIA, prioritize
from .engine.synth import DATASETS, SEEDS, build_dataset

EVIDENCE = db.DATA / "evidence"
CASES = db.DATA / "cases"
DEMO = db.DATA / "demo"
POOL = ThreadPoolExecutor(max_workers=2)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(case_id: str, stage: str, msg: str) -> None:
    db.execute("INSERT INTO audit(case_id, ts, stage, message) VALUES (?,?,?,?)", (case_id, now(), stage, msg))


def ensure_demo() -> None:
    for k in DATASETS:
        img = DEMO / f"demo_{k}_{SEEDS[k]}.img"
        if not img.exists():
            build_dataset(k, DEMO)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_read_only(p: Path) -> None:
    os.chmod(p, stat.S_IREAD)


def create_case(name: str, mode: str, dataset: str | None, src: Path, image_name: str,
                criteria: dict[str, Any] | None = None, move: bool = False) -> str:
    merged = json.loads(json.dumps(DEFAULT_CRITERIA))
    merged.update({k: v for k, v in (criteria or {}).items() if k in merged and v is not None})
    criteria = merged
    cid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    ev_dir = EVIDENCE / cid
    ev_dir.mkdir(parents=True, exist_ok=True)
    dst = ev_dir / image_name
    if move:
        shutil.move(src, dst)
    else:
        shutil.copyfile(src, dst)
    _make_read_only(dst)
    digest = _sha(dst)
    db.execute("INSERT INTO cases(id,name,mode,dataset,status,stage,progress,created_at,image_name,image_path,image_sha256,image_size,criteria)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (cid, name, mode, dataset, "queued", "queued", 0.0, now(), image_name, str(dst), digest, dst.stat().st_size,
                json.dumps(criteria or DEFAULT_CRITERIA)))
    audit(cid, "ingest", f"Evidence '{image_name}' copied to write-protected store; SHA-256 {digest}")
    if mode == "demo":
        audit(cid, "ingest", "DEMO MODE: synthetic image with simulated damage; results are not forensic findings")
    POOL.submit(run_case, cid)
    return cid


def run_case(cid: str) -> None:
    case = db.one("SELECT * FROM cases WHERE id=?", (cid,))
    if not case:
        return
    db.execute("UPDATE cases SET status='running', stage='starting' WHERE id=?", (cid,))

    def progress(p: float, msg: str) -> None:
        db.execute("UPDATE cases SET progress=?, stage=? WHERE id=?", (round(p, 3), msg, cid))

    try:
        res = analyze(case["image_path"], CASES / cid, lambda s, m: audit(cid, s, m), progress,
                      json.loads(case["criteria"]), case["mode"], case["image_name"])
        progress(0.92, "Persisting results")
        db.many("INSERT OR REPLACE INTO fragments VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(cid, f["id"], f["offset"], f["length"], f["family"], f["entropy"], f["assigned_to"],
                  1 if f["header"] else 0, f["duplicate_of"], json.dumps(f)) for f in res["fragments"]])
        save_files(cid, res["files"])
        db.put_blob(cid, "graph", res["graph"])
        db.put_blob(cid, "diskmap", res["diskmap"])
        db.put_blob(cid, "cross_edges", res["cross_edges"])
        ev = None
        if case["mode"] == "demo" and case["dataset"]:
            truth_p = DEMO / f"demo_{case['dataset']}_{SEEDS[case['dataset']]}.truth.json"
            if truth_p.exists():
                ev = evaluate(res, json.loads(truth_p.read_text()))
                audit(cid, "evaluate", f"Post-hoc ground-truth evaluation: placement precision {ev['cluster_placement_precision']}%, "
                                       f"recall {ev['cluster_recall']}%, link accuracy {ev['edge_accuracy']}%")
        progress(0.95, "Advanced analysis (Fragment DNA, possibility, cross-artifact)")
        _advanced_stages(cid, case)
        audit(cid, "report", "Recovery report generated")
        db.execute("UPDATE cases SET status='complete', stage='complete', progress=1.0, finished_at=?, summary=?, evaluation=?, model=?,"
                   " evidence_unchanged=? WHERE id=?",
                   (now(), json.dumps(res["summary"]), json.dumps(ev) if ev else None, json.dumps(res["model"]),
                    1 if res["evidence_unchanged"] else 0, cid))
    except Exception as e:  # surface failures instead of hiding them
        audit(cid, "error", f"Analysis failed: {type(e).__name__}: {e}")
        db.execute("UPDATE cases SET status='failed', error=? WHERE id=?", (traceback.format_exc()[-2000:], cid))


def _advanced_stages(cid: str, case: dict[str, Any]) -> None:
    """Post-reconstruction services; a failure here is logged and never fails the core analysis."""
    from .services import cross_artifact, fragment_dna, possibility
    from .services.context import CaseCtx
    try:
        ctx = CaseCtx.load(cid, allow_running=True)
        idx = fragment_dna.build_index(ctx)
        audit(cid, "dna", f"Fragment DNA fingerprints generated for {len(idx['ids'])} non-empty fragment(s) ({fragment_dna.ML_ENGINE})")
        pos = possibility.assess_case(ctx)
        audit(cid, "possibility", "Recovery possibility assessed: " + ", ".join(f"{k}={v}" for k, v in sorted(pos["counts"].items())))
        xa = cross_artifact.analyze(ctx)
        audit(cid, "cross_artifact", f"Cross-artifact analysis: {len(xa['relationships'])} inferred relationship(s) across {len(ctx.files)} artifact(s)")
    except Exception as e:
        audit(cid, "error", f"Advanced analysis stage failed (core results unaffected): {type(e).__name__}: {e}")


def save_files(cid: str, files: list[dict[str, Any]]) -> None:
    db.execute("DELETE FROM files WHERE case_id=?", (cid,))
    db.many("INSERT INTO files VALUES (?,?,?,?,?,?,?)",
            [(cid, f["file_id"], f["file_name"], f["file_type"], f["recovery_status"], f["priority_score"], json.dumps(f)) for f in files])


def load_files(cid: str) -> list[dict[str, Any]]:
    return [json.loads(r["data"]) for r in db.query("SELECT data FROM files WHERE case_id=? ORDER BY priority_score DESC", (cid,))]


def reprioritize(cid: str, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    files = load_files(cid)
    prioritize(files, criteria)
    for f in files:
        f["explanation"] = explain(f)
    files.sort(key=lambda f: -f["priority_score"])
    save_files(cid, files)
    db.execute("UPDATE cases SET criteria=? WHERE id=?", (json.dumps(criteria), cid))
    audit(cid, "prioritize", f"Priorities recomputed with keywords [{', '.join(criteria.get('keywords', []))}] and weights {criteria.get('weights')}")
    return files
