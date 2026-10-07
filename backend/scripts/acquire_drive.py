"""OPTIONAL native acquisition helper: image a real drive into a .img file for Real Recovery Mode.

Browser JavaScript cannot read raw disk sectors — that is a real, deliberate browser limitation, not
something RecoveryLens works around. This script is the honest way to close that gap: run BY YOU,
FROM AN ELEVATED (Administrator) TERMINAL, on THIS machine, to acquire a real device you choose.

    .venv\\Scripts\\python scripts\\acquire_drive.py --drive E: --out ..\\test_images\\usb_acquired.img

WHAT IT DOES
  * Opens \\\\.\\<DRIVE>: for raw reading, in binary read-only mode. It never opens it for writing.
  * Streams the bytes in 4 MiB chunks to the given output file, hashing as it goes (SHA-256), and
    prints live progress.
  * The resulting .img is a completely ordinary file: upload it via New Analysis -> Real Recovery
    Mode -> "Upload a storage image", exactly like any other evidence image. No RecoveryLens code
    path treats it specially.

WHAT IT REFUSES TO DO, ON PURPOSE
  * It refuses the OS boot volume (normally C:) by default. Imaging the live drive Windows is
    currently running from, from inside that same running Windows, produces an inconsistent,
    partially-locked snapshot -- real forensic practice acquires a boot volume offline (booted from
    other media) or through a hardware write-blocker, not like this. Pass --i-understand-the-risk
    ONLY if you accept that limitation for a demo and know what you are doing.
  * It never writes anywhere except the single output file you specify.

REQUIREMENTS
  * Windows, run as Administrator (raw \\\\.\\<drive> access requires it).
  * A drive letter that is NOT your boot drive, ideally a USB stick you can safely image (a small
    one keeps this fast: a few hundred MB is plenty for a demo).
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import time
from pathlib import Path

CHUNK = 4 * 1024 * 1024


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _boot_drive() -> str:
    return (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper()


def acquire(drive: str, out: Path, allow_boot: bool) -> None:
    drive = drive.rstrip("\\").rstrip(":").upper() + ":"
    if not sys.platform.startswith("win"):
        raise SystemExit("This helper only supports Windows raw-device paths (\\\\.\\<drive>).")
    if not _is_admin():
        raise SystemExit("Raw device access needs Administrator. Re-run this script from an elevated terminal.")
    if drive == _boot_drive() and not allow_boot:
        raise SystemExit(f"Refusing to image the boot drive ({drive}) without --i-understand-the-risk. "
                         "Acquiring a live system drive from inside itself is not real forensic practice; "
                         "point this at a USB stick or another non-boot drive for an honest demo instead.")
    path = f"\\\\.\\{drive}"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Opening {path} read-only...")
    fh = os.open(path, os.O_RDONLY | os.O_BINARY)
    try:
        try:
            size = os.lseek(fh, 0, os.SEEK_END)
            os.lseek(fh, 0, os.SEEK_SET)
        except OSError:
            size = 0  # some raw device handles don't support SEEK_END; fall back to unknown-size progress
        h = hashlib.sha256()
        written = 0
        t0 = time.time()
        with open(out, "wb") as dst:
            while True:
                buf = os.read(fh, CHUNK)
                if not buf:
                    break
                dst.write(buf)
                h.update(buf)
                written += len(buf)
                if size:
                    pct = 100 * written / size
                    print(f"\r  {written:,} / {size:,} bytes ({pct:5.1f}%) - {written / max(0.1, time.time() - t0) / 1e6:.1f} MB/s", end="")
                else:
                    print(f"\r  {written:,} bytes - {written / max(0.1, time.time() - t0) / 1e6:.1f} MB/s", end="")
        print()
    finally:
        os.close(fh)
    print(f"Done: {written:,} bytes written to {out}")
    print(f"SHA-256: {h.hexdigest()}")
    print("Upload this file via New Analysis -> Real Recovery Mode -> 'Upload a storage image'.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drive", required=True, help="Drive letter to acquire, e.g. E:")
    ap.add_argument("--out", type=Path, required=True, help="Output .img path")
    ap.add_argument("--i-understand-the-risk", action="store_true", dest="allow_boot",
                    help="Required to image the current boot drive; not recommended (see the module docstring)")
    a = ap.parse_args()
    acquire(a.drive, a.out, a.allow_boot)
