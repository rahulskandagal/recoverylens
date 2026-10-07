"""Analyse every demo dataset and print engine results next to ground truth.

    python -m scripts.evaluate_datasets
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.analyze import analyze  # noqa: E402
from app.engine.evaluate import evaluate  # noqa: E402
from app.engine.synth import DATASETS, build_dataset  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rl_eval_"))
    for key in DATASETS:
        img, truth = build_dataset(key, tmp / "demo")
        res = analyze(str(img), tmp / key, lambda s, m: None, lambda p, m: None)
        ev = evaluate(res, json.loads(truth.read_text()))
        print(f"\n== {key}: {DATASETS[key]['title']}  ({res['summary']['analysis_seconds']}s)")
        print(f"   placement precision {ev['cluster_placement_precision']}%  recall {ev['cluster_recall']}%  "
              f"link accuracy {ev['edge_accuracy']}%  bit-rot detected {ev['corruption_bytes_detected']}/{ev['corruption_bytes_injected']}")
        for f in res["files"]:
            r = f["reconstruction_percentage"]
            print(f"   {f['priority_level']:13} {f['file_name'][:34]:34} I={f['integrity_score']:5.1f} "
                  f"R={'  n/a' if r is None else f'{r:5.1f}'} C={f['relationship_confidence']:5.1f}  {f['recovery_status']}")


if __name__ == "__main__":
    main()
