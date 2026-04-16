"""
SMB service — pure-Python cross-platform implementation using impacket.
Works on Linux, macOS, and Windows without any system Samba installation.

Connect from clients:
  Windows:   \\<server-ip>:4450\MediaFiles  (Map Network Drive → custom port)
  macOS:     Finder → Go → Connect to Server → smb://<server-ip>:4450/MediaFiles
  Linux:     smbclient //server-ip/MediaFiles -p 4450 -U username
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import settings

logger = logging.getLogger("mediastream.smb")

# Empty LM hash (disabled — forces NTLM/NTLMv2)
_EMPTY_LM = "aad3b435b51404eeaad3b435b51404ee"

_smb_server = None
_smb_thread: threading.Thread | None = None


def _build_server():
    """Construct the impacket SimpleSMBServer with current users."""
    try:
        from impacket.smbserver import SimpleSMBServer
    except ImportError:
        logger.error("impacket not installed. Run: pip install impacket")
        return None

    from app.models import list_users

    media_root = str(Path(settings.media_root).resolve())

    server = SimpleSMBServer(
        listenAddress=settings.smb_host,
        listenPort=settings.smb_port,
    )

    # Disable verbose impacket logging
    server.setLogFile("")

    # Main read-only share for all authorised users
    server.addShare(
        settings.smb_share_name.upper(),
        media_root,
        "MediaStream Files",
    )

    # Admin-only writable upload share
    server.addShare(
        settings.smb_share_name.upper() + "_UPLOAD",
        media_root,
        "MediaStream Upload (Admin)",
    )

    users = list_users()
    smb_users = [u for u in users if u.smb_access and not u.disabled and u.nt_hash]

    if not smb_users:
        logger.warning("No SMB-capable users found (need at least one user with smb_access and a set NT hash)")
    else:
        for u in smb_users:
            server.setCredentials(u.username, "", settings.smb_workgroup, _EMPTY_LM, u.nt_hash.upper())
            logger.debug("Registered SMB user: %s", u.username)

    return server


def start_smb_server() -> dict:
    global _smb_server, _smb_thread

    if _smb_server is not None:
        return {"success": False, "error": "SMB server already running"}

    server = _build_server()
    if server is None:
        return {"success": False, "error": "Failed to build SMB server — check logs"}

    _smb_server = server

    def _run():
        logger.info(
            "SMB server listening on %s:%d  share: \\\\<ip>:%d\\%s",
            settings.smb_host, settings.smb_port,
            settings.smb_port, settings.smb_share_name.upper(),
        )
        try:
            _smb_server.start()
        except Exception as e:
            logger.error("SMB server error: %s", e)

    _smb_thread = threading.Thread(target=_run, daemon=True, name="smb-server")
    _smb_thread.start()
    return {"success": True, **smb_status()}


def stop_smb_server() -> None:
    global _smb_server, _smb_thread
    if _smb_server:
        try:
            _smb_server.stop()
        except Exception:
            pass
        _smb_server = None
        logger.info("SMB server stopped")


def reload_smb_users() -> dict:
    """Restart SMB server so updated user list takes effect."""
    if _smb_server is not None:
        stop_smb_server()
    return start_smb_server()


def smb_status() -> dict:
    return {
        "running": _smb_server is not None,
        "host": settings.smb_host,
        "port": settings.smb_port,
        "workgroup": settings.smb_workgroup,
        "share_name": settings.smb_share_name.upper(),
        "connect_hint": (
            f"Windows: \\\\<server-ip>:{settings.smb_port}\\{settings.smb_share_name.upper()}  |  "
            f"macOS: smb://<server-ip>:{settings.smb_port}/{settings.smb_share_name.upper()}"
        ),
    }
