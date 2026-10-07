"""Post-hoc evaluation against synthetic ground truth (demo datasets only).

Runs AFTER analysis; the engine never sees this data. It measures how often the engine's
placements and links were actually correct, including what it failed to detect.
"""
from __future__ import annotations

from typing import Any

from .common import CLUSTER


def evaluate(result: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    owner: dict[int, tuple[str, int]] = {}
    for tf in truth["files"]:
        for lc, pc in enumerate(tf["cluster_map"]):
            if pc is not None:
                owner[pc] = (tf["name"], lc)
    for d in truth.get("duplicates", []):  # byte-identical copies count as correct sources
        for k in range(d["clusters"]):
            src = d["copy_of_cluster"] + k
            if src in owner:
                owner[d["at_cluster"] + k] = owner[src]
    # which truth file does each candidate represent? (majority of its placed clusters)
    placed_ok = placed_total = 0
    unmatched: list[str] = []
    per_file: dict[str, dict[str, Any]] = {}
    cand_file: dict[str, str | None] = {}
    for f in result["files"]:
        votes: dict[str, int] = {}
        for s in f["segments"]:
            if s["source_offset"] is None:
                continue
            for k in range(s["length"] // CLUSTER or 1):
                o = owner.get(s["source_offset"] // CLUSTER + k)
                if o:
                    votes[o[0]] = votes.get(o[0], 0) + 1
        best = max(votes, key=votes.get) if votes else None
        cand_file[f["file_id"]] = best
        if f["orphan"]:
            continue
        if best is None:
            # carved from data that exists on the image but is not one of the ground-truth files (e.g. leftover
            # text in free space): counted separately, so placement precision measures how files were rebuilt
            unmatched.append(f["file_name"])
            continue
        for s in f["segments"]:
            if s["source_offset"] is None:
                continue
            n = -(-s["length"] // CLUSTER)
            for k in range(n):
                pc = s["source_offset"] // CLUSTER + k
                lc = (s["start"] // CLUSTER) + k
                placed_total += 1
                if owner.get(pc) == (best, lc):
                    placed_ok += 1
    surviving = {tf["name"]: sum(1 for x in tf["cluster_map"] if x is not None) for tf in truth["files"]}
    correct_pairs: set[tuple[str, int]] = set()  # unique (file, logical cluster): split segments must not double count
    for f in result["files"]:
        if f["orphan"] or not cand_file[f["file_id"]]:
            continue
        name = cand_file[f["file_id"]]
        for s in f["segments"]:
            if s["source_offset"] is None:
                continue
            for k in range(-(-s["length"] // CLUSTER)):
                pc = s["source_offset"] // CLUSTER + k
                if owner.get(pc) == (name, s["start"] // CLUSTER + k):
                    correct_pairs.add((name, s["start"] // CLUSTER + k))
    recovered_correct: dict[str, int] = {}
    for name, _ in correct_pairs:
        recovered_correct[name] = recovered_correct.get(name, 0) + 1
    # edges
    e_ok = e_tot = 0
    wrong_edges = []
    edge_records: list[dict[str, Any]] = []
    for f in result["files"]:
        # judge each edge by the clusters of each fragment that the reconstruction actually used
        used: dict[str, list[int]] = {}
        for s in f["segments"]:
            if s["fragment_id"] and s["source_offset"] is not None:
                used.setdefault(s["fragment_id"], []).extend(
                    range(s["source_offset"] // CLUSTER, -(-(s["source_offset"] + s["length"]) // CLUSTER)))
        frag_first = {k: min(v) for k, v in used.items()}
        frag_last = {k: max(v) for k, v in used.items()}
        for e in f["fragment_relationships"]:
            # every scored link (chosen or rejected) with its truth label, for confidence calibration
            if e["type"] in ("continuation", "structural") and e["src"].startswith("F") and e["dst"].startswith("F"):
                if e["src"] in frag_last and e["dst"] in frag_first:  # ends not placed in this file cannot be judged
                    a, b = owner.get(frag_last[e["src"]]), owner.get(frag_first[e["dst"]])
                    ok = bool(a and b and a[0] == b[0] and (b[1] == a[1] + 1 if e["type"] == "continuation" else b[1] > a[1]))
                    edge_records.append({"confidence": e["confidence"], "chosen": bool(e["chosen"]), "correct": ok, "type": e["type"]})
        for e in f["fragment_relationships"]:
            if not e["chosen"] or e["type"] not in ("continuation", "structural") or not e["src"].startswith("F"):
                continue
            a, b = owner.get(frag_last.get(e["src"], -1)), owner.get(frag_first.get(e["dst"], -1))
            e_tot += 1
            ok = bool(a and b and a[0] == b[0] and (b[1] == a[1] + 1 if e["type"] == "continuation" else b[1] > a[1]))
            e_ok += ok
            if not ok:
                wrong_edges.append(f"{e['src']}->{e['dst']} ({e['confidence']}%)")
    # corruption detection
    flipped = [(tf["name"], o) for tf in truth["files"] for o in tf["flipped_offsets"]]
    detected = 0
    for name, o in flipped:
        for f in result["files"]:
            if any(c["source_offset"] is not None and c["source_offset"] <= o < c["source_offset"] + c["length"] for c in f["corrupted_ranges"]):
                detected += 1
                break
    files_out = []
    for tf in truth["files"]:
        cands = [fid for fid, n in cand_file.items() if n == tf["name"]]
        main = next((f for f in result["files"] if f["file_id"] in cands and not f["orphan"]), None) or             next((f for f in result["files"] if f["file_id"] in cands), None)
        true_pct = 100 * surviving[tf["name"]] / tf["clusters"]
        files_out.append({
            "name": tf["name"], "type": tf["type"], "true_surviving_pct": round(true_pct, 1),
            "clusters": tf["clusters"], "surviving_clusters": surviving[tf["name"]],
            "correctly_recovered_clusters": recovered_correct.get(tf["name"], 0),
            "engine_candidate": main["file_id"] if main else None,
            "engine_name": main["file_name"] if main else None,
            "engine_reconstruction_pct": main["reconstruction_percentage"] if main else None,
            "engine_status": main["recovery_status"] if main else ("orphan only" if cands else "not found"),
            "header_lost": tf["header_lost"], "bytes_flipped": len(tf["flipped_offsets"]),
        })
    return {
        "disclaimer": "Measured against synthetic ground truth generated with the demo image. Real-world accuracy may differ.",
        "cluster_placement_precision": round(100 * placed_ok / placed_total, 1) if placed_total else None,
        "cluster_recall": round(100 * sum(recovered_correct.values()) / max(1, sum(surviving.values())), 1),
        "edge_accuracy": round(100 * e_ok / e_tot, 1) if e_tot else None, "edges_evaluated": e_tot,
        "wrong_edges": wrong_edges[:10], "edge_records": edge_records,
        "unmatched_candidates": unmatched,
        "unmatched_note": "Artifacts carved from data that is not one of the ground-truth files (e.g. leftover text in free "
                          "space); excluded from placement precision and listed here instead.",
        "candidate_truth": cand_file,
        "corruption_bytes_injected": len(flipped), "corruption_bytes_detected": detected,
        "corruption_note": "Undetected flips fell in data without checksums or structural constraints (silent corruption).",
        "files": files_out,
    }
