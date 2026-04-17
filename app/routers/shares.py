from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

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
    if not result.get("success"):
        # Return 500 so the JS api() helper throws and shows the error toast
        return JSONResponse(status_code=500, content=result)
    return result


@router.post("/smb/stop")
async def stop_smb(_: User = Depends(require_admin)):
    smb_service.stop_smb_server()
    return {"message": "SMB server stopped"}


@router.post("/smb/reload")
async def reload_smb(_: User = Depends(require_admin)):
    result = smb_service.reload_smb_users()
    if not result.get("success"):
        return JSONResponse(status_code=500, content=result)
    return result


@router.get("/smb/check")
async def check_smb(_: User = Depends(require_admin)):
    """
    Detect the platform and check whether the SMB2/3 backend (Samba or
    Windows LanmanServer) is installed and available.
    Returns needs_install=True when the UI should offer an 'Install' button.
    """
    info = smb_service.check_backend()
    info["needs_install"] = not info["installed"]
    return info


@router.post("/smb/install")
async def install_smb(_: User = Depends(require_admin)):
    """
    Trigger auto-install of Samba (Linux/macOS) or enable Windows SMB service.
    Runs the appropriate script from the scripts/ directory.
    Requires the process to have sudo/admin privileges.
    Long-running — waits up to 5 minutes for the package manager to finish.
    """
    result = await run_in_threadpool(smb_service.install_samba)
    return result


@router.post("/dlna/start")
async def start_dlna(_: User = Depends(require_admin)):
    dlna_service.start_dlna_server()
    return {"message": "DLNA server started", **dlna_service.dlna_status()}


@router.post("/dlna/stop")
async def stop_dlna(_: User = Depends(require_admin)):
    dlna_service.stop_dlna_server()
    return {"message": "DLNA server stopped"}
