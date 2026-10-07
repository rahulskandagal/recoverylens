"""Real Recovery Mode: orchestrates real evidence sources (Windows Recycle Bin today) into the
existing, unmodified case/analysis pipeline, plus a generic "restore the recovered file to a safe
folder" action for any case regardless of how its evidence was acquired.

Deliberately thin: a Recycle-Bin item's bytes are handed to the SAME `pipeline.create_case` /
`engine.analyze.analyze` that already handles an uploaded single file. Nothing here re-implements
scoring, validation or reconstruction — that would risk two engines disagreeing. `mode="recyclebin"`
only changes labelling (never falls back to demo data anywhere; see the `mode ==` audit in
security.py / pipeline.py / report.py — every other branch is the same real path 'upload' already uses).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db, pipeline
from . import recyclebin as rb

SCHEMA = """
CREATE TABLE IF NOT EXISTS recyclebin_scans (
  id TEXT PRIMARY KEY, drive TEXT, created_at TEXT, item_count INTEGER, recoverable_count INTEGER, errors TEXT
);
CREATE TABLE IF NOT EXISTS recyclebin_items (
  scan_id TEXT, item_id TEXT, drive TEXT, sid TEXT, original_name TEXT, original_path TEXT,
  deleted_at TEXT, size INTEGER, has_data INTEGER, case_id TEXT, PRIMARY KEY (scan_id, item_id)
);
CREATE INDEX IF NOT EXISTS rb_items_scan ON recyclebin_items(scan_id);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_drives() -> list[dict[str, Any]]:
    return rb.list_drives()


def start_scan(drive: str, _root: Any | None = None) -> dict[str, Any]:
    """Live, read-only scan of one drive's Recycle Bin. Returns the persisted scan + items immediately
    (this is fast: it only reads small index records, never the recovered file bytes themselves).

    `_root` is test-only: it overrides the drive root a scan looks under (see recyclebin.scan_recycle_bin).
    """
    result = rb.scan_recycle_bin(drive, _root)
    items = [i for i in result["_raw"]]
    sid = "RBSCAN-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    recoverable = sum(1 for i in items if i.has_data)
    db.execute("INSERT INTO recyclebin_scans(id, drive, created_at, item_count, recoverable_count, errors) VALUES (?,?,?,?,?,?)",
              (sid, result["drive"], _now(), len(items), recoverable, json.dumps(result["errors"])))
    db.many("INSERT OR REPLACE INTO recyclebin_items VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(sid, i.index_name, i.drive, i.sid, i.original_name, i.original_path, i.deleted_at, i.size,
              1 if i.has_data else 0, None) for i in items])
    return get_scan(sid)


def get_scan(scan_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM recyclebin_scans WHERE id=?", (scan_id,))
    if not row:
        raise KeyError(scan_id)
    items = db.query("SELECT * FROM recyclebin_items WHERE scan_id=? ORDER BY deleted_at DESC", (scan_id,))
    for it in items:
        it["has_data"] = bool(it["has_data"])
    return {**row, "errors": json.loads(row["errors"] or "[]"), "items": items}


def list_scans() -> list[dict[str, Any]]:
    return db.query("SELECT id, drive, created_at, item_count, recoverable_count FROM recyclebin_scans ORDER BY created_at DESC LIMIT 30")


def recover_item(scan_id: str, item_id: str, name: str | None = None, _root: Any | None = None) -> str:
    """Copies one $R item's exact bytes into the standard evidence intake and runs the real, unmodified
    analysis pipeline on them (single-file validation/repair path — the same one an uploaded corrupted
    file already goes through). Returns the new case id."""
    row = db.one("SELECT * FROM recyclebin_items WHERE scan_id=? AND item_id=?", (scan_id, item_id))
    if not row:
        raise KeyError(f"{scan_id}/{item_id}")
    if not row["has_data"]:
        raise ValueError(f"{row['original_name']}: the Recycle Bin no longer holds this file's data "
                         "(it was likely purged by emptying the Recycle Bin). Try Real Recovery → "
                         "upload an evidence image of the drive to search unallocated space instead.")
    item = rb.find_item(row["drive"], row["sid"], row["item_id"], _root)
    if item is None:
        raise ValueError(f"{row['original_name']}: no longer found in the Recycle Bin (it may have been restored or purged since the scan)")
    data = rb.read_item_bytes(item)  # read once, read-only; nothing is written back
    fd = tempfile.NamedTemporaryFile(delete=False, suffix=Path(item.original_name).suffix or ".bin")
    with fd:
        fd.write(data)
    cid = pipeline.create_case(name or f"Recycle Bin: {item.original_name}", "recyclebin", None,
                               Path(fd.name), item.original_name, None, move=True)
    db.execute("UPDATE recyclebin_items SET case_id=? WHERE scan_id=? AND item_id=?", (cid, scan_id, item_id))
    pipeline.audit(cid, "ingest", f"Real Recovery Mode: recovered from the Windows Recycle Bin on {row['drive']} "
                                  f"(deleted {row['deleted_at'] or 'unknown time'}, original path {row['original_path']}); "
                                  "$R data copied byte-for-byte; the Recycle Bin entry itself was not modified or removed")
    return cid


# ------------------------------------------------------------------------------------------ carving (purged items)

_EXT_TYPE = {
    "pdf": "pdf", "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx", "zip": "zip",
    "db": "sqlite", "sqlite": "sqlite", "sqlite3": "sqlite",
    "mp4": "mp4", "mp3": "mp3", "log": "log", "txt": "txt",
}


def _expected_type(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_TYPE.get(ext)


def _case_input_kind(case_id: str) -> str | None:
    row = db.one("SELECT data FROM files WHERE case_id=? LIMIT 1", (case_id,))
    return json.loads(row["data"]).get("input_type") if row else None


def list_carve_images() -> list[dict[str, Any]]:
    """Completed, non-demo disk-image / raw-image cases: candidates to search for a file whose
    Recycle Bin data has already been purged. Demo (synthetic) cases are never offered here --
    real and simulated evidence are never mixed."""
    rows = db.query("SELECT id, name, mode, image_name, image_size, created_at FROM cases "
                    "WHERE status='complete' ORDER BY created_at DESC")
    out = []
    for r in rows:
        if r["mode"] == "demo":
            continue
        kind = _case_input_kind(r["id"])
        if kind in ("disk_image", "raw_image"):
            out.append({**r, "input_kind": kind})
    return out


def carve_purged_item(scan_id: str, item_id: str, case_id: str) -> dict[str, Any]:
    """Best-effort recovery of a permanently-deleted (Recycle Bin already emptied) item, by searching
    an already-analysed storage image for a candidate of the right file type and a plausible size.

    Carving cannot recover the original filename -- that only ever survived in the now-purged $I/$R
    record -- so a match here is never presented as proven identity, only as the best available
    evidence. If no candidate of the right type exists, or the only ones found are too different in
    size or too damaged to mean anything, this reports INSUFFICIENT_EVIDENCE rather than guessing."""
    row = db.one("SELECT * FROM recyclebin_items WHERE scan_id=? AND item_id=?", (scan_id, item_id))
    if not row:
        raise KeyError(f"{scan_id}/{item_id}")
    if row["has_data"]:
        raise ValueError(f"{row['original_name']}: this item's data is still present in the Recycle Bin; "
                         "use Recover & analyse instead of searching a storage image")
    case = db.one("SELECT * FROM cases WHERE id=?", (case_id,))
    if not case or case["status"] != "complete":
        raise KeyError(case_id)
    if case["mode"] == "demo":
        raise ValueError("a demo (synthetic) case cannot be used to search for real evidence")
    if _case_input_kind(case_id) not in ("disk_image", "raw_image"):
        raise ValueError("the selected case is a single-file analysis, not a disk image, so it has nothing to carve")
    expected_type = _expected_type(row["original_name"])
    candidates = []
    for r in db.query("SELECT data FROM files WHERE case_id=?", (case_id,)):
        f = json.loads(r["data"])
        if expected_type and f.get("file_type") == expected_type:
            candidates.append(f)
    target_size = row["size"] or 0
    if not candidates:
        pipeline.audit(case_id, "carve", f"Real Recovery Mode: searched for permanently deleted "
                        f"'{row['original_name']}' ({expected_type or 'unrecognised type'}, {target_size:,} bytes declared) "
                        f"in this image; no {expected_type or 'matching-type'} candidate exists in the evidence")
        return {"status": "INSUFFICIENT_EVIDENCE", "case_id": case_id, "file_id": None,
                "reason": f"No {expected_type or 'matching-type'} candidate was found anywhere in this storage image."}
    best = min(candidates, key=lambda f: abs(f["size_bytes"] - target_size))
    size_ratio = abs(best["size_bytes"] - target_size) / max(target_size, 1)
    rec_status = best["recovery_status"]
    if rec_status in ("FULLY_RECOVERED", "MOSTLY_RECOVERED") and size_ratio <= 0.5:
        status = "RECOVERED"
    elif rec_status in ("PARTIALLY_RECOVERED", "FRAGMENT_ONLY", "UNCERTAIN") and size_ratio <= 1.0:
        status = "PARTIALLY_RECOVERABLE"
    else:
        status = "INSUFFICIENT_EVIDENCE"
    reason = (f"Best-effort match by file type ({expected_type}) and size proximity in the analysed image "
             f"(declared {target_size:,} B vs. candidate {best['size_bytes']:,} B); carving cannot recover the "
             f"original filename, so this identity is not proven. Engine result: {best['status_reason']}")
    if status != "INSUFFICIENT_EVIDENCE":
        db.execute("UPDATE recyclebin_items SET case_id=? WHERE scan_id=? AND item_id=?", (case_id, scan_id, item_id))
    pipeline.audit(case_id, "carve", f"Real Recovery Mode: permanently deleted '{row['original_name']}' "
                    f"({target_size:,} bytes, deleted {row['deleted_at'] or 'unknown time'}) matched against candidate "
                    f"{best['file_id']} ({best['file_name']}) by type + size proximity; result {status}")
    return {"status": status, "case_id": case_id, "file_id": best["file_id"], "file_name": best["file_name"],
            "recovery_status": rec_status, "reason": reason}


# ------------------------------------------------------------------------------------------ restore-to-folder

def restore_file(cid: str, fid: str, destination_path: str | None, authorized: bool) -> dict[str, Any]:
    """Copies a case's already-produced export bytes to a safe folder (never the original evidence,
    never the file's original real-world location). Generic: works the same whether the case came
    from a disk image, an uploaded single file, or a Recycle-Bin recovery."""
    case = db.one("SELECT * FROM cases WHERE id=?", (cid,))
    if not case:
        raise KeyError(cid)
    r = db.one("SELECT data FROM files WHERE case_id=? AND id=?", (cid, fid))
    if not r:
        raise KeyError(fid)
    f = json.loads(r["data"])
    sec = f.get("security") or {}
    exp = f.get("export") or {}
    if sec.get("status") == "MALWARE_DETECTED" and not authorized:
        raise PermissionError("Malicious content detected. Restoring requires the same explicit authorization as a forensic export; "
                              "the file is never executed.")
    if not authorized:
        raise ValueError("restore requires explicit user confirmation (authorized=true)")
    name = exp.get("file") or f.get("output_file")
    if not name:
        raise ValueError(f.get("error") or "no export artifact exists for this file yet")
    src = pipeline.CASES / cid / "reconstructed" / name
    if not src.is_file():
        raise FileNotFoundError(f"export file missing on server: {src.name}")
    safe_case = "".join(c if c.isalnum() or c in "._- " else "_" for c in case["name"])[:80]
    dest_dir = Path(destination_path).expanduser().resolve() if destination_path else Path.home() / "RecoveryLens_Recovered" / safe_case
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name + (".QUARANTINED" if sec.get("status") == "MALWARE_DETECTED" else ""))
    with open(src, "rb") as sf, open(dest, "wb") as df:  # export copy opened read-only; evidence is never touched
        h = hashlib.sha256()
        data = sf.read()
        h.update(data)
        df.write(data)
    digest = h.hexdigest()
    expected = exp.get("reconstruction_sha256") or f.get("output_sha256")
    identical = digest == expected
    pipeline.audit(cid, "restore", f"{fid}: restored to {dest} ({digest[:16]}…); "
                                   f"{'BYTE-IDENTICAL' if identical else 'HASH MISMATCH'} vs. the reconstruction"
                    + ("; authorized restore of a malware-flagged artifact" if sec.get("status") == "MALWARE_DETECTED" else ""))
    return {"destination": str(dest), "sha256": digest, "byte_identical": identical, "export_class": exp.get("class")}
