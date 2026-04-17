import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.models import create_user, get_user
from app.auth import hash_password, compute_nt_hash
from app.routers import auth, users, media, shares, mappings
from app.services import ftp as ftp_service
from app.services import smb as smb_service
from app.services import dlna as dlna_service

# ── Console logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mediastream")

# ── File logging ──────────────────────────────────────────────────────────────
# Set up rotating file handlers immediately so every log line (including
# startup) lands in the log files, not just the console.
_LOGS_DIR = Path("./logs")
_LOGS_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# app.log — all INFO+ messages, rotated at 10 MB, 7 backups kept
_app_handler = logging.handlers.RotatingFileHandler(
    _LOGS_DIR / "app.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7,
    encoding="utf-8",
)
_app_handler.setLevel(logging.DEBUG)
_app_handler.setFormatter(_fmt)

# error.log — ERROR+ only, for quick diagnostics
_err_handler = logging.handlers.RotatingFileHandler(
    _LOGS_DIR / "error.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_err_handler.setLevel(logging.ERROR)
_err_handler.setFormatter(_fmt)

# Attach both handlers to the root logger so every named logger writes to files
_root = logging.getLogger()
_root.addHandler(_app_handler)
_root.addHandler(_err_handler)

logger.info("File logging initialised → %s/{app,error}.log", _LOGS_DIR.resolve())


def _bootstrap_admin():
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin1234")
    if not get_user("admin"):
        create_user(
            "admin",
            hash_password(admin_pass),
            nt_hash=compute_nt_hash(admin_pass),
            role="admin",
        )
        logger.warning(
            "Default admin created — username: admin  password: %s  "
            "Change immediately via the web UI!",
            admin_pass,
        )


def _ensure_dirs():
    for sub in ("movies", "music", "photos"):
        (Path(settings.media_root) / sub).mkdir(parents=True, exist_ok=True)
    Path("./certs").mkdir(exist_ok=True)
    Path("./config").mkdir(exist_ok=True)
    Path("./logs").mkdir(exist_ok=True)
    logger.debug("DIRS            Ensured: certs/, config/, logs/, media/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_dirs()
    _bootstrap_admin()

    if settings.ftp_enabled:
        ftp_service.start_ftp_server()

    if settings.smb_enabled:
        smb_service.start_smb_server()

    if settings.dlna_enabled:
        dlna_service.start_dlna_server()

    logger.info("MediaStream ready — web UI: http://0.0.0.0:%d", settings.port)
    yield

    ftp_service.stop_ftp_server()
    smb_service.stop_smb_server()
    dlna_service.stop_dlna_server()
    logger.info("MediaStream stopped")


app = FastAPI(
    title="MediaStream",
    description="Secure local network media sharing via SMB, FTP, and DLNA",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(media.router)
app.include_router(shares.router)
app.include_router(mappings.router)

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    index = Path(__file__).parent / "static" / "index.html"
    if index.exists():
        return FileResponse(
            str(index),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return {"message": "MediaStream API", "docs": "/api/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "MediaStream"}
