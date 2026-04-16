"""
SMB service: generates and applies a Samba configuration for secure media sharing.
Requires Samba to be installed on the host (apt install samba).
"""
import subprocess
import logging
import shutil
from pathlib import Path

from app.config import settings
from app.models import list_users

logger = logging.getLogger("mediastream.smb")

SMB_CONF_TEMPLATE = """[global]
    workgroup = {workgroup}
    server string = {server_string}
    security = user
    map to guest = never
    encrypt passwords = yes
    smb passwd file = /etc/samba/smbpasswd
    log file = /var/log/samba/log.%m
    max log size = 1000
    server role = standalone server
    passdb backend = tdbsam
    obey pam restrictions = yes
    unix password sync = no
    passwd program = /usr/bin/passwd %u
    pam password change = yes
    restrict anonymous = 2
    ntlm auth = no
    min protocol = SMB2

[{share_name}]
    comment = MediaStream Shared Files
    path = {media_path}
    browseable = yes
    writable = no
    read only = yes
    valid users = {valid_users}
    create mask = 0644
    directory mask = 0755
    force user = nobody
    force group = nogroup
"""

SMB_CONF_ADMIN_SHARE = """
[{share_name}_upload]
    comment = MediaStream Upload (Admin Only)
    path = {media_path}
    browseable = yes
    writable = yes
    read only = no
    valid users = {admin_users}
    create mask = 0644
    directory mask = 0755
"""


def generate_smb_config() -> str:
    users = list_users()
    smb_users = [u.username for u in users if u.smb_access and not u.disabled]
    admin_users = [u.username for u in users if u.role == "admin" and u.smb_access and not u.disabled]

    media_path = str(Path(settings.media_root).resolve())
    valid_users_str = " ".join(smb_users) if smb_users else "nobody"
    admin_users_str = " ".join(admin_users) if admin_users else "root"

    config = SMB_CONF_TEMPLATE.format(
        workgroup=settings.smb_workgroup,
        server_string=settings.smb_server_string,
        share_name=settings.smb_share_name,
        media_path=media_path,
        valid_users=valid_users_str,
    )

    if admin_users:
        config += SMB_CONF_ADMIN_SHARE.format(
            share_name=settings.smb_share_name,
            media_path=media_path,
            admin_users=admin_users_str,
        )

    return config


def apply_smb_config() -> dict:
    if not shutil.which("samba") and not shutil.which("smbd"):
        return {"success": False, "error": "Samba not installed. Run: apt install samba"}

    config = generate_smb_config()
    config_path = Path(settings.smb_config_path)

    try:
        # Backup existing config
        if config_path.exists():
            shutil.copy2(config_path, str(config_path) + ".bak")

        config_path.write_text(config)
        logger.info("Samba config written to %s", config_path)

        # Validate config
        result = subprocess.run(
            ["testparm", "-s", str(config_path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            logger.error("Samba config validation failed: %s", result.stderr)
            return {"success": False, "error": result.stderr}

        # Reload Samba
        reload = subprocess.run(
            ["smbcontrol", "smbd", "reload-config"],
            capture_output=True, text=True, timeout=10
        )
        if reload.returncode != 0:
            # Try systemctl reload
            subprocess.run(["systemctl", "reload", "smbd"], timeout=10)

        logger.info("Samba config applied successfully")
        return {"success": True, "config": config}

    except PermissionError:
        return {"success": False, "error": "Permission denied writing smb.conf — run as root or use sudo"}
    except Exception as e:
        logger.exception("Failed to apply SMB config")
        return {"success": False, "error": str(e)}


def add_smb_user(username: str, password: str) -> dict:
    if not shutil.which("smbpasswd"):
        return {"success": False, "error": "smbpasswd not found"}
    try:
        proc = subprocess.run(
            ["smbpasswd", "-a", "-s", username],
            input=f"{password}\n{password}\n",
            capture_output=True, text=True, timeout=10
        )
        return {"success": proc.returncode == 0, "output": proc.stdout or proc.stderr}
    except Exception as e:
        return {"success": False, "error": str(e)}


def remove_smb_user(username: str) -> dict:
    if not shutil.which("smbpasswd"):
        return {"success": False, "error": "smbpasswd not found"}
    try:
        proc = subprocess.run(
            ["smbpasswd", "-x", username],
            capture_output=True, text=True, timeout=10
        )
        return {"success": proc.returncode == 0}
    except Exception as e:
        return {"success": False, "error": str(e)}


def smb_status() -> dict:
    has_samba = bool(shutil.which("smbd"))
    running = False
    if has_samba:
        try:
            r = subprocess.run(["systemctl", "is-active", "smbd"], capture_output=True, text=True, timeout=5)
            running = r.stdout.strip() == "active"
        except Exception:
            pass
    return {
        "installed": has_samba,
        "running": running,
        "workgroup": settings.smb_workgroup,
        "share_name": settings.smb_share_name,
    }
