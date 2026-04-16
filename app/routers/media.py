import os
import mimetypes
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from app.auth import get_current_user, require_admin
from app.config import settings
from app.models import User

router = APIRouter(prefix="/api/media", tags=["media"])

MEDIA_ROOT = Path(settings.media_root).resolve()

MEDIA_MIME = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}


def _safe_path(rel: str) -> Path:
    """Resolve path and ensure it stays within MEDIA_ROOT."""
    target = (MEDIA_ROOT / rel).resolve()
    if not str(target).startswith(str(MEDIA_ROOT)):
        raise HTTPException(status_code=403, detail="Access denied")
    return target


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(MEDIA_ROOT)),
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "type": "directory" if path.is_dir() else "file",
        "mime": MEDIA_MIME.get(path.suffix.lower(), "application/octet-stream"),
    }


@router.get("/browse")
async def browse(path: str = "", _: User = Depends(get_current_user)):
    target = _safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    return {
        "path": path,
        "items": [_file_info(p) for p in items],
    }


@router.get("/stream")
async def stream_file(path: str, user: User = Depends(get_current_user)):
    target = _safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    ext = target.suffix.lower()
    if ext not in MEDIA_MIME and ext not in settings.allowed_extensions:
        raise HTTPException(status_code=403, detail="File type not allowed")
    mime = MEDIA_MIME.get(ext, "application/octet-stream")
    return FileResponse(str(target), media_type=mime, filename=target.name)


@router.post("/upload")
async def upload_file(
    path: str = "",
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in settings.allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")
    dest_dir = _safe_path(path)
    if not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")
    dest = dest_dir / file.filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"message": f"Uploaded {file.filename}", "path": str(dest.relative_to(MEDIA_ROOT))}


@router.delete("/delete")
async def delete_file(path: str, _: User = Depends(require_admin)):
    target = _safe_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"message": f"Deleted {path}"}


@router.post("/mkdir")
async def make_dir(path: str, _: User = Depends(require_admin)):
    target = _safe_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return {"message": f"Directory created: {path}"}


@router.get("/stats")
async def media_stats(_: User = Depends(get_current_user)):
    total_size = 0
    total_files = 0
    by_type: dict[str, int] = {}
    for f in MEDIA_ROOT.rglob("*"):
        if f.is_file():
            total_files += 1
            total_size += f.stat().st_size
            ext = f.suffix.lower()
            mime_cat = MEDIA_MIME.get(ext, "other").split("/")[0]
            by_type[mime_cat] = by_type.get(mime_cat, 0) + 1
    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "by_type": by_type,
        "media_root": str(MEDIA_ROOT),
    }
