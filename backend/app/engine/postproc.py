"""Single-file mode, naming, PDF repair output, findings and the storage map."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .common import CLUSTER, Candidate, Segment, sha256
from .pdfcheck import check_pdf, pdf_status, repair_pdf
from .scanner import EvidenceImage, ScanResult, parse_jpeg_header

EXT = {"jpeg": "jpg", "pdf": "pdf", "docx": "docx", "xlsx": "xlsx", "pptx": "pptx", "zip": "zip", "sqlite": "db",
       "log": "log", "txt": "txt", "png": "png", "gif": "gif", "mp4": "mp4"}
CLS_CODE = {"zero": 0, "high_entropy": 1, "text": 2, "log_text": 3, "pdf_text": 4, "jpeg_header": 5, "jpeg_data": 6,
            "sqlite_page": 7, "zip_struct": 8, "xml_text": 9, "fat_dir": 10, "binary": 11}


def signature_stub(data: bytes, ftype: str) -> str | None:
    """A file signature followed by random-looking bytes and none of the format's mandatory structure.

    That is not a damaged file: there is no content to reconstruct. Returns the evidence sentence,
    or None when the bytes carry real format structure (or too little data to judge).
    """
    from .common import entropy
    body = data[64:64 + 16384]  # the bytes right after the signature, where mandatory structure must start
    if len(body) < 2048:
        return None
    e = entropy(body)
    if e < 7.9:
        return None
    head = data[:1 << 20]
    missing = None
    if ftype == "pdf" and not re.search(rb"\d+\s+\d+\s+obj\b", head) and b"xref" not in head:
        missing = "no PDF objects, no cross-reference table"
    elif ftype == "jpeg":
        p, markers = 2, set()
        while p + 4 <= min(len(data), 65536) and data[p] == 0xFF:
            mk = data[p + 1]
            markers.add(mk)
            if mk == 0xDA:
                break
            p += 2 + int.from_bytes(data[p + 2:p + 4], "big")
        need = {0xDB: "DQT", 0xC4: "DHT"}
        absent = [n for m, n in need.items() if m not in markers] + ([] if markers & {0xC0, 0xC1, 0xC2} else ["SOF"])
        if absent:
            missing = f"no {', '.join(absent)} segment(s): the image cannot be decoded"
    elif ftype in ("zip", "docx", "xlsx", "pptx"):
        if b"PK\x01\x02" not in data and b"PK\x05\x06" not in data:
            nlen = int.from_bytes(data[26:28], "little") if len(data) > 30 else 0
            name = data[30:30 + min(nlen, 64)]
            if not name or not all(32 <= c < 127 for c in name):
                missing = "no central directory and the local header's member name is not valid text"
    elif ftype == "png" and data[12:16] != b"IHDR":
        missing = "no IHDR chunk after the signature"
    if not missing:
        return None
    return (f"only the {ftype.upper()} signature is present; the following bytes are random-looking data "
            f"(entropy {e:.2f} bits/byte) with {missing}. There is no file content to reconstruct: "
            "this is a signature stub, not a damaged file")


def _stem(name: str) -> str:
    base = Path(name).name
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "recovered"


def single_file_candidate(img: EvidenceImage, res: ScanResult, info: dict[str, Any], filename: str) -> Candidate:
    ftype = info["format"]
    frags = [f for f in res.fragments if f.family != "zero"]
    c = Candidate(
        id="R001", type=ftype, name=filename, name_source="original filename of the uploaded file",
        fragments=[f.id for f in frags],
        segments=[Segment(0, img.size, "recovered", frags[0].id if frags else None, 0, "single-file input (contiguous)")],
        expected_size=img.size, expected_size_source="single-file input: file length",
        data=img.data, structure={"single_file": True, "original_filename": filename},
        operations=[f"Input identified as a single {info['label'].split('– ')[-1]} file ({'; '.join(info['evidence'])})",
                    "Carving skipped: validated/repaired as one contiguous file"],
    )
    if ftype == "jpeg":
        c.structure["header"] = parse_jpeg_header(img.data[:65536])
    return c


def validator_damage(c: Candidate, v: dict[str, Any]) -> list[tuple[int, int, str]]:
    """Byte ranges that a validator proved damaged (single-file mode: logical == physical)."""
    out = list(v.get("damaged_ranges") or [])
    st = v.get("stats", {})
    for p in st.get("marker_violations", []) or []:
        out.append((p, p + 2, "illegal marker sequence in JPEG scan data"))
    for m in st.get("members", []) or []:
        if m.get("status") == "corrupted":
            out.append((m["data_start"], m["data_end"], f"{m['name']}: CRC-32 mismatch"))
    return [(max(0, a), min(len(c.data), b), n) for a, b, n in out if b > a]


def final_name(c: Candidate, v: dict[str, Any]) -> tuple[str, str]:
    """<best available name>__rec<NNNN>.<ext>, keeping the numeric candidate ID as a suffix."""
    n = int(c.id[1:]) - 1
    ext = "bin" if c.orphan and c.type == "jpeg" else EXT.get(c.type, "bin")
    src = c.name_source
    stem = _stem(c.name)
    title = (v.get("stats", {}).get("info") or {}).get("Title") or (v.get("stats", {}).get("core") or {}).get("title")
    if c.structure.get("single_file"):
        stem, src = _stem(c.structure["original_filename"]), "original filename of the uploaded file"
    elif src.startswith("generated") and title:
        stem, src = _stem(title), "embedded document title"
    return f"{stem}__rec{n:04d}.{ext}", src + " (+ candidate ID suffix)"


def make_repaired_copy(c: Candidate, v: dict[str, Any], out_dir: Path, log: Callable[[str, str], None],
                       carved_sha: str) -> dict[str, Any] | None:
    rep = repair_pdf(c.data, v["pdf_report"])
    if not rep:
        log("repair", f"{c.id}: no intact objects to rebuild a repaired copy from")
        return None
    data, meta = rep
    r2 = check_pdf(data, render=True)
    st, why = pdf_status(r2, complete_bytes=True)
    name = c.name.rsplit(".", 1)[0] + ".repaired.pdf"
    (out_dir / "reconstructed" / name).write_bytes(data)
    h = sha256(data)
    log("repair", f"{c.id}: repaired copy {name} written next to the untouched carved copy; carved SHA-256 {carved_sha}; "
                  f"repaired SHA-256 {h}; {meta['method']}"
        + (f"; synthesized structural objects: {', '.join(meta['synthesized_objects'])}" if meta["synthesized_objects"] else ""))
    return {"file": name, "sha256": h, "carved_sha256": carved_sha, "size_bytes": len(data), **meta,
            "integrity_score": r2.integrity, "status": st, "status_reason": why,
            "render_pages": r2.stats["render_pages"], "render_clean": r2.stats["render_clean"],
            "checks": [{k: x[k] for k in ("id", "name", "status", "weight", "score", "detail")} for x in r2.checks],
            "note": "Derived output. The original evidence and the carved copy are unchanged; only the listed structural objects were added."}


# ------------------------------------------------------------------------------------------
# export decision: what may be exported, under which label
# ------------------------------------------------------------------------------------------

def validation_state(c: Candidate, v: dict[str, Any]) -> tuple[str, str]:
    """validated | partial | failed, decided by the format validator (never by the ML model)."""
    if c.missing_ranges():
        n = len(c.missing_ranges())
        return "failed", f"{n} byte range(s) of the file were not located; a complete file cannot be produced without placeholders"
    if c.orphan or c.structure.get("unstructured"):
        return "failed", "no file header/format structure identified for these bytes"
    hint = v.get("status_hint")
    if hint:
        st = hint[0]
        if st == "FULLY_RECOVERED":
            return "validated", "every format check passed"
        if st in ("MOSTLY_RECOVERED", "PARTIALLY_RECOVERED"):
            return "partial", hint[1]
        return "failed", hint[1]
    s, p, k = v.get("structural"), v.get("parser"), v.get("checksum")
    if "fail" in (s, p) or s in (None, "not_validated"):
        return "failed", "structural or parser validation failed" if s != "not_validated" else "no validator available for this data"
    if s == "pass" and p == "pass" and k in ("pass", "not_available") and not c.corrupted_ranges():
        return "validated", "every format check passed"
    return "partial", "some checks pass only partially" + ("; byte ranges fail validation" if c.corrupted_ranges() else "")


LABELS = {
    "validated_file": ("VALIDATED RECOVERED FILE", None),
    "partially_validated_file": ("PARTIALLY VALIDATED", "Export contains recovered bytes but structural validation is incomplete."),
    "forensic_artifact": ("FORENSIC BYTE ARTIFACT – NOT A VERIFIED RECOVERED FILE",
                          "This artifact contains recovered bytes but does not pass file-format validation. "
                          "It should not be treated as a verified reconstructed file."),
}


def write_export(c: Candidate, v: dict[str, Any], security: dict[str, Any], out_dir: Path,
                 log: Callable[[str, str], None]) -> dict[str, Any]:
    """Write the export artifact from the exact reconstruction buffer and prove byte identity."""
    vstate, vwhy = validation_state(c, v)
    cls = {"validated": "validated_file", "partial": "partially_validated_file"}.get(vstate, "forensic_artifact")
    label, warning = LABELS[cls]
    stem, _, ext = c.name.rpartition(".")
    fname = c.name if cls != "forensic_artifact" else f"{stem}.FORENSIC-ARTIFACT.{ext}"
    buf = bytes(c.data)  # the reconstruction buffer, kept as bytes end-to-end
    recon_sha = sha256(buf)
    missing = c.missing_ranges()
    placeholders = [{"start": m["start"], "end": m["end"], "length": m["length"],
                     "filled_with": "0x00 placeholder bytes (NOT recovered data)"} for m in missing]
    observed = b"".join(buf[s.start:s.start + s.length] for s in c.segments if s.kind != "missing")
    path = out_dir / "reconstructed" / fname
    try:
        with open(path, "wb") as fh:  # binary mode: no encoding or newline translation
            fh.write(buf)
        with open(path, "rb") as fh:
            export_sha = sha256(fh.read())
        exported, error = True, None
    except OSError as e:
        export_sha, exported, error = None, False, f"EXPORT FAILED: {e}"
    identical = export_sha == recon_sha
    if missing:
        (out_dir / "reconstructed" / f"{fname}.manifest.json").write_text(json.dumps({
            "notice": "FORENSIC RECONSTRUCTION ARTIFACT – NOT A VERIFIED RECOVERED FILE",
            "placeholder_ranges": placeholders, "reconstruction_sha256": recon_sha,
            "observed_bytes": len(observed), "observed_sha256": sha256(observed)}, indent=2))
    malware = security.get("status") == "MALWARE_DETECTED"
    log("export", f"{c.id}: validation {vstate.upper()} ({vwhy[:140]}) -> {label}; file {fname}")
    log("export", f"{c.id}: reconstruction SHA-256 {recon_sha}; export file SHA-256 {export_sha}; "
                  f"status {'BYTE-IDENTICAL' if identical else 'MISMATCH'}")
    if cls == "forensic_artifact":
        log("export", f"{c.id}: export blocked as verified reconstruction; forensic byte artifact export available")
    if malware:
        log("security", f"{c.id}: artifact marked as unsafe; automatic execution/opening disabled; export requires explicit authorization")
    return {
        "class": cls, "label": label, "warning": warning, "validation_state": vstate, "validation_reason": vwhy,
        "file": fname, "reconstruction_sha256": recon_sha, "export_sha256": export_sha, "byte_identical": identical,
        "exported": exported, "error": error, "format_valid": vstate == "validated",
        "placeholders": placeholders, "observed_bytes": len(observed), "observed_sha256": sha256(observed),
        "normal_export_enabled": exported and not malware,
        "authorization_required": malware,
    }


def failed_file(c: Candidate, err: Exception, input_kind: str) -> dict[str, Any]:
    """Record for a candidate whose processing crashed: reported, never hidden."""
    msg = f"RECONSTRUCTION FAILED: {type(err).__name__}: {err}"
    return {
        "file_id": c.id, "file_name": c.name, "name_source": c.name_source, "file_type": c.type, "mime": "application/octet-stream",
        "content_class": "Other", "size_bytes": len(c.data), "expected_size": c.expected_size, "expected_size_source": c.expected_size_source,
        "integrity_score": 0.0, "integrity_factors": [], "reconstruction_percentage": None, "reconstruction_basis": msg,
        "recon_confidence_reason": msg, "relationship_confidence": 0.0, "relationship_components": {"basis": msg},
        "links_metric": {"value": None, "kind": "not_applicable", "label": "Links", "reason": msg},
        "validator_checks": [], "repaired_copy": None, "input_type": input_kind, "content_confidence": 0.0,
        "content_confidence_factors": [], "recovery_status": "UNCERTAIN", "status_reason": msg, "error": msg,
        "fragments": c.fragments, "fragment_details": [], "fragment_relationships": [], "missing_ranges": [], "corrupted_ranges": [],
        "segments": [], "conflicts": [], "metadata": {}, "modified": None,
        "validation": {"structural_validation": "not_validated", "parser_validation": "not_validated", "checksum_validation": "not_available", "checks": [], "stats": {}},
        "structure": {}, "assessment": {"exists": msg, "missing": "unknown", "corrupted": "unknown", "further_recovery": "UNCERTAIN",
                                        "reason": msg, "recovered_pct": None, "missing_pct": None},
        "preview": None, "orphan": c.orphan, "header_found": c.header_found, "search_text": "", "embedded": [],
        "output_file": None, "output_sha256": None, "provenance": {"source_image": "", "source_sha256": "", "fragments": [], "operations": [msg],
                                                                   "analysis_version": "", "model": "", "timestamp": "", "output_note": ""},
        "security": {"status": "NOT_SCANNED", "label": "NOT SCANNED", "explanation": "not scanned: processing failed", "executed": False},
        "export": None, "export_validation": {"exported": False, "format_valid": False, "byte_identical": False, "sha256": None},
        "notes": msg,
    }


def sweep_unstructured(ctx, mode: str, log: Callable[[str, str], None]) -> dict[str, Any]:
    """Unassigned fragments are not assumed safe: static rules + characteristics on each one;
    flagged fragments become 'unstructured artifact' candidates so they get a full security record."""
    from .security import rule_scan, static_profile
    scanned = flagged = 0
    for fr in ctx.scan.fragments:
        if fr.family in ("zero", "fat_dir") or not ctx.free(fr):
            continue
        data = ctx.img.frag_bytes(fr)
        scanned += 1
        rules = [r for r in rule_scan(data) if r["severity"] in ("suspicious", "test")]
        exe = [i for i in static_profile(data, "bin")["indicators"] if i["severity"] == "suspicious"]
        if not rules and not exe:
            continue
        flagged += 1
        cid = ctx.next_id()
        is_text = fr.family in ("text", "log", "xml")
        c = Candidate(
            id=cid, type="txt" if is_text else "bin", name=f"unknown_{'text' if is_text else 'binary'}_{fr.offset:08X}.{'txt' if is_text else 'bin'}",
            name_source="generated (unstructured fragment, no name evidence)", fragments=[fr.id],
            segments=[Segment(0, fr.length, "recovered", fr.id, fr.offset, "unstructured fragment")],
            expected_size=None, expected_size_source="unknown (no file structure identified)",
            data=data if is_text else data, structure={"unstructured": True, "reason": "flagged by the security sweep of unassigned fragments"},
            orphan=True, header_found=False,
            operations=[f"Unassigned fragment {fr.id} @ 0x{fr.offset:08X} flagged by static security sweep: "
                        + ", ".join([r["rule"] for r in rules] + [i["text"] for i in exe])[:200]],
        )
        if is_text:
            c.data = data.rstrip(b"\x00")
            c.segments = [Segment(0, len(c.data), "recovered", fr.id, fr.offset, "unstructured fragment")]
        ctx.candidates.append(c)
        ctx.claim(cid, [fr])
        log("security", f"Unassigned fragment {fr.id} @ 0x{fr.offset:08X} flagged ({', '.join(r['rule'] for r in rules) or exe[0]['text']}); "
                        f"promoted to unstructured artifact {cid}")
    log("security", f"Security sweep of {scanned} unassigned fragment(s): {flagged} flagged")
    return {"scanned": scanned, "flagged": flagged}


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


def build_findings(files: list[dict[str, Any]], res: ScanResult, input_type: dict[str, Any],
                   unrec_bytes: int, unclassified: int, extra_stubs: list[str] | None = None,
                   img: EvidenceImage | None = None) -> list[dict[str, str]]:
    out = _build_findings(files, res, input_type, unrec_bytes, unclassified)
    stubs = [f for f in files if f.get("signature_stub")]
    if stubs:
        # "every declared range located" and checksum caveats describe real content; stubs declare nothing
        real = [f for f in files if not f.get("signature_stub")]
        out = [x for x in out if not (x["text"].startswith("Every byte range") and len(real) <= sum(1 for f in real if f["orphan"]))]
        if not any(f["file_type"] == "pdf" for f in real):
            out = [x for x in out if not x["text"].startswith("PDF content has no checksum")]
    if stubs or extra_stubs:
        n = len(stubs) + len(extra_stubs or [])
        out.insert(1, {"level": "warning", "text": f"{_plural(n, 'file signature')} followed only by random-looking data: "
                                                   "signature stubs, not damaged files. Nothing beyond the signature exists, so "
                                                   "there is no content to recover (reported as unrecoverable, never invented)"})
        for f in stubs[:4]:
            out.insert(2, {"level": "info", "text": f"{f['file_name']}: {f['signature_stub']}"})
        for s in (extra_stubs or [])[:3]:
            out.insert(2, {"level": "info", "text": s})
    if img is not None and res.stats.get("duplicate_fragments"):
        dups = [fr for fr in res.fragments if fr.duplicate_of]
        fill = [fr for fr in dups if max(c.entropy for c in fr.clusters) < 2.0]
        if dups and len(fill) >= 0.8 * len(dups):
            sample = img.frag_bytes(fill[0])[:8]
            clusters = sum(fr.n_clusters for fr in fill)
            for i, x in enumerate(out):
                if "duplicate fragment" in x["text"]:
                    out[i] = {"level": "info", "text": f"{_plural(clusters, 'cluster')} repeat one low-entropy fill pattern "
                                                       f"({' '.join(f'{b:02X}' for b in sample)} …): wiped or filled space, not file data"}
    return out


def _build_findings(files: list[dict[str, Any]], res: ScanResult, input_type: dict[str, Any],
                    unrec_bytes: int, unclassified: int) -> list[dict[str, str]]:
    """Case-specific findings. Warnings only appear when their count is > 0."""
    out: list[dict[str, str]] = [{"level": "info", "text": f"Input identified as {input_type['label']} ({input_type['evidence'][0]})"}]
    if input_type["kind"] == "single_file" and files:
        f = files[0]
        chk = f["validator_checks"]
        passed = sum(1 for x in chk if x["status"] == "pass")
        scored = sum(1 for x in chk if x["status"] != "na" and x.get("category") != "advisory")
        lvl = "success" if f["recovery_status"] in ("FULLY_RECOVERED", "MOSTLY_RECOVERED") else "warning"
        out.append({"level": lvl, "text": f"{f['file_name']}: {f['recovery_status'].replace('_', ' ').lower()} — "
                                          f"{passed}/{scored} validator checks pass, integrity {f['integrity_score']:.0f}/100"})
        bad = [x for x in chk if x["status"] in ("fail", "partial")]
        for x in bad[:3]:
            out.append({"level": "warning", "text": f"{x['name']}: {x['detail'].split(';')[0]}"})
        if len(bad) > 3:
            out.append({"level": "info", "text": f"{len(bad) - 3} more failing check(s): see the integrity checklist"})
        dmg = f["corrupted_ranges"]
        if dmg:
            total = sum(d["length"] for d in dmg)
            out.append({"level": "warning", "text": f"{_plural(len(dmg), 'damaged byte range')} ({total:,} bytes), e.g. "
                                                    f"{dmg[0]['start']:,}–{dmg[0]['end']:,}: {dmg[0]['note']}"})
        if f.get("repaired_copy"):
            rc = f["repaired_copy"]
            out.append({"level": "info", "text": f"Repaired copy {rc['file']} built by rebuilding the xref "
                                                 f"(integrity {rc['integrity_score']:.0f}, {rc['render_clean']}/{rc['render_pages']} pages render); "
                                                 "the carved copy and evidence are untouched"})
        return out
    multi = sum(1 for f in files if len(f["fragments"]) >= 3)
    if multi:
        out.append({"level": "warning", "text": f"{_plural(multi, 'file')} fragmented into 3 or more non-contiguous pieces"})
    if unrec_bytes:
        affected = [f["file_name"] for f in files if f["missing_ranges"]]
        out.append({"level": "warning", "text": f"{unrec_bytes:,} bytes referenced by {_plural(len(affected), 'file')}' own structures could not be located "
                                                f"(most likely overwritten): {', '.join(affected[:3])}{'…' if len(affected) > 3 else ''}"})
    elif files:
        out.append({"level": "success", "text": "Every byte range that the recovered structures declare was located"})
    corr = [f for f in files if f["corrupted_ranges"]]
    if corr:
        out.append({"level": "warning", "text": f"{_plural(len(corr), 'file')} contain byte ranges that fail validation: {', '.join(f['file_name'] for f in corr[:3])}"})
    orph = sum(1 for f in files if f["orphan"])
    if orph:
        out.append({"level": "warning", "text": f"{_plural(orph, 'orphan group')} of structured data without a surviving header"})
    if res.stats["duplicate_fragments"]:
        out.append({"level": "info", "text": f"{_plural(res.stats['duplicate_fragments'], 'duplicate fragment')} (byte-identical copies) de-duplicated"})
    silent = sorted({f["file_type"].upper() for f in files if f["file_type"] in ("pdf", "log", "txt") and not f["orphan"]})
    if silent:
        out.append({"level": "info", "text": f"{', '.join(silent)} content has no checksum, so silent byte changes inside it cannot be ruled out"})
    if unclassified:
        out.append({"level": "info", "text": f"{_plural(unclassified, 'unallocated fragment')} of unstructured/high-entropy data (likely free space) left unclassified"})
    good = sum(1 for f in files if f["recovery_status"] == "FULLY_RECOVERED")
    if good:
        out.append({"level": "success", "text": f"{_plural(good, 'file')} fully recovered with every validator passing"})
    return out


def build_diskmap(img: EvidenceImage, res: ScanResult, files: list[dict[str, Any]]) -> dict[str, Any]:
    """One cell per cluster; small inputs use 512-byte sectors so they are not a single square."""
    unit = 512 if res.stats["clusters"] < 256 else CLUSTER
    cells = -(-img.size // unit)
    cls = []
    for i in range(cells):
        chunk = img.data[i * unit:(i + 1) * unit]
        if not chunk.strip(b"\x00"):
            cls.append(0)
        else:
            cls.append(CLS_CODE.get(res.clusters[min(len(res.clusters) - 1, i * unit // CLUSTER)].cls, 11))
    owner = [-1] * cells
    damaged = [0] * cells
    idx = {f["file_id"]: i for i, f in enumerate(files)}
    for f in files:
        for s in f["segments"]:
            if s["source_offset"] is None:
                continue
            for k in range(s["source_offset"] // unit, -(-(s["source_offset"] + s["length"]) // unit)):
                if 0 <= k < cells:
                    owner[k] = idx[f["file_id"]]
                    if s["kind"] == "corrupted":
                        damaged[k] = 1
    return {"clusters": cells, "unit": unit, "size": img.size, "cls": cls, "owner": owner, "damaged": damaged,
            "legend": CLS_CODE, "files": [f["file_id"] for f in files],
            "file_names": {f["file_id"]: f["file_name"] for f in files}}
