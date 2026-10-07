"""API for the Recovery Shield module (continuous folder protection)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import shield

router = APIRouter(prefix="/api/shield")


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as e:
        raise HTTPException(404, f"not found: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/overview")
def overview() -> dict[str, Any]:
    return shield.overview()


class NewSource(BaseModel):
    name: str = ""
    path: str


@router.post("/sources")
def create_source(body: NewSource) -> dict[str, Any]:
    return _wrap(shield.register_source, body.name, body.path)


@router.get("/sources")
def sources() -> list[dict[str, Any]]:
    return shield.list_sources()


@router.get("/sources/{sid}")
def source(sid: str) -> dict[str, Any]:
    return _wrap(shield.get_source, sid)


@router.delete("/sources/{sid}")
def remove_source(sid: str, confirm: bool = False) -> dict[str, bool]:
    if not confirm:
        raise HTTPException(400, "pass confirm=true to remove a protected source and its recovery points")
    _wrap(shield.delete_source, sid)
    return {"deleted": True}


@router.post("/sources/{sid}/snapshots")
def create_snapshot(sid: str) -> dict[str, str]:
    return {"id": _wrap(shield.create_snapshot, sid)}


@router.get("/sources/{sid}/snapshots")
def snapshots(sid: str) -> list[dict[str, Any]]:
    _wrap(shield.get_source, sid)
    return shield.list_snapshots(sid)


@router.get("/snapshots/{snap_id}")
def snapshot(snap_id: str) -> dict[str, Any]:
    return _wrap(shield.get_snapshot, snap_id)


@router.post("/snapshots/{snap_id}/verify")
def verify(snap_id: str) -> dict[str, Any]:
    return _wrap(shield.verify_snapshot, snap_id)


@router.post("/sources/{sid}/scan")
def scan(sid: str) -> dict[str, Any]:
    return _wrap(shield.scan_and_diff, sid, False)


class MonitorReq(BaseModel):
    enable: bool


@router.post("/sources/{sid}/monitor")
def monitor(sid: str, body: MonitorReq) -> dict[str, Any]:
    _wrap(shield.get_source, sid)
    return shield.start_monitor(sid) if body.enable else shield.stop_monitor(sid)


@router.get("/sources/{sid}/audit")
def audit(sid: str, after: int = 0) -> list[dict[str, Any]]:
    _wrap(shield.get_source, sid)
    return shield.get_audit(sid, after)


@router.get("/incidents")
def incidents(source_id: str | None = None) -> list[dict[str, Any]]:
    return shield.list_incidents(source_id)


@router.get("/incidents/{iid}")
def incident(iid: str) -> dict[str, Any]:
    return _wrap(shield.get_incident, iid)


class RestoreReq(BaseModel):
    snapshot_id: str
    files: list[str]
    destination_mode: str = "recovery"
    destination_path: str | None = None
    authorized: bool = False


@router.post("/sources/{sid}/restore")
def restore(sid: str, body: RestoreReq) -> dict[str, Any]:
    return _wrap(shield.restore, sid, body.snapshot_id, body.files, body.destination_mode, body.destination_path, body.authorized)


@router.get("/sources/{sid}/restores")
def restores(sid: str) -> list[dict[str, Any]]:
    _wrap(shield.get_source, sid)
    return shield.list_restores(sid)
