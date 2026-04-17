from __future__ import annotations

import aiofiles
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import StreamingResponse

from app.auth import get_current_user, require_admin, validate_token
from app.config import settings, get_media_mappings, resolve_media_path
from app.models import User

router = APIRouter(prefix="/api/media", tags=["media"])

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

CHUNK = 512 * 1024  # 512 KB


def _http_resolve(virtual: str) -> tuple[str, Path, Path]:
    """Wrap resolve_media_path and convert ValueError → HTTPException."""
    try:
        name, root, target = resolve_media_path(virtual)
        return name, root, target
    except ValueError as exc:
        detail = str(exc)
        if "traversal" in detail:
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=404, detail="Path not found")


def _file_info(path: Path, mapping_name: str, mapping_root: Path) -> dict:
    stat = path.stat()
    rel = path.relative_to(mapping_root)
    # Build the virtual path: "MappingName" or "MappingName/sub/file.mp4"
    virtual_path = mapping_name if str(rel) == "." else f"{mapping_name}/{rel.as_posix()}"
    return {
        "name": path.name,
        "path": virtual_path,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "type": "directory" if path.is_dir() else "file",
        "mime": MEDIA_MIME.get(path.suffix.lower(), "application/octet-stream"),
    }


def _virtual_root_items() -> list[dict]:
    """Each configured mapping appears as a top-level directory."""
    items = []
    for m in get_media_mappings():
        mp: Path = m["path"]
        items.append({
            "name": m["name"],
            "path": m["name"],
            "size": 0,
            "modified": mp.stat().st_mtime if mp.exists() else 0,
            "type": "directory",
            "mime": "inode/directory",
        })
    return items


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/mappings")
async def list_mappings(_: User = Depends(get_current_user)):
    """Return configured folder mappings and whether each path exists on disk."""
    return [
        {
            "name": m["name"],
            "path": str(m["path"]),
            "exists": m["path"].exists(),
        }
        for m in get_media_mappings()
    ]


@router.get("/browse")
async def browse(path: str = "", _: User = Depends(get_current_user)):
    path = path.strip("/")

    if not path:
        # Virtual root: list all configured mappings as directories
        return {"path": "", "items": _virtual_root_items()}

    mapping_name, mapping_root, target = _http_resolve(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    items = sorted(
        (p for p in target.iterdir() if not p.name.startswith(".")),
        key=lambda p: (p.is_file(), p.name.lower()),
    )
    return {
        "path": path,
        "items": [_file_info(p, mapping_name, mapping_root) for p in items],
    }


@router.get("/stream")
async def stream_file(
    path: str,
    request: Request,
    token: str = Query(default=""),
):
    # Accept token from Authorization header OR ?token= query param
    raw = token
    if not raw:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            raw = auth[7:]
    user = validate_token(raw) if raw else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    mapping_name, mapping_root, target = _http_resolve(path.strip("/"))
    if not target or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = target.suffix.lower()
    if ext not in MEDIA_MIME and ext not in settings.allowed_extensions:
        raise HTTPException(status_code=403, detail="File type not allowed")

    mime = MEDIA_MIME.get(ext, "application/octet-stream")
    file_size = target.stat().st_size
    range_header = request.headers.get("Range")

    if range_header:
        try:
            byte_range = range_header.strip().replace("bytes=", "")
            start_str, end_str = byte_range.split("-", 1)
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
            end = min(end, file_size - 1)
        except Exception:
            raise HTTPException(status_code=416, detail="Invalid Range header")

        length = end - start + 1

        async def ranged_chunks():
            async with aiofiles.open(target, "rb") as f:
                await f.seek(start)
                remaining = length
                while remaining > 0:
                    data = await f.read(min(CHUNK, remaining))
                    if not data:
                        break
                    yield data
                    remaining -= len(data)

        return StreamingResponse(
            ranged_chunks(),
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="{target.name}"',
            },
        )
    else:
        async def full_chunks():
            async with aiofiles.open(target, "rb") as f:
                while True:
                    data = await f.read(CHUNK)
                    if not data:
                        break
                    yield data

        return StreamingResponse(
            full_chunks(),
            status_code=200,
            media_type=mime,
            headers={
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="{target.name}"',
            },
        )


@router.post("/upload")
async def upload_file(
    path: str = "",
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in settings.allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")

    path = path.strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="Upload path must include a mapping name")

    _, _, dest_dir = _http_resolve(path)
    if not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Destination is not a directory")

    dest = dest_dir / file.filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"message": f"Uploaded {file.filename}", "path": f"{path}/{file.filename}"}


@router.delete("/delete")
async def delete_file(path: str, _: User = Depends(require_admin)):
    path = path.strip("/")
    _, _, target = _http_resolve(path)
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
    path = path.strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="Path must include a mapping name")
    _, _, target = _http_resolve(path)
    target.mkdir(parents=True, exist_ok=True)
    return {"message": f"Directory created: {path}"}


@router.get("/stats")
async def media_stats(_: User = Depends(get_current_user)):
    total_size = 0
    total_files = 0
    by_type: dict[str, int] = {}
    for m in get_media_mappings():
        mp: Path = m["path"]
        if not mp.exists():
            continue
        for f in mp.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                total_files += 1
                total_size += f.stat().st_size
                ext = f.suffix.lower()
                mime_cat = MEDIA_MIME.get(ext, "other").split("/")[0]
                by_type[mime_cat] = by_type.get(mime_cat, 0) + 1
    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "by_type": by_type,
        "mappings": [{"name": m["name"], "path": str(m["path"])} for m in get_media_mappings()],
    }
