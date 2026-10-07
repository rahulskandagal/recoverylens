"""Shared constants and small helpers used across the recovery engine."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

SECTOR = 512
CLUSTER = 4096
ANALYSIS_VERSION = "recoverylens-engine 0.9.0"

# Signatures checked at cluster starts (files on most filesystems start on a cluster boundary).
HEADER_SIGS: dict[str, bytes] = {
    "jpeg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
    "pdf": b"%PDF-",
    "zip": b"PK\x03\x04",
    "sqlite": b"SQLite format 3\x00",
    "gif": b"GIF8",
    "mp4": b"ftyp",  # at offset 4
}

TS_RE = re.compile(rb"(20\d\d-[01]\d-[0-3]\d[T ][0-2]\d:[0-5]\d:[0-5]\d)")


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    counts = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256).astype(np.float64)
    p = counts[counts > 0] / len(b)
    return float(-(p * np.log2(p)).sum())


def histogram(b: bytes) -> np.ndarray:
    h = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256).astype(np.float64)
    n = np.linalg.norm(h)
    return h / n if n else h


def printable_ratio(b: bytes) -> float:
    if not b:
        return 0.0
    a = np.frombuffer(b, dtype=np.uint8)
    ok = ((a >= 32) & (a < 127)) | (a == 9) | (a == 10) | (a == 13)
    return float(ok.mean())


def hexoff(n: int) -> str:
    return f"0x{n:08X}"


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def rel_label(conf: float) -> str:
    """Map a 0-1 relationship probability to calibrated language (never 'certain')."""
    if conf >= 0.9:
        return "Strong"
    if conf >= 0.75:
        return "Probable"
    if conf >= 0.55:
        return "Possible"
    if conf >= 0.35:
        return "Weak"
    return "Uncertain"


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))


@dataclass
class Segment:
    """A range of the reconstructed (logical) file and where its bytes came from."""
    start: int
    length: int
    kind: str  # "recovered" | "missing" | "corrupted"
    fragment_id: str | None = None
    source_offset: int | None = None  # absolute offset in the evidence image
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start, "end": self.start + self.length, "length": self.length,
            "kind": self.kind, "fragment_id": self.fragment_id,
            "source_offset": self.source_offset,
            "source_offset_hex": hexoff(self.source_offset) if self.source_offset is not None else None,
            "note": self.note,
        }


@dataclass
class Edge:
    src: str
    dst: str
    type: str  # continuation | structural | metadata | content_similarity | temporal | container | duplicate | member
    confidence: float  # 0..1
    evidence: list[str] = field(default_factory=list)
    contributions: list[dict[str, Any]] = field(default_factory=list)
    chosen: bool = True
    features: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src, "dst": self.dst, "type": self.type,
            "confidence": round(self.confidence * 100, 1),
            "label": rel_label(self.confidence),
            "evidence": self.evidence, "contributions": self.contributions, "chosen": self.chosen,
            "features": {k: round(v, 3) for k, v in self.features.items()},
        }


@dataclass
class Candidate:
    """A reconstruction candidate produced by an assembler."""
    id: str
    type: str  # technical type: jpeg, pdf, docx, zip, sqlite, log, txt, png, unknown
    name: str
    name_source: str
    fragments: list[str]
    segments: list[Segment]
    expected_size: int | None
    expected_size_source: str
    data: bytes  # reconstructed bytes; missing ranges zero-filled and listed in segments
    edges: list[Edge] = field(default_factory=list)
    structure: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    orphan: bool = False
    header_found: bool = True
    remaining_compatible: int = 0  # unassigned compatible clusters that might still hold content
    operations: list[str] = field(default_factory=list)

    def recovered_bytes(self) -> int:
        return sum(s.length for s in self.segments if s.kind in ("recovered", "corrupted"))

    def missing_ranges(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.segments if s.kind == "missing"]

    def corrupted_ranges(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.segments if s.kind == "corrupted"]


def merge_segments(segs: list[Segment]) -> list[Segment]:
    segs = sorted(segs, key=lambda s: s.start)
    out: list[Segment] = []
    for s in segs:
        if s.length <= 0:
            continue
        if out and out[-1].kind == s.kind == "missing" and out[-1].start + out[-1].length == s.start:
            out[-1].length += s.length
        else:
            out.append(s)
    return out


def fill_gaps(segs: list[Segment], total: int) -> list[Segment]:
    """Insert explicit 'missing' segments for every uncovered byte range in [0, total)."""
    segs = sorted([s for s in segs if s.length > 0], key=lambda s: s.start)
    out: list[Segment] = []
    pos = 0
    for s in segs:
        if s.start > pos:
            out.append(Segment(pos, s.start - pos, "missing", note="No compatible fragment located"))
        if s.start + s.length > pos:
            if s.start < pos:  # overlap: trim
                cut = pos - s.start
                s = Segment(pos, s.length - cut, s.kind, s.fragment_id,
                            (s.source_offset + cut) if s.source_offset is not None else None, s.note)
            out.append(s)
            pos = s.start + s.length
    if total > pos:
        out.append(Segment(pos, total - pos, "missing", note="No compatible fragment located"))
    return merge_segments(out)


def split_corrupted(segs: list[Segment], bad: list[tuple[int, int, str]]) -> list[Segment]:
    """Mark logical byte ranges [a,b) inside recovered segments as corrupted."""
    for a, b, note in bad:
        new: list[Segment] = []
        for s in segs:
            e = s.start + s.length
            if s.kind != "recovered" or b <= s.start or a >= e:
                new.append(s)
                continue
            lo, hi = max(a, s.start), min(b, e)
            src = s.source_offset
            if lo > s.start:
                new.append(Segment(s.start, lo - s.start, "recovered", s.fragment_id, src, s.note))
            new.append(Segment(lo, hi - lo, "corrupted", s.fragment_id,
                               src + (lo - s.start) if src is not None else None, note))
            if hi < e:
                new.append(Segment(hi, e - hi, "recovered", s.fragment_id,
                                   src + (hi - s.start) if src is not None else None, s.note))
        segs = new
    return segs
