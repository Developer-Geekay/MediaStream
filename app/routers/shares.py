from fastapi import APIRouter, Depends

from app.auth import require_admin, get_current_user
from app.models import User
from app.services import ftp as ftp_service
from app.services import smb as smb_service
from app.services import dlna as dlna_service

router = APIRouter(prefix="/api/shares", tags=["shares"])


@router.get("/status")
async def all_status(_: User = Depends(get_current_user)):
    return {
        "ftp": ftp_service.ftp_status(),
        "smb": smb_service.smb_status(),
        "dlna": dlna_service.dlna_status(),
    }


@router.post("/ftp/start")
async def start_ftp(_: User = Depends(require_admin)):
    ftp_service.start_ftp_server()
    return {"message": "FTP server started", **ftp_service.ftp_status()}


@router.post("/ftp/stop")
async def stop_ftp(_: User = Depends(require_admin)):
    ftp_service.stop_ftp_server()
    return {"message": "FTP server stopped"}


@router.post("/smb/start")
async def start_smb(_: User = Depends(require_admin)):
    result = smb_service.start_smb_server()
    return result


@router.post("/smb/stop")
async def stop_smb(_: User = Depends(require_admin)):
    smb_service.stop_smb_server()
    return {"message": "SMB server stopped"}


@router.post("/smb/reload")
async def reload_smb(_: User = Depends(require_admin)):
    result = smb_service.reload_smb_users()
    return result


@router.post("/dlna/start")
async def start_dlna(_: User = Depends(require_admin)):
    dlna_service.start_dlna_server()
    return {"message": "DLNA server started", **dlna_service.dlna_status()}


@router.post("/dlna/stop")
async def stop_dlna(_: User = Depends(require_admin)):
    dlna_service.stop_dlna_server()
    return {"message": "DLNA server stopped"}
