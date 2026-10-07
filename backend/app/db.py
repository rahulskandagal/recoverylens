"""SQLite persistence (prototype). The schema maps 1:1 to PostgreSQL for production."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DATA = Path(__file__).resolve().parents[1] / "data"
DB_PATH = DATA / "recoverylens.db"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, name TEXT, mode TEXT, dataset TEXT, status TEXT, stage TEXT, progress REAL,
  created_at TEXT, finished_at TEXT, image_name TEXT, image_path TEXT, image_sha256 TEXT, image_size INTEGER,
  error TEXT, criteria TEXT, summary TEXT, evaluation TEXT, model TEXT, evidence_unchanged INTEGER
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT, stage TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS audit_case ON audit(case_id);
CREATE TABLE IF NOT EXISTS fragments (
  case_id TEXT, id TEXT, offset INTEGER, length INTEGER, family TEXT, entropy REAL, assigned_to TEXT,
  has_header INTEGER, duplicate_of TEXT, data TEXT, PRIMARY KEY (case_id, id)
);
CREATE INDEX IF NOT EXISTS frag_case_family ON fragments(case_id, family);
CREATE TABLE IF NOT EXISTS files (
  case_id TEXT, id TEXT, name TEXT, type TEXT, status TEXT, priority_score REAL, data TEXT, PRIMARY KEY (case_id, id)
);
CREATE TABLE IF NOT EXISTS blobs (case_id TEXT, kind TEXT, data TEXT, PRIMARY KEY (case_id, kind));
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on error, and always close the connection."""
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def execute(sql: str, args: tuple = ()) -> None:
    with _lock, conn() as c:
        c.execute(sql, args)


def many(sql: str, rows: list[tuple]) -> None:
    with _lock, conn() as c:
        c.executemany(sql, rows)


def query(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql: str, args: tuple = ()) -> dict[str, Any] | None:
    r = query(sql, args)
    return r[0] if r else None


def put_blob(case_id: str, kind: str, obj: Any) -> None:
    execute("INSERT OR REPLACE INTO blobs VALUES (?,?,?)", (case_id, kind, json.dumps(obj)))


def get_blob(case_id: str, kind: str) -> Any:
    r = one("SELECT data FROM blobs WHERE case_id=? AND kind=?", (case_id, kind))
    return json.loads(r["data"]) if r else None
