"""Transparent, configurable prioritisation.

Priority answers "what should be reviewed first under the current criteria?", not "what is
important?". Every point is attributable to an evidence score or to a user-set criterion.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

DEFAULT_CRITERIA: dict[str, Any] = {
    "keywords": ["project", "aurora", "budget"],
    "type_weights": {"Document": 1.0, "Database": 0.8, "Image": 0.5, "Log": 0.5, "Archive": 0.4, "Other": 0.2},
    "recency_days": 30,
    "weights": {"integrity": 0.25, "reconstruction": 0.20, "relationship": 0.15, "relevance": 0.40},
    "bands": {"CRITICAL": 88, "HIGH": 72, "MEDIUM": 52, "LOW": 35},
}


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s[:19] if "Z" not in fmt else s[:20], fmt)
        except ValueError:
            continue
    return None


def relevance(f: dict[str, Any], crit: dict[str, Any], ref_time: datetime | None) -> tuple[float, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    hay = " ".join([f["file_name"], f.get("search_text", ""), str(f.get("metadata", {}))]).lower()
    hits = [k for k in crit.get("keywords", []) if k.strip() and k.lower() in hay]
    kpts = min(50.0, 25.0 * len(hits))
    items.append({"signal": "Keyword match", "points": kpts, "max": 50,
                  "detail": f"Matches: {', '.join(hits)}" if hits else "No configured keyword found in name, text or metadata"})
    tw = crit.get("type_weights", {}).get(f["content_class"], crit.get("type_weights", {}).get("Other", 0.2))
    items.append({"signal": "Content type preference", "points": round(20 * tw, 1), "max": 20,
                  "detail": f"{f['content_class']} weighted {tw:.1f}"})
    mt = _parse_dt(f.get("modified"))
    if mt and ref_time:
        recent = ref_time - mt <= timedelta(days=crit.get("recency_days", 30))
        items.append({"signal": "Recency", "points": 15.0 if recent else 0.0, "max": 15,
                      "detail": f"Modified {mt:%Y-%m-%d}; {'within' if recent else 'outside'} {crit.get('recency_days', 30)} days of latest evidence timestamp"})
    else:
        items.append({"signal": "Recency", "points": 0.0, "max": 15, "detail": "No reliable timestamp"})
    n = f.get("related_files", 0)
    items.append({"signal": "Related artifacts", "points": float(min(15, 5 * n)), "max": 15,
                  "detail": f"{n} related recovered artifact(s)"})
    return round(sum(i["points"] for i in items), 1), items


def prioritize(files: list[dict[str, Any]], crit: dict[str, Any]) -> None:
    times = [t for t in (_parse_dt(f.get("modified")) for f in files) if t]
    ref = max(times) if times else None
    w = crit["weights"]
    wsum = sum(w.values()) or 1
    for f in files:
        rel, items = relevance(f, crit, ref)
        recon = f["reconstruction_percentage"] if f["reconstruction_percentage"] is not None else 50.0
        parts = [
            {"component": "Integrity", "value": f["integrity_score"], "weight": w["integrity"]},
            {"component": "Reconstruction", "value": recon, "weight": w["reconstruction"]},
            {"component": "Relationship confidence", "value": f["relationship_confidence"], "weight": w["relationship"]},
            {"component": "Configured relevance", "value": rel, "weight": w["relevance"]},
        ]
        for p in parts:
            p["contribution"] = round(p["value"] * p["weight"] / wsum, 1)
        score = round(sum(p["contribution"] for p in parts), 1)
        b = crit["bands"]
        level = ("CRITICAL" if score >= b["CRITICAL"] else "HIGH" if score >= b["HIGH"] else
                 "MEDIUM" if score >= b["MEDIUM"] else "LOW" if score >= b["LOW"] else "INFORMATIONAL")
        if f["recovery_status"] == "UNRECOVERABLE":
            level = "INFORMATIONAL" if level in ("LOW", "MEDIUM") else level
        top = sorted(parts, key=lambda p: -p["contribution"])
        kw = next(i for i in items if i["signal"] == "Keyword match")
        reason = (f"Score {score}/100 under current criteria: "
                  + ", ".join(f"{p['component'].lower()} {p['value']:.0f} (x{p['weight']:.2f})" for p in top[:3])
                  + (f"; {kw['detail'].lower()}" if kw["points"] else "; no configured keyword matched")
                  + ". Priority means 'review earlier', not 'proven important'.")
        f.update(priority_score=score, priority_level=level, priority_reason=reason,
                 priority_breakdown=parts, relevance_score=rel, relevance_indicators=items)
