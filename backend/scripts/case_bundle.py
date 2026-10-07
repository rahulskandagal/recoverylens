"""Move one analysed case to another machine (e.g. a presenter's laptop) without re-running it.

    .venv\\Scripts\\python -m scripts.case_bundle export <case_id> <out.zip>
    .venv\\Scripts\\python -m scripts.case_bundle import <bundle.zip>

The bundle holds the case's database rows plus its evidence copy and outputs. On import the
evidence path is rewritten to this machine's data folder; nothing else in the rows is machine-specific.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, pipeline, realrecovery  # noqa: E402

TABLES = [("cases", "id"), ("audit", "case_id"), ("fragments", "case_id"), ("files", "case_id"),
          ("blobs", "case_id"), ("investigator", "case_id"), ("recyclebin_items", "case_id")]
EVIDENCE = db.DATA / "evidence"


def export(cid: str, out: Path) -> None:
    if not db.one("SELECT id FROM cases WHERE id=?", (cid,)):
        raise SystemExit(f"case {cid} not found")
    rows = {t: db.query(f"SELECT * FROM {t} WHERE {k}=?", (cid,)) for t, k in TABLES}
    scan_ids = {r["scan_id"] for r in rows["recyclebin_items"]}
    rows["recyclebin_scans"] = [s for sid in scan_ids for s in db.query("SELECT * FROM recyclebin_scans WHERE id=?", (sid,))]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("rows.json", json.dumps({"case_id": cid, "rows": rows}))
        for base, name in ((pipeline.CASES / cid, "cases"), (EVIDENCE / cid, "evidence")):
            for f in base.rglob("*"):
                if f.is_file():
                    z.write(f, f"{name}/{cid}/{f.relative_to(base).as_posix()}")
    print(f"exported {cid} -> {out}")


def import_(bundle: Path) -> None:
    db.init()
    realrecovery.init()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(bundle) as z:
        z.extractall(tmp)
        meta = json.loads((Path(tmp) / "rows.json").read_text())
        cid = meta["case_id"]
        for name, dest in (("cases", pipeline.CASES), ("evidence", EVIDENCE)):
            src = Path(tmp) / name / cid
            if src.exists():
                shutil.rmtree(dest / cid, ignore_errors=True)
                shutil.copytree(src, dest / cid)
    ev = next((p for p in (EVIDENCE / cid).iterdir() if p.is_file()), None) if (EVIDENCE / cid).exists() else None
    with db.conn() as c:
        c.execute("DELETE FROM audit WHERE case_id=?", (cid,))
        for table, rows in meta["rows"].items():
            if table == "audit":  # global AUTOINCREMENT ids would collide with this machine's other cases
                rows = [{k: v for k, v in r.items() if k != "id"} for r in sorted(rows, key=lambda r: r["id"])]
            for r in rows:
                if table == "cases" and ev is not None:
                    r["image_path"] = str(ev)
                cols = ", ".join(r)
                c.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(r))})", tuple(r.values()))
    print(f"imported case {cid}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "export":
        export(sys.argv[2], Path(sys.argv[3]))
    elif len(sys.argv) == 3 and sys.argv[1] == "import":
        import_(Path(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
