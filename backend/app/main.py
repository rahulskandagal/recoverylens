"""RecoveryLens API."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, pipeline, realrecovery, shield
from .api_advanced import advanced_report, router as advanced_router
from .api_realrecovery import router as realrecovery_router
from .api_shield import router as shield_router
from .engine.common import ANALYSIS_VERSION, hexoff
from .services import benchmark, investigator
from .engine.model import EdgeModel
from .engine.priority import DEFAULT_CRITERIA
from .engine.synth import DATASETS, SEEDS
from .report import build_report

MAX_UPLOAD = 1024 * 1024 * 1024  # 1 GiB prototype limit

# Real Recovery Mode and Recovery Shield both act on the SERVER's own local filesystem (drive
# letters, arbitrary folder paths, raw device access). That is meaningless and unsafe on a public
# host: a remote visitor would be scanning/registering paths on OUR server, not their own machine.
# A public deployment sets RECOVERYLENS_PUBLIC_DEMO=true to disable exactly those local-filesystem
# routes (plus free-form storage-image upload, to bound storage/abuse) while every Demo Mode
# scenario and every read-only analysis page keeps working normally.
PUBLIC_DEMO = os.environ.get("RECOVERYLENS_PUBLIC_DEMO", "false").strip().lower() in ("1", "true", "yes")


def _no_local_access() -> None:
    if PUBLIC_DEMO:
        raise HTTPException(403, "This public demo only runs Demo Mode. Real Recovery Mode and Recovery Shield act on "
                                 "a machine's own local drives/folders, so they are disabled here — run RecoveryLens "
                                 "locally (see the README) to use them on your own machine.")


app = FastAPI(title="RecoveryLens API", version=ANALYSIS_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(advanced_router)
if not PUBLIC_DEMO:
    app.include_router(shield_router)
    app.include_router(realrecovery_router)


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {"public_demo": PUBLIC_DEMO}


@app.on_event("startup")
def _startup() -> None:
    db.init()
    benchmark.init()
    investigator.init()
    shield.init()
    realrecovery.init()
    db.execute("UPDATE benchmarks SET status='failed', error='Interrupted by server restart' WHERE status IN ('running','queued')")
    db.execute("UPDATE shield_snapshots SET status='failed', error='Interrupted by server restart' WHERE status='running'")
    db.execute("UPDATE shield_sources SET protection='paused' WHERE protection='active'")  # monitor threads do not survive a restart
    pipeline.ensure_demo()
    # any job interrupted by a restart is marked failed rather than left "running" forever
    db.execute("UPDATE cases SET status='failed', error='Interrupted by server restart' WHERE status IN ('running','queued')")


def _case(cid: str) -> dict[str, Any]:
    c = db.one("SELECT * FROM cases WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "case not found")
    for k in ("criteria", "summary", "evaluation", "model"):
        c[k] = json.loads(c[k]) if c.get(k) else None
    c.pop("image_path", None)
    return c


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": ANALYSIS_VERSION}


@app.get("/api/datasets")
def datasets() -> list[dict[str, Any]]:
    out = []
    for k, v in DATASETS.items():
        p = pipeline.DEMO / f"demo_{k}_{SEEDS[k]}.img"
        out.append({"key": k, **v, "size_bytes": p.stat().st_size if p.exists() else None, "synthetic": True})
    return out


@app.get("/api/model")
def model_card() -> dict[str, Any]:
    return EdgeModel.load().card()


class NewCase(BaseModel):
    dataset: str
    name: str | None = None
    criteria: dict[str, Any] | None = None


@app.post("/api/cases")
def new_demo_case(body: NewCase) -> dict[str, str]:
    if body.dataset not in DATASETS:
        raise HTTPException(400, "unknown dataset")
    src = pipeline.DEMO / f"demo_{body.dataset}_{SEEDS[body.dataset]}.img"
    cid = pipeline.create_case(body.name or DATASETS[body.dataset]["title"], "demo", body.dataset, src,
                               src.name, body.criteria)
    return {"id": cid}


@app.post("/api/cases/upload")
async def upload_case(file: UploadFile = File(...), authorized: bool = Form(False), name: str = Form("")) -> dict[str, str]:
    _no_local_access()
    if not authorized:
        raise HTTPException(400, "You must confirm you are authorised to analyse this storage image.")
    fd = tempfile.NamedTemporaryFile(delete=False, suffix=".img")
    size = 0
    with fd:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                Path(fd.name).unlink(missing_ok=True)
                raise HTTPException(413, "Prototype limit is 1 GiB per image")
            fd.write(chunk)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (file.filename or "upload.img"))
    cid = pipeline.create_case(name or safe, "upload", None, Path(fd.name), safe, None, move=True)
    return {"id": cid}


@app.get("/api/cases")
def list_cases() -> list[dict[str, Any]]:
    rows = db.query("SELECT id,name,mode,dataset,status,stage,progress,created_at,finished_at,image_name,image_size FROM cases ORDER BY created_at DESC")
    return rows


@app.get("/api/cases/{cid}")
def get_case(cid: str) -> dict[str, Any]:
    return _case(cid)


@app.delete("/api/cases/{cid}")
def delete_case(cid: str) -> dict[str, bool]:
    """Removes the case's analysis records and outputs (the evidence copy is retained on disk)."""
    _case(cid)
    for t in ("audit", "fragments", "files", "blobs"):
        db.execute(f"DELETE FROM {t} WHERE case_id=?", (cid,))
    db.execute("DELETE FROM cases WHERE id=?", (cid,))
    return {"deleted": True}


@app.get("/api/cases/{cid}/audit")
def get_audit(cid: str, after: int = 0) -> list[dict[str, Any]]:
    return db.query("SELECT id, ts, stage, message FROM audit WHERE case_id=? AND id>? ORDER BY id", (cid, after))


def _slim(f: dict[str, Any]) -> dict[str, Any]:
    keys = ("file_id", "file_name", "file_type", "content_class", "size_bytes", "integrity_score", "reconstruction_percentage",
            "relationship_confidence", "content_confidence", "recovery_status", "priority_level", "priority_score",
            "priority_reason", "orphan", "modified", "name_source")
    d = {k: f.get(k) for k in keys}
    d["fragment_count"] = len(f["fragments"])
    d["relationships"] = sum(1 for e in f["fragment_relationships"] if e["chosen"])
    d["missing_ranges"] = len(f["missing_ranges"])
    d["corrupted_ranges"] = len(f["corrupted_ranges"])
    d["has_preview"] = bool(f.get("preview"))
    d["status_reason"] = f.get("status_reason")
    d["recon_confidence_reason"] = f.get("recon_confidence_reason") or f.get("reconstruction_basis")
    d["links_metric"] = f.get("links_metric") or {"value": f["relationship_confidence"], "kind": "fragment_relationships",
                                                 "label": "Links", "reason": "fragment relationship confidence"}
    d["has_repaired_copy"] = bool(f.get("repaired_copy"))
    sec = f.get("security") or {}
    d["security"] = {k: sec.get(k) for k in ("status", "label", "scanner", "simulated", "is_test", "explanation")}
    exp = f.get("export") or {}
    d["export"] = {k: exp.get(k) for k in ("class", "label", "warning", "byte_identical", "normal_export_enabled", "authorization_required")}
    d["input_type"] = f.get("input_type")
    return d


@app.get("/api/cases/{cid}/files")
def list_files(cid: str, q: str = "", type: str = "", status: str = "", priority: str = "",
               min_integrity: float = 0, min_confidence: float = 0, min_reconstruction: float = 0,
               sort: str = "priority_score", order: str = "desc") -> list[dict[str, Any]]:
    files = pipeline.load_files(cid)
    out = []
    ql = q.lower().strip()
    for f in files:
        if type and f["file_type"] not in type.split(","):
            continue
        if status and f["recovery_status"] not in status.split(","):
            continue
        if priority and f["priority_level"] not in priority.split(","):
            continue
        if f["integrity_score"] < min_integrity or f["relationship_confidence"] < min_confidence:
            continue
        if min_reconstruction and (f["reconstruction_percentage"] or 0) < min_reconstruction:
            continue
        if ql and not (ql in f["file_name"].lower() or any(ql == x.lower() for x in f["fragments"])
                       or ql in f.get("search_text", "").lower()):
            continue
        out.append(_slim(f))
    rev = order != "asc"
    out.sort(key=lambda d: (d.get(sort) is None, d.get(sort) if d.get(sort) is not None else 0), reverse=rev)
    return out


@app.get("/api/cases/{cid}/files/{fid}")
def get_file(cid: str, fid: str) -> dict[str, Any]:
    r = db.one("SELECT data FROM files WHERE case_id=? AND id=?", (cid, fid))
    if not r:
        raise HTTPException(404, "file not found")
    f = json.loads(r["data"])
    f.pop("search_text", None)
    return f


@app.get("/api/cases/{cid}/files/{fid}/preview")
def file_preview(cid: str, fid: str, page: int = 0) -> FileResponse:
    name = f"{fid}_p{page}.png" if page else f"{fid}.png"
    p = pipeline.CASES / cid / "previews" / name
    if "/" in name or "\\" in name or not p.exists():
        raise HTTPException(404, "no preview")
    return FileResponse(p, media_type="image/png")


def _export_error(reason: str, code: int = 500) -> JSONResponse:
    return JSONResponse({"error": "EXPORT FAILED", "reason": reason, "evidence": "preserved (never modified)"}, status_code=code)


@app.get("/api/cases/{cid}/files/{fid}/download", response_model=None)
def file_download(cid: str, fid: str, variant: str = "export", inline: bool = False, authorized: bool = False) -> Response:
    """Binary-safe export of the exact reconstruction buffer.

    variant=export    the export artifact (validated / partially validated / forensic byte artifact)
    variant=repaired  derived PDF copy with a rebuilt xref (if one exists)
    variant=segments  ZIP of only the observed byte segments + manifest (no placeholders)
    Artifacts flagged MALWARE_DETECTED require authorized=true and are served under a non-executable name.
    """
    try:
        f = get_file(cid, fid)
    except HTTPException as e:
        return _export_error(str(e.detail), e.status_code)
    sec = f.get("security") or {}
    exp = f.get("export") or {}
    malware = sec.get("status") == "MALWARE_DETECTED"
    if malware and not authorized and not inline:
        pipeline.audit(cid, "export", f"{fid}: export refused: malware detected and no explicit authorization")
        return JSONResponse({"error": "EXPORT BLOCKED", "reason": "Malicious content detected. Normal export is disabled; "
                             "request a forensic export with explicit authorization. The artifact is never executed."}, status_code=403)
    if malware and inline:
        return JSONResponse({"error": "PREVIEW BLOCKED", "reason": "Inline rendering is disabled for artifacts flagged as malware."}, status_code=403)
    root = pipeline.CASES / cid / "reconstructed"
    if variant == "segments":
        src = root / (exp.get("file") or f["output_file"] or "")
        if not src.is_file():
            return _export_error(f"reconstruction file missing on server: {src.name}")
        buf = src.read_bytes()
        mem = io.BytesIO()
        manifest = {"notice": "Observed byte segments only; no placeholder bytes are included.",
                    "file_id": fid, "source_image_sha256": f["provenance"]["source_sha256"], "segments": []}
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_STORED) as z:
            for i, sgm in enumerate(x for x in f["segments"] if x["kind"] != "missing"):
                part = buf[sgm["start"]:sgm["end"]]
                name = f"segment_{i:03d}_logical_{sgm['start']:08X}_source_{(sgm['source_offset'] or 0):08X}.bin"
                z.writestr(name, part)
                manifest["segments"].append({**{k: sgm[k] for k in ("start", "end", "length", "kind", "fragment_id", "source_offset_hex", "note")},
                                             "file": name, "sha256": hashlib.sha256(part).hexdigest()})
            manifest["missing_ranges"] = [{k: m[k] for k in ("start", "end", "length")} for m in f["missing_ranges"]]
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        data = mem.getvalue()
        pipeline.audit(cid, "export", f"{fid}: observed-segments ZIP exported ({len(manifest['segments'])} segments); ZIP SHA-256 {hashlib.sha256(data).hexdigest()}")
        stem = f["file_name"].rsplit(".", 1)[0]
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{stem}.observed-segments.zip"'})
    if variant == "repaired":
        rc = f.get("repaired_copy")
        if not rc:
            return _export_error("no repaired copy exists for this file", 404)
        path, expected, label = root / rc["file"], rc["sha256"], "REPAIRED COPY (derived)"
    else:
        name = exp.get("file") or f.get("output_file")
        if not name:
            return _export_error(f.get("error") or "no export artifact was produced for this candidate")
        path, expected, label = root / name, exp.get("reconstruction_sha256") or f.get("output_sha256"), exp.get("label", "EXPORT")
    if not path.is_file():
        return _export_error(f"export file missing on server: {path.name}")
    with open(path, "rb") as fh:  # bytes end to end; no text decoding anywhere
        data = fh.read()
    served = hashlib.sha256(data).hexdigest()
    identical = served == expected
    fname = path.name + (".QUARANTINED" if malware else "")
    if not inline:
        pipeline.audit(cid, "export", f"{fid}: {label} exported as {fname}; served SHA-256 {served}; reconstruction SHA-256 {expected}; "
                                      f"{'BYTE-IDENTICAL' if identical else 'MISMATCH'}" + ("; authorized forensic export of flagged artifact" if malware else ""))
    media = "application/octet-stream" if malware else f["mime"]
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{fname}"',
        "X-RecoveryLens-SHA256": served, "X-RecoveryLens-Expected-SHA256": expected or "",
        "X-RecoveryLens-Byte-Identical": str(identical).lower(), "X-RecoveryLens-Export-Class": exp.get("class", variant),
        "X-RecoveryLens-Missing-Ranges": ";".join(f"{m['start']}-{m['end']}" for m in f["missing_ranges"]) or "none",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


class RestoreReq(BaseModel):
    destination_path: str | None = None
    authorized: bool = False


@app.post("/api/cases/{cid}/files/{fid}/restore")
def restore_file(cid: str, fid: str, body: RestoreReq) -> dict[str, Any]:
    """Copies the file's already-produced export bytes to a safe folder (default ~/RecoveryLens_Recovered/<case>/).
    Never writes to the evidence store; never overwrites the file's original real-world location automatically."""
    _no_local_access()
    from . import realrecovery
    try:
        return realrecovery.restore_file(cid, fid, body.destination_path, body.authorized)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError as e:
        return _export_error(str(e))


class ExportVerification(BaseModel):
    variant: str = "export"
    sha256: str
    size: int


@app.post("/api/cases/{cid}/files/{fid}/export-verification")
def export_verification(cid: str, fid: str, body: ExportVerification) -> dict[str, Any]:
    """Client re-hashed the downloaded Blob; record whether it is byte-identical to the reconstruction."""
    f = get_file(cid, fid)
    exp = f.get("export") or {}
    expected = (f.get("repaired_copy") or {}).get("sha256") if body.variant == "repaired" else exp.get("reconstruction_sha256")
    ok = body.sha256.lower() == (expected or "").lower()
    pipeline.audit(cid, "export", f"{fid}: client re-hash of downloaded {body.variant} ({body.size:,} bytes): SHA-256 {body.sha256}; "
                                  f"{'BYTE-IDENTICAL' if ok else 'MISMATCH with ' + str(expected)}")
    return {"byte_identical": ok, "expected_sha256": expected, "received_sha256": body.sha256}


@app.get("/api/cases/{cid}/fragments")
def list_fragments(cid: str, page: int = 1, size: int = Query(100, le=500), family: str = "", assigned: str = "",
                   q: str = "") -> dict[str, Any]:
    where, args = ["case_id=?"], [cid]
    if family:
        where.append("family=?")
        args.append(family)
    if assigned == "yes":
        where.append("assigned_to IS NOT NULL")
    elif assigned == "no":
        where.append("assigned_to IS NULL")
    if q:
        where.append("(id LIKE ? OR assigned_to LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    w = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) n FROM fragments WHERE {w}", tuple(args))["n"]
    rows = db.query(f"SELECT data FROM fragments WHERE {w} ORDER BY offset LIMIT ? OFFSET ?", tuple(args + [size, (page - 1) * size]))
    fams = db.query("SELECT family, COUNT(*) n FROM fragments WHERE case_id=? GROUP BY family ORDER BY n DESC", (cid,))
    return {"total": total, "page": page, "size": size, "items": [json.loads(r["data"]) for r in rows], "families": fams}


@app.get("/api/cases/{cid}/fragments/{frag}/hex")
def fragment_hex(cid: str, frag: str, offset: int = 0, length: int = Query(512, le=4096)) -> dict[str, Any]:
    c = db.one("SELECT image_path FROM cases WHERE id=?", (cid,))
    fr = db.one("SELECT offset, length FROM fragments WHERE case_id=? AND id=?", (cid, frag))
    if not c or not fr:
        raise HTTPException(404, "not found")
    start = fr["offset"] + max(0, min(offset, fr["length"] - 1))
    with open(c["image_path"], "rb") as fh:  # read-only access to evidence
        fh.seek(start)
        b = fh.read(min(length, fr["offset"] + fr["length"] - start))
    lines = []
    for i in range(0, len(b), 16):
        chunk = b[i:i + 16]
        lines.append({"offset": hexoff(start + i), "hex": " ".join(f"{x:02X}" for x in chunk),
                      "ascii": "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)})
    return {"fragment": frag, "start": start, "lines": lines}


@app.get("/api/cases/{cid}/graph")
def graph(cid: str) -> dict[str, Any]:
    g = db.get_blob(cid, "graph")
    if g is None:
        raise HTTPException(404, "graph not ready")
    return g


@app.get("/api/cases/{cid}/diskmap")
def diskmap(cid: str) -> dict[str, Any]:
    d = db.get_blob(cid, "diskmap")
    if d is None:
        raise HTTPException(404, "not ready")
    return d


@app.get("/api/cases/{cid}/criteria")
def get_criteria(cid: str) -> dict[str, Any]:
    return _case(cid)["criteria"] or DEFAULT_CRITERIA


@app.put("/api/cases/{cid}/criteria")
def put_criteria(cid: str, criteria: dict[str, Any]) -> dict[str, Any]:
    base = json.loads(json.dumps(DEFAULT_CRITERIA))
    base.update({k: v for k, v in criteria.items() if k in base})
    base["keywords"] = [k.strip() for k in base.get("keywords", []) if str(k).strip()][:20]
    files = pipeline.reprioritize(cid, base)
    return {"criteria": base, "files": [_slim(f) for f in files]}


def _full_report(cid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    c = db.one("SELECT * FROM cases WHERE id=?", (cid,))
    if not c or c["status"] != "complete":
        raise HTTPException(409, "report not ready")
    summary = json.loads(c["summary"])
    model = json.loads(c["model"]) if c["model"] else None
    rep = build_report(c, summary, pipeline.load_files(cid),
                       db.query("SELECT ts, stage, message FROM audit WHERE case_id=? ORDER BY id", (cid,)), model)
    rep["report_metadata"]["schema_version"] = "1.3"
    rep["report_metadata"]["schema_note"] += " 1.3 adds advanced_analysis (possibility, cross-artifact, guardian, confidence, investigator log)."
    try:
        rep["advanced_analysis"] = advanced_report(cid)
    except Exception as e:  # the core report never fails because an advanced section did
        rep["advanced_analysis"] = {"error": f"NOT AVAILABLE IN CURRENT ANALYSIS: {type(e).__name__}: {e}"}
    return rep, c


@app.get("/api/cases/{cid}/report.pdf", response_model=None)
def report_pdf(cid: str) -> Response:
    """Printable forensic report rendered from exactly the same data as the JSON report."""
    from .pdf_report import render_pdf, sha256 as _sha
    rep, c = _full_report(cid)
    try:
        pdf = render_pdf(rep, c)
    except Exception as e:
        return JSONResponse({"error": "REPORT GENERATION FAILED", "reason": f"{type(e).__name__}: {e}"}, status_code=500)
    pipeline.audit(cid, "report", f"Forensic PDF report generated ({len(pdf):,} bytes, SHA-256 {_sha(pdf)})")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="recoverylens_report_{cid}.pdf"', "Cache-Control": "no-store"})


@app.get("/api/cases/{cid}/report")
def report(cid: str, download: bool = False) -> Response:
    rep, c = _full_report(cid)
    if download:
        pipeline.audit(cid, "report", "Forensic-style JSON report generated and exported (schema 1.3)")
    body = json.dumps(rep, indent=2)
    headers = {"Content-Disposition": f'attachment; filename="recoverylens_report_{cid}.json"'} if download else {}
    return Response(body, media_type="application/json", headers=headers)


# serve the built frontend if present (single-process deployment)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = _dist / path
        return FileResponse(f if path and f.is_file() else _dist / "index.html")
