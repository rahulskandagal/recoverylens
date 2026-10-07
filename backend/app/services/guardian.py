"""Recovery Safety Guardian: static-only security review of every reconstructed artifact.

Never executes, opens or renders recovered content, scripts, macros or embedded objects. Flagged
artifacts are copied (as inert data, read-only, non-executable name) into an isolated per-case
analysis folder; the evidence and the export files are never modified.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from ..engine.common import sha256
from ..engine.security import CLEAN_NOTE, YARA_ENGINE, get_scanner, scan_artifact

GUARDIAN_STATUS = {
    "CLEAN": ("NO DETECTION", "NO MALWARE DETECTED BY AVAILABLE SCANNERS"),
    "SUSPICIOUS": ("SUSPICIOUS", "SUSPICIOUS CHARACTERISTICS FOUND"),
    "MALWARE_DETECTED": ("MALWARE DETECTED", "MALWARE DETECTED"),
    "SCAN_FAILED": ("SCAN UNAVAILABLE", "SCAN UNAVAILABLE – THE SCANNER FAILED"),
    "NOT_SCANNED": ("SCAN UNAVAILABLE", "SCAN UNAVAILABLE – NO ANTIVIRUS ENGINE CONFIGURED"),
}
ABSENCE_NOTE = "Absence of a detection does not guarantee that the file is safe."


def isolate(out_dir: Path, fid: str, data: bytes, security: dict[str, Any]) -> dict[str, Any] | None:
    """Copy a flagged artifact into <case>/isolation/ as inert, read-only data."""
    if security.get("status") not in ("MALWARE_DETECTED", "SUSPICIOUS"):
        return None
    d = out_dir / "isolation"
    d.mkdir(parents=True, exist_ok=True)
    h = sha256(data)
    p = d / f"{fid}_{h[:16]}.bin.QUARANTINED"
    if not p.exists():
        p.write_bytes(data)
        os.chmod(p, stat.S_IREAD)
    return {"path": f"isolation/{p.name}", "sha256": h, "read_only": True,
            "note": "inert copy for static analysis only; neutral non-executable name; never opened by any application"}


def view(f: dict[str, Any]) -> dict[str, Any]:
    sec = f.get("security") or {}
    st = sec.get("status") or "NOT_SCANNED"
    short, headline = GUARDIAN_STATUS.get(st, ("SCAN UNAVAILABLE", "SCAN UNAVAILABLE"))
    ts = sec.get("scan_timestamp")
    dets: list[dict[str, Any]] = []
    for d in sec.get("detections") or []:
        dets.append({"name": d, "scanner": sec.get("scanner"), "rule": d, "evidence": "antivirus signature match",
                     "region": None, "timestamp": ts, "scanner_status": "completed", "simulated": bool(sec.get("simulated")),
                     "kind": "engine"})
    for m in sec.get("yara_matches") or []:
        dets.append({"name": m["rule"], "scanner": sec.get("yara_engine") or YARA_ENGINE, "rule": m["rule"],
                     "evidence": m.get("description"), "severity": m.get("severity"),
                     "region": ", ".join(f"0x{o:X}" for o in (m.get("offsets") or [])) or None,
                     "timestamp": ts, "scanner_status": "completed", "kind": "rule"})
    for i in (sec.get("static") or {}).get("indicators", []):
        dets.append({"name": i["text"], "scanner": "static structural analysis", "rule": None, "evidence": i["text"],
                     "severity": i["severity"], "region": f"0x{i['offset']:X}" if i.get("offset") is not None else None,
                     "timestamp": ts, "scanner_status": "completed", "kind": "static"})
    engine_status = ("completed" if sec.get("scanner") and st != "SCAN_FAILED" else
                     "failed: " + (sec.get("engine_error") or "unknown") if st == "SCAN_FAILED" else "not available")
    return {
        "file_id": f["file_id"], "file_name": f["file_name"], "file_type": f["file_type"],
        "status": short, "headline": headline, "raw_status": st,
        "is_test": bool(sec.get("is_test")), "simulated": bool(sec.get("simulated")),
        "explanation": sec.get("explanation"), "absence_note": ABSENCE_NOTE if st in ("CLEAN", "NOT_SCANNED", "SCAN_FAILED") else None,
        "clean_note": CLEAN_NOTE if st == "CLEAN" else None,
        "detections": dets, "scanner": sec.get("scanner"), "scanner_version": sec.get("scanner_version"),
        "engine_status": engine_status, "rule_engine": sec.get("yara_engine") or YARA_ENGINE,
        "static": sec.get("static") or {}, "sha256": sec.get("sha256"), "scan_timestamp": ts,
        "isolation": sec.get("isolation"), "executed": False, "opened": False,
        "export_gate": "explicit authorization required" if st == "MALWARE_DETECTED" else "normal export available after validation",
    }


def overview(ctx) -> dict[str, Any]:
    items = [view(f) for f in ctx.files]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    s = (ctx.summary or {}).get("security", {})
    return {"items": items, "counts": counts, "engine": s.get("engine"), "rule_engine": s.get("rule_engine") or YARA_ENGINE,
            "simulated_engine": s.get("simulated_engine"), "unassigned_scanned": s.get("unassigned_fragments_scanned"),
            "unassigned_flagged": s.get("unassigned_fragments_flagged"),
            "policy": ["Static analysis only: recovered files, scripts, macros and embedded objects are never executed",
                       "Flagged artifacts are isolated as read-only inert copies; evidence and exports are unchanged",
                       "Malware-flagged artifacts are never opened or previewed automatically; export needs explicit authorization",
                       ABSENCE_NOTE]}


def rescan(ctx, fid: str | None, audit) -> dict[str, Any]:
    """Re-run the security stage on stored reconstruction bytes (read as data only)."""
    from ..pipeline import save_files
    scanner = get_scanner(ctx.row.get("mode") or "upload")
    changed = []
    for f in ctx.files:
        if fid and f["file_id"] != fid:
            continue
        data = ctx.reconstruction_bytes(f)
        if data is None:
            continue
        old = (f.get("security") or {}).get("status")
        audit("security", f"{f['file_id']}: Safety Guardian re-scan started (static analysis only; artifact not executed)")
        sec = scan_artifact(data, f["file_type"], ctx.row.get("mode") or "upload", scanner)
        sec["isolation"] = isolate(ctx.out_dir, f["file_id"], data, sec)
        f["security"] = sec
        if f.get("export"):
            malware = sec["status"] == "MALWARE_DETECTED"
            f["export"]["normal_export_enabled"] = bool(f["export"].get("exported")) and not malware
            f["export"]["authorization_required"] = malware
        audit("security", f"{f['file_id']}: re-scan completed: {sec['label']}" + (f" (was {old})" if old != sec["status"] else "")
              + (f"; isolated copy {sec['isolation']['path']}" if sec.get("isolation") else ""))
        changed.append({"file_id": f["file_id"], "before": old, "after": sec["status"]})
    save_files(ctx.id, ctx.files)
    return {"rescanned": len(changed), "results": changed, "engine": scanner.get_scan_engine() if scanner else None}
