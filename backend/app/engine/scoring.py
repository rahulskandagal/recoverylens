"""Explainable scoring. Four separate measures, never collapsed into one number:

  reconstruction %        how much of the *expected* structure was located
  integrity (0-100)       how structurally valid / internally consistent the result is
  relationship confidence how strongly evidence supports that the fragments belong together
  content confidence      how sure we are about the technical type classification

Each score is a sum of itemised factors that the UI shows verbatim.
"""
from __future__ import annotations

import math
from typing import Any

from .common import Candidate, clamp
from .validators import validate

CONTENT_CLASS = {"jpeg": "Image", "png": "Image", "gif": "Image", "pdf": "Document", "docx": "Document",
                 "xlsx": "Document", "txt": "Document", "zip": "Archive", "sqlite": "Database", "log": "Log",
                 "mp4": "Video", "mp3": "Audio"}
MIME = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "zip": "application/zip",
        "sqlite": "application/vnd.sqlite3", "log": "text/plain", "txt": "text/plain"}


def run_validation(c: Candidate, header: dict[str, Any] | None) -> dict[str, Any]:
    missing = [(m["start"], m["end"]) for m in c.missing_ranges()]
    kw: dict[str, Any] = {"missing": missing}
    if c.type == "jpeg":
        kw["header"] = header
        if c.orphan and c.structure.get("transplant_bytes"):
            v = validate("jpeg", c.structure["transplant_bytes"], header=None)
            v["checks"].insert(0, {"name": "Own decoding tables", "status": "fail", "category": "structural",
                                   "detail": "No header: rendering below uses tables borrowed from "
                                             f"{c.structure['transplant_donor']} and is UNVERIFIED"})
            return v
        if c.orphan:
            return {"checks": [{"name": "JPEG header", "status": "fail", "category": "structural",
                                "detail": "Entropy-coded data without SOI/DQT/DHT/SOF - not decodable"},
                               {"name": "Restart markers", "status": "partial", "category": "checksum",
                                "detail": f"{c.structure.get('rst_markers', 0)} restart markers identify this as baseline JPEG scan data"}],
                    "structural": "fail", "parser": "fail", "checksum": "partial", "stats": {}}
    if c.type == "pdf":
        kw["xref"] = {int(k): v for k, v in (c.structure.get("xref") or {}).items()}
    if c.type == "sqlite":
        if c.orphan:
            return {"checks": [{"name": "Database header", "status": "fail", "category": "structural",
                                "detail": "Pages without their database header"}], "structural": "fail",
                    "parser": "fail", "checksum": "not_available", "stats": {}}
        kw["page_status"] = {int(k): v for k, v in c.structure.get("page_status", {}).items()}
    return validate(c.type if not (c.orphan and c.type == "pdf") else "txt", c.data, **kw)


TERMINATORS = ("EOI marker", "%%EOF trailer marker", "End of central directory", "IEND chunk", "Header page size / count")


def reconstruction_pct(c: Candidate, v: dict[str, Any] | None = None) -> tuple[float | None, str]:
    """Share of the expected structure that was located. Never an unexplained blank."""
    if c.structure.get("single_file"):
        return 100.0, (f"Contiguous: single-file input, all {len(c.data):,} bytes are present in one unbroken block "
                       "(reconstruction was not needed; damage inside the file is measured by integrity)")
    if c.type == "jpeg" and c.structure.get("rows_total") and c.structure.get("rows_recovered") is not None:
        return 100.0 * c.structure["rows_recovered"] / c.structure["rows_total"], \
            f"{c.structure['rows_recovered']} of {c.structure['rows_total']} MCU rows backed by recovered data (restart-marker accounting)"
    if c.expected_size:
        rec = min(c.recovered_bytes(), c.expected_size)
        return 100.0 * rec / c.expected_size, f"{rec:,} of {c.expected_size:,} expected bytes located ({c.expected_size_source})"
    term = next((x for x in (v or {}).get("checks", []) if x["name"] in TERMINATORS and x["status"] == "pass"), None)
    if len(c.fragments) == 1 and c.header_found and term and not c.missing_ranges():
        return 100.0, f"Contiguous: one fragment bounded by its header and {term['name']}; no gaps to account for"
    if c.orphan:
        return None, "N/A – orphan fragments: the original file's size and layout are unknown"
    return None, "N/A – the format stores no size/index reference and no terminator bounds the data (e.g. logs, plain text)"


def _checksum_ratio(c: Candidate, v: dict[str, Any]) -> tuple[float | None, str]:
    st = v.get("stats", {})
    if c.type in ("docx", "zip", "xlsx") and st.get("members"):
        ok = sum(1 for m in st["members"] if m["status"] == "ok")
        return ok / len(st["members"]), f"{ok}/{len(st['members'])} members match CRC-32"
    if c.type == "pdf" and st.get("objects"):
        det = st.get("objects_detail", [])
        present = [o for o in det if o["status"] in ("ok", "corrupted")]
        ok = sum(1 for o in present if o.get("reason") != "object header not at xref offset")
        return (ok / len(det)) if det else None, f"{ok}/{len(det)} xref-indexed objects present at their exact offsets"
    if c.type == "sqlite" and c.structure.get("page_status"):
        ps = c.structure["page_status"]
        ok = sum(1 for x in ps.values() if x == "ok")
        return ok / len(ps), f"{ok}/{len(ps)} pages carry a valid b-tree header"
    if c.type == "jpeg" and "rst_found" in st and c.structure.get("rows_total"):
        exp = max(1, c.structure["rows_total"] - 1)
        good = max(0, st["rst_found"] - st.get("rst_breaks", 0))
        return min(1.0, good / exp), f"{good}/{exp} restart markers in unbroken sequence"
    return None, "Format carries no checksum/index usable for verification"


def integrity(c: Candidate, v: dict[str, Any], recon: float | None) -> tuple[float, list[dict[str, Any]]]:
    f: list[dict[str, Any]] = []
    checks = v.get("checks", [])
    if v.get("weighted"):
        # format validator with documented weights (PDF): integrity = weighted mean of its checks
        for x in checks:
            f.append({"factor": x["name"], "points": x["points"] if x["points"] is not None else 0.0,
                      "max": x["weight"] if x["status"] != "na" else 0, "detail": x["evidence"],
                      "status": x["status"], "weight": x["weight"]})
        return float(v["integrity"]), f

    def add(name: str, pts: float, mx: float, detail: str) -> None:
        f.append({"factor": name, "points": round(pts, 1), "max": mx, "detail": detail})

    first = checks[0] if checks else {"status": "fail", "detail": ""}
    add("Header / signature", 10 if c.header_found and first["status"] == "pass" else 0, 10,
        first["detail"] if c.header_found else "File header not recovered")
    term_names = {"jpeg": "EOI marker", "pdf": "%%EOF trailer marker", "docx": "End of central directory",
                  "zip": "End of central directory", "xlsx": "End of central directory", "sqlite": "Header page size / count"}
    tn = term_names.get(c.type)
    tc = next((x for x in checks if x["name"] == tn), None) if tn else None
    if tc:
        add("Terminator / trailer", 8 if tc["status"] == "pass" else 4 if tc["status"] == "partial" else 0, 8, tc["detail"])
    else:
        add("Terminator / trailer", 4, 8, "Format defines no terminator; neutral score")
    pv = v.get("parser", "fail")
    add("Parser validation", {"pass": 25, "partial": 12, "fail": 0}.get(pv, 8), 25,
        "; ".join(x["detail"] for x in checks if x["category"] == "parser")[:240] or "No parser available")
    ratio, why = _checksum_ratio(c, v)
    if ratio is None:
        add("Checksum / index consistency", 5, 15, why)
    else:
        add("Checksum / index consistency", 15 * ratio, 15, why)
    if recon is None:
        add("Expected content present", 15, 30, "Completeness unknown (no size reference); neutral score")
    else:
        add("Expected content present", 30 * recon / 100, 30, f"{recon:.1f}% of expected structure located")
    nm = len(c.missing_ranges())
    add("Continuity", 12 * max(0.0, 1 - 0.25 * nm), 12, "No gaps" if nm == 0 else f"{nm} missing range(s) break continuity")
    nc = len(c.corrupted_ranges())
    if nc:
        add("Corrupted ranges", -min(20, 4 * nc), 0, f"{nc} range(s) fail validation")
    if c.conflicts:
        add("Conflicting fragments", -min(10, 5 * len(c.conflicts)), 0, c.conflicts[0])
    if c.orphan:
        add("Orphan (no header)", -10, 0, "Fragments could not be attached to a file header")
    total = clamp(sum(x["points"] for x in f))
    return round(total, 1), f


def relationship(c: Candidate) -> tuple[float, dict[str, Any]]:
    internal = [e for e in c.edges if e.chosen and e.type in ("continuation", "structural")]
    meta = [e for e in c.edges if e.type == "metadata"]
    comp: dict[str, Any] = {}
    if c.structure.get("single_file"):
        comp.update(signature_compatibility=100.0 if c.header_found else 0.0, metadata_correlation=None,
                    conflicting_evidence=0.0, basis="Single uploaded file: its bytes are one contiguous block, so no fragment linking is inferred")
        return 100.0, comp
    if internal:
        logp = sum(math.log(max(e.confidence, 1e-6)) for e in internal) / len(internal)
        conf = math.exp(logp)
        comp["sequence_compatibility"] = round(100 * sum(e.features.get("struct_order", 0.5) for e in internal) / len(internal), 1)
        comp["structural_verification"] = round(100 * sum(1 for e in internal if e.features.get("index_verified", 0) >= 1) / len(internal), 1)
        comp["boundary_continuity"] = round(100 * sum(e.features.get("boundary_smooth", 0.5) for e in internal) / len(internal), 1)
        basis = f"Geometric mean of {len(internal)} fragment-link probabilities from the edge model"
    elif c.orphan:
        conf = 0.3
        basis = "Single orphan fragment: no linking evidence"
    else:
        conf = 0.97 if not c.missing_ranges() else 0.9
        basis = "Single contiguous fragment: no inter-fragment inference required"
    comp["signature_compatibility"] = 100.0 if c.header_found else 0.0
    comp["metadata_correlation"] = round(100 * max(e.confidence for e in meta), 1) if meta else None
    conf *= max(0.5, 1 - 0.1 * len(c.conflicts))
    comp["conflicting_evidence"] = round(min(100.0, 10.0 * len(c.conflicts)), 1)
    comp["basis"] = basis
    return round(100 * conf, 1), comp


def content_confidence(c: Candidate, v: dict[str, Any]) -> tuple[float, list[str]]:
    why = []
    s = 0.0
    if c.header_found:
        s += 45
        why.append(f"+45 file signature identifies {c.type.upper()}")
    else:
        s += 25
        why.append(f"+25 content structure typical of {c.type.upper()} (no signature)")
    pv = v.get("parser")
    pts = {"pass": 40, "partial": 25}.get(pv, 5)
    s += pts
    why.append(f"+{pts} parser validation: {pv}")
    de = c.metadata.get("directory_entry")
    if de:
        ext = de["name"].rsplit(".", 1)[-1].lower()
        agree = ext in {"jpeg": ("jpg", "jpeg"), "pdf": ("pdf",), "docx": ("docx",), "sqlite": ("db", "sqlite"),
                        "log": ("log",), "txt": ("txt",), "zip": ("zip",), "xlsx": ("xlsx",)}.get(c.type, ())
        s += 15 if agree else 0
        why.append("+15 directory-entry extension agrees" if agree else "+0 directory-entry extension disagrees")
    else:
        s += 7
        why.append("+7 no directory metadata to cross-check")
    return round(min(100.0, s), 1), why


def checks_digest(v: dict[str, Any]) -> str:
    """Compact list of the validator checks that decided a status."""
    checks = [x for x in v.get("checks", []) if x.get("category") != "advisory"]
    bad = [x for x in checks if x["status"] in ("fail", "partial")]
    good = [x["name"] for x in checks if x["status"] == "pass"]
    parts = [f"{x['name']}: {x['detail'].split(';')[0]}" for x in bad[:3]]
    if good:
        parts.append(f"{len(good)}/{len(checks)} checks pass")
    return "; ".join(parts)


def links_metric(c: Candidate, v: dict[str, Any], rel: float) -> dict[str, Any]:
    """What the 'Links' figure means for this candidate, stated explicitly."""
    if c.type == "pdf":
        ref = next((x for x in v.get("checks", []) if x.get("id") == "references"), None)
        if ref and ref["status"] != "na":
            return {"value": round(100 * ref["score"], 1), "kind": "internal_references",
                    "label": "Links (internal refs)", "reason": ref["evidence"]}
    if c.structure.get("single_file"):
        return {"value": None, "kind": "not_applicable", "label": "Links",
                "reason": f"N/A – single contiguous file; {c.type.upper()} has no internal reference table that is checked here"}
    return {"value": rel, "kind": "fragment_relationships", "label": "Links (fragments)",
            "reason": "Confidence that this candidate's fragments belong together (edge model + structural anchors)"}


def status(c: Candidate, v: dict[str, Any], recon: float | None, integ: float, rel: float) -> tuple[str, str]:
    st, why = _status(c, v, recon, integ, rel)
    digest = checks_digest(v)
    if digest and not v.get("status_hint"):
        why = f"{why}. Checks: {digest}"
    return st, why


def _status(c: Candidate, v: dict[str, Any], recon: float | None, integ: float, rel: float) -> tuple[str, str]:
    if c.structure.get("unstructured"):
        return "UNCERTAIN", ("Unstructured data: no file format could be identified, so completeness and validity cannot be "
                             "assessed; kept as evidence because the security sweep flagged it")
    if v.get("status_hint") and not c.orphan:
        st, why = v["status_hint"]
        if recon is not None and recon < 99.5 and st == "FULLY_RECOVERED":
            st = "MOSTLY_RECOVERED"
        return st, why
    corrupted = bool(c.corrupted_ranges())
    if c.orphan:
        if recon is not None and recon < 20 or integ < 30:
            return "UNRECOVERABLE", "Only headerless fragments survive; the data cannot be decoded or validated as a file"
        return "FRAGMENT_ONLY", "Fragments are identifiable but not attached to a recoverable file structure"
    if recon is None:
        return "UNCERTAIN", "Completeness cannot be measured and fragment order is inferred, not verified"
    if recon >= 99.5 and v.get("parser") == "pass" and not corrupted and not c.conflicts:
        return "FULLY_RECOVERED", "All expected structure located and every validator passes"
    if rel < 60:
        return "UNCERTAIN", f"Relationship confidence {rel:.0f}% is too low to assert the assembly"
    if recon < 20:
        return "UNRECOVERABLE", f"Only {recon:.0f}% of the expected structure was located"
    if recon >= 85 and integ >= 70:
        return "MOSTLY_RECOVERED", f"{recon:.0f}% located with integrity {integ:.0f}/100"
    if recon >= 40:
        return "PARTIALLY_RECOVERED", f"{recon:.0f}% located; gaps or damage limit usability"
    if c.type == "docx" and v.get("stats", {}).get("text_verified"):
        return "PARTIALLY_RECOVERED", (f"Only {recon:.0f}% of bytes located, but every required OOXML part including "
                                       "word/document.xml passes CRC-32: the document text is fully recovered; missing bytes belong to other members")
    return "FRAGMENT_ONLY", f"Only {recon:.0f}% located"


def assessment(c: Candidate, v: dict[str, Any], recon: float | None, recon_why: str) -> dict[str, Any]:
    missing = c.missing_ranges()
    corrupted = c.corrupted_ranges()
    mbytes = sum(m["length"] for m in missing)
    alts = [e for e in c.edges if not e.chosen and e.type == "continuation" and e.confidence >= 0.5]
    if not missing and not c.orphan:
        fr, why = "NO", "Nothing is missing according to the file's own structure; further recovery is not needed."
    elif alts:
        fr, why = "YES", (f"{len(alts)} candidate link(s) scored 50-70%: plausible but below the acceptance threshold "
                          f"(e.g. {alts[0].src} -> {alts[0].dst}, {alts[0].confidence:.0%}); an analyst could test them.")
    elif c.remaining_compatible > 0:
        fr, why = "UNCERTAIN", (f"{c.remaining_compatible} unassigned fragment(s) of a compatible type remain, but none "
                                "passed the continuity/verification tests. The missing ranges may be overwritten or stored elsewhere.")
    else:
        fr, why = "NO", ("No unassigned fragments of a compatible type remain; the missing ranges were most likely "
                         "overwritten. Overwritten bytes cannot be recovered by any software.")
    exp = c.expected_size
    return {
        "exists": recon_why if recon is not None else f"{c.recovered_bytes():,} bytes recovered; completeness not estimable.",
        "missing": (f"{len(missing)} range(s) totalling {mbytes:,} bytes" + (f" (~{100 * mbytes / exp:.1f}% of the expected structure)" if exp else "")
                    + " were not located.") if missing else "No missing ranges detected.",
        "corrupted": (f"{len(corrupted)} recovered range(s) fail validation: " + "; ".join(sorted({x['note'] for x in corrupted}))[:300])
        if corrupted else "No recovered range fails validation (silent corruption without checksums cannot be ruled out).",
        "further_recovery": fr, "reason": why,
        "recovered_pct": None if recon is None else round(recon, 1),
        "missing_pct": None if recon is None else round(100 - recon, 1),
    }
