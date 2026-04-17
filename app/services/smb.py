"""
SMB service — platform-aware SMB2/3 backend.

Platform backends:
  Linux   → smbd (Samba)          — SMB2/3, custom port (default 4450)
  macOS   → smbd (Samba via brew) — SMB2/3, custom port (default 4450)
  Windows → net share (built-in LanmanServer) — SMB2/3, port 445

Auto-install:
  Linux   → scripts/install_samba_linux.sh   (apt / yum / dnf / pacman / zypper)
  macOS   → scripts/install_samba_macos.sh   (brew install samba)
  Windows → scripts/install_samba_windows.ps1 (enables LanmanServer service)

All SMB operations are logged to logs/app.log.
Samba process logs go to logs/smb.log (written by smbd via smb.conf).
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import settings, get_media_mappings

logger = logging.getLogger("mediastream.smb")

# ─── Paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent   # MediaStream/
_SAMBA_DIR    = _PROJECT_ROOT / "config" / "samba"
_LOGS_DIR     = _PROJECT_ROOT / "logs"
_SMB_CONF     = _SAMBA_DIR / "smb.conf"

# ─── State ───────────────────────────────────────────────────────────────────
_smbd_proc: subprocess.Popen | None = None
_windows_shares: list[str] = []
_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# Platform detection
# ══════════════════════════════════════════════════════════════════════════════

def get_platform() -> str:
    """Return 'linux', 'macos', or 'windows'."""
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def _find_smbd() -> str | None:
    """
    Return the path to Samba's smbd binary, or None if not installed.

    IMPORTANT — macOS caveat:
      /usr/sbin/smbd is Apple's own SMB daemon (part of macOS), NOT Samba.
      It does not accept Samba CLI flags (--foreground, --configfile, etc.)
      and exits immediately with code 0 when invoked directly.
      We skip /usr/sbin entirely on macOS and look only in Homebrew paths.
    """
    if sys.platform == "darwin":
        candidates = [
            "/opt/homebrew/sbin/smbd",   # Homebrew on Apple Silicon (M1/M2/M3/M4)
            "/usr/local/sbin/smbd",       # Homebrew on Intel Mac
        ]
        for p in candidates:
            if Path(p).is_file() and os.access(p, os.X_OK):
                logger.debug("SMB CHECK       Found Homebrew smbd: %s", p)
                return p
        logger.debug(
            "SMB CHECK       Homebrew smbd not found "
            "(skipped /usr/sbin/smbd — that is Apple's daemon, not Samba)"
        )
        return None   # Apple's /usr/sbin/smbd is NOT Samba

    # Linux / other — use PATH
    path = shutil.which("smbd")
    logger.debug("SMB CHECK       smbd via PATH: %s", path or "not found")
    return path


def check_samba_installed() -> bool:
    """Return True if Samba's smbd is available (not Apple's built-in)."""
    return _find_smbd() is not None


def check_backend() -> dict:
    """
    Return backend availability info — called by the UI to decide whether
    to show an 'Install Samba' prompt.
    """
    platform = get_platform()
    logger.info("SMB CHECK       Platform detected: %s", platform)

    if platform == "windows":
        # net share is always available; check if LanmanServer service is running
        try:
            r = subprocess.run(
                ["sc", "query", "LanmanServer"],
                capture_output=True, text=True, timeout=5,
            )
            service_running = "RUNNING" in r.stdout
        except Exception as exc:
            logger.warning("SMB CHECK       Could not query LanmanServer: %s", exc)
            service_running = False

        logger.info(
            "SMB CHECK       Windows LanmanServer running: %s", service_running
        )
        return {
            "platform": "windows",
            "backend": "windows_lanman",
            "installed": True,
            "available": service_running,
            "protocol": "SMB2/3",
            "port": 445,
            "install_note": (
                None if service_running
                else "Run scripts/install_samba_windows.ps1 as Administrator to enable SMB."
            ),
            "install_script": str(_PROJECT_ROOT / "scripts" / "install_samba_windows.ps1"),
        }

    # Linux / macOS
    installed = check_samba_installed()
    smbd_path = _find_smbd() or "not found"
    if platform == "macos":
        install_note = "brew install samba   (then re-start MediaStream)"
    else:
        install_note = "apt install samba  OR  yum install samba  (then re-start MediaStream)"

    logger.info(
        "SMB CHECK       smbd=%s  installed=%s  port=%d",
        smbd_path, installed, settings.smb_port,
    )
    return {
        "platform": platform,
        "backend": "samba",
        "installed": installed,
        "available": installed,
        "protocol": "SMB2/3",
        "port": settings.smb_port,
        "install_note": None if installed else install_note,
        "install_script": str(_PROJECT_ROOT / "scripts" / f"install_samba_{platform}.sh"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Auto-install
# ══════════════════════════════════════════════════════════════════════════════

def install_samba() -> dict:
    """
    Run the platform-appropriate install script.
    Requires sudo on Linux/macOS, or the process to be elevated on Windows.
    Called via POST /api/shares/smb/install after the user clicks 'Install'.
    """
    platform = get_platform()
    logger.info("SMB INSTALL     Requested on platform=%s", platform)

    if platform == "windows":
        script = _PROJECT_ROOT / "scripts" / "install_samba_windows.ps1"
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    else:
        script = _PROJECT_ROOT / "scripts" / f"install_samba_{platform}.sh"
        if not script.exists():
            msg = f"Install script not found: {script}"
            logger.error("SMB INSTALL     %s", msg)
            return {"success": False, "error": msg}
        script.chmod(0o755)
        cmd = ["bash", str(script)]

    if not script.exists():
        msg = f"Install script not found: {script}"
        logger.error("SMB INSTALL     %s", msg)
        return {"success": False, "error": msg}

    logger.info("SMB INSTALL     Running: %s", " ".join(str(c) for c in cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,   # 5-minute ceiling for package downloads
        )

        for line in (result.stdout or "").splitlines():
            logger.info("SMB INSTALL     [stdout] %s", line)
        for line in (result.stderr or "").splitlines():
            logger.warning("SMB INSTALL     [stderr] %s", line)

        if result.returncode != 0:
            msg = f"Install script exited with code {result.returncode}"
            logger.error("SMB INSTALL     FAILED — %s", msg)
            return {
                "success": False,
                "error": msg,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        logger.info("SMB INSTALL     SUCCESS — Samba installed on %s", platform)
        return {
            "success": True,
            "platform": platform,
            "message": "Samba installed successfully. You can now start the SMB server.",
        }

    except subprocess.TimeoutExpired:
        msg = "Install script timed out after 300 s"
        logger.error("SMB INSTALL     %s", msg)
        return {"success": False, "error": msg}
    except Exception as exc:
        logger.exception("SMB INSTALL     Unexpected error: %s", exc)
        return {"success": False, "error": str(exc)}


# ══════════════════════════════════════════════════════════════════════════════
# Samba backend helpers (Linux / macOS)
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_samba_dirs():
    for d in [
        _SAMBA_DIR,
        _SAMBA_DIR / "private",
        _SAMBA_DIR / "lock",
        _SAMBA_DIR / "state",
        _SAMBA_DIR / "cache",
        _LOGS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
        logger.debug("SMB DIRS        Ensured: %s", d)


def _generate_smb_conf() -> Path:
    """
    Write smb.conf from the current media mappings.
    Protocol is locked to SMB2/3 minimum — SMB1 is never offered.
    All shares are guest-readable (no OS-level user account needed).
    """
    _ensure_samba_dirs()
    mappings = get_media_mappings()

    lines = [
        "# Auto-generated by MediaStream — do not edit manually",
        "",
        "[global]",
        f"    workgroup     = {settings.smb_workgroup}",
        f"    server string = MediaStream",
        f"    netbios name  = MEDIASTREAM",
        "",
        "    # ── Protocol ──────────────────────────────────────────",
        "    # Enforce SMB2/3; refuse SMB1 connections entirely",
        "    server min protocol = SMB2",
        "    server max protocol = SMB3",
        "",
        f"    # Custom port avoids conflict with OS SMB on 445",
        f"    smb ports = {settings.smb_port}",
        "",
        "    # ── Authentication ────────────────────────────────────",
        "    # Guest access: unauthenticated clients land on nobody/guest",
        "    security     = user",
        "    map to guest = Bad User",
        "",
        "    # ── Internal paths ────────────────────────────────────",
        f"    private dir     = {_SAMBA_DIR / 'private'}",
        f"    pid directory   = {_SAMBA_DIR}",
        f"    lock directory  = {_SAMBA_DIR / 'lock'}",
        f"    state directory = {_SAMBA_DIR / 'state'}",
        f"    cache directory = {_SAMBA_DIR / 'cache'}",
        "",
        "    # ── Logging ───────────────────────────────────────────",
        f"    log file   = {_LOGS_DIR / 'smb.log'}",
        "    log level  = 2",
        "    max log size = 51200",
        "",
        "    # ── Misc ──────────────────────────────────────────────",
        "    load printers    = no",
        "    printing         = bsd",
        "    printcap name    = /dev/null",
        "    disable spoolss  = yes",
        "    unix extensions  = no",
        "",
    ]

    # One share per mapping
    for m in mappings:
        share_name = m["name"].upper().replace(" ", "_")[:20]
        path = str(m["path"])
        lines += [
            f"[{share_name}]",
            f"    comment     = MediaStream — {m['name']}",
            f"    path        = {path}",
            "    guest ok    = yes",
            "    read only   = yes",
            "    browseable  = yes",
            "    create mask = 0644",
            "    directory mask = 0755",
            "",
        ]
        logger.debug("SMB CONFIG      Share: %s → %s", share_name, path)

    # Legacy MEDIAFILES share — always present, points to first mapping
    if mappings:
        legacy_path = str(mappings[0]["path"])
    else:
        legacy_path = str(Path(settings.media_root).resolve())

    share_name = settings.smb_share_name.upper()
    lines += [
        f"[{share_name}]",
        f"    comment    = MediaStream Files",
        f"    path       = {legacy_path}",
        "    guest ok   = yes",
        "    read only  = yes",
        "    browseable = yes",
        "",
    ]

    _SMB_CONF.write_text("\n".join(lines))
    logger.info(
        "SMB CONFIG      Written %s  shares=%d",
        _SMB_CONF, len(mappings) + 1,
    )
    return _SMB_CONF


# ══════════════════════════════════════════════════════════════════════════════
# Samba start / stop / reload
# ══════════════════════════════════════════════════════════════════════════════

def _start_samba() -> dict:
    global _smbd_proc

    if not check_samba_installed():
        info = check_backend()
        msg = (
            "smbd not found — Samba is not installed. "
            f"Use POST /api/shares/smb/install or run: {info['install_note']}"
        )
        logger.error("SMB START       %s", msg)
        return {"success": False, "error": msg, "needs_install": True}

    _generate_smb_conf()

    smbd = _find_smbd()
    cmd = [
        smbd,
        "--foreground",
        "--no-process-group",
        f"--configfile={_SMB_CONF}",
        "--debuglevel=2",
        f"--log-basename={_LOGS_DIR / 'smb'}",
    ]
    logger.info("SMB START       Launching: %s", " ".join(cmd))

    try:
        _smbd_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Short delay to catch instant crash
        time.sleep(0.8)
        if _smbd_proc.poll() is not None:
            rc = _smbd_proc.returncode
            _smbd_proc = None
            msg = (
                f"smbd exited immediately (code={rc}). "
                f"Check {_LOGS_DIR / 'smb.log'} for details."
            )
            logger.error("SMB START       %s", msg)
            return {"success": False, "error": msg}

        logger.info(
            "SMB START       smbd running  PID=%d  port=%d  protocol=SMB2/3",
            _smbd_proc.pid, settings.smb_port,
        )
        return {"success": True, **smb_status()}

    except Exception as exc:
        _smbd_proc = None
        logger.exception("SMB START       Failed to launch smbd: %s", exc)
        return {"success": False, "error": str(exc)}


def _stop_samba():
    global _smbd_proc
    if _smbd_proc is None:
        return
    pid = _smbd_proc.pid
    logger.info("SMB STOP        Terminating smbd PID=%d", pid)
    try:
        _smbd_proc.terminate()
        try:
            _smbd_proc.wait(timeout=5)
            logger.info("SMB STOP        smbd PID=%d exited cleanly", pid)
        except subprocess.TimeoutExpired:
            _smbd_proc.kill()
            logger.warning("SMB STOP        smbd PID=%d force-killed (did not terminate in 5 s)", pid)
    except Exception as exc:
        logger.warning("SMB STOP        Error stopping smbd: %s", exc)
    finally:
        _smbd_proc = None


def _reload_samba() -> dict:
    """Re-generate config and send SIGHUP — smbd reloads shares and users."""
    _generate_smb_conf()
    if _smbd_proc and _smbd_proc.poll() is None:
        try:
            _smbd_proc.send_signal(signal.SIGHUP)
            logger.info("SMB RELOAD      Sent SIGHUP to smbd PID=%d", _smbd_proc.pid)
        except Exception as exc:
            logger.warning("SMB RELOAD      Could not send SIGHUP: %s", exc)
    return {"success": True, **smb_status()}


# ══════════════════════════════════════════════════════════════════════════════
# Windows backend helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_lanman_running():
    """Start Windows File Sharing service if it is not already running."""
    try:
        r = subprocess.run(
            ["sc", "query", "LanmanServer"],
            capture_output=True, text=True, timeout=10,
        )
        if "RUNNING" in r.stdout:
            logger.debug("SMB WINDOWS     LanmanServer already running")
            return
        logger.info("SMB WINDOWS     LanmanServer not running — starting...")
        result = subprocess.run(
            ["net", "start", "LanmanServer"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("SMB WINDOWS     LanmanServer started")
        else:
            logger.warning(
                "SMB WINDOWS     Could not start LanmanServer: %s",
                result.stderr.strip(),
            )
    except Exception as exc:
        logger.warning("SMB WINDOWS     LanmanServer check failed: %s", exc)


def _start_windows_smb() -> dict:
    global _windows_shares
    _ensure_lanman_running()

    mappings = get_media_mappings()
    created: list[str] = []

    for m in mappings:
        share_name = m["name"].upper().replace(" ", "_")[:20]
        path = str(m["path"])
        logger.info("SMB WINDOWS     Creating share: %s → %s", share_name, path)
        try:
            r = subprocess.run(
                [
                    "net", "share",
                    f"{share_name}={path}",
                    "/GRANT:Everyone,READ",
                    f"/REMARK:MediaStream — {m['name']}",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0:
                created.append(share_name)
                logger.info("SMB WINDOWS     Share created: %s", share_name)
            else:
                logger.warning(
                    "SMB WINDOWS     Share %s failed (rc=%d): %s",
                    share_name, r.returncode, r.stderr.strip(),
                )
        except Exception as exc:
            logger.error("SMB WINDOWS     Error creating share %s: %s", share_name, exc)

    # Legacy MEDIAFILES share
    legacy_share = settings.smb_share_name.upper()
    legacy_path = str(mappings[0]["path"]) if mappings else str(Path(settings.media_root).resolve())
    try:
        r = subprocess.run(
            ["net", "share", f"{legacy_share}={legacy_path}", "/GRANT:Everyone,READ",
             "/REMARK:MediaStream Files"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            created.append(legacy_share)
            logger.info("SMB WINDOWS     Legacy share created: %s", legacy_share)
        else:
            logger.warning("SMB WINDOWS     Legacy share failed: %s", r.stderr.strip())
    except Exception as exc:
        logger.error("SMB WINDOWS     Error creating legacy share: %s", exc)

    _windows_shares = created

    if not created:
        msg = "No SMB shares could be created — run as Administrator or check logs/app.log"
        logger.error("SMB WINDOWS     %s", msg)
        return {"success": False, "error": msg}

    logger.info(
        "SMB WINDOWS     Started  shares=%s  port=445  protocol=SMB2/3",
        created,
    )
    return {"success": True, **smb_status()}


def _stop_windows_smb():
    global _windows_shares
    for share_name in list(_windows_shares):
        logger.info("SMB WINDOWS     Deleting share: %s", share_name)
        try:
            r = subprocess.run(
                ["net", "share", share_name, "/DELETE", "/Y"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                logger.info("SMB WINDOWS     Share deleted: %s", share_name)
            else:
                logger.warning(
                    "SMB WINDOWS     Could not delete share %s: %s",
                    share_name, r.stderr.strip(),
                )
        except Exception as exc:
            logger.error("SMB WINDOWS     Error deleting share %s: %s", share_name, exc)
    _windows_shares = []
    logger.info("SMB WINDOWS     All shares removed")


# ══════════════════════════════════════════════════════════════════════════════
# Public API — called by shares router and main.py lifespan
# ══════════════════════════════════════════════════════════════════════════════

def start_smb_server() -> dict:
    with _lock:
        platform = get_platform()
        logger.info("SMB START       Requested  platform=%s", platform)

        if platform == "windows":
            if _windows_shares:
                logger.warning("SMB START       Already running (Windows shares exist)")
                return {"success": False, "error": "SMB server already running"}
            return _start_windows_smb()
        else:
            if _smbd_proc is not None:
                logger.warning("SMB START       Already running (smbd PID=%d)", _smbd_proc.pid)
                return {"success": False, "error": "SMB server already running"}
            return _start_samba()


def stop_smb_server() -> None:
    with _lock:
        platform = get_platform()
        logger.info("SMB STOP        Requested  platform=%s", platform)
        if platform == "windows":
            _stop_windows_smb()
        else:
            _stop_samba()


def reload_smb_users() -> dict:
    """
    Reload user list / shares:
    • Samba  → re-generate smb.conf + SIGHUP (no restart needed)
    • Windows → delete and re-create shares
    """
    with _lock:
        platform = get_platform()
        logger.info("SMB RELOAD      Requested  platform=%s", platform)

        if platform == "windows":
            _stop_windows_smb()
            return _start_windows_smb()
        else:
            if _smbd_proc is None or _smbd_proc.poll() is not None:
                logger.info("SMB RELOAD      smbd not running — doing fresh start")
                return _start_samba()
            return _reload_samba()


def smb_status() -> dict:
    platform = get_platform()

    if platform == "windows":
        running = bool(_windows_shares)
        port = 445
        share = settings.smb_share_name.upper()
        connect_hint = (
            f"Windows: Map Network Drive → \\\\<server-ip>\\{share}\n"
            f"Linux:   smbclient //<server-ip>/{share} -U <user>\n"
            f"macOS:   Finder → Go → Connect to Server → smb://<server-ip>/{share}"
        )
    else:
        running = _smbd_proc is not None and _smbd_proc.poll() is None
        port = settings.smb_port
        share = settings.smb_share_name.upper()
        connect_hint = (
            f"Linux:   smbclient //<server-ip>/{share} -p {port}\n"
            f"macOS:   Finder → Go → Connect to Server → smb://<server-ip>:{port}/{share}\n"
            f"Windows: net use Z: \\\\<server-ip>\\{share}   (custom port — use net use, not Explorer)"
        )

    return {
        "running": running,
        "host": settings.smb_host,
        "port": port,
        "workgroup": settings.smb_workgroup,
        "share_name": share,
        "platform": platform,
        "protocol": "SMB2/3",
        "connect_hint": connect_hint,
    }
