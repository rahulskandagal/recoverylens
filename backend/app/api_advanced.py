"""API for the advanced analysis services (mounted under /api)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import db, pipeline
from .services import benchmark, confidence, cross_artifact, fragment_dna, guardian, investigator, possibility, simulator, structure, twin
from .services.context import CaseCtx, CaseNotReady

router = APIRouter(prefix="/api")


def _ctx(cid: str) -> CaseCtx:
    try:
        return CaseCtx.load(cid)
    except KeyError:
        raise HTTPException(404, "case not found")
    except CaseNotReady as e:
        raise HTTPException(409, f"NOT AVAILABLE IN CURRENT ANALYSIS: {e}")


def _file(ctx: CaseCtx, fid: str) -> dict[str, Any]:
    try:
        return ctx.file(fid)
    except KeyError:
        raise HTTPException(404, "file not found")


def _audit(cid: str):
    return lambda stage, msg: pipeline.audit(cid, stage, msg)


# ------------------------------------------------------------------ Fragment DNA
@router.get("/cases/{cid}/dna")
def dna_list(cid: str, page: int = 1, size: int = Query(24, le=60), family: str = "", q: str = "") -> dict[str, Any]:
    return fragment_dna.dna_list(_ctx(cid), page, size, family, q)


@router.get("/cases/{cid}/dna/map")
def dna_map(cid: str) -> dict[str, Any]:
    return fragment_dna.dna_map(_ctx(cid))


@router.get("/cases/{cid}/dna/{frag}")
def dna_card(cid: str, frag: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    if frag not in ctx.fragments:
        raise HTTPException(404, "fragment not found")
    return {"card": fragment_dna.dna_card(ctx, frag), "similar": fragment_dna.similar(ctx, frag)}


# ------------------------------------------------------------------ Structure Explorer
@router.get("/cases/{cid}/files/{fid}/structure")
def file_structure(cid: str, fid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    return structure.explore(ctx, _file(ctx, fid))


# ------------------------------------------------------------------ Digital Twin
@router.get("/cases/{cid}/twin")
def twin_default(cid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    fid = twin.pick_default(ctx)
    if not fid:
        raise HTTPException(404, "INSUFFICIENT EVIDENCE: no reconstructed artifacts in this case")
    return twin.build(ctx, fid)


@router.get("/cases/{cid}/files/{fid}/twin")
def file_twin(cid: str, fid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    _file(ctx, fid)
    return twin.build(ctx, fid)


# ------------------------------------------------------------------ Recovery Possibility
@router.get("/cases/{cid}/possibility")
def case_possibility(cid: str) -> dict[str, Any]:
    return possibility.assess_case(_ctx(cid))


@router.get("/cases/{cid}/files/{fid}/possibility")
def file_possibility(cid: str, fid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    return possibility.assess_file(ctx, _file(ctx, fid))


# ------------------------------------------------------------------ Counterfactual Simulator
class SimRequest(BaseModel):
    fragment_id: str
    range_index: int = 0
    source_offset: int = 0


@router.post("/cases/{cid}/files/{fid}/simulate")
def simulate(cid: str, fid: str, body: SimRequest) -> dict[str, Any]:
    ctx = _ctx(cid)
    _file(ctx, fid)
    pipeline.audit(cid, "simulation", f"{fid}: SIMULATION started with candidate {body.fragment_id} for missing range #{body.range_index} "
                                      "(temporary copies; evidence and exports untouched)")
    try:
        r = simulator.simulate(ctx, fid, body.fragment_id, body.range_index, body.source_offset)
    except ValueError as e:
        pipeline.audit(cid, "simulation", f"{fid}: SIMULATION FAILED: {e}")
        raise HTTPException(400, str(e))
    pipeline.audit(cid, "simulation", f"{fid}: SIMULATION result (not evidence): {r['verdict']}; integrity {r['before']['integrity']} → "
                                      f"{r['after']['integrity']}; simulated copy SHA-256 {r['simulated_sha256'][:16]}… discarded")
    return r


# ------------------------------------------------------------------ Cross-Artifact Intelligence
@router.get("/cases/{cid}/cross-artifact")
def cross(cid: str) -> dict[str, Any]:
    return cross_artifact.analyze(_ctx(cid))


# ------------------------------------------------------------------ Safety Guardian
@router.get("/cases/{cid}/guardian")
def guardian_overview(cid: str) -> dict[str, Any]:
    return guardian.overview(_ctx(cid))


@router.post("/cases/{cid}/guardian/rescan")
def guardian_rescan(cid: str, file_id: str | None = None) -> dict[str, Any]:
    ctx = _ctx(cid)
    if file_id:
        _file(ctx, file_id)
    return guardian.rescan(ctx, file_id, _audit(cid))


# ------------------------------------------------------------------ Confidence
@router.get("/cases/{cid}/files/{fid}/confidence")
def file_confidence(cid: str, fid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    f = _file(ctx, fid)
    return confidence.profile(ctx, f, possibility.assess_file(ctx, f))


@router.get("/calibration")
def calibration() -> dict[str, Any]:
    return confidence.calibration()


# ------------------------------------------------------------------ Benchmark Lab
class BenchRequest(BaseModel):
    scenarios: list[str] | None = None
    seed: int | None = None


@router.get("/benchmarks/scenarios")
def bench_scenarios() -> dict[str, Any]:
    return {"scenarios": benchmark.SCENARIOS, "stages": benchmark.STAGES, "label": benchmark.LABEL}


@router.post("/benchmarks")
def bench_start(body: BenchRequest) -> dict[str, str]:
    try:
        return {"id": benchmark.start(body.scenarios, body.seed)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/benchmarks")
def bench_list() -> list[dict[str, Any]]:
    return benchmark.list_runs()


@router.get("/benchmarks/{bid}")
def bench_get(bid: str) -> dict[str, Any]:
    r = benchmark.get(bid)
    if not r:
        raise HTTPException(404, "benchmark not found")
    return r


# ------------------------------------------------------------------ AI Recovery Investigator
@router.get("/cases/{cid}/investigator")
def inv_list(cid: str) -> dict[str, Any]:
    _ctx(cid)
    return {"recommendations": investigator.list_recs(cid), "planner": investigator.PLANNER, "llm": investigator.llm_status(),
            "never": investigator.NEVER, "log": investigator.decision_log(cid)}


class PlanRequest(BaseModel):
    focus: str = ""


@router.post("/cases/{cid}/investigator/plan")
def inv_plan(cid: str, body: PlanRequest) -> dict[str, Any]:
    ctx = _ctx(cid)
    investigator.generate(ctx, body.focus, _audit(cid))
    return inv_list(cid)


class Decision(BaseModel):
    decision: str  # approve | reject


@router.post("/cases/{cid}/investigator/{rid}/decision")
def inv_decide(cid: str, rid: str, body: Decision) -> dict[str, Any]:
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    ctx = _ctx(cid)
    try:
        return investigator.decide(ctx, rid, body.decision, _audit(cid))
    except KeyError:
        raise HTTPException(404, "recommendation not found")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/cases/{cid}/investigator/narrative")
def inv_narrative(cid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    r = investigator.narrative(ctx)
    if r.get("text"):
        pipeline.audit(cid, "investigator", f"AI narrative summary generated by {r['model']} from structured evidence (labelled, not evidence)")
    return r


# ------------------------------------------------------------------ Provenance
@router.get("/cases/{cid}/files/{fid}/provenance")
def provenance(cid: str, fid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    f = _file(ctx, fid)
    p = f.get("provenance") or {}
    approved = [r for r in investigator.list_recs(cid) if r.get("decision") == "approved"
                and fid in (r["target"].get("file_id"), r["target"].get("source"), r["target"].get("target"))]
    sims = [a for a in db.query("SELECT ts, message FROM audit WHERE case_id=? AND stage='simulation' ORDER BY id", (cid,))
            if a["message"].startswith(f"{fid}:")]
    sec = f.get("security") or {}
    return {
        "question": "Where did this recovered artifact come from?",
        "artifact": {"file_id": fid, "name": f["file_name"], "type": f["file_type"], "size": f["size_bytes"],
                     "reconstruction_sha256": f.get("reconstruction_sha256"), "export": (f.get("export") or {}).get("file")},
        "source_image": {"name": p.get("source_image"), "sha256": p.get("source_sha256"), "evidence_unchanged": bool(ctx.row.get("evidence_unchanged"))},
        "fragments": [{"id": s["fragment_id"], "source_offset": s["source_offset_hex"], "logical": f"{s['start']:,}–{s['end']:,}", "length": s["length"],
                       "state": s["kind"], "sha256": next((d["sha256"] for d in f.get("fragment_details", []) if d["id"] == s["fragment_id"]), None)}
                      for s in f.get("segments", [])],
        "reconstruction_operations": p.get("operations", []),
        "validation": {"structural": f["validation"].get("structural_validation"), "parser": f["validation"].get("parser_validation"),
                       "checksum": f["validation"].get("checksum_validation"), "integrity": f["integrity_score"]},
        "security_scan": {"status": sec.get("status"), "scanner": sec.get("scanner"), "rule_engine": sec.get("yara_engine"),
                          "timestamp": sec.get("scan_timestamp"), "sha256": sec.get("sha256")},
        "analysis_version": p.get("analysis_version"), "model": p.get("model"), "ai_planner": investigator.PLANNER,
        "timestamp": p.get("timestamp"),
        "user_approved_operations": [{"id": r["id"], "action": r["action_label"], "title": r["title"], "decided": r["decision_ts"],
                                      "result": (r.get("result") or {}).get("summary")} for r in approved],
        "simulations": sims,
    }


def advanced_report(cid: str) -> dict[str, Any]:
    ctx = _ctx(cid)
    pos = possibility.assess_case(ctx)
    xa = cross_artifact.analyze(ctx)
    g = guardian.overview(ctx)
    return {
        "recovery_possibility": [{"file_id": p["file_id"], "file_name": p["file_name"], "level": p["level"], "supporting": p["plus"],
                                  "opposing": p["minus"], "regions": [{k: r[k] for k in ("kind", "start", "end", "length", "expected_content", "level", "reason")}
                                                                     | {"candidates": [c["id"] for c in r["candidates"]]} for r in p["regions"]]}
                                 for p in pos["files"]],
        "cross_artifact_relationships": [{k: r[k] for k in ("type", "source", "target", "confidence", "label", "evidence", "data_class")}
                                         for r in xa["relationships"]],
        "security_guardian": [{"file_id": i["file_id"], "status": i["status"], "headline": i["headline"], "detections":
                               [{k: d.get(k) for k in ("name", "scanner", "rule", "region", "timestamp")} for d in i["detections"]],
                               "isolation": i["isolation"], "absence_note": i["absence_note"]} for i in g["items"]],
        "confidence_profiles": [{"file_id": f["file_id"], "measures": {m["key"]: m["value"] for m in
                                                                       confidence.profile(ctx, f, next(p for p in pos["files"] if p["file_id"] == f["file_id"]))["measures"]}}
                                for f in ctx.files],
        "confidence_note": confidence.DISCLAIMER,
        "ai_investigator": {"planner": investigator.PLANNER, "decisions": investigator.decision_log(cid)},
        "data_class_legend": {"OBSERVED": "bytes/offsets read from evidence", "DERIVED": "computed from observed data (hashes, validation)",
                              "INFERRED RELATIONSHIP": "evidence-weighted link, not proof", "RECONSTRUCTED": "assembled output",
                              "SIMULATED RESULT": "simulator output on temporary copies; never evidence", "MISSING DATA": "not located; never synthesized",
                              "UNVERIFIED CONTENT": "present but not verifiable by checksum/structure"},
    }


# ------------------------------------------------------------------ dashboard extras
@router.get("/cases/{cid}/insights")
def insights(cid: str) -> dict[str, Any]:
    """Aggregates for the dashboard (all computed from stored records; nothing estimated)."""
    ctx = _ctx(cid)
    files = ctx.files
    pos = possibility.assess_case(ctx)
    ints = [f["integrity_score"] for f in files]
    recs = [f["reconstruction_percentage"] for f in files if f.get("reconstruction_percentage") is not None]
    rel_edges = [e for e in ctx.graph.get("edges", []) if e.get("type") not in ("member",)]
    xa = cross_artifact.analyze(ctx)

    def hist(vals: list[float], step: int = 10) -> list[dict[str, Any]]:
        return [{"bin": f"{lo}–{lo + step}", "n": sum(1 for v in vals if lo <= v < lo + step or (lo + step == 100 and v == 100))}
                for lo in range(0, 100, step)]
    return {
        "average_integrity": round(sum(ints) / len(ints), 1) if ints else None,
        "average_reconstruction": round(sum(recs) / len(recs), 1) if recs else None,
        "reconstruction_na": len(files) - len(recs),
        "relationship_count": len(rel_edges), "artifact_relationships": len(xa["relationships"]),
        "possibility_counts": pos["counts"], "heatmap": pos["heatmap"], "heatmap_legend": pos["legend"],
        "integrity_hist": hist(ints), "reconstruction_hist": hist(recs),
        "relationship_conf_hist": hist([float(e.get("confidence") or 0) for e in rel_edges]),
        "security_counts": guardian.overview(ctx)["counts"],
    }
