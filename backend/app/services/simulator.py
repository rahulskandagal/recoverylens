"""Counterfactual Recovery Simulator.

"What if fragment X filled missing range R?" The simulator:
  1. copies the reconstruction buffer into a TEMPORARY directory (never the evidence, never the export),
  2. reads the candidate fragment's bytes read-only from the evidence copy,
  3. splices them into the temporary copy at the gap (the rest of the gap stays placeholder),
  4. re-runs the SAME deterministic validators and scoring rules on both baseline and simulated copies,
  5. reports the difference. Nothing is persisted into the case's file records.

Results are labelled SIMULATION — NOT EVIDENCE.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..engine.common import Candidate, Segment, hexoff, sha256
from ..engine.postproc import validation_state
from ..engine.scoring import integrity, reconstruction_pct, run_validation, status
from .context import CaseCtx

LABEL = "SIMULATION — NOT EVIDENCE"
# integrity factors that only measure how many bytes are present, not whether they are correct
COVERAGE_FACTORS = ("Expected content present", "Continuity")


def _candidate(f: dict[str, Any], data: bytes, segs: list[Segment]) -> Candidate:
    st = {k: v for k, v in (f.get("structure") or {}).items()}
    return Candidate(id=f["file_id"], type=f["file_type"], name=f["file_name"], name_source=f.get("name_source", ""),
                     fragments=list(f.get("fragments", [])), segments=segs, expected_size=f.get("expected_size"),
                     expected_size_source=f.get("expected_size_source") or "", data=data, structure=st,
                     metadata=dict(f.get("metadata") or {}), conflicts=list(f.get("conflicts") or []),
                     orphan=bool(f.get("orphan")), header_found=bool(f.get("header_found", True)))


def _evaluate(c: Candidate, header: dict[str, Any] | None) -> dict[str, Any]:
    v = run_validation(c, header)
    if c.type == "jpeg" and v.get("stats", {}).get("rows_decoded") is not None and c.structure.get("rows_total"):
        # keep reconstruction % on the decoder's own row accounting for both baseline and simulation
        c.structure["rows_recovered"] = min(c.structure["rows_total"], v["stats"]["rows_decoded"])
    recon, recon_why = reconstruction_pct(c, v)
    integ, factors = integrity(c, v, recon)
    st, why = status(c, v, recon, integ, 0.0 if not c.edges else 0.9)
    vstate, vwhy = validation_state(c, v)
    checks = {x.get("name"): x.get("status") for x in v.get("checks", [])}
    return {"integrity": integ, "reconstruction": None if recon is None else round(recon, 1), "reconstruction_basis": recon_why,
            "status": st, "status_reason": why, "validation_state": vstate, "validation_reason": vwhy, "checks": checks,
            "structural": v.get("structural"), "parser": v.get("parser"), "checksum": v.get("checksum"),
            "factors": {x["factor"]: float(x.get("points") or 0) for x in factors},
            "missing_ranges": len(c.missing_ranges()), "missing_bytes": sum(m["length"] for m in c.missing_ranges())}


def simulate(ctx: CaseCtx, fid: str, fragment_id: str, range_index: int = 0, source_offset: int = 0) -> dict[str, Any]:
    f = ctx.file(fid)
    if fragment_id not in ctx.fragments:
        raise ValueError(f"unknown fragment {fragment_id}")
    miss = [s for s in f["segments"] if s["kind"] == "missing"]
    if not miss:
        raise ValueError("this artifact has no missing ranges to simulate against")
    if not 0 <= range_index < len(miss):
        raise ValueError(f"missing range index {range_index} out of range (0..{len(miss) - 1})")
    base = ctx.reconstruction_bytes(f)
    if base is None:
        raise ValueError("reconstruction bytes are not available on the server")
    gap = miss[range_index]
    fr = ctx.fragments[fragment_id]
    frag = ctx.frag_bytes(fragment_id)[source_offset:]
    take = min(len(frag), gap["length"])
    header = None
    first = f["fragments"][0] if f.get("fragments") else None
    if first and first in ctx.fragments:
        header = ctx.fragments[first].get("header")
    if f.get("structure", {}).get("single_file"):
        header = f["structure"].get("header")

    def segs_from(records: list[dict[str, Any]]) -> list[Segment]:
        return [Segment(s["start"], s["length"], s["kind"], s["fragment_id"], s["source_offset"], s.get("note", "")) for s in records]

    new_records = []
    for s in f["segments"]:
        if s is gap:
            new_records.append({**s, "length": take, "end": s["start"] + take, "kind": "recovered", "fragment_id": fragment_id,
                                "source_offset": fr["offset"] + source_offset, "note": "SIMULATED fill"})
            if take < gap["length"]:
                new_records.append({**s, "start": s["start"] + take, "length": gap["length"] - take, "kind": "missing",
                                    "fragment_id": None, "source_offset": None})
        else:
            new_records.append(s)

    with tempfile.TemporaryDirectory(prefix="rl_sim_") as td:  # isolated temporary copies only
        tmp_base = Path(td) / "baseline.bin"
        tmp_sim = Path(td) / "simulated.bin"
        tmp_base.write_bytes(base)
        sim = bytearray(tmp_base.read_bytes())
        sim[gap["start"]:gap["start"] + take] = frag[:take]
        tmp_sim.write_bytes(bytes(sim))
        before = _evaluate(_candidate(f, tmp_base.read_bytes(), segs_from(f["segments"])), header)
        after = _evaluate(_candidate(f, tmp_sim.read_bytes(), segs_from(new_records)), header)
        sim_sha = sha256(tmp_sim.read_bytes())

    changed_checks = []
    rank = {"pass": 3, "partial": 2, "na": 1, "not_available": 1, "fail": 0, None: -1}
    for name in sorted(set(before["checks"]) | set(after["checks"])):
        a, b = before["checks"].get(name), after["checks"].get(name)
        if a != b:
            changed_checks.append({"check": name, "before": a, "after": b, "direction": "improved" if rank.get(b, 0) > rank.get(a, 0) else "worse"})
    satisfied = [c["check"] for c in changed_checks if c["direction"] == "improved"]
    regressed = [c["check"] for c in changed_checks if c["direction"] == "worse"]
    conflicts = []
    if fr.get("header"):
        conflicts.append(f"{fragment_id} carries its own file header: it most likely starts a different file")
    owner = next((x for x in ctx.files if x["file_id"] == fr.get("assigned_to")), None)
    if owner and owner["file_id"] != fid and not owner.get("orphan"):
        conflicts.append(f"{fragment_id} is already used by reconstruction {owner['file_id']} ({owner['file_name']})")
    if fr.get("duplicate_of"):
        conflicts.append(f"{fragment_id} is a byte-identical duplicate of {fr['duplicate_of']}")
    if len(frag) > gap["length"]:
        conflicts.append(f"fragment is {len(frag) - gap['length']:,} B longer than the gap; the excess was ignored")
    d_int = round(after["integrity"] - before["integrity"], 1)
    d_rec = None if after["reconstruction"] is None or before["reconstruction"] is None else round(after["reconstruction"] - before["reconstruction"], 1)
    # Only validator evidence counts. Filling a gap always lowers the missing-byte count and so always raises the
    # byte-coverage factors of the integrity score; that alone says nothing about whether the bytes are the right ones.
    fa, fb = before.pop("factors"), after.pop("factors")
    d_cov = round(sum(fb.get(k, 0) - fa.get(k, 0) for k in COVERAGE_FACTORS), 1)
    d_val = round(d_int - d_cov, 1)
    worse = d_val < 0 or bool(regressed)
    validator_gain = d_val > 0 or bool(satisfied)
    if worse:
        verdict = "CANDIDATE REJECTED BY SIMULATION"
        why = "; ".join(filter(None, [f"validator-based integrity {d_val:+}" if d_val < 0 else "",
                                      f"validator checks got worse: {', '.join(regressed)}" if regressed else ""]))
    elif validator_gain:
        verdict = "CANDIDATE IMPROVES RECONSTRUCTION (SIMULATED)"
        why = "; ".join(filter(None, [f"validator-based integrity +{d_val}" if d_val > 0 else "", f"newly satisfied: {', '.join(satisfied)}" if satisfied else ""]))
    elif conflicts:
        verdict = "CANDIDATE REJECTED BY SIMULATION"
        why = "no validator confirms the fill and the candidate conflicts with the evidence: " + "; ".join(conflicts)
    else:
        verdict = "NO VALIDATOR CHANGE — FILL UNCONFIRMED"
        why = ("the gap would contain bytes, but no validator result changed, so nothing confirms they are the right bytes"
               + (f" (integrity {d_int:+} comes only from byte-coverage factors)" if d_cov else "")
               if take else "the validators report the same result with or without this fragment")
    return {
        "label": LABEL, "file_id": fid, "file_name": f["file_name"], "fragment_id": fragment_id,
        "fragment": {"offset_hex": hexoff(fr["offset"]), "length": fr["length"], "family": fr["family"], "sha256": fr.get("sha256")},
        "gap": {"index": range_index, "start": gap["start"], "end": gap["end"], "length": gap["length"], "bytes_filled": take},
        "before": before, "after": after, "delta": {"integrity": d_int, "integrity_from_coverage": d_cov, "integrity_from_validators": d_val,
                                                    "reconstruction": d_rec,
                                                    "missing_bytes": after["missing_bytes"] - before["missing_bytes"]},
        "changed_checks": changed_checks, "newly_satisfied": satisfied, "regressed": regressed,
        "newly_resolved_ranges": [{"start": gap["start"], "end": gap["start"] + take}] if take else [],
        "new_conflicts": conflicts, "verdict": verdict, "verdict_reason": why or "—",
        "simulated_sha256": sim_sha, "evidence_modified": False, "persisted": False,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "temporary copies in an isolated temp directory; deterministic validators re-run on both; evidence opened read-only",
    }
