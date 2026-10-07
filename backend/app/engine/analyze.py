"""End-to-end analysis pipeline over a read-only evidence image."""
from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from .assemble.base import Ctx
from .assemble.jpeg import assemble_jpeg
from .assemble.naming import dir_node_id
from .assemble.orphans import assemble_orphans
from .assemble.pdf import assemble_pdf
from .assemble.sqlitedb import assemble_sqlite
from .assemble.text import assemble_text
from .assemble.zipdoc import assemble_zip
from .common import ANALYSIS_VERSION, CLUSTER, Candidate, hexoff, sha256, split_corrupted
from .explain import explain
from .intake import detect
from .model import EdgeModel
from .postproc import (build_diskmap, build_findings, failed_file, final_name, make_repaired_copy, signature_stub,
                       single_file_candidate, sweep_unstructured, validator_damage, write_export)
from .security import YARA_ENGINE, get_scanner, scan_artifact
from ..services.guardian import isolate
from .priority import DEFAULT_CRITERIA, prioritize
from .relations import cross_relations
from .scanner import EvidenceImage, scan
from .scoring import (CONTENT_CLASS, MIME, assessment, content_confidence, integrity, links_metric,
                      reconstruction_pct, relationship, run_validation, status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _search_text(c: Candidate, v: dict[str, Any]) -> str:
    st = v.get("stats", {})
    if c.type == "pdf":
        return "\n".join(p["text"] for p in st.get("text_pages", [])) or c.structure.get("text", "")
    if c.type in ("docx",):
        return st.get("text", "")
    if c.type in ("log", "txt"):
        return c.data[:200000].decode("utf-8", "replace")
    if c.type == "sqlite":
        rows = []
        for t in c.structure.get("tables", []):
            rows += [" ".join(str(x) for x in r.values()) for r in t["sample"]]
        return "\n".join(rows)
    if c.type == "jpeg":
        ex = c.metadata.get("exif") or {}
        return " ".join(str(x) for x in ex.values())
    return ""


def _jpeg_preview(c: Candidate, v: dict[str, Any], path: Path) -> dict[str, Any] | None:
    raw = c.structure.get("transplant_bytes") if c.orphan else c.data
    if not raw:
        return None
    try:
        im = Image.open(io.BytesIO(raw + (b"" if raw.endswith(b"\xff\xd9") else b"\xff\xd9")))
        im.load()
        im = im.convert("RGB")
    except Exception:
        return None
    st = v.get("stats", {})
    mh = (c.structure.get("header") or {}).get("mcu_h", 16)
    rows_ok = st.get("rows_decoded")
    if c.orphan:
        rows_ok = c.structure.get("rst_markers")
    d = ImageDraw.Draw(im, "RGBA")
    w, h = im.size
    if rows_ok is not None and rows_ok * mh < h:
        y0 = rows_ok * mh
        d.rectangle([0, y0, w, h], fill=(20, 20, 28, 255))
        for x in range(-h, w, 18):
            d.line([(x, y0), (x + (h - y0), h)], fill=(220, 60, 60, 110), width=3)
        d.text((10, y0 + 8), "NOT RECOVERED - no data located for these rows (nothing synthesized)", fill=(255, 120, 120, 255))
    for r in st.get("suspect_rows", []):
        d.rectangle([0, r * mh, w, (r + 1) * mh], outline=(255, 200, 0, 255), width=2)
    if c.orphan:
        d.rectangle([0, 0, w, 22], fill=(120, 20, 20, 230))
        d.text((8, 5), f"UNVERIFIED RENDERING - decoding tables borrowed from {c.structure.get('transplant_donor')}", fill=(255, 255, 255, 255))
    im.thumbnail((900, 900))
    im.save(path, "PNG")
    return {"kind": "image", "file": path.name, "unverified": c.orphan,
            "note": ("Rendered with borrowed tables; colours/geometry may be wrong" if c.orphan else
                     "Hatched area = rows with no recovered data; yellow boxes = statistically suspect rows")}


def analyze(image_path: str, out_dir: Path, log: Callable[[str, str], None],
            progress: Callable[[float, str], None], criteria: dict[str, Any] | None = None,
            mode: str = "demo", source_label: str = "") -> dict[str, Any]:
    t_start = time.time()
    crit = json.loads(json.dumps(criteria or DEFAULT_CRITERIA))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "reconstructed").mkdir(exist_ok=True)
    (out_dir / "previews").mkdir(exist_ok=True)

    progress(0.02, "Hashing evidence image")
    img = EvidenceImage(image_path)
    img_hash = sha256(img.data)
    log("ingest", f"Evidence image opened read-only: {img.size:,} bytes, SHA-256 {img_hash}")

    progress(0.05, "Scanning clusters")
    res = scan(img, lambda p, m: progress(0.05 + 0.2 * p, m))
    log("scan", f"{res.stats['clusters']:,} clusters ({res.stats['sectors']:,} sectors) scanned; {res.stats['fragments']:,} fragments detected")
    log("identify", f"{res.stats['header_fragments']} file signatures identified; {res.stats['duplicate_fragments']} duplicate fragment(s); "
                    f"{res.stats['directory_entries']} directory entries recovered")
    fam_counts: dict[str, int] = {}
    for f in res.fragments:
        fam_counts[f.family] = fam_counts.get(f.family, 0) + 1
    log("identify", "Fragment families: " + ", ".join(f"{k}={v}" for k, v in sorted(fam_counts.items(), key=lambda t: -t[1])))

    input_type = detect(img.data, source_label or Path(image_path).name)
    single = input_type["kind"] == "single_file"
    log("intake", f"Input type: {input_type['label']} ({'; '.join(input_type['evidence'])})")
    if input_type.get("note"):
        log("intake", input_type["note"])

    progress(0.3, "Relationship analysis & reconstruction")
    model = EdgeModel.load()
    ctx = Ctx(img, res, model, log)
    if single:
        # file repair/validation mode: the upload IS the file; carving would only re-find it
        log("reconstruct", "Single-file input: carving skipped; running file validation/repair mode")
        ctx.candidates.append(single_file_candidate(img, res, input_type, source_label or Path(image_path).name))
        ctx.claim("R001", [f for f in res.fragments if f.family != "zero"])
    else:
        log("relate", f"Relationship analysis started (edge model: {'trained' if model.p.get('trained') else 'prior weights'})")
        for name, fn in [("ZIP/DOCX", assemble_zip), ("PDF", assemble_pdf), ("SQLite", assemble_sqlite),
                         ("JPEG", assemble_jpeg), ("logs/text", assemble_text), ("orphans", assemble_orphans)]:
            fn(ctx)
            progress(0.3 + 0.3 * (["ZIP/DOCX", "PDF", "SQLite", "JPEG", "logs/text", "orphans"].index(name) + 1) / 6, f"Reconstructed {name}")
    n_edges = sum(len(c.edges) for c in ctx.candidates)
    log("reconstruct", f"{len(ctx.candidates)} reconstruction groups generated; {n_edges} candidate relationships evaluated")

    # remaining compatible fragments per family (for 'further recovery' assessment)
    fam_of = {"jpeg": "jpeg", "pdf": "pdf", "sqlite": "sqlite", "docx": "compressed", "zip": "compressed", "log": "log", "txt": "text"}
    free_by_family: dict[str, int] = {}
    for f in res.fragments:
        if ctx.free(f):
            free_by_family[f.family] = free_by_family.get(f.family, 0) + 1
    orphan_jpeg = sum(1 for c in ctx.candidates if c.orphan and c.type == "jpeg")
    for c in ctx.candidates:
        if not c.remaining_compatible and c.missing_ranges():
            if c.type in ("docx", "zip", "xlsx"):
                # a missing stored image member could survive as headerless JPEG data elsewhere
                lost = [m["name"] for m in c.structure.get("members", []) if m.get("status") != "ok"]
                if any(n.lower().endswith((".jpeg", ".jpg")) for n in lost):
                    c.remaining_compatible = orphan_jpeg
            else:
                c.remaining_compatible = free_by_family.get(fam_of.get(c.type, ""), 0)

    sweep = {"scanned": 0, "flagged": 0}
    if not single:
        progress(0.62, "Security sweep of unassigned fragments")
        sweep = sweep_unstructured(ctx, mode, log)
    scanner = get_scanner(mode)
    engine_name = scanner.get_scan_engine() if scanner else None
    log("security", f"Malware scanning engine: {engine_name or 'NONE AVAILABLE (ClamAV not installed/reachable)'}; "
                    f"rule engine: {YARA_ENGINE}. Recovered artifacts are scanned as data and never executed.")
    progress(0.65, "Validating reconstructions")
    files: list[dict[str, Any]] = []

    def build_file(c: Candidate) -> dict[str, Any]:
        header = (c.structure.get("header") if c.structure.get("single_file") else None) or \
            (res.frag(c.fragments[0]).header if c.fragments else None)
        v = run_validation(c, header)
        if c.structure.get("single_file"):
            c.segments = split_corrupted(c.segments, validator_damage(c, v))
        final, name_src = final_name(c, v)
        c.name, c.name_source = final, name_src
        recon, recon_why = reconstruction_pct(c, v)
        integ, ifac = integrity(c, v, recon)
        rel, rcomp = relationship(c)
        links = links_metric(c, v, rel)
        cc, ccwhy = content_confidence(c, v)
        st, st_why = status(c, v, recon, integ, rel)
        assess = assessment(c, v, recon, recon_why)
        stub = None if c.orphan else signature_stub(c.data, c.type)
        if stub:
            # the evidence is conclusive, not uncertain: nothing beyond the signature exists
            st, st_why = "UNRECOVERABLE", f"Signature stub: {stub}."
            assess = {**assess, "further_recovery": "NO", "reason": st_why}
            log("validate", f"{c.id}: {st_why}")
        log("security", f"{c.id}: malware scan started ({engine_name or 'no antivirus engine'}; {YARA_ENGINE.split(' (')[0]})")
        security = scan_artifact(c.data, c.type, mode, scanner)
        log("security", f"{c.id}: scan completed: {security['label']}"
                        + (f"; detections {security['detections']}" if security['detections'] else "")
                        + (f"; {len(security['yara_matches'])} rule match(es): {', '.join(m['rule'] for m in security['yara_matches'])}" if security['yara_matches'] else "")
                        + f"; SHA-256 {security['sha256']}")
        if security["status"] == "MALWARE_DETECTED":
            log("security", f"{c.id}: MALWARE DETECTED{' (TEST SIGNATURE)' if security.get('is_test') else ''}; recovered bytes preserved unchanged; NOT executed")
        security["isolation"] = isolate(out_dir, c.id, c.data, security)
        if security["isolation"]:
            log("security", f"{c.id}: flagged artifact isolated as inert read-only copy {security['isolation']['path']} (static analysis only)")
        export = write_export(c, v, security, out_dir, log)
        out_file = out_dir / "reconstructed" / export["file"]
        out_sha = export["export_sha256"] or sha256(c.data)
        n_chk = v.get("checks", [])
        log("validate", f"{c.id} {c.name}: '{c.type}' validator ran {len(n_chk)} checks "
                        f"({sum(1 for x in n_chk if x['status'] == 'pass')} pass, {sum(1 for x in n_chk if x['status'] == 'partial')} partial, "
                        f"{sum(1 for x in n_chk if x['status'] == 'fail')} fail, {sum(1 for x in n_chk if x['status'] == 'na')} n/a) -> "
                        f"integrity {integ:.1f}, {st}; carved copy SHA-256 {out_sha}")
        repaired = None
        if c.type == "pdf" and v.get("pdf_report") and any(
                x["status"] != "pass" for x in v["checks"] if x["id"] in ("startxref", "xref_offsets", "trailer")):
            repaired = make_repaired_copy(c, v, out_dir, log, out_sha)
        previews = v.pop("previews", None) or []
        v.pop("pdf_report", None)
        damaged = v.pop("damaged_ranges", None)
        de = c.metadata.get("directory_entry") or {}
        modified = de.get("modified")
        if not modified and c.type == "jpeg":
            dt = (c.metadata.get("exif") or {}).get("DateTime")
            modified = dt.replace(":", "-", 2).replace(" ", "T") if dt else None
        if not modified and c.type == "docx":
            modified = (v.get("stats", {}).get("core") or {}).get("modified", "").replace("Z", "") or None
        if not modified and c.type == "log" and c.metadata.get("time_range"):
            modified = c.metadata["time_range"][1].replace(" ", "T")
        preview: dict[str, Any] | None = None
        if c.type == "jpeg":
            preview = _jpeg_preview(c, v, out_dir / "previews" / f"{c.id}.png")
        elif c.type == "pdf":
            st_ = v.get("stats", {})
            images = []
            for i, png in enumerate(previews):
                pth = out_dir / "previews" / f"{c.id}_p{i + 1}.png"
                pth.write_bytes(png)
                images.append({"page": i + 1, "file": pth.name})
            preview = {"kind": "pdf", "images": images, "pages": (st_.get("text_pages") or [])[:12],
                       "render_pages": st_.get("render_pages"), "render_clean": st_.get("render_clean"),
                       "renderer": st_.get("renderer"), "repaired_by_renderer": st_.get("render_repaired"),
                       "inventory": st_.get("object_inventory", [])[:200], "damaged_ranges": [list(d) for d in (damaged or [])][:20],
                       "text": c.structure.get("text", "")}
        elif c.type == "docx":
            s = v.get("stats", {})
            preview = {"kind": "text", "text": s.get("text", "")[:5000], "verified": s.get("text_verified", False)} if s.get("text") else None
        elif c.type in ("log", "txt"):
            preview = {"kind": "text", "text": c.data[:6000].decode("utf-8", "replace"), "verified": False}
        elif c.type == "sqlite" and not c.orphan:
            preview = {"kind": "tables", "tables": c.structure.get("tables", [])}
        embedded = []
        if c.type == "docx":
            for m in v.get("stats", {}).get("members", []):
                if m["name"].startswith("word/media/"):
                    embedded.append({"name": m["name"], "status": m["status"], "node_id": f"{c.id}:{m['name'].rsplit('/', 1)[-1]}",
                                     "size": m["usize"]})
        frag_details = []
        for fid in c.fragments:
            fr = res.frag(fid)
            frag_details.append({"id": fid, "offset": fr.offset, "offset_hex": hexoff(fr.offset), "length": fr.length,
                                 "sector": fr.offset // 512, "clusters": fr.n_clusters, "family": fr.family, "sha256": fr.sha256,
                                 "entropy": round(float(np.mean([x.entropy for x in fr.clusters])), 3)})
        structure = {k: v_ for k, v_ in c.structure.items() if k not in ("transplant_bytes",)}
        f = {
            "file_id": c.id, "file_name": c.name, "name_source": c.name_source, "file_type": c.type,
            "mime": MIME.get(c.type, "application/octet-stream"), "content_class": CONTENT_CLASS.get(c.type, "Other"),
            "size_bytes": len(c.data), "expected_size": c.expected_size, "expected_size_source": c.expected_size_source,
            "integrity_score": integ, "integrity_factors": ifac,
            "reconstruction_percentage": None if recon is None else round(recon, 1), "reconstruction_basis": recon_why,
            "recon_confidence_reason": recon_why,
            "relationship_confidence": rel, "relationship_components": rcomp, "links_metric": links,
            "validator_checks": [{k: x.get(k) for k in ("id", "name", "status", "weight", "score", "points", "category", "detail")}
                                 for x in v.get("checks", [])],
            "repaired_copy": repaired, "input_type": input_type["kind"],
            "security": security, "export": export,
            "export_validation": {"exported": export["exported"], "format_valid": export["format_valid"],
                                  "byte_identical": export["byte_identical"], "sha256": export["export_sha256"],
                                  "export_class": export["class"], "label": export["label"]},
            "content_confidence": cc, "content_confidence_factors": ccwhy,
            "recovery_status": st, "status_reason": st_why, "signature_stub": stub,
            "fragments": c.fragments, "fragment_details": frag_details,
            "fragment_relationships": [e.to_dict() for e in c.edges],
            "missing_ranges": c.missing_ranges(), "corrupted_ranges": c.corrupted_ranges(),
            "segments": [s.to_dict() for s in c.segments], "conflicts": c.conflicts,
            "metadata": {k: v_ for k, v_ in c.metadata.items()}, "modified": modified,
            "validation": {"structural_validation": v.get("structural"), "parser_validation": v.get("parser"),
                           "checksum_validation": v.get("checksum"), "checks": v.get("checks", []),
                           "stats": {k: v_ for k, v_ in v.get("stats", {}).items() if k not in ("text", "text_pages", "seam_profile", "objects_detail")}},
            "structure": structure, "assessment": assess, "preview": preview, "orphan": c.orphan, "header_found": c.header_found,
            "search_text": _search_text(c, v)[:20000], "embedded": embedded,
            "output_file": out_file.name, "output_sha256": out_sha, "reconstruction_sha256": export["reconstruction_sha256"],
            "provenance": {
                "source_image": source_label or Path(image_path).name, "source_sha256": img_hash,
                "fragments": [{"id": d["id"], "offset_hex": d["offset_hex"], "length": d["length"], "sha256": d["sha256"]} for d in frag_details],
                "operations": c.operations + [f"Validated with '{c.type}' validator: structure={v.get('structural')}, parser={v.get('parser')}, checksum={v.get('checksum')}"],
                "analysis_version": ANALYSIS_VERSION, "model": "edge-model " + ("trained" if model.p.get("trained") else "prior"),
                "timestamp": _now(), "output_note": "Missing ranges are zero-filled in the exported file and listed in missing_ranges.",
            },
            "notes": "; ".join(c.notes),
        }
        return f

    for c in ctx.candidates:
        try:
            files.append(build_file(c))
        except Exception as e:  # one broken candidate must not take the whole analysis down
            log("error", f"{c.id}: RECONSTRUCTION FAILED: {type(e).__name__}: {e}")
            files.append(failed_file(c, e, input_type["kind"]))
    log("validate", f"Validation completed for {len(files)} candidate(s)")
    progress(0.8, "Classification & relationships")
    xedges = cross_relations(files, res)
    for f in files:
        f["related_files"] = len({(e.dst if e.src == f["file_id"] else e.src) for e in xedges
                                  if f["file_id"] in (e.src, e.dst) and e.type in ("content_similarity", "temporal")})
    log("classify", "Classification: " + ", ".join(f"{k}={sum(1 for f in files if f['content_class'] == k)}" for k in sorted({f['content_class'] for f in files})))
    prioritize(files, crit)
    log("prioritize", f"Priority analysis completed with {len(crit['keywords'])} keyword(s): {', '.join(crit['keywords'])}")
    for f in files:
        f["explanation"] = explain(f)
    files.sort(key=lambda f: -f["priority_score"])
    # ---- graph ------------------------------------------------------------------------------------
    graph = build_graph(files, xedges, res)
    # ---- summary ----------------------------------------------------------------------------------
    unrec_bytes = sum(m["length"] for f in files for m in f["missing_ranges"])
    exp_total = sum(f["expected_size"] or 0 for f in files if f["expected_size"] and not f["orphan"] and f["input_type"] != "single_file")
    rec_total = sum(min(f["expected_size"], sum(s["length"] for s in f["segments"] if s["kind"] != "missing"))
                    for f in files if f["expected_size"] and not f["orphan"] and f["input_type"] != "single_file")
    by_status: dict[str, int] = {}
    for f in files:
        by_status[f["recovery_status"]] = by_status.get(f["recovery_status"], 0) + 1
    unclassified = sum(1 for fr in res.fragments if ctx.free(fr) and fr.family in ("compressed", "text", "binary"))
    # coverage: structure-declared bytes located; fallback: bytes assigned / non-empty bytes scanned
    assigned = set()
    for f in files:
        for s in f["segments"]:
            if s["source_offset"] is not None and s["kind"] != "missing":
                assigned.update(range(s["source_offset"] // 512, -(-(s["source_offset"] + s["length"]) // 512)))
    nonempty = sum(1 for k in range(-(-img.size // 512)) if img.data[k * 512:(k + 1) * 512].strip(b"\x00"))
    assigned_cov = round(100 * min(len(assigned), nonempty) / nonempty, 1) if nonempty else None
    if single:
        coverage, coverage_reason = None, ("N/A – no file-system metadata (single-file input): nothing outside the file declares "
                                           "which bytes should exist, so structural coverage is not defined")
    elif exp_total:
        coverage = round(100 * rec_total / exp_total, 1)
        coverage_reason = ("Bytes located ÷ bytes declared by the recovered files' own structures "
                           "(PDF xref, ZIP central directory, SQLite page count, directory-entry sizes)")
    else:
        coverage, coverage_reason = None, "N/A – no file-system metadata and no recovered structure declares a size (carved input)"
    extra_stubs = []  # signatures of formats without an assembler (e.g. PNG) are still checked, never ignored
    for ci in res.clusters:
        if ci.sig in ("png", "gif", "mp4"):
            off = ci.index * CLUSTER
            s = signature_stub(img.data[off:off + (1 << 20)], ci.sig)
            if s:
                extra_stubs.append(f"{ci.sig.upper()} signature at {hexoff(off)}: {s}")
                log("validate", extra_stubs[-1])
    findings = build_findings(files, res, input_type, unrec_bytes, unclassified, extra_stubs, img)
    challenges = [x["text"] for x in findings if x["level"] == "warning"]
    summary = {
        "storage_medium": source_label or Path(image_path).name, "storage_size_bytes": img.size,
        "total_sectors_scanned": -(-img.size // 512), "total_clusters": res.stats["clusters"],
        "potential_fragments_identified": res.stats["fragments"],
        "candidate_files_identified": len(files),
        "files_reconstructed": sum(1 for f in files if f["recovery_status"] in ("FULLY_RECOVERED", "MOSTLY_RECOVERED", "PARTIALLY_RECOVERED")),
        "fully_recovered": by_status.get("FULLY_RECOVERED", 0), "mostly_recovered": by_status.get("MOSTLY_RECOVERED", 0),
        "partially_recovered": by_status.get("PARTIALLY_RECOVERED", 0), "fragment_only": by_status.get("FRAGMENT_ONLY", 0),
        "uncertain": by_status.get("UNCERTAIN", 0), "unrecoverable": by_status.get("UNRECOVERABLE", 0),
        "unrecoverable_sectors": -(-unrec_bytes // 512),
        "unrecoverable_sectors_definition": "Sectors inside byte ranges that recovered file structures reference but that could not be located",
        "overall_recovery_coverage": coverage, "coverage_reason": coverage_reason,
        "assigned_coverage": assigned_cov,
        "assigned_coverage_reason": "Non-empty sectors used by a recovered file ÷ non-empty sectors scanned",
        "input_type": input_type, "findings": findings,
        "security": {
            "engine": engine_name, "rule_engine": YARA_ENGINE, "simulated_engine": bool(scanner and "SIMULATED" in (engine_name or "")),
            "counts": {k: sum(1 for f in files if (f.get("security") or {}).get("status") == k)
                       for k in ("CLEAN", "SUSPICIOUS", "MALWARE_DETECTED", "SCAN_FAILED", "NOT_SCANNED")},
            "unassigned_fragments_scanned": sweep["scanned"], "unassigned_fragments_flagged": sweep["flagged"],
        },
        "key_challenges": challenges, "cluster_classes": res.stats["cluster_classes"],
        "fragment_families": fam_counts, "directory_entries": res.dir_entries,
        "analysis_seconds": round(time.time() - t_start, 2), "image_sha256": img_hash,
    }
    diskmap = build_diskmap(img, res, files)
    fragments = []
    for fr in res.fragments:
        d = fr.summary()
        d["assigned_to"] = ctx.assigned.get(fr.id)
        fragments.append(d)
    # evidence integrity: re-hash the source to prove it was not modified
    with open(image_path, "rb") as fh:
        after = sha256(fh.read())
    log("verify", f"Evidence re-hashed after analysis: {'UNCHANGED' if after == img_hash else 'MODIFIED!'} (SHA-256 {after[:16]}...)")
    return {"summary": summary, "files": files, "fragments": fragments, "graph": graph, "diskmap": diskmap,
            "cross_edges": [e.to_dict() for e in xedges], "criteria": crit, "model": model.card(),
            "evidence_unchanged": after == img_hash}


def build_graph(files: list[dict[str, Any]], xedges, res) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for f in files:
        nodes[f["file_id"]] = {"id": f["file_id"], "kind": "file", "label": f["file_name"], "type": f["file_type"],
                               "status": f["recovery_status"], "integrity": f["integrity_score"], "priority": f["priority_level"],
                               "group": f["file_id"]}
        for fid in f["fragments"]:
            fr = res.frag(fid)
            nodes[fid] = {"id": fid, "kind": "fragment", "label": fid, "type": fr.family, "offset": hexoff(fr.offset),
                          "length": fr.length, "group": f["file_id"], "header": bool(fr.header)}
            edges.append({"src": fid, "dst": f["file_id"], "type": "member", "confidence": f["relationship_confidence"],
                          "label": "member", "chosen": True, "evidence": [f"{fid} is part of reconstruction {f['file_id']}"]})
        for e in f["fragment_relationships"]:
            for end in (e["src"], e["dst"]):
                if end not in nodes:
                    if end.startswith("D"):
                        de = next((d for d in res.dir_entries if dir_node_id(d) == end), None)
                        nodes[end] = {"id": end, "kind": "metadata", "label": de["short_name"] if de else end,
                                      "type": "directory entry", "detail": de, "group": f["file_id"]}
                    elif end.startswith("F"):
                        fr = res.frag(end)
                        nodes[end] = {"id": end, "kind": "fragment", "label": end, "type": fr.family,
                                      "offset": hexoff(fr.offset), "length": fr.length, "group": None, "header": bool(fr.header)}
            edges.append(e)
        for m in f.get("embedded", []):
            nodes[m["node_id"]] = {"id": m["node_id"], "kind": "embedded", "label": m["name"].rsplit("/", 1)[-1],
                                   "type": "embedded", "status": m["status"], "group": f["file_id"]}
    for e in xedges:
        d = e.to_dict()
        for end in (d["src"], d["dst"]):
            if end not in nodes and end.startswith("F"):
                fr = res.frag(end)
                nodes[end] = {"id": end, "kind": "fragment", "label": end, "type": fr.family, "offset": hexoff(fr.offset),
                              "length": fr.length, "group": None, "header": bool(fr.header)}
        if d["src"] in nodes and d["dst"] in nodes:
            edges.append(d)
    return {"nodes": list(nodes.values()), "edges": edges}
