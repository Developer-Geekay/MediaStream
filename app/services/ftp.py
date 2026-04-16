"""
FTP service using pyftpdlib with optional TLS.
Users are authenticated against the shared users DB.
"""
import threading
import logging
from pathlib import Path

from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer
from pyftpdlib.authorizers import DummyAuthorizer

from app.config import settings
from app.models import list_users
from app.auth import verify_password

logger = logging.getLogger("mediastream.ftp")


class MediaAuthorizer(DummyAuthorizer):
    """Authorizer that delegates to the shared users database."""

    def validate_authentication(self, username, password, handler):
        from app.models import get_user
        user = get_user(username)
        if user is None or user.disabled or not user.ftp_access:
            raise Exception("Authentication failed")
        if not verify_password(password, user.hashed_password):
            raise Exception("Authentication failed")

    def get_home_dir(self, username):
        return str(Path(settings.media_root).resolve())

    def has_user(self, username):
        from app.models import get_user
        u = get_user(username)
        return u is not None and not u.disabled and u.ftp_access

    def has_perm(self, username, perm, path=None):
        from app.models import get_user
        user = get_user(username)
        if user is None or user.disabled:
            return False
        # Admins get read+write; viewers get read-only
        read_perms = set("elradfmw") if user.role == "admin" else set("elr")
        return perm in read_perms

    def get_perms(self, username):
        from app.models import get_user
        user = get_user(username)
        if user and user.role == "admin":
            return "elradfmw"
        return "elr"

    def get_msg_login(self, username):
        return f"Welcome to MediaStream FTP, {username}!"

    def get_msg_quit(self, username):
        return "Goodbye."


_ftp_server: FTPServer | None = None
_ftp_thread: threading.Thread | None = None


def start_ftp_server() -> None:
    global _ftp_server, _ftp_thread

    authorizer = MediaAuthorizer()

    use_tls = (
        settings.ftp_tls_enabled
        and Path(settings.ftp_tls_cert).exists()
        and Path(settings.ftp_tls_key).exists()
    )

    if use_tls:
        handler = TLS_FTPHandler
        handler.certfile = settings.ftp_tls_cert
        handler.keyfile = settings.ftp_tls_key
        handler.tls_control_required = False  # allow plain AUTH TLS upgrade
        logger.info("FTP TLS enabled with cert: %s", settings.ftp_tls_cert)
    else:
        handler = FTPHandler
        logger.warning("FTP TLS disabled — certs not found, running plain FTP")

    handler.authorizer = authorizer
    handler.passive_ports = range(settings.ftp_passive_start, settings.ftp_passive_end)
    handler.max_cons = settings.ftp_max_connections
    handler.max_cons_per_ip = settings.ftp_max_per_ip
    handler.banner = "MediaStream FTP Service"
    handler.masquerade_address = None  # set to public IP if behind NAT

    media_root = Path(settings.media_root).resolve()
    media_root.mkdir(parents=True, exist_ok=True)

    _ftp_server = FTPServer((settings.ftp_host, settings.ftp_port), handler)

    def _run():
        logger.info("FTP server starting on %s:%d", settings.ftp_host, settings.ftp_port)
        _ftp_server.serve_forever()

    _ftp_thread = threading.Thread(target=_run, daemon=True, name="ftp-server")
    _ftp_thread.start()


def stop_ftp_server() -> None:
    global _ftp_server
    if _ftp_server:
        _ftp_server.close_all()
        _ftp_server = None
        logger.info("FTP server stopped")


def ftp_status() -> dict:
    return {
        "running": _ftp_server is not None,
        "host": settings.ftp_host,
        "port": settings.ftp_port,
        "tls": settings.ftp_tls_enabled and Path(settings.ftp_tls_cert).exists(),
    }
