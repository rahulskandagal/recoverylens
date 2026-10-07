"""Minimal SQLite b-tree page and record parser (table b-trees, no overflow chains)."""
from __future__ import annotations

import re
import struct
from typing import Any


def varint(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    for k in range(9):
        c = b[i + k]
        if k == 8:
            return (v << 8) | c, 9
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, k + 1
    return v, 9


def parse_record(payload: bytes) -> list[Any]:
    hlen, n = varint(payload, 0)
    types = []
    i = n
    while i < hlen:
        t, k = varint(payload, i)
        types.append(t)
        i += k
    out: list[Any] = []
    p = hlen
    for t in types:
        if t == 0:
            out.append(None)
        elif 1 <= t <= 6:
            ln = [0, 1, 2, 3, 4, 6, 8][t]
            out.append(int.from_bytes(payload[p:p + ln], "big", signed=True))
            p += ln
        elif t == 7:
            out.append(round(struct.unpack(">d", payload[p:p + 8])[0], 4))
            p += 8
        elif t in (8, 9):
            out.append(t - 8)
        elif t >= 12 and t % 2 == 0:
            ln = (t - 12) // 2
            out.append(payload[p:p + ln].hex()[:32])
            p += ln
        elif t >= 13:
            ln = (t - 13) // 2
            out.append(payload[p:p + ln].decode("utf-8", "replace"))
            p += ln
        else:
            raise ValueError(f"reserved serial type {t}")
    if p > len(payload):
        raise ValueError("record overruns payload")
    return out


def parse_page(b: bytes, page_size: int = 4096, is_first: bool = False) -> dict[str, Any]:
    h = 100 if is_first else 0
    t = b[h]
    info: dict[str, Any] = {"type": {0x0D: "table_leaf", 0x05: "table_interior", 0x0A: "index_leaf", 0x02: "index_interior"}.get(t, "invalid")}
    if info["type"] == "invalid":
        return info
    _, ncell, content, _ = struct.unpack(">HHHB", b[h + 1:h + 8])
    hl = 12 if t in (0x05, 0x02) else 8
    ptrs = struct.unpack(f">{ncell}H", b[h + hl:h + hl + 2 * ncell])
    info["ncell"] = ncell
    if t == 0x05:
        info["rightmost"] = struct.unpack(">I", b[h + 8:h + 12])[0]
        cells = []
        for p in ptrs:
            child = struct.unpack(">I", b[p:p + 4])[0]
            key, _ = varint(b, p + 4)
            cells.append((child, key))
        info["cells"] = cells
    elif t == 0x0D:
        rows, errors = [], 0
        usable = page_size - 35
        for p in ptrs:
            try:
                plen, k1 = varint(b, p)
                rowid, k2 = varint(b, p + k1)
                if plen > usable:
                    rows.append((rowid, None))  # overflow chain not followed in prototype
                    continue
                rows.append((rowid, parse_record(b[p + k1 + k2:p + k1 + k2 + plen])))
            except Exception:
                errors += 1
        info["rows"] = rows
        info["errors"] = errors
        if rows:
            info["min_rowid"] = min(r for r, _ in rows)
            info["max_rowid"] = max(r for r, _ in rows)
            cols = [len(v) for _, v in rows if v is not None]
            info["columns"] = max(set(cols), key=cols.count) if cols else None
    return info


def table_columns(sql: str) -> list[str]:
    m = re.search(r"\((.*)\)", sql or "", re.S)
    if not m:
        return []
    depth, cur, parts = 0, "", []
    for ch in m.group(1):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    cols = []
    for p in parts:
        w = p.strip().split()
        if w and w[0].upper() not in ("PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT"):
            cols.append(w[0].strip('"`[]'))
    return cols
