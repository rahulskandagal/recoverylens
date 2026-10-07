"""Fragment DNA: a deterministic forensic fingerprint for every detected fragment + a similarity engine.

Fingerprint fields are measured from the fragment's own bytes (read-only from the evidence copy).
Similarity is statistical (byte-distribution cosine, entropy, encoding) and is reported with its
evidence. Similarity NEVER implies that two fragments belong to the same file.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from ..engine.common import CLUSTER, hexoff, sha256
from ..engine.scanner import _jpeg_legal, _sqlite_page_ok
from .context import CaseCtx

try:  # scikit-learn is optional; NumPy implements the same maths when it is unavailable
    from sklearn.metrics.pairwise import cosine_similarity as _sk_cosine  # type: ignore
    ML_ENGINE = "scikit-learn cosine similarity + NumPy SVD (PCA)"
except Exception:  # ImportError, or a native DLL blocked by host policy
    _sk_cosine = None
    ML_ENGINE = "NumPy cosine similarity + NumPy SVD (PCA) – scikit-learn not available on this host"

SIG_ENGINE = "built-in magic-number table (libmagic not available on this host)"

# deterministic signature table: (magic, offset, label, mime)
SIGNATURES: list[tuple[bytes, int, str, str]] = [
    (b"%PDF-", 0, "PDF document", "application/pdf"),
    (b"\xff\xd8\xff", 0, "JPEG image (SOI)", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", 0, "PNG image", "image/png"),
    (b"PK\x03\x04", 0, "ZIP / OOXML container", "application/zip"),
    (b"SQLite format 3\x00", 0, "SQLite 3 database", "application/vnd.sqlite3"),
    (b"GIF8", 0, "GIF image", "image/gif"),
    (b"ftyp", 4, "ISO-BMFF / MP4", "video/mp4"),
    (b"MZ", 0, "Windows executable (MZ)", "application/vnd.microsoft.portable-executable"),
    (b"\x7fELF", 0, "ELF executable", "application/x-elf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "OLE2 compound document", "application/x-ole-storage"),
    (b"\x1f\x8b", 0, "gzip stream", "application/gzip"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7-Zip archive", "application/x-7z-compressed"),
    (b"Rar!", 0, "RAR archive", "application/vnd.rar"),
]
FOOTERS = [(b"\xff\xd9", "JPEG EOI"), (b"%%EOF", "PDF %%EOF"), (b"PK\x05\x06", "ZIP end of central directory"), (b"IEND", "PNG IEND")]
FAMILY_MIME = {"jpeg": "image/jpeg (entropy-coded scan data)", "pdf": "application/pdf (object data)", "sqlite": "application/vnd.sqlite3 (pages)",
               "compressed": "application/octet-stream (compressed data)", "text": "text/plain", "log": "text/plain (log lines)",
               "xml": "application/xml", "fat_dir": "inode/directory (FAT directory entries)", "zero": "application/x-empty",
               "binary": "application/octet-stream"}
SAMPLE = 256 * 1024  # bytes per fragment used for statistics (the SHA-256 always covers the whole fragment)


def _entropy(counts: np.ndarray, n: int) -> float:
    if n == 0:
        return 0.0
    p = counts[counts > 0] / n
    return float(-(p * np.log2(p)).sum())


def _signature(b: bytes) -> tuple[str | None, str | None]:
    for magic, off, label, mime in SIGNATURES:
        if b[off:off + len(magic)] == magic:
            return label, mime
    return None, None


def _encoding(b: bytes) -> dict[str, Any]:
    if not b:
        return {"label": "empty", "printable": 0.0}
    a = np.frombuffer(b, dtype=np.uint8)
    printable = float((((a >= 32) & (a < 127)) | (a == 9) | (a == 10) | (a == 13)).mean())
    zeros_odd = float((a[1::2] == 0).mean()) if len(a) > 1 else 0.0
    try:
        b.rstrip(b"\x00").decode("utf-8")
        utf8 = True
    except UnicodeDecodeError:
        utf8 = False
    if zeros_odd > 0.9 and printable > 0.45:
        label = "UTF-16LE text (every second byte is 0x00)"
    elif printable > 0.95:
        label = "ASCII text" if utf8 and (a < 128).all() else ("UTF-8 text" if utf8 else "mostly printable (mixed encoding)")
    elif printable > 0.75:
        label = "mixed text/binary"
    else:
        label = "binary"
    return {"label": label, "printable": round(printable, 3), "utf8_valid": utf8}


def _compression(b: bytes, ent: float, fr: dict[str, Any]) -> dict[str, Any]:
    ev = []
    if b.find(b"/FlateDecode") != -1:
        ev.append("PDF /FlateDecode stream filter present")
    if re.search(rb"x[\x01\x5e\x9c\xda]", b[:64]):
        ev.append("zlib stream header at fragment start")
    if fr.get("zip_members"):
        ev.append(f"ZIP local headers ({len(fr['zip_members'])} member(s))")
    if b[:2] == b"\x1f\x8b":
        ev.append("gzip header")
    if ent > 7.5 and fr.get("family") in ("compressed", "binary"):
        ev.append(f"entropy {ent:.2f} bits/byte is consistent with compressed or encrypted data")
    if fr.get("family") == "jpeg":
        ev.append("JPEG entropy coding (Huffman-compressed image data)")
    return {"detected": bool(ev), "evidence": ev}


def _parser_compat(b: bytes, fr: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    ok, rst, _eoi, viol = _jpeg_legal(b[:CLUSTER * 4])
    if ok:
        out.append({"parser": "JPEG scan data", "result": "compatible", "evidence": f"only legal 0xFF sequences; {len(rst)} RST marker(s), {len(viol)} violation(s)"})
    if b.find(b" obj") != -1 or b.find(b"endobj") != -1:
        out.append({"parser": "PDF object syntax", "result": "compatible", "evidence": f"{len(fr.get('pdf_objects') or [])} object header(s) found"})
    try:
        if len(b) >= 8 and _sqlite_page_ok(b[:CLUSTER]):
            out.append({"parser": "SQLite b-tree page", "result": "compatible", "evidence": "page header and cell pointers are self-consistent"})
    except Exception:
        pass
    if b[:4] == b"PK\x03\x04" or fr.get("zip_members"):
        out.append({"parser": "ZIP local file header", "result": "compatible", "evidence": "PK\\x03\\x04 record(s) parse"})
    if fr.get("family") in ("text", "log", "xml"):
        out.append({"parser": "text", "result": "compatible", "evidence": "printable, line-structured bytes"})
    if not out:
        out.append({"parser": "none", "result": "no deterministic parser accepts these bytes", "evidence": "unidentified data"})
    return out


def _anomalies(b: bytes, fr: dict[str, Any], ent: float, enc: dict[str, Any]) -> list[str]:
    out = list(fr.get("corruption") or [])
    fam = fr.get("family")
    if fam in ("text", "log", "xml") and ent > 6.2:
        out.append(f"entropy {ent:.2f} is unusually high for {fam} data")
    if fam == "jpeg" and enc["printable"] > 0.9:
        out.append("printable ratio too high for JPEG entropy-coded data")
    if fr.get("duplicate_of"):
        out.append(f"byte-identical duplicate of {fr['duplicate_of']}")
    tail = b[-512:]
    if b and tail.count(0) == len(tail) and fam not in ("zero",):
        out.append("fragment ends in zero padding (slack space or end of file)")
    if fam not in ("zero", "fat_dir") and b.count(b"\x00" * 256) > 4 and ent > 5:
        out.append("long zero runs inside high-entropy data (possible overwrite or sparse region)")
    return out


def dna_card(ctx: CaseCtx, fid: str, relationships: int | None = None) -> dict[str, Any]:
    fr = ctx.fragments[fid]
    full = ctx.frag_bytes(fid)
    b = full[:SAMPLE]
    counts = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256).astype(np.float64) if b else np.zeros(256)
    ent = _entropy(counts, len(b))
    sig, mime = _signature(b)
    enc = _encoding(b)
    footer = next((name for pat, name in FOOTERS if full.rfind(pat) != -1 and (name != "JPEG EOI" or fr["family"] == "jpeg")), None)
    hdr = fr.get("header")
    header_state = ("VALID" if hdr and not hdr.get("error") else "DAMAGED") if hdr else ("PRESENT (signature only)" if sig else "ABSENT")
    footer_state = "PRESENT" if fr.get("has_footer") or footer else "ABSENT"
    markers = []
    if fr.get("pdf_objects"):
        markers.append(f"PDF objects {min(fr['pdf_objects'])}–{max(fr['pdf_objects'])} ({len(fr['pdf_objects'])})")
    if fr.get("rst_markers"):
        markers.append(f"{fr['rst_markers']} JPEG restart markers")
    if fr.get("zip_members"):
        markers.append("ZIP members: " + ", ".join(fr["zip_members"][:4]))
    if fr.get("embedded"):
        markers.append(f"{len(fr['embedded'])} embedded signature(s)")
    structure = "COMPLETE" if header_state == "VALID" and footer_state == "PRESENT" else \
        "PARTIAL" if (hdr or footer_state == "PRESENT" or markers) else "NONE IDENTIFIED"
    meta = []
    if fr.get("ts_first"):
        meta.append(f"timestamps {fr['ts_first']} → {fr['ts_last']}")
    if hdr and hdr.get("exif"):
        meta.append("EXIF: " + ", ".join(f"{k}={v}" for k, v in list(hdr["exif"].items())[:3]))
    for key in (b"/Author", b"/Title", b"/CreationDate", b"/Producer"):
        m = re.search(re.escape(key) + rb"\s*\(([^)]{1,60})\)", b)
        if m:
            meta.append(f"{key.decode()[1:]}: {m.group(1).decode('latin-1')}")
    windows = max(1, min(64, len(b) // 512))
    step = max(1, len(b) // windows)
    profile = []
    for i in range(windows):
        w = b[i * step:(i + 1) * step]
        c = np.bincount(np.frombuffer(w, dtype=np.uint8), minlength=256).astype(np.float64)
        profile.append(round(_entropy(c, len(w)), 2))
    top = np.argsort(-counts)[:6]
    if relationships is None:
        relationships = len(ctx.edges_for(fid))
    return {
        "id": fid, "sha256": fr.get("sha256") or sha256(full), "size": fr["length"], "offset": fr["offset"], "offset_hex": hexoff(fr["offset"]),
        "clusters": fr.get("clusters"), "family": fr["family"], "assigned_to": fr.get("assigned_to"),
        "entropy": round(ent, 3), "entropy_profile": profile,
        "byte_histogram": [int(x) for x in counts.reshape(32, 8).sum(axis=1)],
        "top_bytes": [{"byte": f"0x{int(x):02X}", "share": round(float(counts[x] / max(1, len(b))), 4)} for x in top],
        "signature": sig, "mime": mime or FAMILY_MIME.get(fr["family"], "application/octet-stream"),
        "mime_basis": "magic number at fragment start" if mime else f"content family '{fr['family']}' (no signature at fragment start)",
        "header": header_state, "footer": footer_state, "footer_marker": footer, "structure": structure,
        "structural_markers": markers, "compression": _compression(b, ent, fr), "encoding": enc,
        "alignment": {"sector_aligned": fr["offset"] % 512 == 0, "cluster_aligned": fr["offset"] % CLUSTER == 0,
                      "sector": fr["offset"] // 512, "cluster": fr["offset"] // CLUSTER},
        "parser_compatibility": _parser_compat(b, fr), "metadata_indicators": meta,
        "anomalies": _anomalies(b, fr, ent, enc), "potential_relationships": relationships,
        "sampled_bytes": len(b), "sample_note": None if len(b) == len(full) else f"statistics use the first {len(b):,} of {len(full):,} bytes",
        "data_class": "OBSERVED (measured from evidence bytes)", "signature_engine": SIG_ENGINE,
    }


# ------------------------------------------------------------------------------------------ index + similarity

def _index_path(ctx: CaseCtx) -> Path:
    return ctx.out_dir / "fragment_dna_index.npz"


def build_index(ctx: CaseCtx) -> dict[str, np.ndarray]:
    """Per-fragment feature vectors (sqrt byte distribution, entropy, printable). Cached per case."""
    p = _index_path(ctx)
    if p.exists():
        z = np.load(p, allow_pickle=False)
        return {k: z[k] for k in z.files}
    ids, hists, ents, prints = [], [], [], []
    with open(ctx.image_path, "rb") as fh:  # read-only
        for fid, fr in ctx.fragments.items():
            if fr["family"] == "zero":
                continue
            fh.seek(fr["offset"])
            b = fh.read(min(fr["length"], 64 * 1024))
            if not b:
                continue
            a = np.frombuffer(b, dtype=np.uint8)
            c = np.bincount(a, minlength=256).astype(np.float64)
            h = np.sqrt(c / len(b))
            n = np.linalg.norm(h)
            ids.append(fid)
            hists.append(h / n if n else h)
            ents.append(_entropy(c, len(b)))
            prints.append(float((((a >= 32) & (a < 127)) | (a == 10) | (a == 13) | (a == 9)).mean()))
    idx = {"ids": np.array(ids), "hist": np.array(hists).reshape(-1, 256), "entropy": np.array(ents), "printable": np.array(prints)}
    np.savez_compressed(p, **idx)
    return idx


def _cos(a: np.ndarray, m: np.ndarray) -> np.ndarray:
    if _sk_cosine is not None:
        return _sk_cosine(a.reshape(1, -1), m)[0]
    return m @ a  # rows are already unit vectors


def _label(cos: float, d_ent: float, same_fam: bool) -> str:
    if cos >= 0.97 and d_ent <= 0.2 and same_fam:
        return "STRONG SIMILARITY"
    if cos >= 0.92 and d_ent <= 0.5:
        return "POSSIBLE SIMILARITY"
    if cos >= 0.8:
        return "WEAK SIMILARITY"
    return "NO SIGNIFICANT SIMILARITY"


def _structural_hint(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    pa, pb = a.get("pdf_objects") or [], b.get("pdf_objects") or []
    if pa and pb and 0 < min(pb) - max(pa) <= 3:
        return f"PDF object numbers continue ({max(pa)} → {min(pb)})"
    if a.get("zip_members") and b.get("zip_members") and set(a["zip_members"]) & set(b["zip_members"]):
        return "both carry the same ZIP member names"
    if a.get("ts_last") and b.get("ts_first") and a["ts_last"] <= b["ts_first"] and a["ts_last"][:10] == b["ts_first"][:10]:
        return f"timestamps continue ({a['ts_last']} → {b['ts_first']})"
    return None


def similar(ctx: CaseCtx, fid: str, top: int = 12) -> dict[str, Any]:
    idx = build_index(ctx)
    ids = [str(x) for x in idx["ids"]]
    if fid not in ids:
        return {"fragment": fid, "engine": ML_ENGINE, "items": [], "note": "empty (all-zero) fragments are not fingerprinted"}
    i = ids.index(fid)
    cos = _cos(idx["hist"][i], idx["hist"])
    order = [j for j in np.argsort(-cos) if j != i][: top * 3]
    me = ctx.fragments[fid]
    edges = {(e["src"], e["dst"]): e for e in ctx.graph.get("edges", [])}
    items = []
    for j in order:
        oid = str(ids[j])
        o = ctx.fragments[oid]
        d_ent = abs(float(idx["entropy"][i] - idx["entropy"][j]))
        same = o["family"] == me["family"]
        lab = _label(float(cos[j]), d_ent, same)
        ev = [f"byte-distribution cosine similarity {cos[j]:.3f}", f"entropy {idx['entropy'][i]:.2f} vs {idx['entropy'][j]:.2f} (Δ {d_ent:.2f})",
              f"content family {me['family']} vs {o['family']}" + (" (same)" if same else " (different)"),
              f"printable ratio {idx['printable'][i]:.2f} vs {idx['printable'][j]:.2f}"]
        if idx["entropy"][i] > 7.5 and idx["entropy"][j] > 7.5 and lab in ("STRONG SIMILARITY", "POSSIBLE SIMILARITY"):
            # compressed/encrypted/random data all look uniform: a high cosine here carries almost no information
            lab = "WEAK SIMILARITY"
            ev.append("both byte distributions are near-uniform (entropy > 7.5): compressed, encrypted and random data are "
                      "statistically indistinguishable, so the similarity score is capped at WEAK")
        hint = _structural_hint(me, o) or _structural_hint(o, me)
        if hint:
            ev.append("structural hint: " + hint)
        e = edges.get((fid, oid)) or edges.get((oid, fid))
        if e:
            ev.append(f"engine relationship: {e['type']} {e.get('confidence')}% ({e.get('label', '')})")
        same_file = me.get("assigned_to") and me.get("assigned_to") == o.get("assigned_to")
        items.append({"id": oid, "label": lab, "cosine": round(float(cos[j]), 4), "entropy_delta": round(d_ent, 3), "family": o["family"],
                      "offset_hex": hexoff(o["offset"]), "assigned_to": o.get("assigned_to"), "same_reconstruction": bool(same_file),
                      "evidence": ev})
        if len(items) >= top:
            break
    return {"fragment": fid, "engine": ML_ENGINE, "items": items,
            "caveat": "Similarity is a statistical observation. It does NOT establish that two fragments belong to the same file; "
                      "only structural evidence (indexes, markers, checksums) can support that."}


def dna_map(ctx: CaseCtx, limit: int = 2500) -> dict[str, Any]:
    """2-D PCA projection of fragment fingerprints (NumPy SVD) for the DNA overview scatter."""
    idx = build_index(ctx)
    n = len(idx["ids"])
    if n < 3:
        return {"points": [], "engine": ML_ENGINE, "note": "fewer than 3 non-empty fragments: projection not meaningful"}
    sel = np.arange(n) if n <= limit else np.linspace(0, n - 1, limit).astype(int)
    X = np.hstack([idx["hist"][sel], idx["entropy"][sel, None] / 8.0, idx["printable"][sel, None]])
    Xc = X - X.mean(axis=0)
    _, s, vt = np.linalg.svd(Xc, full_matrices=False)
    P = Xc @ vt[:2].T
    var = (s[:2] ** 2 / max(1e-12, (s ** 2).sum())).round(3).tolist()
    pts = []
    for k, j in enumerate(sel):
        fr = ctx.fragments[str(idx["ids"][j])]
        pts.append({"id": str(idx["ids"][j]), "x": round(float(P[k, 0]), 4), "y": round(float(P[k, 1]), 4), "family": fr["family"],
                    "entropy": round(float(idx["entropy"][j]), 2), "assigned_to": fr.get("assigned_to")})
    return {"points": pts, "engine": ML_ENGINE, "explained_variance": var, "sampled": n > limit, "total": n,
            "note": "Axes are principal components of byte-distribution fingerprints; proximity is similarity, not common origin."}


def dna_list(ctx: CaseCtx, page: int = 1, size: int = 24, family: str = "", q: str = "") -> dict[str, Any]:
    frs = [f for f in ctx.fragments.values() if f["family"] != "zero" and (not family or f["family"] == family)
           and (not q or q.lower() in f["id"].lower() or q.lower() in (f.get("assigned_to") or "").lower())]
    total = len(frs)
    chunk = frs[(page - 1) * size: page * size]
    rel_count: dict[str, int] = {}
    for e in ctx.graph.get("edges", []):
        for end in (e.get("src"), e.get("dst")):
            rel_count[end] = rel_count.get(end, 0) + 1
    return {"total": total, "page": page, "size": size, "engine": SIG_ENGINE, "ml_engine": ML_ENGINE,
            "items": [dna_card(ctx, f["id"], rel_count.get(f["id"], 0)) for f in chunk],
            "families": sorted({f["family"] for f in ctx.fragments.values() if f["family"] != "zero"})}
