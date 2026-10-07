"""SQLite reconstruction.

Page numbers are recovered from the database's own structure:
  * page 1 (header) gives page size, page count and the schema (table -> root page);
  * a table's interior page lists (child page number, max rowid) pairs, so a stray leaf
    page is numbered by the rowid range it holds (deterministic);
  * if a table's interior page is lost, leaf placement falls back to rowid continuity with
    an already-placed neighbour. This is weaker and is labelled as inferred.
Rows are parsed directly from leaf pages, so records remain readable even when the file as
a whole fails PRAGMA integrity_check.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..common import CLUSTER, Candidate, Edge, Segment, hexoff, split_corrupted
from ..scanner import Fragment
from ..sqlitefmt import parse_page, parse_record, table_columns
from .base import Ctx, edge_from, generic_feats, materialize
from .naming import resolve_name


def assemble_sqlite(ctx: Ctx) -> None:
    for H in [f for f in ctx.scan.fragments if f.header and f.header.get("type") == "sqlite" and ctx.free(f)]:
        _build(ctx, H)


def _page_bytes(ctx: Ctx, f: Fragment, i: int) -> bytes:
    return ctx.img.cluster(f.start_cluster + i)


def _build(ctx: Ctx, H: Fragment) -> None:
    cid = ctx.next_id()
    hdr = H.header or {}
    ps, P = hdr.get("page_size", 4096), hdr.get("page_count", H.n_clusters)
    ops = [f"Header page in {H.id} @ {hexoff(H.offset)}: page_size={ps}, page_count={P}"]
    conflicts: list[str] = []
    if ps != CLUSTER:
        ops.append("Page size differs from cluster size; only the header fragment is used in this prototype")
    # ---- schema from page 1 -----------------------------------------------------------------
    p1 = parse_page(_page_bytes(ctx, H, 0), ps, is_first=True)
    tables: dict[str, dict[str, Any]] = {}
    for _, rec in p1.get("rows", []):
        if rec and len(rec) >= 5 and rec[0] == "table":
            cols = table_columns(rec[4])
            tables[rec[1]] = {"name": rec[1], "root": rec[3], "sql": rec[4], "columns": cols, "ncols": len(cols)}
    ops.append(f"Schema from page 1: {', '.join(f'{t} (root page {v['root']}, {v['ncols']} cols)' for t, v in tables.items())}")
    # ---- known pages: header fragment is contiguous -----------------------------------------
    pages: dict[int, tuple[Fragment, int, str]] = {}
    for i in range(min(H.n_clusters, P)):
        pages[i + 1] = (H, i, "header")
    # candidate pages from other SQLite-page fragments
    cand_frags = [f for f in ctx.pool("sqlite") if f is not H]
    parsed: dict[tuple[str, int], dict[str, Any]] = {}
    for f in cand_frags + [H]:
        for i in range(f.n_clusters):
            if f is H and i == 0:
                continue
            parsed[(f.id, i)] = parse_page(_page_bytes(ctx, f, i), ps)

    def table_of(info: dict[str, Any]) -> str | None:
        return next((t for t, v in tables.items() if v["ncols"] == info.get("columns")), None) if info.get("columns") else None

    # ---- locate interior (root) pages ---------------------------------------------------------
    interiors: dict[str, dict[str, Any]] = {}
    for t, v in tables.items():
        if v["root"] in pages:
            f, i, _ = pages[v["root"]]
            info = parsed.get((f.id, i)) or parse_page(_page_bytes(ctx, f, i), ps)
            if info["type"] == "table_interior":
                interiors[t] = info
    frag_by_id = {f.id: f for f in cand_frags}
    for (fid, i), info in parsed.items():
        if info["type"] != "table_interior" or fid == H.id:
            continue
        first_key = info["cells"][0][1] if info["cells"] else None
        # identify the table: a leaf holding rowid 1..first_key with that table's column count
        for (fid2, j), leaf in parsed.items():
            if leaf.get("type") == "table_leaf" and leaf.get("min_rowid") == 1 and first_key and leaf["max_rowid"] <= first_key:
                t = table_of(leaf)
                if t and t not in interiors:
                    interiors[t] = info
                    root = tables[t]["root"]
                    f = frag_by_id[fid]
                    for k in range(f.n_clusters):
                        pages.setdefault(root - i + k, (f, k, "b-tree root"))
                    ops.append(f"Interior page of '{t}' identified in {fid} (child key ranges start at rowid 1) -> page {root}")
                    break
    # ---- number leaf pages by the interior key ranges ------------------------------------------
    votes: dict[str, Counter] = {}
    for t, info in interiors.items():
        ranges = []
        lo = 1
        for child, key in info["cells"]:
            ranges.append((child, lo, key))
            lo = key + 1
        ranges.append((info["rightmost"], lo, 1 << 62))
        tables[t]["ranges"] = ranges
        for (fid, i), leaf in parsed.items():
            if leaf.get("type") != "table_leaf" or table_of(leaf) != t:
                continue
            for child, a, b in ranges:
                if a <= leaf["min_rowid"] and leaf["max_rowid"] <= b:
                    votes.setdefault(fid, Counter())[child - i] += 1
                    break
    placed_how: dict[str, str] = {}
    for fid, v in votes.items():
        if fid == H.id:
            continue
        start, n = v.most_common(1)[0]
        f = frag_by_id[fid]
        for k in range(f.n_clusters):
            pg = start + k
            if 1 <= pg <= P and pg not in pages:
                pages[pg] = (f, k, "b-tree key range")
        placed_how[fid] = "b-tree key range"
        ops.append(f"{fid} numbered as pages {start}-{start + f.n_clusters - 1}: {n} leaf page(s) fall exactly in interior key ranges")
    # ---- weaker fallback: rowid continuity with a placed neighbour ------------------------------
    edges: list[Edge] = []
    for f in cand_frags:
        if f.id in placed_how or any(pages.get(pg, (None,))[0] is f for pg in pages):
            continue
        leaves = [parsed[(f.id, i)] for i in range(f.n_clusters) if parsed[(f.id, i)].get("type") == "table_leaf"]
        if not leaves:
            continue
        t = table_of(leaves[0])
        best = None
        for pg, (pf, pi, _) in pages.items():
            info = parsed.get((pf.id, pi))
            if not info or info.get("type") != "table_leaf" or table_of(info) != t:
                continue
            if info.get("max_rowid", -9) + 1 == leaves[0].get("min_rowid"):
                cand_start = pg + 1
                if all((cand_start + k) not in pages and cand_start + k <= P for k in range(f.n_clusters)):
                    feats = generic_feats(ctx, pf, f)
                    feats.update(struct_order=1.0, index_verified=0.0, boundary_smooth=0.8, content_sim=0.5, time_consistent=0.5)
                    e = edge_from(ctx, pf.id, f.id, feats,
                                  [f"Rowid continuity: page {pg} ends at rowid {info['max_rowid']}, {f.id} starts at {leaves[0]['min_rowid']}",
                                   f"Table '{t}' interior page not recovered: page numbers inferred, not verified"])
                    if not best or e.confidence > best[1].confidence:
                        best = (cand_start, e)
        if best and best[1].confidence >= 0.5:
            for k in range(f.n_clusters):
                pages[best[0] + k] = (f, k, "rowid continuity (inferred)")
            placed_how[f.id] = "rowid continuity (inferred)"
            edges.append(best[1])
            ops.append(f"{f.id} placed at pages {best[0]}.. by rowid continuity (inferred; interior page missing)")
    # ---- materialize ----------------------------------------------------------------------------
    total = P * ps
    placements = []
    for pg, (f, i, how) in pages.items():
        placements.append(((pg - 1) * ps - i * CLUSTER, f, 99 if how == "header" else 10 if how != "rowid continuity (inferred)" else 1, how))
    # de-duplicate by fragment (materialize works per fragment start)
    uniq: dict[tuple[str, int], tuple[int, Fragment, int, str]] = {}
    for p in placements:
        uniq.setdefault((p[1].id, p[0]), p)
    data, segs, used = materialize(ctx, list(uniq.values()), total)
    # ---- page status, rows ----------------------------------------------------------------------
    page_status: dict[int, str] = {}
    bad = []
    rows_by_table: dict[str, list[tuple[int, list[Any]]]] = {t: [] for t in tables}
    parse_errors = 0
    for pg in range(1, P + 1):
        lo = (pg - 1) * ps
        seg = next((s for s in segs if s.start <= lo < s.start + s.length), None)
        if not seg or seg.kind == "missing":
            page_status[pg] = "missing"
            continue
        info = parse_page(data[lo:lo + ps], ps, is_first=pg == 1)
        if info["type"] == "invalid":
            page_status[pg] = "corrupted"
            bad.append((lo, lo + ps, f"page {pg}: invalid b-tree page header"))
            continue
        if info.get("errors"):
            parse_errors += info["errors"]
            page_status[pg] = "corrupted"
            bad.append((lo, lo + ps, f"page {pg}: {info['errors']} record(s) fail to parse"))
        else:
            page_status[pg] = "ok"
        if info["type"] == "table_leaf" and pg != 1:
            t = table_of(info)
            if t:
                rows_by_table[t] += [r for r in info["rows"] if r[1] is not None]
    segs = split_corrupted(segs, bad)
    # rows readable from SQLite pages that could not be placed
    orphan_rows = 0
    for f in cand_frags:
        if f in used:
            continue
        for i in range(f.n_clusters):
            info = parsed.get((f.id, i), {})
            orphan_rows += len(info.get("rows", []))
    tables_out = []
    for t, v in tables.items():
        rows = sorted(rows_by_table[t])
        expected = None
        src = "unknown"
        if "ranges" in v:
            last_key = v["ranges"][-2][2] if len(v["ranges"]) > 1 else 0
            per_leaf = last_key / max(1, len(v["ranges"]) - 1)
            right = [r for r in rows if r[0] > last_key]
            expected = int(max(max((r[0] for r in right), default=0), last_key + per_leaf * 0.5 if not right else 0))
            src = "interior-page key ranges (assumes contiguous rowids; last leaf estimated)"
        elif rows:
            expected = max(r[0] for r in rows)
            src = "highest recovered rowid (lower bound)"
        cols = v["columns"]
        tables_out.append({
            "name": t, "root_page": v["root"], "columns": cols, "rows_recovered": len(rows),
            "rows_expected": expected, "rows_expected_source": src, "interior_recovered": t in interiors,
            "sample": [dict(zip(cols, [rid if c is None and k == 0 else c for k, c in enumerate(r)])) for rid, r in rows[:25]],
        })
    # ---- edges between consecutive placed fragments ----------------------------------------------
    order = []
    for pg in sorted(pages):
        f = pages[pg][0]
        if f not in order:
            order.append(f)
    for fa, fb_ in zip(order, order[1:]):
        if any(e.src == fa.id and e.dst == fb_.id for e in edges):
            continue
        how = placed_how.get(fb_.id, "b-tree")
        pa = max(pg for pg, v in pages.items() if v[0] is fa)
        pb = min(pg for pg, v in pages.items() if v[0] is fb_)
        feats = generic_feats(ctx, fa, fb_)
        feats.update(struct_order=1.0, index_verified=1.0 if "inferred" not in how else 0.0,
                     boundary_smooth=0.8 if pb == pa + 1 else 0.5, content_sim=0.5, time_consistent=0.5)
        ev = [f"{fb_.id} numbered by {how}", f"Page {pa} -> page {pb}" + ("" if pb == pa + 1 else f" ({pb - pa - 1} page(s) missing between)")]
        edges.append(edge_from(ctx, fa.id, fb_.id, feats, ev, "continuation" if pb == pa + 1 else "structural"))
    ctx.claim(cid, used)
    fat, name, src, medges = resolve_name(ctx, cid, H, "db", None, "")
    cand = Candidate(
        id=cid, type="sqlite", name=name, name_source=src, fragments=[f.id for f in used], segments=segs,
        expected_size=total, expected_size_source="page_count x page_size (database header)", data=data,
        edges=edges + medges, conflicts=conflicts,
        structure={"page_size": ps, "page_count": P, "page_status": {str(k): v for k, v in page_status.items()},
                   "tables": tables_out, "orphan_rows_unplaced": orphan_rows,
                   "placement": {str(pg): {"fragment": f.id, "method": how} for pg, (f, i, how) in sorted(pages.items())}},
        metadata={"text_encoding": hdr.get("text_encoding")}, operations=ops,
        remaining_compatible=sum(1 for f in cand_frags if f not in used),
    )
    if fat:
        cand.metadata["directory_entry"] = fat
    ctx.candidates.append(cand)
    ok = sum(1 for v in page_status.values() if v == "ok")
    ctx.log("reconstruct", f"SQLite {name}: {ok}/{P} pages valid; rows recovered " +
            ", ".join(f"{t['name']}={t['rows_recovered']}" for t in tables_out))
