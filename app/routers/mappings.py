from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin, get_current_user
from app.models import User
from app.services.media_mappings import load_mappings, add_mapping, remove_mapping

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


class AddMappingRequest(BaseModel):
    path: str
    name: Optional[str] = None


@router.get("/")
async def list_mappings(_: User = Depends(get_current_user)):
    return [
        {"name": m["name"], "path": str(m["path"]), "exists": m["path"].exists()}
        for m in load_mappings()
    ]


@router.post("/")
async def add_mapping_endpoint(req: AddMappingRequest, _: User = Depends(require_admin)):
    try:
        return add_mapping(req.path, req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}")
async def remove_mapping_endpoint(name: str, _: User = Depends(require_admin)):
    if not remove_mapping(name):
        raise HTTPException(status_code=404, detail=f"Mapping '{name}' not found")
    return {"message": f"Mapping '{name}' removed"}


@router.get("/fs")
async def browse_filesystem(path: str = "", _: User = Depends(require_admin)):
    """
    Server-side directory browser for the folder-picker UI.
    Returns the current path, its parent, and its immediate subdirectories.
    """
    target = Path(path).expanduser().resolve() if path else Path.home()

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Path not found or not a directory")

    try:
        dirs = sorted(
            child.name
            for child in target.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )
    except PermissionError:
        dirs = []

    parent = str(target.parent) if target.parent != target else None
    return {
        "path": str(target),
        "parent": parent,
        "dirs": dirs,
    }
