import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.models import create_user, get_user
from app.auth import hash_password
from app.routers import auth, users, media, shares
from app.services import ftp as ftp_service
from app.services import dlna as dlna_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mediastream")


def _bootstrap_admin():
    """Create default admin account on first run."""
    admin_pass = os.environ.get("ADMIN_PASSWORD", "admin1234")
    if not get_user("admin"):
        create_user("admin", hash_password(admin_pass), role="admin")
        logger.warning(
            "Created default admin account. Username: admin  Password: %s  "
            "Change this immediately via the web UI!",
            admin_pass,
        )


def _ensure_dirs():
    Path(settings.media_root).mkdir(parents=True, exist_ok=True)
    for sub in ("movies", "music", "photos"):
        (Path(settings.media_root) / sub).mkdir(exist_ok=True)
    Path("./certs").mkdir(exist_ok=True)
    Path("./config").mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_dirs()
    _bootstrap_admin()

    if settings.ftp_enabled:
        ftp_service.start_ftp_server()

    if settings.dlna_enabled:
        dlna_service.start_dlna_server()

    logger.info("MediaStream started — web UI at http://0.0.0.0:%d", settings.port)
    yield

    ftp_service.stop_ftp_server()
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

# Serve static web UI
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def root():
    index = Path(__file__).parent / "static" / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "MediaStream API", "docs": "/api/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "MediaStream"}
