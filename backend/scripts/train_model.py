"""Train the edge-continuation model on synthetic images with known ground truth.

    python -m scripts.train_model [n_train] [n_test]

Every relationship the assemblers evaluate is recorded with its features, then labelled
from ground truth:
  continuation edge -> positive iff B's first cluster is the logical successor of A's last
  structural edge   -> positive iff same file and B lies after A
Train and test images use disjoint seeds; test metrics are stored in the model card.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import model as M  # noqa: E402
from app.engine.assemble.base import Ctx  # noqa: E402
from app.engine.assemble.jpeg import assemble_jpeg  # noqa: E402
from app.engine.assemble.orphans import assemble_orphans  # noqa: E402
from app.engine.assemble.pdf import assemble_pdf  # noqa: E402
from app.engine.assemble.sqlitedb import assemble_sqlite  # noqa: E402
from app.engine.assemble.text import assemble_text  # noqa: E402
from app.engine.assemble.zipdoc import assemble_zip  # noqa: E402
from app.engine.common import CLUSTER  # noqa: E402
from app.engine.scanner import EvidenceImage, scan  # noqa: E402
from app.engine.synth import (FileSpec, WORDS, _report_pages, _sentence, build_image, make_docx,  # noqa: E402
                              make_jpeg, make_log, make_pdf, make_sqlite, make_text)


def random_specs(rng: random.Random) -> tuple[list[FileSpec], int]:
    specs: list[FileSpec] = []
    t0 = datetime(2025, rng.randint(1, 9), rng.randint(1, 28), 9)

    def frag_plan(n_cl: int) -> tuple[int, list[int], list[tuple[int, int]]]:
        k = min(n_cl, rng.randint(1, 6))
        drop = [i for i in range(1, k) if rng.random() < 0.15]
        flips = [(rng.randrange(k), rng.randint(1, 3))] if rng.random() < 0.3 else []
        return k, drop, flips

    for i in range(rng.randint(3, 6)):
        d = make_jpeg(rng, f"img_{i}", rng.choice([640, 800, 960]), rng.choice([480, 600]), t0, rng.randint(0, 3))
        k, drop, flips = frag_plan(len(d) // CLUSTER)
        specs.append(FileSpec(f"photo_{i}.jpg", d, "jpeg", k, drop=drop, flips=flips, dir_entry=rng.random() < 0.7))
    for i in range(rng.randint(1, 3)):
        d = make_pdf(rng, f"Doc {i}", "x", t0, _report_pages(rng, f"Doc {i}", rng.randint(3, 10), ["alpha"]))
        k, drop, flips = frag_plan(len(d) // CLUSTER)
        specs.append(FileSpec(f"doc_{i}.pdf", d, "pdf", k, drop=drop, flips=flips))
    for i in range(rng.randint(1, 2)):
        img = make_jpeg(rng, "emb", 480, 360, t0, 1) if rng.random() < 0.5 else None
        d = make_docx(rng, f"Word {i}", "x", t0, [_sentence(rng, 14) for _ in range(rng.randint(60, 250))], img)
        k, drop, flips = frag_plan(len(d) // CLUSTER)
        specs.append(FileSpec(f"word_{i}.docx", d, "docx", k, drop=drop, flips=flips))
    if rng.random() < 0.6:
        d = make_sqlite(rng, rng.randint(200, 500), rng.randint(500, 1200))
        k, _, _ = frag_plan(len(d) // CLUSTER)
        specs.append(FileSpec("data.db", d, "sqlite", k, drop_clusters=rng.sample(range(2, len(d) // CLUSTER), 3)))
    for i in range(rng.randint(1, 2)):
        d = make_log(rng, f"host{i}", t0 + timedelta(days=i), rng.randint(200, 500))
        k, drop, _ = frag_plan(len(d) // CLUSTER)
        specs.append(FileSpec(f"sys_{i}.log", d, "log", k, drop=drop))
    d = make_text(rng, "Notes on the plan", rng.randint(20, 40))
    specs.append(FileSpec("notes.txt", d, "txt", min(3, len(d) // CLUSTER)))
    total = sum(len(s.data) // CLUSTER + 2 for s in specs)
    return specs, int(total * rng.uniform(2.2, 3.5)) + 200


def collect(seed: int) -> list[tuple[dict, int]]:
    rng = random.Random(seed)
    specs, n = random_specs(rng)
    with tempfile.TemporaryDirectory() as td:
        img_p, tr_p = Path(td) / "t.img", Path(td) / "t.json"
        truth = build_image(specs, n, seed, img_p, tr_p, "training")
        img = EvidenceImage(str(img_p))
        res = scan(img)
        owner = {}
        for tf in truth["files"]:
            for lc, pc in enumerate(tf["cluster_map"]):
                if pc is not None:
                    owner[pc] = (tf["name"], lc)
        rows: list[tuple[dict, dict]] = []
        M.RECORDER.clear()
        M.RECORDER.append(lambda feats, ctx: rows.append((dict(feats), dict(ctx))))
        ctx = Ctx(img, res, M.EdgeModel.load(), lambda a, b: None)
        for fn in (assemble_zip, assemble_pdf, assemble_sqlite, assemble_jpeg, assemble_text, assemble_orphans):
            fn(ctx)
        M.RECORDER.clear()
    out = []
    for feats, c in rows:
        a, b = c.get("a", ""), c.get("b", "")
        if not a.startswith("F") or not b.startswith("F"):
            continue
        fa, fb = res.frag(a), res.frag(b)
        oa, ob = owner.get(fa.end_cluster - 1), owner.get(fb.start_cluster)
        if c.get("etype") == "structural":
            y = int(bool(oa and ob and oa[0] == ob[0] and ob[1] > oa[1]))
        else:
            y = int(bool(oa and ob and oa[0] == ob[0] and ob[1] == oa[1] + 1))
        out.append((feats, y))
    return out


def auc(y: np.ndarray, p: np.ndarray) -> float:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    return float(np.mean([(pp > neg).mean() + 0.5 * (pp == neg).mean() for pp in pos]))


def main() -> None:
    n_train = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    n_test = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    # use prior weights while collecting so collection does not depend on an older trained model
    if M.MODEL_PATH.exists():
        M.MODEL_PATH.unlink()
    train, test = [], []
    for s in range(1000, 1000 + n_train):
        train += collect(s)
        print(f"train seed {s}: {len(train)} samples")
    for s in range(5000, 5000 + n_test):
        test += collect(s)
    Xtr = np.array([[f.get(k, 0.5) for k in M.FEATURES] for f, _ in train])
    ytr = np.array([y for _, y in train])
    Xte = np.array([[f.get(k, 0.5) for k in M.FEATURES] for f, _ in test])
    yte = np.array([y for _, y in test])
    params = M.fit_logistic(Xtr, ytr)
    mdl = M.EdgeModel(params)
    pte = np.array([mdl.predict(dict(zip(M.FEATURES, x)))[0] for x in Xte])
    ptr = np.array([mdl.predict(dict(zip(M.FEATURES, x)))[0] for x in Xtr])
    pri = M.EdgeModel(M.PRIOR)
    pprior = np.array([pri.predict(dict(zip(M.FEATURES, x)))[0] for x in Xte])
    pred = pte >= 0.5
    tp = int(((pred == 1) & (yte == 1)).sum())
    metrics = {
        "train_images": n_train, "test_images": n_test, "train_samples": int(len(ytr)), "test_samples": int(len(yte)),
        "train_positive_rate": round(float(ytr.mean()), 3), "test_positive_rate": round(float(yte.mean()), 3),
        "test_auc": round(auc(yte, pte), 4), "train_auc": round(auc(ytr, ptr), 4),
        "test_accuracy": round(float((pred == yte).mean()), 4),
        "test_precision": round(tp / max(1, int(pred.sum())), 4), "test_recall": round(tp / max(1, int(yte.sum())), 4),
        "prior_test_auc": round(auc(yte, pprior), 4),
        "prior_test_accuracy": round(float(((pprior >= 0.5) == yte).mean()), 4),
        "data": "Synthetic fragmented images (seeds 1000+ train, 5000+ test), disjoint from demo datasets",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    params["training"] = metrics
    M.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    M.MODEL_PATH.write_text(json.dumps(params, indent=1))
    print(json.dumps(metrics, indent=1))
    print("weights:", {k: round(v, 2) for k, v in params["weights"].items()})


if __name__ == "__main__":
    main()
