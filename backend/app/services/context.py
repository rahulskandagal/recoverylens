"""Case context shared by the advanced services: persisted records + read-only evidence access."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .. import db

CASES = db.DATA / "cases"


class CaseNotReady(Exception):
    pass


@dataclass
class CaseCtx:
    id: str
    row: dict[str, Any]
    files: list[dict[str, Any]]
    _frag_cache: dict[str, dict[str, Any]] | None = field(default=None, repr=False)

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, cid: str, allow_running: bool = False) -> "CaseCtx":
        row = db.one("SELECT * FROM cases WHERE id=?", (cid,))
        if not row:
            raise KeyError(cid)
        if row["status"] != "complete" and not (allow_running and row["status"] == "running"):
            raise CaseNotReady(f"case {cid} is {row['status']}")
        files = [json.loads(r["data"]) for r in db.query("SELECT data FROM files WHERE case_id=? ORDER BY priority_score DESC", (cid,))]
        return cls(cid, row, files)

    @property
    def demo(self) -> bool:
        return self.row.get("mode") == "demo"

    @cached_property
    def summary(self) -> dict[str, Any]:
        return json.loads(self.row["summary"]) if self.row.get("summary") else {}

    @property
    def out_dir(self) -> Path:
        return CASES / self.id

    @property
    def image_path(self) -> str:
        return self.row["image_path"]

    def file(self, fid: str) -> dict[str, Any]:
        for f in self.files:
            if f["file_id"] == fid:
                return f
        raise KeyError(fid)

    @property
    def fragments(self) -> dict[str, dict[str, Any]]:
        if self._frag_cache is None:
            self._frag_cache = {}
            for r in db.query("SELECT data, assigned_to FROM fragments WHERE case_id=? ORDER BY offset", (self.id,)):
                d = json.loads(r["data"])
                d["assigned_to"] = r["assigned_to"]
                self._frag_cache[d["id"]] = d
        return self._frag_cache

    @cached_property
    def graph(self) -> dict[str, Any]:
        return db.get_blob(self.id, "graph") or {"nodes": [], "edges": []}

    @cached_property
    def diskmap(self) -> dict[str, Any] | None:
        return db.get_blob(self.id, "diskmap")

    # ---------------------------------------------------------------- evidence (read-only)
    def read(self, offset: int, length: int) -> bytes:
        with open(self.image_path, "rb") as fh:  # evidence is only ever opened with 'rb'
            fh.seek(max(0, offset))
            return fh.read(max(0, length))

    def frag_bytes(self, fid: str, limit: int | None = None) -> bytes:
        fr = self.fragments[fid]
        n = fr["length"] if limit is None else min(fr["length"], limit)
        return self.read(fr["offset"], n)

    def reconstruction_bytes(self, f: dict[str, Any]) -> bytes | None:
        """Exact reconstruction buffer (the export file is written byte-identical to it)."""
        name = (f.get("export") or {}).get("file") or f.get("output_file")
        if not name:
            return None
        p = self.out_dir / "reconstructed" / name
        return p.read_bytes() if p.is_file() else None

    def edges_for(self, node: str) -> list[dict[str, Any]]:
        return [e for e in self.graph.get("edges", []) if node in (e.get("src"), e.get("dst"))]


def seg_at(f: dict[str, Any], a: int, b: int) -> list[dict[str, Any]]:
    """Segments of a file that overlap logical range [a, b)."""
    return [s for s in f.get("segments", []) if s["start"] < b and s["end"] > a]


def range_state(f: dict[str, Any], a: int, b: int) -> tuple[str, list[str]]:
    """recovered | missing | corrupted | partial for logical range [a, b), plus source fragments."""
    segs = seg_at(f, a, b)
    if not segs:
        return ("missing" if b > a else "recovered"), []
    kinds = {s["kind"] for s in segs}
    frags = sorted({s["fragment_id"] for s in segs if s["fragment_id"]})
    if kinds == {"recovered"}:
        return "recovered", frags
    if kinds == {"missing"}:
        return "missing", frags
    if "missing" in kinds:
        return "partial", frags
    return "corrupted", frags
