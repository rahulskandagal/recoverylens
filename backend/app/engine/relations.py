"""Cross-artifact relationships: container, content similarity, temporal, duplicate.

These are *inferred* relationships between recovered artifacts, distinct from the
fragment-continuation evidence used during reconstruction.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from .common import Edge

STOP = set("the and for with from that this are was were have has into over page of to in on by at as is it be".split())


def _terms(text: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP)


def cross_relations(files: list[dict[str, Any]], scan) -> list[Edge]:
    edges: list[Edge] = []
    # container relationships (embedded members)
    for f in files:
        for m in f.get("embedded", []):
            edges.append(Edge(f["file_id"], m["node_id"], "container", 0.99 if m["status"] == "ok" else 0.9,
                              [f"'{m['name']}' is a member of {f['file_name']} (listed in its central directory)",
                               f"Member status: {m['status']}"]))
    # content similarity (tf-idf cosine over extracted text)
    docs = {f["file_id"]: _terms(f.get("search_text", "")) for f in files if len(f.get("search_text", "")) > 200}
    df: Counter = Counter()
    for t in docs.values():
        df.update(set(t))
    n = max(1, len(docs))
    vec = {k: {w: c * math.log(1 + n / df[w]) for w, c in t.items()} for k, t in docs.items()}
    ids = list(vec)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            va, vb = vec[a], vec[b]
            num = sum(va[w] * vb[w] for w in va.keys() & vb.keys())
            den = math.sqrt(sum(x * x for x in va.values())) * math.sqrt(sum(x * x for x in vb.values()))
            sim = num / den if den else 0
            if sim >= 0.3:
                shared = sorted(va.keys() & vb.keys(), key=lambda w: -(va[w] * vb[w]))[:6]
                edges.append(Edge(a, b, "content_similarity", min(0.85, sim), [
                    f"TF-IDF cosine similarity {sim:.2f} between extracted texts",
                    f"Shared distinctive terms: {', '.join(shared)}",
                    "Similar content suggests a topical relationship, not common origin"]))
    # temporal proximity
    def dt(f: dict[str, Any]) -> datetime | None:
        s = f.get("modified")
        try:
            return datetime.fromisoformat(s.replace("Z", "")) if s else None
        except ValueError:
            return None
    timed = [(f, dt(f)) for f in files if dt(f)]
    for i, (a, ta) in enumerate(timed):
        for b, tb in timed[i + 1:]:
            h = abs((ta - tb).total_seconds()) / 3600
            if h <= 24:
                edges.append(Edge(a["file_id"], b["file_id"], "temporal", round(0.6 * math.exp(-h / 12), 3), [
                    f"Modification times {ta:%Y-%m-%d %H:%M} and {tb:%Y-%m-%d %H:%M} are {h:.1f} h apart",
                    "Temporal proximity alone is weak evidence"]))
    # duplicate fragments
    for fr in scan.fragments:
        if fr.duplicate_of:
            edges.append(Edge(fr.duplicate_of, fr.id, "duplicate", 0.99,
                              [f"{fr.id} is a byte-identical copy (SHA-256 {fr.sha256[:16]}...) of {fr.duplicate_of}"]))
    return edges
