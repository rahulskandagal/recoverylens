"""Build a realistic damaged storage image for testing RecoveryLens end to end.

Unlike a "signature-only" image (a magic number followed by random bytes, which contains nothing to
recover), every file placed here is a REAL, valid file: a Pillow-encoded JPEG, a PDF with objects and
an xref table, a DOCX with an embedded picture, a SQLite database and a log. Damage is then applied
the way real media degrade: fragmentation, deleted (overwritten) clusters, bit flips, a wiped region
filled with a repeating pattern, and deleted FAT directory entries that still carry the file names.

A ground-truth JSON is written next to the image (never read by the engine) so results can be checked.

    .venv\\Scripts\\python scripts\\make_test_image.py                       (18 MB, seed 2025)
    .venv\\Scripts\\python scripts\\make_test_image.py --size-mb 32 --seed 7 --out D:\\images\\test.img
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.common import CLUSTER  # noqa: E402,F401
from app.engine.synth import build_image, realistic_specs  # noqa: E402


def build(size_mb: int, seed: int, out: Path) -> dict:
    """Same file set as the built-in "Damaged USB drive (realistic)" demo dataset, at any size and seed."""
    rng = random.Random(seed)
    specs = realistic_specs(rng)
    out.parent.mkdir(parents=True, exist_ok=True)
    truth = out.with_suffix(".truth.json")
    build_image(specs, size_mb * 1024 * 1024 // CLUSTER, seed, out, truth,
                f"Realistic damaged storage image ({size_mb} MB, seed {seed})", wipe_clusters=64)
    return {"image": str(out), "truth": str(truth), "files": [(s.name, len(s.data)) for s in specs]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size-mb", type=int, default=18)
    ap.add_argument("--seed", type=int, default=2025)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / "test_images" / "realistic_recovery_test_18MB.img")
    a = ap.parse_args()
    r = build(a.size_mb, a.seed, a.out)
    print(f"image: {r['image']}\nground truth: {r['truth']}")
    for name, size in r["files"]:
        print(f"  {name:32} {size:>9,} B")
