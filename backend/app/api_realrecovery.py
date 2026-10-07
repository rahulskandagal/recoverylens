"""API for Real Recovery Mode: live Windows Recycle Bin evidence, and restore-to-folder for any case."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import realrecovery as rr

router = APIRouter(prefix="/api/real")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.get("/drives")
def drives() -> list[dict[str, Any]]:
    return rr.list_drives()


class ScanReq(BaseModel):
    drive: str


@router.post("/recyclebin/scan")
def scan(body: ScanReq) -> dict[str, Any]:
    return _wrap(rr.start_scan, body.drive)


@router.get("/recyclebin/scans")
def scans() -> list[dict[str, Any]]:
    return rr.list_scans()


@router.get("/recyclebin/scans/{sid}")
def scan_detail(sid: str) -> dict[str, Any]:
    return _wrap(rr.get_scan, sid)


class RecoverReq(BaseModel):
    name: str | None = None


@router.post("/recyclebin/scans/{sid}/items/{item_id}/recover")
def recover(sid: str, item_id: str, body: RecoverReq) -> dict[str, str]:
    return {"case_id": _wrap(rr.recover_item, sid, item_id, body.name)}


@router.get("/recyclebin/carve-images")
def carve_images() -> list[dict[str, Any]]:
    """Completed real disk-image analyses that a permanently-deleted (Recycle Bin already emptied)
    item can be searched against. Demo cases are never offered here."""
    return rr.list_carve_images()


class CarveReq(BaseModel):
    case_id: str


@router.post("/recyclebin/scans/{sid}/items/{item_id}/carve")
def carve(sid: str, item_id: str, body: CarveReq) -> dict[str, Any]:
    """Search an already-analysed storage image for a file matching this permanently-deleted item's
    type and declared size. Never claims recovery if the bytes are no longer present in the evidence."""
    return _wrap(rr.carve_purged_item, sid, item_id, body.case_id)
