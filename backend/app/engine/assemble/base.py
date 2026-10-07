"""Shared assembler context and generic relationship features."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..common import CLUSTER, Candidate, Edge, Segment, histogram
from ..model import EdgeModel
from ..scanner import EvidenceImage, Fragment, ScanResult


@dataclass
class Ctx:
    img: EvidenceImage
    scan: ScanResult
    model: EdgeModel
    log: Callable[[str, str], None]
    assigned: dict[str, str] = field(default_factory=dict)  # fragment id -> candidate id
    assigned_clusters: set[int] = field(default_factory=set)
    candidates: list[Candidate] = field(default_factory=list)
    _n: int = 0

    def next_id(self) -> str:
        self._n += 1
        return f"R{self._n:03d}"

    def free(self, f: Fragment) -> bool:
        return f.id not in self.assigned and f.duplicate_of is None

    def claim(self, cand_id: str, frags: list[Fragment]) -> None:
        for f in frags:
            self.assigned[f.id] = cand_id
            self.assigned_clusters.update(range(f.start_cluster, f.end_cluster))

    def claim_clusters(self, clusters: range | list[int]) -> None:
        self.assigned_clusters.update(clusters)

    def pool(self, *families: str) -> list[Fragment]:
        return [f for f in self.scan.fragments if f.family in families and self.free(f)]

    def fat_entry_for(self, start_cluster: int) -> dict[str, Any] | None:
        for e in self.scan.dir_entries:
            if e["image_cluster"] == start_cluster:
                return e
        return None


def entropy_sim(a: Fragment, b: Fragment) -> float:
    ha = a.clusters[-1].entropy
    hb = b.clusters[0].entropy
    return 1.0 - abs(ha - hb) / 8.0


def hist_sim(img: EvidenceImage, a: Fragment, b: Fragment) -> float:
    ha = histogram(img.cluster(a.end_cluster - 1))
    hb = histogram(img.cluster(b.start_cluster))
    return float(np.dot(ha, hb))


def proximity(a: Fragment, b: Fragment) -> float:
    d = b.start_cluster - a.end_cluster
    if d >= 0:
        return math.exp(-d / 96.0)
    return 0.5 * math.exp(-abs(d) / 96.0)


WORD_RE = re.compile(rb"[A-Za-z]{3,}")


def token_sim(x: bytes, y: bytes) -> float:
    a = Counter(w.lower() for w in WORD_RE.findall(x))
    b = Counter(w.lower() for w in WORD_RE.findall(y))
    if not a or not b:
        return 0.5
    num = sum(a[k] * b[k] for k in a.keys() & b.keys())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def generic_feats(ctx: Ctx, a: Fragment, b: Fragment) -> dict[str, float]:
    return {
        "type_compat": 1.0 if a.family == b.family else 0.0,
        "entropy_sim": entropy_sim(a, b),
        "hist_sim": hist_sim(ctx.img, a, b),
        "proximity": proximity(a, b),
    }


def edge_from(ctx: Ctx, a_id: str, b_id: str, feats: dict[str, float], evidence: list[str],
              etype: str = "continuation", truth_ctx: dict[str, Any] | None = None) -> Edge:
    tc = dict(truth_ctx or {"a": a_id, "b": b_id})
    tc["etype"] = etype
    p, contrib = ctx.model.predict(feats, tc)
    return Edge(a_id, b_id, etype, p, evidence, contrib[:5], features=dict(feats))


def frag_list_bytes(ctx: Ctx, frags: list[Fragment]) -> bytes:
    return b"".join(ctx.img.frag_bytes(f) for f in frags)


Placement = tuple[int, Fragment, int, str]  # (logical start, fragment, anchor strength, method)


def materialize(ctx: Ctx, placements: list[Placement], total: int,
                anchors: dict[str, list[int]] | None = None) -> tuple[bytes, list[Segment], list[Fragment]]:
    """Resolve placements at cluster granularity.

    Evidence for a cluster's position decays with its distance from a verified anchor
    (header, object header, local header) inside the same fragment. So when a fragment
    also carries unrelated clusters (merged with adjacent filler), a better-anchored
    fragment wins those logical slots. Ties are left to the format checksum (CRC) stage.
    """
    from ..common import Segment, fill_gaps
    anchors = anchors or {}
    claims: list[tuple[int, int, int, Fragment, int, str]] = []  # (distance, -strength, slot, frag, idx, how)
    for start, f, strength, how in placements:
        anc = anchors.get(f.id) or ([0] if how == "header" else [f.n_clusters - 1] if how == "central directory" else list(range(f.n_clusters)))
        for i in range(f.n_clusters):
            lo = start + i * CLUSTER
            if lo < 0 or lo >= total:
                continue
            key = lo // CLUSTER if start % CLUSTER == 0 else lo
            claims.append((min(abs(i - a) for a in anc), -strength, key, f, i, how))
    slot: dict[int, tuple[Fragment, int, str]] = {}
    for _, _, key, f, i, how in sorted(claims, key=lambda t: (t[0], t[1])):
        if key not in slot:
            slot[key] = (f, i, how)
    buf = bytearray(total)
    segs: list[Segment] = []
    used: list[Fragment] = []
    for key in sorted(slot):
        f, i, how = slot[key]
        lo = key * CLUSTER
        n = min(CLUSTER, total - lo)
        src = f.offset + i * CLUSTER
        buf[lo:lo + n] = ctx.img.read(src, n)
        if segs and segs[-1].fragment_id == f.id and segs[-1].start + segs[-1].length == lo and \
                segs[-1].source_offset + segs[-1].length == src:
            segs[-1].length += n
        else:
            segs.append(Segment(lo, n, "recovered", f.id, src, how))
        if f not in used:
            used.append(f)
    return bytes(buf), fill_gaps(segs, total), used


def safe_name(s: str, ext: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip()).strip("_") or "recovered"
    return s if s.lower().endswith("." + ext) else f"{s}.{ext}"


__all__ = ["Ctx", "CLUSTER", "Candidate", "Edge", "generic_feats", "edge_from", "token_sim",
           "frag_list_bytes", "safe_name", "proximity", "entropy_sim", "hist_sim"]
