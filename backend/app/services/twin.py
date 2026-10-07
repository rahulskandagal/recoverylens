"""Recovery Digital Twin: the reconstruction history of one artifact as an explorable graph.

storage image → fragments (source offsets) → logical ordering incl. missing/corrupted ranges →
reconstruction operations → validation → security → final artifact.

Every placement decision carries a concise evidence-based explanation assembled from the recorded
relationship evidence (never free-form reasoning).
"""
from __future__ import annotations

from typing import Any

from .context import CaseCtx


def _decision(e: dict[str, Any] | None, prev: str, cur: str, ftype: str) -> str:
    if not e:
        return (f"{cur} was placed after {prev} by the {ftype.upper()} assembler's layout; no pairwise relationship was scored "
                "for this join (placement follows the file's own index).")
    ev = [x for x in e.get("evidence", []) if x][:2]
    # lower-case only ordinary sentence starts, never identifiers such as F0171 or PDF/RST acronyms
    why = "; ".join(x[0].lower() + x[1:] if len(x) > 1 and x[1].islower() else x for x in ev) or "recorded structural evidence"
    return f"{cur} was placed after {prev} because {why}. Relationship score {e.get('confidence')}% ({e.get('label', '')})."


def build(ctx: CaseCtx, fid: str) -> dict[str, Any]:
    f = ctx.file(fid)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    rels = f.get("fragment_relationships", [])
    by_pair = {(e["src"], e["dst"]): e for e in rels if e.get("chosen")}
    img = f.get("provenance", {}).get("source_image") or ctx.row.get("image_name")
    nodes.append({"id": "image", "kind": "image", "label": "STORAGE IMAGE", "data_class": "OBSERVED",
                  "detail": {"name": img, "sha256": ctx.row.get("image_sha256"), "size": ctx.row.get("image_size"),
                             "evidence_unchanged": bool(ctx.row.get("evidence_unchanged")), "access": "read-only copy"}})
    frag_info = {d["id"]: d for d in f.get("fragment_details", [])}
    order: list[dict[str, Any]] = []
    for i, s in enumerate(f.get("segments", [])):
        nid = f"seg{i}"
        if s["kind"] == "missing":
            node = {"id": nid, "kind": "missing", "label": "MISSING RANGE", "data_class": "MISSING",
                    "detail": {"logical": f"{s['start']:,}–{s['end']:,}", "length": s["length"], "note": s.get("note"),
                               "export_behaviour": "0x00 placeholder bytes in the forensic artifact; never presented as recovered"}}
        else:
            fd = frag_info.get(s["fragment_id"] or "", {})
            node = {"id": nid, "kind": "corrupted" if s["kind"] == "corrupted" else "fragment",
                    "label": s["fragment_id"] or "segment", "fragment_id": s["fragment_id"],
                    "data_class": "CORRUPTED" if s["kind"] == "corrupted" else "OBSERVED",
                    "detail": {"fragment": s["fragment_id"], "source_offset": s.get("source_offset_hex"), "logical": f"{s['start']:,}–{s['end']:,}",
                               "length": s["length"], "sha256": fd.get("sha256"), "family": fd.get("family"), "entropy": fd.get("entropy"),
                               "note": s.get("note"), "state": s["kind"]}}
        nodes.append(node)
        order.append({"node": nid, **s})
    # storage → fragment edges (where the bytes physically came from)
    seen_src: set[str] = set()
    for o in order:
        if o["kind"] != "missing" and o["fragment_id"] and o["fragment_id"] not in seen_src:
            seen_src.add(o["fragment_id"])
            edges.append({"id": f"img-{o['node']}", "src": "image", "dst": o["node"], "kind": "source",
                          "label": o.get("source_offset_hex") or "", "detail": f"bytes read from {o.get('source_offset_hex')} (read-only)"})
    # ordering edges with decisions
    decisions = []
    for a, b in zip(order, order[1:]):
        e = None
        if a.get("fragment_id") and b.get("fragment_id") and a["fragment_id"] != b["fragment_id"]:
            e = by_pair.get((a["fragment_id"], b["fragment_id"]))
        if b["kind"] == "missing":
            text = f"Gap of {b['length']:,} B after {a.get('fragment_id') or 'the previous range'}: the file's structure requires bytes here that were not located."
        elif a["kind"] == "missing":
            text = (f"{b['fragment_id']} resumes at logical {b['start']:,} after the gap; its position comes from the file's structure, "
                    "not from adjacency to the missing bytes.")
        elif a.get("fragment_id") == b.get("fragment_id"):
            text = f"{b['fragment_id']} continues (same fragment; a corrupted/validated range boundary splits it)."
        else:
            text = _decision(e, a.get("fragment_id") or "?", b.get("fragment_id") or "?", f["file_type"])
        decisions.append({"from": a["node"], "to": b["node"], "text": text, "confidence": e.get("confidence") if e else None,
                          "evidence": e.get("evidence", []) if e else [], "type": e.get("type") if e else "layout"})
        edges.append({"id": f"{a['node']}-{b['node']}", "src": a["node"], "dst": b["node"], "kind": "order",
                      "label": f"{e['confidence']}%" if e else ("gap" if "missing" in (a["kind"], b["kind"]) else "layout"),
                      "detail": text, "confidence": e.get("confidence") if e else None})
    ops = f.get("provenance", {}).get("operations", [])
    checks = f.get("validator_checks", [])
    passed = sum(1 for c in checks if c.get("status") == "pass")
    sec = f.get("security") or {}
    exp = f.get("export") or {}
    tail = [
        {"id": "recon", "kind": "operation", "label": "RECONSTRUCTION", "data_class": "RECONSTRUCTED",
         "detail": {"operations": ops[:-1] if ops else [], "recovered_bytes": sum(s["length"] for s in f["segments"] if s["kind"] != "missing"),
                    "missing_bytes": sum(s["length"] for s in f["segments"] if s["kind"] == "missing"),
                    "reconstruction_sha256": f.get("reconstruction_sha256")}},
        {"id": "validate", "kind": "validation", "label": "VALIDATION", "data_class": "DERIVED",
         "detail": {"summary": f"{passed}/{len(checks)} checks pass", "structural": f["validation"].get("structural_validation"),
                    "parser": f["validation"].get("parser_validation"), "checksum": f["validation"].get("checksum_validation"),
                    "checks": [{k: c.get(k) for k in ("name", "status", "detail")} for c in checks],
                    "integrity": f["integrity_score"], "status": f["recovery_status"], "reason": f.get("status_reason")}},
        {"id": "security", "kind": "security", "label": "SECURITY GUARDIAN", "data_class": "DERIVED",
         "detail": {"status": sec.get("status"), "label": sec.get("label"), "scanner": sec.get("scanner"), "explanation": sec.get("explanation"),
                    "executed": False}},
        {"id": "artifact", "kind": "artifact", "label": exp.get("label") or "RECOVERED FILE",
         "data_class": "RECONSTRUCTED" if exp.get("class") != "validated_file" else "RECONSTRUCTED (VALIDATED)",
         "detail": {"name": f["file_name"], "size": f["size_bytes"], "export_class": exp.get("class"), "file": exp.get("file"),
                    "sha256": exp.get("reconstruction_sha256"), "byte_identical": exp.get("byte_identical")}},
    ]
    nodes.extend(tail)
    for o in order:
        edges.append({"id": f"{o['node']}-recon", "src": o["node"], "dst": "recon", "kind": "assemble", "label": ""})
    for a, b, lab in (("recon", "validate", "validate"), ("validate", "security", "static scan"), ("security", "artifact", "export decision")):
        edges.append({"id": f"{a}-{b}", "src": a, "dst": b, "kind": "pipeline", "label": lab})
    audit = ctx_audit(ctx, fid)
    return {"file_id": fid, "file_name": f["file_name"], "file_type": f["file_type"], "nodes": nodes, "edges": edges,
            "decisions": decisions, "timeline": audit,
            "ranges": {"missing": f.get("missing_ranges", []), "corrupted": f.get("corrupted_ranges", [])},
            "note": "Explanations are assembled from recorded evidence (relationship evidence, validator checks, audit trail)."}


def ctx_audit(ctx: CaseCtx, fid: str) -> list[dict[str, Any]]:
    from .. import db
    rows = db.query("SELECT ts, stage, message FROM audit WHERE case_id=? ORDER BY id", (ctx.id,))
    out = []
    for r in rows:
        m = r["message"]
        if m.startswith(f"{fid}:") or m.startswith(f"{fid} ") or r["stage"] in ("ingest", "scan", "identify") and len(out) < 4:
            out.append(r)
    return out[:40]


def pick_default(ctx: CaseCtx) -> str | None:
    """Most instructive artifact: prefer multi-fragment files with gaps."""
    best = sorted(ctx.files, key=lambda f: (-(len(f.get("missing_ranges", [])) > 0), -len(f.get("fragments", [])), -f["priority_score"]))
    return best[0]["file_id"] if best else None
