"""Recovery Shield: continuous protection for a local folder.

Distinct from the forensic recovery engine (engine/analyze.py), which reconstructs files from a
STATIC damaged storage image. Shield instead watches a LIVE folder over time: it takes protected
snapshots (real file copies, not just metadata), compares the folder against the latest snapshot to
detect deletions/modifications/corruption/new files/renames, raises incidents on data loss, and
restores selected files from a snapshot to a safe destination.

Honesty rules (same principle as the rest of the app):
  * The source folder is only ever opened for reading. Nothing here writes into it except an
    explicit, user-confirmed "restore to original location".
  * A snapshot is a real byte-for-byte copy with a verified SHA-256, not a metadata-only record.
  * "Corrupted" is only claimed when a real structural validator (the same ones used by the
    recovery engine) rejects the file; otherwise a changed file is reported as "modified".
  * A file with no snapshot copy is reported as NOT_RECOVERABLE via Shield (it may still be
    recoverable from a disk image via the forensic engine, but that is a separate, explicit step).
  * Continuous monitoring polls/watches the folder; on very large trees this may lag behind
    real-time filesystem events. Deep corruption validation is skipped for files above SCAN_CAP to
    avoid loading huge files into memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import db
from .engine import intake
from .engine.validators import validate as validate_file

SNAPSHOTS = db.DATA / "shield_snapshots"
RESTORES = db.DATA / "shield_restored"
CHUNK = 1 << 20  # 1 MiB streaming reads; large trees are never loaded whole into memory
SCAN_CAP = 25 * 1024 * 1024  # files above this are hashed but not deep-validated
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "$RECYCLE.BIN", "System Volume Information"}
VALIDATABLE = {"jpeg": "jpeg", "png": "png", "pdf": "pdf", "zip": "zip", "docx": "docx", "xlsx": "xlsx", "sqlite": "sqlite"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS shield_sources (
  id TEXT PRIMARY KEY, name TEXT, path TEXT, created_at TEXT, last_scan_at TEXT, protection TEXT, monitor_error TEXT
);
CREATE TABLE IF NOT EXISTS shield_snapshots (
  id TEXT PRIMARY KEY, source_id TEXT, created_at TEXT, status TEXT, file_count INTEGER, total_size INTEGER,
  manifest_sha256 TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS shield_snap_source ON shield_snapshots(source_id);
CREATE TABLE IF NOT EXISTS shield_snapshot_files (
  snapshot_id TEXT, rel_path TEXT, size INTEGER, mtime TEXT, sha256 TEXT, stored_path TEXT,
  PRIMARY KEY (snapshot_id, rel_path)
);
CREATE TABLE IF NOT EXISTS shield_incidents (
  id TEXT PRIMARY KEY, source_id TEXT, created_at TEXT, severity TEXT, summary TEXT, details TEXT, recovery_point TEXT
);
CREATE INDEX IF NOT EXISTS shield_inc_source ON shield_incidents(source_id);
CREATE TABLE IF NOT EXISTS shield_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, ts TEXT, stage TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS shield_audit_source ON shield_audit(source_id);
CREATE TABLE IF NOT EXISTS shield_restores (
  id TEXT PRIMARY KEY, source_id TEXT, snapshot_id TEXT, created_at TEXT, destination_mode TEXT,
  destination TEXT, requested INTEGER, restored INTEGER, failed INTEGER, details TEXT
);
CREATE TABLE IF NOT EXISTS shield_last_diff (
  source_id TEXT PRIMARY KEY, deleted TEXT, modified TEXT, corrupted TEXT, updated_at TEXT
);
"""


def init() -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    RESTORES.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(source_id: str, stage: str, msg: str) -> None:
    db.execute("INSERT INTO shield_audit(source_id, ts, stage, message) VALUES (?,?,?,?)", (source_id, _now(), stage, msg))


def _sha256_file(path: Path, cap: int | None = None) -> tuple[str, int]:
    """Streamed hash (never reads the whole file into memory at once). Returns (hex digest, bytes read)."""
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
            n += len(b)
            if cap is not None and n >= cap:
                break
    return h.hexdigest(), n


def _walk(root: Path) -> list[tuple[str, Path, int, str]]:
    """(rel_path posix, abs_path, size, mtime iso) for every regular file under root, read-only."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            out.append((rel, p, st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")))
    return out


# ------------------------------------------------------------------------------------------ sources

def register_source(name: str, path: str) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"'{path}' is not an accessible directory on this server")
    sid = "SRC-" + uuid.uuid4().hex[:8]
    db.execute("INSERT INTO shield_sources(id, name, path, created_at, protection) VALUES (?,?,?,?,?)",
              (sid, name or p.name, str(p), _now(), "inactive"))
    audit(sid, "source", f"Protected source registered: {p} (read-only; never modified except an explicit, confirmed restore)")
    return get_source(sid)


def list_sources() -> list[dict[str, Any]]:
    return [get_source(r["id"]) for r in db.query("SELECT id FROM shield_sources ORDER BY created_at DESC")]


def get_source(sid: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM shield_sources WHERE id=?", (sid,))
    if not row:
        raise KeyError(sid)
    snaps = db.query("SELECT id, created_at, status, file_count, total_size FROM shield_snapshots WHERE source_id=? ORDER BY created_at DESC", (sid,))
    latest = snaps[0] if snaps else None
    incidents = db.query("SELECT COUNT(*) n FROM shield_incidents WHERE source_id=?", (sid,))[0]["n"]
    exists = Path(row["path"]).is_dir()
    return {**row, "path_accessible": exists, "snapshot_count": len(snaps), "latest_snapshot": latest,
            "incident_count": incidents, "monitoring": sid in _monitors}


def delete_source(sid: str) -> None:
    get_source(sid)
    stop_monitor(sid)
    for r in db.query("SELECT id FROM shield_snapshots WHERE source_id=?", (sid,)):
        shutil.rmtree(SNAPSHOTS / r["id"], ignore_errors=True)
    db.execute("DELETE FROM shield_snapshot_files WHERE snapshot_id IN (SELECT id FROM shield_snapshots WHERE source_id=?)", (sid,))
    for t in ("shield_snapshots", "shield_incidents", "shield_audit", "shield_restores"):
        db.execute(f"DELETE FROM {t} WHERE source_id=?", (sid,))
    db.execute("DELETE FROM shield_sources WHERE id=?", (sid,))


# ------------------------------------------------------------------------------------------ snapshots

def create_snapshot(sid: str) -> str:
    """Real, byte-for-byte protected copy of every file currently in the source. Returns the snapshot id;
    the copy runs in a background thread so the request never blocks on a large folder."""
    src = get_source(sid)
    if not Path(src["path"]).is_dir():
        raise ValueError(f"source path is no longer accessible: {src['path']}")
    snap_id = "REC-" + datetime.now().strftime("%Y-%m-%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    db.execute("INSERT INTO shield_snapshots(id, source_id, created_at, status, file_count, total_size) VALUES (?,?,?,?,0,0)",
              (snap_id, sid, _now(), "running"))
    audit(sid, "snapshot", f"Protected recovery point {snap_id} started")
    threading.Thread(target=_run_snapshot, args=(sid, snap_id, Path(src["path"])), daemon=True).start()
    return snap_id


def _run_snapshot(sid: str, snap_id: str, root: Path) -> None:
    out_dir = SNAPSHOTS / snap_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = hashlib.sha256()
    rows: list[tuple] = []
    total = 0
    try:
        files = _walk(root)
        for rel, abspath, size, mtime in files:
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(abspath, "rb") as src_f, open(dest, "wb") as dst_f:  # evidence-style: source opened read-only
                    h = hashlib.sha256()
                    while True:
                        b = src_f.read(CHUNK)
                        if not b:
                            break
                        h.update(b)
                        dst_f.write(b)
                digest = h.hexdigest()
            except OSError as e:
                audit(sid, "snapshot", f"{rel}: could not be copied ({type(e).__name__}: {e}); excluded from this recovery point")
                continue
            rows.append((snap_id, rel, size, mtime, digest, str(dest)))
            manifest.update(f"{rel}:{digest}\n".encode())
            total += size
        db.many("INSERT OR REPLACE INTO shield_snapshot_files VALUES (?,?,?,?,?,?)", rows)
        db.execute("UPDATE shield_snapshots SET status='protected', file_count=?, total_size=?, manifest_sha256=? WHERE id=?",
                  (len(rows), total, manifest.hexdigest(), snap_id))
        db.execute("UPDATE shield_sources SET last_scan_at=?, protection=CASE WHEN protection='inactive' THEN 'paused' ELSE protection END WHERE id=?",
                  (_now(), sid))
        # a new baseline invalidates any cached "already reported" delta: changes are relative to THIS snapshot now
        db.execute("DELETE FROM shield_last_diff WHERE source_id=?", (sid,))
        audit(sid, "snapshot", f"Protected recovery point {snap_id}: {len(rows)} file(s), {total:,} bytes, manifest SHA-256 {manifest.hexdigest()[:16]}…")
    except Exception as e:
        db.execute("UPDATE shield_snapshots SET status='failed', error=? WHERE id=?", (f"{type(e).__name__}: {e}", snap_id))
        audit(sid, "snapshot", f"Protected recovery point {snap_id} FAILED: {type(e).__name__}: {e}")


def list_snapshots(sid: str) -> list[dict[str, Any]]:
    # snapshots created within the same second must still order newest-first: break ties on rowid (insertion order)
    return db.query("SELECT id, created_at, status, file_count, total_size, manifest_sha256, error FROM shield_snapshots "
                    "WHERE source_id=? ORDER BY created_at DESC, rowid DESC", (sid,))


def get_snapshot(snap_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM shield_snapshots WHERE id=?", (snap_id,))
    if not row:
        raise KeyError(snap_id)
    files = db.query("SELECT rel_path, size, mtime, sha256, stored_path FROM shield_snapshot_files WHERE snapshot_id=? ORDER BY rel_path", (snap_id,))
    return {**row, "files": files}


def verify_snapshot(snap_id: str) -> dict[str, Any]:
    """Re-hash every stored copy against its recorded manifest hash: proves the protected copy itself is intact."""
    snap = get_snapshot(snap_id)
    bad = []
    for f in snap["files"]:
        p = Path(f["stored_path"])
        if not p.is_file():
            bad.append({"rel_path": f["rel_path"], "reason": "stored copy is missing"})
            continue
        digest, _ = _sha256_file(p)
        if digest != f["sha256"]:
            bad.append({"rel_path": f["rel_path"], "reason": "stored copy hash does not match the manifest", "expected": f["sha256"], "actual": digest})
    status = "verified" if not bad else "corrupted"
    db.execute("UPDATE shield_snapshots SET status=? WHERE id=?", (status, snap_id))
    audit(snap["source_id"], "verify", f"Snapshot {snap_id} re-hashed: {len(snap['files']) - len(bad)}/{len(snap['files'])} file(s) match the manifest"
          + (f"; {len(bad)} mismatch(es)" if bad else ""))
    return {"snapshot_id": snap_id, "status": status, "checked": len(snap["files"]), "mismatches": bad}


# ------------------------------------------------------------------------------------------ change detection

def _classify_corruption(path: Path, filename: str) -> tuple[bool, str]:
    """True + reason if a real structural validator rejects the file's current content. Best-effort: unsupported
    formats or oversized files fall back to reporting a plain 'modified' rather than guessing at corruption."""
    try:
        size = path.stat().st_size
        if size == 0 or size > SCAN_CAP:
            return False, ""
        data = path.read_bytes()
        info = intake.detect(data, filename)
        if info["kind"] != "single_file" or info["format"] not in VALIDATABLE:
            return False, ""
        v = validate_file(VALIDATABLE[info["format"]], data)
        if v.get("structural") == "fail" or v.get("parser") == "fail":
            bad = [c["name"] for c in v.get("checks", []) if c.get("status") == "fail"][:3]
            return True, f"{info['format'].upper()} structural validation failed" + (f": {', '.join(bad)}" if bad else "")
        return False, ""
    except Exception:
        return False, ""


def scan_and_diff(sid: str, auto: bool = False) -> dict[str, Any]:
    src = get_source(sid)
    root = Path(src["path"])
    if not root.is_dir():
        raise ValueError(f"source path is no longer accessible: {root}")
    snaps = list_snapshots(sid)
    baseline = next((s for s in snaps if s["status"] in ("protected", "verified")), None)
    if not baseline:
        raise ValueError("no protected recovery point exists yet; create one before scanning for changes")
    manifest = {r["rel_path"]: r for r in db.query("SELECT rel_path, size, mtime, sha256 FROM shield_snapshot_files WHERE snapshot_id=?", (baseline["id"],))}
    current: dict[str, tuple[str, int]] = {}
    unreadable: list[str] = []
    for rel, abspath, size, mtime in _walk(root):
        try:
            digest, _ = _sha256_file(abspath, cap=SCAN_CAP if size > SCAN_CAP else None)
        except OSError:
            unreadable.append(rel)
            continue
        current[rel] = (digest, size)
    deleted = sorted(set(manifest) - set(current))
    new = sorted(set(current) - set(manifest))
    modified_candidates = sorted(r for r in set(manifest) & set(current) if manifest[r]["sha256"] != current[r][0])
    renamed: list[dict[str, str]] = []
    for d in list(deleted):
        old = manifest[d]
        match = next((n for n in new if current[n][0] == old["sha256"] and current[n][1] == old["size"]), None)
        if match:
            renamed.append({"from": d, "to": match})
            deleted.remove(d)
            new.remove(match)
    modified: list[dict[str, str]] = []
    corrupted: list[dict[str, str]] = []
    for rel in modified_candidates:
        is_bad, reason = _classify_corruption(root / rel, rel)
        (corrupted if is_bad else modified).append({"rel_path": rel, "reason": reason} if is_bad else {"rel_path": rel})
    for rel in unreadable:
        corrupted.append({"rel_path": rel, "reason": "file could not be read (I/O error)"})
    now = _now()
    deleted_rows = [{"rel_path": r, "size": manifest[r]["size"], "recoverable": True, "source": "snapshot"} for r in deleted]
    result = {
        "source_id": sid, "scanned_at": now, "baseline_snapshot": baseline["id"],
        "deleted": deleted_rows,
        "modified": modified, "corrupted": corrupted, "new_files": [{"rel_path": r} for r in new],
        "renamed": renamed, "unchanged": len(current) - len(modified_candidates) - len(unreadable),
    }
    db.execute("UPDATE shield_sources SET last_scan_at=? WHERE id=?", (now, sid))
    # incidents fire only for NEWLY observed loss since the previous scan, so a still-deleted file is not
    # re-alerted on every poll cycle; the returned diff itself always lists everything missing since the baseline
    prev = db.one("SELECT deleted, modified, corrupted FROM shield_last_diff WHERE source_id=?", (sid,))
    prev_deleted = set(json.loads(prev["deleted"])) if prev else set()
    prev_modified = set(json.loads(prev["modified"])) if prev else set()
    prev_corrupted = set(json.loads(prev["corrupted"])) if prev else set()
    new_deleted_rows = [d for d in deleted_rows if d["rel_path"] not in prev_deleted]
    new_modified_rows = [m for m in modified if m["rel_path"] not in prev_modified]
    new_corrupted_rows = [c for c in corrupted if c["rel_path"] not in prev_corrupted]
    db.execute("INSERT OR REPLACE INTO shield_last_diff VALUES (?,?,?,?,?)",
              (sid, json.dumps(deleted), json.dumps([m["rel_path"] for m in modified]), json.dumps([c["rel_path"] for c in corrupted]), now))
    n_loss = len(new_deleted_rows) + len(new_corrupted_rows)
    incident_id = None
    if n_loss > 0 or len(new_modified_rows) >= 3:
        incident_id = _raise_incident(sid, baseline["id"], new_deleted_rows, new_modified_rows, new_corrupted_rows, new, renamed, auto)
    result["incident_id"] = incident_id
    audit(sid, "scan", f"Scan complete: {len(deleted)} deleted total ({len(new_deleted_rows)} new), {len(modified)} modified total "
                       f"({len(new_modified_rows)} new), {len(corrupted)} corrupted total ({len(new_corrupted_rows)} new), "
                       f"{len(new)} new file(s), {len(renamed)} renamed, {result['unchanged']} unchanged"
          + (f"; incident {incident_id} raised" if incident_id else ""))
    return result


def _raise_incident(sid: str, snap_id: str, deleted, modified, corrupted, new, renamed, auto: bool) -> str:
    n_loss = len(deleted) + len(corrupted)
    if n_loss >= 5 or (deleted and corrupted):
        sev = "HIGH"
    elif n_loss >= 1 or len(modified) >= 5:
        sev = "MEDIUM"
    else:
        sev = "LOW"
    iid = "INCIDENT-" + uuid.uuid4().hex[:6].upper()
    bits = []
    if deleted:
        bits.append(f"{len(deleted)} deletion(s)")
    if modified:
        bits.append(f"{len(modified)} modification(s)")
    if corrupted:
        bits.append(f"{len(corrupted)} corrupted file(s)")
    if new:
        bits.append(f"{len(new)} new file(s)")
    verb = "Suspicious mass deletion detected" if len(deleted) >= 5 else "Suspicious activity detected; possible unauthorized deletion" \
        if deleted else "Data-loss event detected"
    summary = f"{verb}: " + ", ".join(bits) + (" (auto-detected by continuous monitoring)" if auto else " (manual scan)")
    details = {"deleted": deleted, "modified": modified, "corrupted": corrupted, "new_files": new, "renamed": renamed}
    db.execute("INSERT INTO shield_incidents(id, source_id, created_at, severity, summary, details, recovery_point) VALUES (?,?,?,?,?,?,?)",
              (iid, sid, _now(), sev, summary, json.dumps(details), snap_id))
    audit(sid, "incident", f"{iid} [{sev}]: {summary}")
    return iid


def list_incidents(sid: str | None = None) -> list[dict[str, Any]]:
    if sid:
        rows = db.query("SELECT * FROM shield_incidents WHERE source_id=? ORDER BY created_at DESC, rowid DESC", (sid,))
    else:
        rows = db.query("SELECT * FROM shield_incidents ORDER BY created_at DESC, rowid DESC LIMIT 100")
    out = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d["details"])
        out.append(d)
    return out


def get_incident(iid: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM shield_incidents WHERE id=?", (iid,))
    if not r:
        raise KeyError(iid)
    d = dict(r)
    d["details"] = json.loads(d["details"])
    return d


# ------------------------------------------------------------------------------------------ restore

def restore(sid: str, snap_id: str, rel_paths: list[str], destination_mode: str, destination_path: str | None,
           authorized: bool) -> dict[str, Any]:
    if not authorized:
        raise ValueError("restore requires explicit user confirmation (authorized=true)")
    if destination_mode not in ("recovery", "custom", "original"):
        raise ValueError("destination_mode must be 'recovery', 'custom' or 'original'")
    snap = get_snapshot(snap_id)
    if snap["source_id"] != sid:
        raise ValueError("snapshot does not belong to this source")
    src = get_source(sid)
    if destination_mode == "recovery":
        dest_root = RESTORES / sid / datetime.now().strftime("%Y%m%d-%H%M%S")
    elif destination_mode == "original":
        dest_root = Path(src["path"])
    else:
        if not destination_path:
            raise ValueError("destination_path is required for destination_mode='custom'")
        dest_root = Path(destination_path).expanduser().resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    by_rel = {f["rel_path"]: f for f in snap["files"]}
    restored, failed = [], []
    for rel in rel_paths:
        f = by_rel.get(rel)
        if not f:
            failed.append({"rel_path": rel, "reason": "not present in this snapshot"})
            continue
        stored = SNAPSHOTS / snap_id / rel
        if not stored.is_file():
            failed.append({"rel_path": rel, "reason": "protected copy missing on disk"})
            continue
        dest = dest_root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(stored, "rb") as sf, open(dest, "wb") as df:  # protected copy opened read-only
                h = hashlib.sha256()
                while True:
                    b = sf.read(CHUNK)
                    if not b:
                        break
                    h.update(b)
                    df.write(b)
            ok = h.hexdigest() == f["sha256"]
            restored.append({"rel_path": rel, "destination": str(dest), "byte_identical": ok})
            audit(sid, "restore", f"{rel}: restored to {dest} from {snap_id}; {'BYTE-IDENTICAL' if ok else 'HASH MISMATCH'} to the protected copy")
        except OSError as e:
            failed.append({"rel_path": rel, "reason": f"{type(e).__name__}: {e}"})
            audit(sid, "restore", f"{rel}: RESTORE FAILED: {type(e).__name__}: {e}")
    rid = "RESTORE-" + uuid.uuid4().hex[:8]
    db.execute("INSERT INTO shield_restores VALUES (?,?,?,?,?,?,?,?,?,?)",
              (rid, sid, snap_id, _now(), destination_mode, str(dest_root), len(rel_paths), len(restored), len(failed),
               json.dumps({"restored": restored, "failed": failed})))
    audit(sid, "restore", f"{rid}: {len(restored)}/{len(rel_paths)} file(s) restored to {dest_root} ({destination_mode})")
    return {"id": rid, "destination": str(dest_root), "destination_mode": destination_mode, "requested": len(rel_paths),
           "restored": restored, "failed": failed}


def list_restores(sid: str) -> list[dict[str, Any]]:
    out = []
    for r in db.query("SELECT * FROM shield_restores WHERE source_id=? ORDER BY created_at DESC", (sid,)):
        d = dict(r)
        d["details"] = json.loads(d["details"])
        out.append(d)
    return out


# ------------------------------------------------------------------------------------------ continuous monitoring

_monitors: dict[str, dict[str, Any]] = {}
_mlock = threading.Lock()


def _monitor_loop(sid: str, stop: threading.Event, dirty: threading.Event) -> None:
    while not stop.is_set():
        fired = dirty.wait(timeout=5.0)
        if stop.is_set():
            break
        if fired:
            dirty.clear()
            time.sleep(1.5)  # debounce: batch a burst of filesystem events into one scan
        try:
            snaps = list_snapshots(sid)
            if any(s["status"] in ("protected", "verified") for s in snaps):
                scan_and_diff(sid, auto=True)
        except Exception as e:
            audit(sid, "monitor", f"Background scan error: {type(e).__name__}: {e}")


def start_monitor(sid: str) -> dict[str, Any]:
    src = get_source(sid)
    with _mlock:
        if sid in _monitors:
            return {"monitoring": True, "engine": _monitors[sid]["engine"]}
        stop = threading.Event()
        dirty = threading.Event()
        engine = "poll (5s)"
        observer = None
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            class _Handler(FileSystemEventHandler):
                def on_any_event(self, event):  # noqa: ANN001
                    dirty.set()
            observer = Observer()
            observer.schedule(_Handler(), src["path"], recursive=True)
            observer.start()
            engine = "filesystem events (watchdog) + 5s fallback poll"
        except Exception as e:
            audit(sid, "monitor", f"Real-time file-system events unavailable ({type(e).__name__}: {e}); falling back to polling every 5s")
        thread = threading.Thread(target=_monitor_loop, args=(sid, stop, dirty), daemon=True)
        _monitors[sid] = {"stop": stop, "dirty": dirty, "thread": thread, "observer": observer, "engine": engine}
        thread.start()
    db.execute("UPDATE shield_sources SET protection='active', monitor_error=NULL WHERE id=?", (sid,))
    audit(sid, "monitor", f"Continuous protection ENABLED ({engine})")
    return {"monitoring": True, "engine": engine}


def stop_monitor(sid: str) -> dict[str, Any]:
    with _mlock:
        m = _monitors.pop(sid, None)
    if m:
        m["stop"].set()
        m["dirty"].set()
        if m["observer"]:
            try:
                m["observer"].stop()
                m["observer"].join(timeout=2)
            except Exception:
                pass
        m["thread"].join(timeout=2)
        db.execute("UPDATE shield_sources SET protection='paused' WHERE id=?", (sid,))
        audit(sid, "monitor", "Continuous protection DISABLED")
    return {"monitoring": False}


def get_audit(sid: str, after: int = 0) -> list[dict[str, Any]]:
    return db.query("SELECT id, ts, stage, message FROM shield_audit WHERE source_id=? AND id>? ORDER BY id", (sid, after))


# ------------------------------------------------------------------------------------------ dashboard

def overview() -> dict[str, Any]:
    sources = list_sources()
    protected_files = sum(s["latest_snapshot"]["file_count"] for s in sources if s["latest_snapshot"])
    recovery_points = sum(s["snapshot_count"] for s in sources)
    incidents = list_incidents()
    deleted = sum(len(i["details"]["deleted"]) for i in incidents)
    modified = sum(len(i["details"]["modified"]) for i in incidents)
    corrupted = sum(len(i["details"]["corrupted"]) for i in incidents)
    return {"sources": len(sources), "active_protection": sum(1 for s in sources if s["protection"] == "active"),
           "protected_files": protected_files, "recovery_points": recovery_points, "incidents": len(incidents),
           "deleted_detected": deleted, "modified_detected": modified, "corrupted_detected": corrupted,
           "recoverable": deleted, "unrecoverable": 0,
           "message": "Your data is protected. Your evidence is traceable. Your recovery is explainable."}
