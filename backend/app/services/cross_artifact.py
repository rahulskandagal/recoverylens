"""Cross-Artifact Intelligence: evidence-based relationships BETWEEN recovered artifacts.

Detectors are deterministic (metadata parsers, string/identifier matching, hashing, storage layout)
plus the existing TF-IDF content similarity. Every relationship records type, evidence, confidence,
source and target, and is labelled INFERRED RELATIONSHIP. Nothing here asserts common authorship
or intent; it states which observable facts are shared.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
import zipfile
from datetime import datetime
from typing import Any

import networkx as nx

from ..engine.common import CLUSTER, rel_label
from .context import CaseCtx

ID_PATTERNS = [
    (re.compile(r"\b(?:INV|PO|REQ|TKT|CASE|INC)[-_ ]?\d{3,}\b", re.I), "document identifier"),
    (re.compile(r"\b(?:19|20)\d\d-\d{3,}\b"), "numbered identifier"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "e-mail address"),
    (re.compile(r"\bProject [A-Z][a-z]{2,}\b"), "project name"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "UUID"),
]
TYPES = {"hash_duplicate": "Hash relationship", "container": "Container relationship", "references": "Referenced filename",
         "shared_identifier": "Shared identifier", "shared_metadata": "Shared metadata", "directory": "Shared directory metadata",
         "timestamp": "Shared timestamp", "filename": "Similar filename", "content": "Document content similarity",
         "database": "Database relationship", "storage": "Common storage region"}


def _stem(name: str) -> str:
    n = re.sub(r"__rec\d+", "", name.rsplit(".", 1)[0])
    return re.sub(r"\.FORENSIC-ARTIFACT$", "", n)


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[\W_]+", _stem(name).lower()) if len(t) > 2 and not t.isdigit()}


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "")) if s else None
    except ValueError:
        return None


def _meta(ctx: CaseCtx, f: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    info = (f.get("validation", {}).get("stats", {}) or {}).get("info") or {}
    for k in ("Author", "Creator", "Producer"):
        if info.get(k):
            out[k.lower()] = str(info[k])
    core = (f.get("validation", {}).get("stats", {}) or {}).get("core") or {}
    for k in ("creator", "lastModifiedBy", "title"):
        if core.get(k):
            out["author" if k in ("creator", "lastModifiedBy") else k] = str(core[k])
    ex = (f.get("metadata") or {}).get("exif") or {}
    for k in ("Make", "Model", "Software", "Artist"):
        if ex.get(k):
            out[k.lower()] = str(ex[k])
    return out


def _media_hashes(ctx: CaseCtx, f: dict[str, Any]) -> dict[str, str]:
    if f["file_type"] not in ("docx", "xlsx", "zip"):
        return {}
    data = ctx.reconstruction_bytes(f)
    out = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(data or b""))
        for zi in z.infolist():  # read as data only; never extracted to disk or opened
            if "/media/" in zi.filename or zi.filename.lower().endswith((".jpg", ".jpeg", ".png")):
                try:
                    out[zi.filename] = hashlib.sha256(z.read(zi)).hexdigest()
                except Exception:
                    pass
    except Exception:
        pass
    return out


def analyze(ctx: CaseCtx) -> dict[str, Any]:
    files = [f for f in ctx.files if not f.get("error")]
    rels: list[dict[str, Any]] = []

    def add(t: str, a: dict[str, Any], b: dict[str, Any], conf: float, ev: list[str]) -> None:
        rels.append({"type": t, "type_label": TYPES[t], "source": a["file_id"], "target": b["file_id"],
                     "source_name": a["file_name"], "target_name": b["file_name"], "confidence": round(conf * 100, 1),
                     "label": rel_label(conf), "evidence": ev, "data_class": "INFERRED RELATIONSHIP"})

    texts = {f["file_id"]: (f.get("search_text") or "") for f in files}
    sha = {f["file_id"]: f.get("reconstruction_sha256") or f.get("output_sha256") for f in files}
    metas = {f["file_id"]: _meta(ctx, f) for f in files}
    ids = {fid: {(m.group(0).strip(), lab) for p, lab in ID_PATTERNS for m in p.finditer(t)} for fid, t in texts.items()}
    media = {f["file_id"]: _media_hashes(ctx, f) for f in files}
    spans = {}
    for f in files:
        so = [(s["source_offset"], s["source_offset"] + s["length"]) for s in f.get("segments", []) if s["source_offset"] is not None]
        spans[f["file_id"]] = so
    for i, a in enumerate(files):
        for b in files[i + 1:]:
            A, B = a["file_id"], b["file_id"]
            if sha[A] and sha[A] == sha[B]:
                add("hash_duplicate", a, b, 0.99, [f"identical reconstruction SHA-256 {sha[A][:16]}…"])
            for member, h in media[A].items():
                if h == sha[B]:
                    add("container", a, b, 0.97, [f"member '{member}' of {a['file_name']} has the same SHA-256 as {b['file_name']}"])
            for member, h in media[B].items():
                if h == sha[A]:
                    add("container", b, a, 0.97, [f"member '{member}' of {b['file_name']} has the same SHA-256 as {a['file_name']}"])
            for x, y in ((a, b), (b, a)):
                stem = _stem(y["file_name"])
                if len(stem) >= 5 and re.search(re.escape(stem).replace("_", "[ _]"), texts[x["file_id"]], re.I):
                    add("references", x, y, 0.85, [f"text of {x['file_name']} mentions '{stem}'"])
            shared = ids[A] & ids[B]
            if shared:
                s = sorted(shared)[:5]
                add("shared_identifier", a, b, min(0.8, 0.45 + 0.1 * len(shared)),
                    [f"both contain {lab} '{v}'" for v, lab in s])
            mm = [(k, v) for k, v in metas[A].items() if metas[B].get(k) == v and v.strip()]
            if mm:
                add("shared_metadata", a, b, min(0.75, 0.45 + 0.1 * len(mm)), [f"same {k}: '{v}'" for k, v in mm])
            da, db_ = (a.get("metadata") or {}).get("directory_entry"), (b.get("metadata") or {}).get("directory_entry")
            if da and db_ and da.get("dir_offset") is not None and da.get("dir_offset") == db_.get("dir_offset"):
                add("directory", a, b, 0.5, ["both names come from the same recovered directory cluster"])
            ta, tb = _dt(a.get("modified")), _dt(b.get("modified"))
            if ta and tb:
                h = abs((ta - tb).total_seconds())
                if h <= 3600:
                    add("timestamp", a, b, 0.55 if h <= 300 else 0.4,
                        [f"modification times {ta:%Y-%m-%d %H:%M:%S} and {tb:%Y-%m-%d %H:%M:%S} are {h / 60:.1f} min apart",
                         "temporal proximity alone is weak evidence"])
            ra = difflib.SequenceMatcher(None, _stem(a["file_name"]).lower(), _stem(b["file_name"]).lower()).ratio()
            common = _tokens(a["file_name"]) & _tokens(b["file_name"])
            # names generated by the engine (orphans, unstructured data) are not filename evidence
            generated = any(x.get("orphan") or "generated" in (x.get("name_source") or "") for x in (a, b))
            if not generated and ((common and ra >= 0.45) or ra >= 0.75):
                add("filename", a, b, min(0.6, 0.25 + 0.4 * ra),
                    [f"name similarity {ra:.2f}"] + ([f"shared name token(s): {', '.join(sorted(common))}"] if common else []))
            if "sqlite" in (a["file_type"], b["file_type"]):
                dbf, other = (a, b) if a["file_type"] == "sqlite" else (b, a)
                vals = {v for v in re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b|\b[A-Z]{2,}-\d{2,}\b", texts[dbf["file_id"]])}
                hit = sorted(v for v in vals if v in texts[other["file_id"]])[:5]
                if len(hit) >= 2:
                    add("database", dbf, other, min(0.7, 0.35 + 0.08 * len(hit)), [f"database value '{v}' appears in {other['file_name']}" for v in hit])
            gap = min((abs(x0 - y1) if x0 >= y1 else abs(y0 - x1) if y0 >= x1 else 0 for x0, x1 in spans[A] for y0, y1 in spans[B]), default=None)
            if gap is not None and gap <= 2 * CLUSTER:
                add("storage", a, b, 0.25, [f"fragments lie within {gap:,} bytes of each other on the storage image",
                                            "adjacency may reflect allocation order, not a logical link"])
    for e in ctx.graph.get("edges", []):
        if e.get("type") == "content_similarity":
            try:
                a, b = ctx.file(e["src"]), ctx.file(e["dst"])
            except KeyError:
                continue
            add("content", a, b, float(e["confidence"]) / 100, e.get("evidence", [])[:3])
    G = nx.MultiGraph()
    for f in files:
        G.add_node(f["file_id"], name=f["file_name"], type=f["file_type"])
    for r in rels:
        G.add_edge(r["source"], r["target"], weight=r["confidence"] / 100, type=r["type"])
    simple = nx.Graph(G)
    comps = [sorted(c) for c in nx.connected_components(simple) if len(c) > 1]
    cent = nx.degree_centrality(simple) if simple.number_of_nodes() > 1 else {}
    top = sorted(cent.items(), key=lambda kv: -kv[1])[:5]
    return {"relationships": rels, "nodes": [{"id": f["file_id"], "name": f["file_name"], "type": f["file_type"],
                                              "status": f["recovery_status"], "degree": G.degree(f["file_id"])} for f in files],
            "types": TYPES, "clusters": comps,
            "most_connected": [{"id": k, "name": ctx.file(k)["file_name"], "centrality": round(v, 3)} for k, v in top if v > 0],
            "engine": f"NetworkX {nx.__version__} + deterministic metadata/identifier/hash detectors",
            "caveat": "Relationships are inferred from shared observable facts. They do not prove common origin, authorship or intent."}
