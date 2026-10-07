"""Evidence-grounded explanations.

Explanations are generated from observable signals (validator results, factor tables, edge
evidence and model feature contributions) using fixed templates. No language model is
involved, so every sentence traces back to a recorded signal and nothing is paraphrased
into claims the evidence does not support.
"""
from __future__ import annotations

from typing import Any

from .common import rel_label
from .model import FEATURE_LABELS


def edge_sentence(e: dict[str, Any]) -> str:
    pos = [c for c in e.get("contributions", []) if c["contribution"] > 0.3][:2]
    neg = [c for c in e.get("contributions", []) if c["contribution"] < -0.3][:1]
    s = (f"{e['src']} -> {e['dst']}: {rel_label(e['confidence'] / 100).lower()} {e['type'].replace('_', ' ')} "
         f"relationship ({e['confidence']:.0f}%). ")
    if e.get("evidence"):
        s += " ".join(x.rstrip(".") + "." for x in e["evidence"][:2]) + " "
    if pos:
        s += "Main supporting signals: " + ", ".join(f"{FEATURE_LABELS.get(c['feature'], c['feature']).lower()} ({c['value']:.2f})" for c in pos) + ". "
    if neg:
        s += f"Counter-signal: {FEATURE_LABELS.get(neg[0]['feature'], neg[0]['feature']).lower()} ({neg[0]['value']:.2f})."
    return s.strip()


def explain(f: dict[str, Any]) -> dict[str, Any]:
    t = f["file_type"].upper()
    rec = f["reconstruction_percentage"]
    internal = [e for e in f["fragment_relationships"] if e["chosen"] and e["type"] in ("continuation", "structural")]
    out: dict[str, Any] = {}
    # summary
    out["summary"] = (
        f"{f['file_name']} was assembled from {len(f['fragments'])} fragment(s) "
        + (f"covering {rec:.1f}% of its expected structure. " if rec is not None else "(completeness not estimable). ")
        + f"Validators report structure '{f['validation']['structural_validation']}', parser '{f['validation']['parser_validation']}', "
        f"checksum/index '{f['validation']['checksum_validation']}'. Status: {f['recovery_status'].replace('_', ' ').lower()}."
    )
    # why linked
    if internal:
        weakest = min(internal, key=lambda e: e["confidence"])
        out["why_linked"] = (f"{len(internal)} fragment link(s) support this assembly. Weakest link: " + edge_sentence(weakest))
    elif f.get("orphan"):
        out["why_linked"] = "These fragments have no recovered header, so they could not be linked to a file with verifiable evidence."
    else:
        out["why_linked"] = "The file occupies a single contiguous fragment; no inter-fragment inference was needed."
    # why status
    out["why_status"] = f"{f['recovery_status']}: {f['status_reason']}."
    # why integrity
    pos = [x for x in f["integrity_factors"] if x["points"] > 0 and x["max"] and x["points"] >= 0.8 * x["max"]]
    lost = [x for x in f["integrity_factors"] if (x["max"] and x["points"] < 0.8 * x["max"]) or x["points"] < 0]
    out["why_integrity"] = (f"Integrity {f['integrity_score']:.0f}/100. "
                            + ("Strengths: " + "; ".join(f"{x['factor'].lower()} ({x['detail']})" for x in pos[:3]) + ". " if pos else "")
                            + ("Deductions: " + "; ".join(f"{x['factor'].lower()} {x['points']:+.0f}/{x['max']} ({x['detail']})" for x in lost[:4]) + "." if lost else ""))
    # why priority
    out["why_priority"] = f"{f['priority_level']}: {f['priority_reason']}"
    # partial recovery explanation
    a = f["assessment"]
    out["why_partial"] = f"{a['exists']} {a['missing']} {a['corrupted']}"
    out["further_recovery"] = f"{a['further_recovery']}: {a['reason']}"
    out["edge_explanations"] = [edge_sentence(e) for e in f["fragment_relationships"] if e["type"] != "metadata"][:12]
    out["provenance_note"] = ("All bytes come from the listed source offsets in the read-only evidence image. "
                              "Missing ranges are zero-filled in exports and listed; no content was generated.")
    out["method"] = "Template-based explanation over recorded validator results, score factors and edge-model feature contributions (no LLM)."
    return out
