"""AI Recovery Investigator: an approval-gated investigation planner over STRUCTURED evidence.

Recommendations are produced by an evidence planner that reads only backend records (possibility
map, fragment DNA, structure, validation, security, cross-artifact links, user criteria). Each one
states ACTION, REASON, EVIDENCE, EXPECTED BENEFIT, RISK and CONFIDENCE, and nothing runs until the
user approves it. Every executable action is read-only or a simulation on temporary copies:
no action can modify or delete evidence, execute recovered content, or fabricate bytes.

An optional LLM (Anthropic API, only when ANTHROPIC_API_KEY is configured) may write a narrative
summary of the same structured evidence. It never sees raw bytes, never decides validity, and its
output is labelled as an AI-generated summary.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .. import db
from ..engine.common import CLUSTER, hexoff
from . import cross_artifact, fragment_dna, guardian, possibility, simulator, structure
from .context import CaseCtx

PLANNER = "evidence planner v1 (deterministic rules over structured backend evidence)"
LLM_MODEL = os.environ.get("RECOVERYLENS_LLM_MODEL", "claude-sonnet-5")
ACTIONS = {"SIMULATE_CANDIDATE": "Simulate candidate fragment", "INSPECT_RANGE": "Inspect storage range (read-only)",
           "COMPARE_FRAGMENTS": "Compare fragment DNA", "RESCAN_SECURITY": "Re-run static security scan",
           "VALIDATE_STRUCTURE": "Validate file structure", "REVIEW_RELATIONSHIP": "Review cross-artifact relationship"}
NEVER = ["modify original evidence", "delete evidence", "execute recovered files, scripts or macros", "fabricate missing bytes",
         "change forensic metadata silently", "present uncertain evidence as fact"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigator (
  case_id TEXT, id TEXT, created TEXT, status TEXT, rec TEXT, decision_ts TEXT, decision TEXT, result TEXT,
  PRIMARY KEY (case_id, id)
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rid(*parts: Any) -> str:
    return "AI-" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8].upper()


def _kw_hits(f: dict[str, Any], kws: list[str]) -> list[str]:
    t = (f.get("search_text") or "").lower() + " " + f["file_name"].lower()
    return [k for k in kws if k.lower() in t]


def plan(ctx: CaseCtx, focus: str = "") -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    crit = json.loads(ctx.row.get("criteria") or "{}")
    kws = crit.get("keywords", [])
    files = [f for f in ctx.files if not focus or f["file_id"] == focus]
    for f in files:
        if f.get("error"):
            continue
        kh = _kw_hits(f, kws)
        boost = 1 if kh else 0
        pa = possibility.assess_file(ctx, f)
        for r in pa["regions"]:
            structural = [c for c in r["candidates"] if c["strength"] == "structural" and c["compatible"]]
            # structural matches are worth testing individually; family-only matches get one representative test
            picks = structural[:2] or [c for c in r["candidates"] if c["compatible"]][:1]
            for c in picks:
                if r["kind"] != "missing":
                    continue
                conf = "HIGH" if c["strength"] == "structural" else "MEDIUM"
                recs.append({
                    "id": _rid("SIM", f["file_id"], r["index"], c["id"]), "action": "SIMULATE_CANDIDATE",
                    "title": f"Test {c['id']} as a fill for missing range #{r['index']} of {f['file_name']}",
                    "target": {"file_id": f["file_id"], "fragment_id": c["id"], "range_index": r["index"]},
                    "reason": ("Strong structural compatibility with what the file expects in this gap" if c["strength"] == "structural"
                               else "Same content family and a fitting size; nothing structural links it yet") + (f"; matches investigation keyword(s) {', '.join(kh)}" if kh else ""),
                    "evidence": [f"missing range {r['offset_hex']} ({r['length']:,} B): expected {r['expected_content']}"] + r["known_evidence"][:2] + c["evidence"][:3],
                    "expected_benefit": f"could resolve up to {min(r['length'], c['length']):,} missing bytes if the validators accept the fill",
                    "risk": "none to evidence (runs on temporary copies); a wrong fill is rejected by the deterministic validators",
                    "confidence": conf, "confidence_basis": f"candidate strength '{c['strength']}' from the Recovery Possibility rules",
                    "priority": (3 if conf == "HIGH" else 2) + boost})
            if r["kind"] == "missing" and not any(c["compatible"] for c in r["candidates"]):
                prev = next((s for s in reversed(f["segments"]) if s["end"] <= r["start"] and s["source_offset"] is not None), None)
                if prev:
                    off = prev["source_offset"] + prev["length"]
                    off -= off % CLUSTER if off % CLUSTER else 0
                    at = next((fr for fr in ctx.fragments.values() if fr["offset"] <= off < fr["offset"] + fr["length"]), None)
                    recs.append({
                        "id": _rid("INS", f["file_id"], r["index"], off), "action": "INSPECT_RANGE",
                        "title": f"Inspect storage range {hexoff(off)}–{hexoff(off + CLUSTER)}",
                        "target": {"file_id": f["file_id"], "offset": off, "length": 512},
                        "reason": "Referenced by the file structure: the missing bytes would most plausibly follow the previous fragment physically",
                        "evidence": [f"missing range {r['offset_hex']} ({r['length']:,} B): expected {r['expected_content']}",
                                     f"previous fragment {prev['fragment_id']} ends at {hexoff(prev['source_offset'] + prev['length'])}",
                                     (f"that location holds {at['id']} (family {at['family']}, assigned to {at.get('assigned_to') or 'nothing'})"
                                      if at else "that location holds no detected fragment (empty space)")],
                        "expected_benefit": "confirms whether the adjacent cluster was overwritten (explains the gap) or holds relocatable data",
                        "risk": "none (read-only hex view of the evidence copy)", "confidence": "LOW",
                        "confidence_basis": "no compatible candidate exists; adjacency is only a weak prior", "priority": 1 + boost})
        if f["integrity_score"] < 60 and not f.get("orphan") and f["file_type"] in structure.PARSERS:
            bad = [c["name"] for c in f.get("validator_checks", []) if c.get("status") == "fail"][:3]
            recs.append({"id": _rid("VAL", f["file_id"]), "action": "VALIDATE_STRUCTURE",
                         "title": f"Map failing structure components of {f['file_name']}", "target": {"file_id": f["file_id"]},
                         "reason": f"Integrity {f['integrity_score']}/100 with failing checks" + (f": {', '.join(bad)}" if bad else ""),
                         "evidence": [f"status {f['recovery_status']}: {f.get('status_reason', '')[:160]}"] + [f"failing check: {b}" for b in bad],
                         "expected_benefit": "pinpoints which components (objects, members, pages, segments) are missing or corrupted",
                         "risk": "none (deterministic parser on the stored reconstruction)", "confidence": "HIGH",
                         "confidence_basis": "validator results are deterministic", "priority": 2 + boost})
        st = (f.get("security") or {}).get("status")
        if st in ("SUSPICIOUS", "SCAN_FAILED"):
            recs.append({"id": _rid("SEC", f["file_id"], st), "action": "RESCAN_SECURITY",
                         "title": f"Re-run the Safety Guardian on {f['file_name']}", "target": {"file_id": f["file_id"]},
                         "reason": f"Current security status {st}: " + ((f.get("security") or {}).get("explanation") or "")[:160],
                         "evidence": [m["rule"] + ": " + m["description"] for m in (f.get("security") or {}).get("yara_matches", [])][:3] or ["static indicators recorded"],
                         "expected_benefit": "refreshes the scan with the currently configured engines (e.g. after installing ClamAV)",
                         "risk": "none (static analysis only; the artifact is never executed or opened)", "confidence": "MEDIUM",
                         "confidence_basis": "rule/indicator matches are indicators, not confirmed infections", "priority": 3})
    # orphan / unassigned fragments strongly similar to a reconstructed file's fragments
    orphans = [f for f in ctx.files if f.get("orphan")][:4]
    for o in orphans:
        if focus and o["file_id"] != focus:
            continue
        fid = o["fragments"][0] if o.get("fragments") else None
        if not fid:
            continue
        try:
            sim = fragment_dna.similar(ctx, fid, top=6)
        except Exception:
            continue
        best = next((x for x in sim["items"] if x["label"] in ("STRONG SIMILARITY", "POSSIBLE SIMILARITY") and x["assigned_to"]
                     and x["assigned_to"] != o["file_id"]), None)
        if best:
            recs.append({"id": _rid("DNA", fid, best["id"]), "action": "COMPARE_FRAGMENTS",
                         "title": f"Compare orphan fragment {fid} with {best['id']} (in {best['assigned_to']})",
                         "target": {"fragment_id": fid, "other": best["id"]},
                         "reason": f"{best['label'].title()} between an orphan fragment and a fragment of a reconstructed file",
                         "evidence": best["evidence"][:4] + [sim["caveat"]],
                         "expected_benefit": "may indicate which file the orphan data came from (to be confirmed structurally)",
                         "risk": "similarity is statistical; it must not be treated as proof of common origin", "confidence": "LOW",
                         "confidence_basis": "statistical similarity only", "priority": 1})
    if not focus:
        xa = cross_artifact.analyze(ctx)
        for r in sorted(xa["relationships"], key=lambda r: -r["confidence"])[:3]:
            if r["confidence"] < 55:
                continue
            recs.append({"id": _rid("REL", r["source"], r["target"], r["type"]), "action": "REVIEW_RELATIONSHIP",
                         "title": f"Review {r['type_label'].lower()}: {r['source_name']} ↔ {r['target_name']}",
                         "target": {"source": r["source"], "target": r["target"], "type": r["type"]},
                         "reason": f"{r['label']} inferred relationship ({r['confidence']}%)", "evidence": r["evidence"][:4],
                         "expected_benefit": "links artifacts for the investigation narrative; may reveal where missing content lives",
                         "risk": "inferred relationship: it is not proof of common origin", "confidence": "MEDIUM" if r["confidence"] >= 75 else "LOW",
                         "confidence_basis": "deterministic detector with stated evidence", "priority": 1})
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    recs.sort(key=lambda r: (-r["priority"], -order[r["confidence"]]))
    for r in recs:
        r["action_label"] = ACTIONS[r["action"]]
        r["planner"] = PLANNER
        r["data_class"] = "INFERRED (recommendation)"
    return recs[:20]


def generate(ctx: CaseCtx, focus: str, audit: Callable[[str, str], None]) -> list[dict[str, Any]]:
    init()
    recs = plan(ctx, focus)
    existing = {r["id"]: r for r in db.query("SELECT id, status FROM investigator WHERE case_id=?", (ctx.id,))}
    new = 0
    for r in recs:
        if r["id"] in existing:
            continue
        new += 1
        db.execute("INSERT INTO investigator(case_id, id, created, status, rec) VALUES (?,?,?,?,?)", (ctx.id, r["id"], _now(), "pending", json.dumps(r)))
        audit("investigator", f"AI recommendation generated {r['id']}: {r['title']} (confidence {r['confidence']}; evidence: {'; '.join(r['evidence'][:2])[:220]})")
    audit("investigator", f"Investigation plan refreshed by {PLANNER}: {len(recs)} recommendation(s), {new} new; awaiting user approval")
    return list_recs(ctx.id)


def list_recs(cid: str) -> list[dict[str, Any]]:
    init()
    out = []
    for r in db.query("SELECT * FROM investigator WHERE case_id=? ORDER BY created", (cid,)):
        rec = json.loads(r["rec"])
        out.append({**rec, "status": r["status"], "created": r["created"], "decision_ts": r["decision_ts"], "decision": r["decision"],
                    "result": json.loads(r["result"]) if r["result"] else None})
    order = {"pending": 0, "executed": 1, "rejected": 2, "failed": 1}
    out.sort(key=lambda r: (order.get(r["status"], 3), -r.get("priority", 0)))
    return out


def _execute(ctx: CaseCtx, rec: dict[str, Any], audit: Callable[[str, str], None]) -> dict[str, Any]:
    t = rec["target"]
    a = rec["action"]
    if a == "SIMULATE_CANDIDATE":
        r = simulator.simulate(ctx, t["file_id"], t["fragment_id"], t.get("range_index", 0))
        return {"summary": f"{r['verdict']}: integrity {r['before']['integrity']} → {r['after']['integrity']}; {r['verdict_reason']}",
                "label": simulator.LABEL, "detail": r}
    if a == "INSPECT_RANGE":
        b = ctx.read(t["offset"], t.get("length", 512))
        lines = [{"offset": hexoff(t["offset"] + i), "hex": " ".join(f"{x:02X}" for x in b[i:i + 16]),
                  "ascii": "".join(chr(x) if 32 <= x < 127 else "." for x in b[i:i + 16])} for i in range(0, len(b), 16)]
        nz = sum(1 for x in b if x)
        return {"summary": f"{len(b)} bytes read at {hexoff(t['offset'])} (read-only): {nz} non-zero byte(s)" +
                           (" – all zero: the region is empty (consistent with overwritten/wiped data)" if nz == 0 else ""),
                "label": "OBSERVED", "detail": {"lines": lines}}
    if a == "COMPARE_FRAGMENTS":
        sim = fragment_dna.similar(ctx, t["fragment_id"], top=40)
        hit = next((x for x in sim["items"] if x["id"] == t["other"]), None)
        return {"summary": f"{t['fragment_id']} vs {t['other']}: {hit['label'] if hit else 'NO SIGNIFICANT SIMILARITY'} – {sim['caveat']}",
                "label": "DERIVED", "detail": {"comparison": hit, "engine": sim["engine"]}}
    if a == "RESCAN_SECURITY":
        r = guardian.rescan(ctx, t["file_id"], audit)
        after = r["results"][0]["after"] if r["results"] else "not rescanned"
        return {"summary": f"Static re-scan completed: {after} (engine: {r['engine'] or 'none available'}); artifact not executed",
                "label": "DERIVED", "detail": r}
    if a == "VALIDATE_STRUCTURE":
        ex = structure.explore(ctx, ctx.file(t["file_id"]))
        bad = []

        def walk(ns: list[dict[str, Any]]) -> None:
            for n in ns:
                if n["validation"] == "fail" or n["state"] in ("missing", "corrupted"):
                    bad.append(f"{n['name']} ({n['state']}, {n['validation']})")
                walk(n["children"])
        walk(ex.get("tree", []))
        return {"summary": (f"{ex.get('parser')}: {len(bad)} failing/missing component(s): " + "; ".join(bad[:6])) if ex.get("available")
                else f"{ex['status']}: {ex['reason']}", "label": "DERIVED", "detail": {"counts": ex.get("counts"), "failing": bad[:30]}}
    if a == "REVIEW_RELATIONSHIP":
        xa = cross_artifact.analyze(ctx)
        rel = [r for r in xa["relationships"] if r["source"] == t["source"] and r["target"] == t["target"] and r["type"] == t["type"]]
        return {"summary": f"{len(rel)} matching relationship record(s); evidence shown. Inferred, not proof.", "label": "INFERRED RELATIONSHIP",
                "detail": {"relationships": rel}}
    raise ValueError(f"unknown action {a}")


def decide(ctx: CaseCtx, rid: str, decision: str, audit: Callable[[str, str], None]) -> dict[str, Any]:
    init()
    row = db.one("SELECT * FROM investigator WHERE case_id=? AND id=?", (ctx.id, rid))
    if not row:
        raise KeyError(rid)
    if row["status"] != "pending":
        raise ValueError(f"recommendation {rid} is already {row['status']}")
    rec = json.loads(row["rec"])
    ts = _now()
    if decision == "reject":
        db.execute("UPDATE investigator SET status='rejected', decision_ts=?, decision='rejected' WHERE case_id=? AND id=?", (ts, ctx.id, rid))
        audit("investigator", f"User REJECTED {rid}: {rec['title']}; no action taken")
        return {"status": "rejected"}
    audit("investigator", f"User APPROVED {rid}: {rec['title']}; action taken: {ACTIONS[rec['action']]} (read-only/simulation)")
    try:
        res = _execute(ctx, rec, audit)
        status = "executed"
    except Exception as e:
        res = {"summary": f"ACTION FAILED: {type(e).__name__}: {e}", "label": "ERROR", "detail": {}}
        status = "failed"
    db.execute("UPDATE investigator SET status=?, decision_ts=?, decision='approved', result=? WHERE case_id=? AND id=?",
               (status, ts, json.dumps(res), ctx.id, rid))
    audit("investigator", f"{rid} result: {res['summary'][:300]}")
    return {"status": status, "result": res}


def decision_log(cid: str) -> list[dict[str, Any]]:
    out = []
    for r in list_recs(cid):
        out.append({"timestamp": r.get("decision_ts") or r["created"], "recommendation": f"{r['id']}: {r['title']}", "evidence": r["evidence"][:3],
                    "decision": r.get("decision") or "pending", "action_taken": r["action_label"] if r.get("decision") == "approved" else "none",
                    "result": (r.get("result") or {}).get("summary")})
    return out


# ------------------------------------------------------------------------------------------ optional LLM narrative

def llm_status() -> dict[str, Any]:
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {"configured": key, "model": LLM_MODEL if key else None,
            "status": "LLM CONFIGURED" if key else "LLM NOT CONFIGURED",
            "note": ("Narrative summaries are generated from structured evidence only." if key else
                     "No ANTHROPIC_API_KEY on the server: recommendations come from the deterministic evidence planner; "
                     "no AI narrative is generated (none is simulated).")}


def narrative(ctx: CaseCtx) -> dict[str, Any]:
    st = llm_status()
    if not st["configured"]:
        return {**st, "text": None}
    recs = list_recs(ctx.id)[:12]
    evidence = {
        "case": {"id": ctx.id, "demo_mode": ctx.demo, "image": ctx.row.get("image_name")},
        "files": [{k: f.get(k) for k in ("file_id", "file_name", "file_type", "recovery_status", "integrity_score", "reconstruction_percentage")}
                  | {"missing_ranges": len(f.get("missing_ranges", [])), "security": (f.get("security") or {}).get("status")} for f in ctx.files[:25]],
        "recommendations": [{k: r.get(k) for k in ("id", "title", "reason", "evidence", "confidence", "status")} for r in recs],
    }
    system = ("You summarise digital-recovery evidence for an investigator. Use ONLY the JSON evidence provided. "
              "Never claim bytes exist, are valid, or are malicious unless the evidence says so; label inference as inference; "
              "never suggest executing recovered content or modifying evidence. Be concise (under 180 words). "
              + ("This is DEMO / SYNTHETIC data; say so." if ctx.demo else ""))
    body = json.dumps({"model": LLM_MODEL, "max_tokens": 600, "system": system,
                       "messages": [{"role": "user", "content": "Structured evidence:\n" + json.dumps(evidence)}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST", headers={
        "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        return {**st, "text": text, "label": "AI-GENERATED SUMMARY OF STRUCTURED EVIDENCE (not evidence)"}
    except Exception as e:
        return {**st, "text": None, "error": f"LLM request failed: {type(e).__name__}: {e}"}
