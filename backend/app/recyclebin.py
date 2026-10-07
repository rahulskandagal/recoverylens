"""Real Windows Recycle Bin evidence: read-only detection of files still recoverable via $I/$R records.

This is genuine forensic technique (the same one Recuva-class tools use), not a simulation: Windows
never actually erases a file on delete-to-Recycle-Bin. It moves the bytes into
`<drive>:\\$Recycle.Bin\\<SID>\\$R<suffix>` and writes a small index record `$I<suffix>` next to it
recording the original name, path, size and deletion time. As long as `$R<suffix>` has not itself been
deleted (e.g. by emptying the Recycle Bin) and not yet overwritten, the original bytes are sitting on
disk untouched.

Everything here opens files with `"rb"` only. Nothing is ever written back into `$Recycle.Bin` or to
the file's original location. When the Recycle Bin has been emptied, `$I`/`$R` are gone and this module
correctly reports nothing — the honest next step is unallocated-space carving on a real evidence image
via the existing upload pipeline (engine/analyze.py), which this module deliberately does not attempt.
"""
from __future__ import annotations

import shutil
import string
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .engine.common import sha256

# Windows FILETIME epoch (1601-01-01) to Unix epoch, in 100ns ticks
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _filetime_to_iso(ticks: int) -> str | None:
    if ticks <= 0:
        return None
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=ticks / 10)).isoformat(timespec="seconds")
    except (OverflowError, OSError):
        return None


@dataclass
class RecycleItem:
    drive: str
    sid: str
    index_name: str  # e.g. "$I3K2J8F.pdf" — the stable identifier for this entry
    index_path: str
    data_path: str | None  # the matching "$R..." path, or None if already purged
    original_name: str
    original_path: str
    deleted_at: str | None
    size: int
    has_data: bool

    def to_dict(self) -> dict[str, Any]:
        return {"drive": self.drive, "sid": self.sid, "index_name": self.index_name, "original_name": self.original_name,
                "original_path": self.original_path, "deleted_at": self.deleted_at, "size": self.size, "has_data": self.has_data}


def list_drives() -> list[dict[str, Any]]:
    """Fixed and removable drives visible to this process, read-only enumeration."""
    out = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        try:
            usage = shutil.disk_usage(root)
            total, free = usage.total, usage.free
        except OSError:
            total = free = None
        rb = root / "$Recycle.Bin"
        out.append({"drive": f"{letter}:", "total_bytes": total, "free_bytes": free, "has_recycle_bin": rb.is_dir()})
    return out


def _parse_index(path: Path) -> tuple[int, str, str] | None:
    """(size, deleted_at ISO or empty, original path) from a $I record, or None if unparseable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 24:
        return None
    version = struct.unpack_from("<q", data, 0)[0]
    size = struct.unpack_from("<q", data, 8)[0]
    ticks = struct.unpack_from("<q", data, 16)[0]
    deleted_at = _filetime_to_iso(ticks) or ""
    try:
        if version == 2 and len(data) >= 28:
            nchars = struct.unpack_from("<i", data, 24)[0]
            raw = data[28:28 + nchars * 2]
            # nchars counts the trailing NUL terminator too, so the decoded string ends in "\x00";
            # left in, that embedded NUL crashes os.open() later when the name is used to build a path.
            orig = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        else:  # version 1 (pre-Windows 10 1809), or an unrecognised/older layout: read to the trailing NUL
            raw = data[24:]
            orig = raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    except (struct.error, UnicodeDecodeError):
        return None
    return size, deleted_at, orig


def scan_recycle_bin(drive: str, root: Path | None = None) -> dict[str, Any]:
    """Every $I/$R pair currently sitting in <drive>:\\$Recycle.Bin, across every readable SID folder.

    `root` overrides where the drive's root directory is looked up (only ever passed by tests, which
    point it at a hand-built temporary tree instead of a real drive letter); production calls omit it.
    """
    drive = drive.rstrip("\\").rstrip(":") + ":"
    rb = (root if root is not None else Path(f"{drive}\\")) / "$Recycle.Bin"
    items: list[RecycleItem] = []
    errors: list[str] = []
    if not rb.is_dir():
        return {"drive": drive, "items": [], "errors": [f"No $Recycle.Bin folder on {drive} (nothing has been deleted to it, "
                                                        "or this drive does not use one)"]}
    try:
        sid_dirs = [d for d in rb.iterdir() if d.is_dir()]
    except OSError as e:
        return {"drive": drive, "items": [], "errors": [f"Cannot list {rb}: {type(e).__name__}: {e}"]}
    for sid_dir in sid_dirs:
        try:
            entries = list(sid_dir.iterdir())
        except OSError as e:
            errors.append(f"{sid_dir.name}: {type(e).__name__}: {e} (likely another user's Recycle Bin; needs elevation)")
            continue
        for p in entries:
            if not p.name.startswith("$I"):
                continue
            parsed = _parse_index(p)
            if parsed is None:
                errors.append(f"{p}: could not parse index record")
                continue
            size, deleted_at, orig = parsed
            r_name = "$R" + p.name[2:]
            r_path = p.with_name(r_name)
            has_data = r_path.is_file()
            items.append(RecycleItem(
                drive=drive, sid=sid_dir.name, index_name=p.name, index_path=str(p), data_path=str(r_path) if has_data else None,
                original_name=orig.rsplit("\\", 1)[-1] or orig, original_path=orig, deleted_at=deleted_at or None,
                size=size, has_data=has_data,
            ))
    items.sort(key=lambda i: i.deleted_at or "", reverse=True)
    return {"drive": drive, "items": [i.to_dict() for i in items], "errors": errors,
            "_raw": items}  # _raw carries dataclasses for recover_item(); stripped before it reaches the API


def find_item(drive: str, sid: str, index_name: str, root: Path | None = None) -> RecycleItem | None:
    scan = scan_recycle_bin(drive, root)
    for it in scan["_raw"]:
        if it.sid == sid and it.index_name == index_name:
            return it
    return None


def read_item_bytes(item: RecycleItem) -> bytes:
    """The original file's exact bytes, read once, read-only. Raises if the $R data is gone."""
    if not item.has_data or not item.data_path:
        raise FileNotFoundError(f"{item.index_name}: $R data is no longer present (Recycle Bin already emptied for this item)")
    with open(item.data_path, "rb") as fh:
        return fh.read()


def verify_item_hash(item: RecycleItem) -> str:
    return sha256(read_item_bytes(item))
