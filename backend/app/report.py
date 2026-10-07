"""Machine-readable recovery report (schema from the project brief, plus provenance)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .engine.common import ANALYSIS_VERSION


def build_report(case: dict[str, Any], summary: dict[str, Any], files: list[dict[str, Any]],
                 audit: list[dict[str, Any]], model: dict[str, Any] | None) -> dict[str, Any]:
    s = summary
    recovered = []
    for f in files:
        recovered.append({
            "file_id": f["file_id"], "file_name": f["file_name"], "name_source": f["name_source"],
            "file_type": f["file_type"], "content_class": f["content_class"], "size_bytes": f["size_bytes"],
            "expected_size_bytes": f["expected_size"],
            "integrity_score": f["integrity_score"], "reconstruction_percentage": f["reconstruction_percentage"],
            "relationship_confidence": f["relationship_confidence"], "content_confidence": f["content_confidence"],
            # --- schema 1.1 additions (optional; absent in 1.0 reports) ---
            "input_type": f.get("input_type"),
            "validator_checks": f.get("validator_checks", []),
            "recon_confidence_reason": f.get("recon_confidence_reason") or f.get("reconstruction_basis"),
            "links_metric": f.get("links_metric"),
            "repaired_copy": ({k: v for k, v in f["repaired_copy"].items() if k != "checks"} if f.get("repaired_copy") else None),
            # --- schema 1.2 additions ---
            "security_analysis": {
                "scanner": (f.get("security") or {}).get("scanner"),
                "scanner_version": (f.get("security") or {}).get("scanner_version"),
                "simulated_engine": (f.get("security") or {}).get("simulated", False),
                "rule_engine": (f.get("security") or {}).get("yara_engine"),
                "sha256": (f.get("security") or {}).get("sha256"),
                "status": (f.get("security") or {}).get("status"),
                "detections": (f.get("security") or {}).get("detections", []),
                "yara_matches": (f.get("security") or {}).get("yara_matches", []),
                "static_indicators": ((f.get("security") or {}).get("static") or {}).get("indicators", []),
                "scan_timestamp": (f.get("security") or {}).get("scan_timestamp"),
                "executed": False,
                "explanation": (f.get("security") or {}).get("explanation"),
            },
            "export_validation": {
                "exported": (f.get("export") or {}).get("exported", False),
                "export_class": (f.get("export") or {}).get("class"),
                "label": (f.get("export") or {}).get("label"),
                "format_valid": (f.get("export") or {}).get("format_valid", False),
                "byte_identical": (f.get("export") or {}).get("byte_identical", False),
                "sha256": (f.get("export") or {}).get("export_sha256"),
                "reconstruction_sha256": (f.get("export") or {}).get("reconstruction_sha256"),
                "placeholder_ranges": (f.get("export") or {}).get("placeholders", []),
            },
            "priority_level": f["priority_level"], "priority_score": f["priority_score"], "priority_reason": f["priority_reason"],
            "recovery_status": f["recovery_status"], "status_reason": f["status_reason"],
            "fragment_relationships": [
                {"fragment_id": e["dst"] if e["src"].startswith("D") else e["src"], "related_to": e["dst"],
                 "relationship_type": e["type"], "confidence": e["confidence"], "label": e["label"],
                 "chosen": e["chosen"], "evidence": "; ".join(e["evidence"])}
                for e in f["fragment_relationships"]],
            "missing_ranges": [{"start": m["start"], "end": m["end"], "length": m["length"]} for m in f["missing_ranges"]],
            # schema 1.3: byte-exact map of which evidence bytes form which part of the artifact
            "reconstruction_map": [{"start": s["start"], "end": s["end"], "length": s["length"], "state": s["kind"],
                                    "fragment_id": s["fragment_id"], "source_offset": s["source_offset_hex"]} for s in f.get("segments", [])],
            "corrupted_ranges": [{"start": m["start"], "end": m["end"], "source_offset": m["source_offset_hex"], "reason": m["note"]}
                                 for m in f["corrupted_ranges"]],
            "metadata": f["metadata"],
            "validation": {"structural_validation": f["validation"]["structural_validation"],
                           "parser_validation": f["validation"]["parser_validation"],
                           "checksum_validation": f["validation"]["checksum_validation"],
                           "checks": f["validation"]["checks"]},
            "integrity_factors": f["integrity_factors"],
            "assessment": f["assessment"],
            "provenance": f["provenance"],
            "data_classes": {
                "observed": "Fragment bytes and offsets listed in provenance.fragments",
                "derived": "Hashes, entropy, validation results",
                "inferred": "Fragment relationships and their confidences",
                "reconstructed": f"Exported file {f['output_file']} (SHA-256 {f['output_sha256']}); missing ranges zero-filled",
                "unverified": "Any content in ranges without checksum verification" + ("; orphan preview rendered with borrowed tables" if f["orphan"] else ""),
            },
            "notes": f.get("notes", ""),
        })
    statuses = {}
    for f in files:
        statuses[f["recovery_status"]] = statuses.get(f["recovery_status"], 0) + 1
    rels = sum(1 for f in files for e in f["fragment_relationships"] if e["chosen"] and e["type"] in ("continuation", "structural"))
    avg_rel = (sum(e["confidence"] for f in files for e in f["fragment_relationships"] if e["chosen"] and e["type"] in ("continuation", "structural")) / rels) if rels else None
    corrupted = sum(len(f["corrupted_ranges"]) for f in files)
    return {
        "report_metadata": {
            "report_id": f"RL-{case['id']}", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema_version": "1.2",
            "schema_note": "1.1 adds input_type, validator_checks, status_reason, recon_confidence_reason, links_metric, "
                           "repaired_copy, coverage_reason and findings; 1.2 adds security_analysis and export_validation; all 1.0 fields are unchanged.",
            "analysis_version": ANALYSIS_VERSION, "mode": case["mode"],
            "mode_notice": {
                "demo": "DEMO: synthetic storage image with simulated damage. Results are genuine engine output on "
                        "synthetic evidence and are NOT forensic findings about real data.",
                "recyclebin": "REAL RECOVERY MODE: recovered from an actual Windows Recycle Bin record on the user's own machine. "
                              "The original bytes were copied read-only from the still-present $R data; nothing was simulated.",
            }.get(case["mode"], "REAL RECOVERY MODE: uploaded storage image analysed by a research prototype; "
                                "not equivalent to certified forensic software."),
            "evidence": {"name": case["image_name"], "sha256": case["image_sha256"], "size_bytes": case["image_size"],
                         "unchanged_after_analysis": bool(case.get("evidence_unchanged"))},
            "edge_model": {k: model.get(k) for k in ("type", "trained", "training")} if model else None,
        },
        "summary": {
            "storage_medium": s["storage_medium"], "storage_size_bytes": s["storage_size_bytes"],
            "total_sectors_scanned": s["total_sectors_scanned"],
            "potential_fragments_identified": s["potential_fragments_identified"],
            "candidate_files_identified": s["candidate_files_identified"], "files_reconstructed": s["files_reconstructed"],
            "fully_recovered": s["fully_recovered"], "mostly_recovered": s.get("mostly_recovered", 0),
            "partially_recovered": s["partially_recovered"], "fragment_only": s.get("fragment_only", 0),
            "uncertain": s["uncertain"], "unrecoverable": s["unrecoverable"],
            "unrecoverable_sectors": s["unrecoverable_sectors"],
            "unrecoverable_sectors_definition": s["unrecoverable_sectors_definition"],
            "overall_recovery_coverage": s["overall_recovery_coverage"], "key_challenges": s["key_challenges"],
            "coverage_reason": s.get("coverage_reason"),
            "assigned_coverage": s.get("assigned_coverage"), "assigned_coverage_reason": s.get("assigned_coverage_reason"),
            "input_type": s.get("input_type"), "findings": s.get("findings", []),
        },
        "recovered_files": recovered,
        "unrecoverable_sectors": s["unrecoverable_sectors"],
        "analysis_insights": {
            "fragment_relationship_analysis": (
                f"{rels} fragment link(s) accepted across {len(files)} candidate(s)"
                + (f", mean link confidence {avg_rel:.1f}%" if avg_rel else "")
                + ". Links anchored by container indexes (PDF xref, ZIP central directory, SQLite b-tree) are "
                  "deterministic; JPEG and text links are model-scored and labelled accordingly."),
            "corruption_analysis": (
                f"{corrupted} corrupted range(s) detected by validators. Formats without checksums (PDF text streams, "
                "logs, JPEG scan data) can contain silent corruption that no validator can detect."),
            "reconstruction_analysis": ", ".join(f"{k.replace('_', ' ').lower()}: {v}" for k, v in sorted(statuses.items())) + ".",
            "realistic_recovery_assessment": (
                f"{s['unrecoverable_sectors']:,} sector(s) referenced by recovered structures were not located and are most "
                "likely overwritten. Overwritten data cannot be recovered; missing ranges are reported, never synthesized."),
            "recommendations": [
                "Review CRITICAL/HIGH items first; priority reflects configured criteria, not proven importance.",
                "Treat UNCERTAIN and orphan items as leads that require manual verification.",
                "Verify exported files against the listed SHA-256 values before use; missing ranges are zero-filled.",
                "Keep the original image write-protected; re-hash it to confirm SHA-256 " + (case["image_sha256"] or "")[:16] + "...",
            ],
        },
        "audit_log": [{"ts": a["ts"], "stage": a["stage"], "message": a["message"]} for a in audit],
    }
