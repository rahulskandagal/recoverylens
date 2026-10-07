"""Edge-relationship model: P(fragment B directly continues fragment A).

A small logistic-regression model over explainable features. Deterministic format checks
(restart-marker continuity, container-index verification...) enter as *features*, so the
model combines hard evidence with softer statistical signals, and every prediction can be
decomposed into per-feature contributions for the explanation panel.

The model is trained on synthetic fragmented images with known ground truth
(scripts/train_model.py). Without a trained file it falls back to documented prior weights.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np

FEATURES = [
    "type_compat",      # same content family (JPEG scan data, PDF text, ...)
    "struct_order",     # format sequence check: RST n->n+1, PDF obj k->k+1, SQLite page/rowid, log time
    "index_verified",   # +1 placement verified by a container index (xref/central dir/b-tree), -1 contradicted
    "boundary_smooth",  # continuity across the join (JPEG pixel seam, text line-join)
    "entropy_sim",      # 1 - |H(A) - H(B)| / 8
    "hist_sim",         # byte-histogram cosine similarity at the join
    "proximity",        # physical closeness on the medium
    "content_sim",      # token similarity (text formats)
    "time_consistent",  # timestamps ordered and close (1), contradictory (0), n/a (0.5)
]

FEATURE_LABELS = {
    "type_compat": "Fragment type compatibility",
    "struct_order": "Format sequence continuity",
    "index_verified": "Container index verification",
    "boundary_smooth": "Boundary/seam continuity",
    "entropy_sim": "Entropy profile similarity",
    "hist_sim": "Byte-distribution similarity",
    "proximity": "Physical proximity",
    "content_sim": "Content similarity",
    "time_consistent": "Timestamp consistency",
}

PRIOR = {  # hand-set, documented prior used only if no trained model exists
    "weights": {"type_compat": 1.5, "struct_order": 3.0, "index_verified": 3.5, "boundary_smooth": 2.5,
                "entropy_sim": 0.8, "hist_sim": 0.6, "proximity": 0.7, "content_sim": 1.0, "time_consistent": 1.0},
    "bias": -6.2, "mean": {f: 0.0 for f in FEATURES}, "std": {f: 1.0 for f in FEATURES},
    "trained": False,
}

MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "model" / "edge_model.json"

# optional recorder used by the training script to collect (features, context) samples
RECORDER: list[Callable[[dict[str, float], dict[str, Any]], None]] = []


class EdgeModel:
    def __init__(self, params: dict[str, Any]):
        self.p = params
        self.w = np.array([params["weights"][f] for f in FEATURES])
        self.mu = np.array([params["mean"][f] for f in FEATURES])
        self.sd = np.array([params["std"][f] or 1.0 for f in FEATURES])
        self.b = params["bias"]

    @classmethod
    def load(cls) -> "EdgeModel":
        if MODEL_PATH.exists():
            return cls(json.loads(MODEL_PATH.read_text()))
        return cls(PRIOR)

    def vec(self, feats: dict[str, float]) -> np.ndarray:
        return np.array([float(feats.get(f, 0.5)) for f in FEATURES])

    def predict(self, feats: dict[str, float], ctx: dict[str, Any] | None = None) -> tuple[float, list[dict[str, Any]]]:
        for r in RECORDER:
            r(feats, ctx or {})
        z = (self.vec(feats) - self.mu) / self.sd
        contrib = self.w * z
        logit = float(contrib.sum() + self.b)
        p = 1.0 / (1.0 + math.exp(-max(-40, min(40, logit))))
        items = [{"feature": f, "label": FEATURE_LABELS[f], "value": round(float(feats.get(f, 0.5)), 3),
                  "contribution": round(float(c), 3)} for f, c in zip(FEATURES, contrib)]
        items.sort(key=lambda d: -abs(d["contribution"]))
        return p, items

    def card(self) -> dict[str, Any]:
        return {
            "type": "Logistic regression (edge continuation classifier)",
            "features": [{"name": f, "label": FEATURE_LABELS[f], "weight": round(float(self.p["weights"][f]), 3)} for f in FEATURES],
            "bias": round(float(self.b), 3), "trained": self.p.get("trained", False),
            "training": self.p.get("training", {}),
            "limitations": [
                "Trained only on synthetic fragmented images; real-world fragmentation patterns may differ.",
                "Outputs are evidence-weighted probabilities, not proof that fragments belong together.",
                "Deterministic validators, not this model, decide whether reconstructed bytes are structurally valid.",
            ],
        }


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 0.05, iters: int = 4000, lr: float = 0.3) -> dict[str, Any]:
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1])
    b = 0.0
    pos = y.mean()
    cw = np.where(y == 1, 0.5 / max(pos, 1e-3), 0.5 / max(1 - pos, 1e-3))  # class balancing
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ w + b)))
        g = (p - y) * cw
        w -= lr * (Z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return {"weights": dict(zip(FEATURES, w.tolist())), "bias": float(b),
            "mean": dict(zip(FEATURES, mu.tolist())), "std": dict(zip(FEATURES, sd.tolist())), "trained": True}
