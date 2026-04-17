# MediaStream — Codebase Analysis & Git Reorganization Plan

## Context

The project was built on an auto-generated Claude branch `claude/media-streaming-app-J4QxD`. The git history has been reorganized into a proper branching model: a `main` branch (production-ready) and a `dev` branch (ongoing development). This was a one-time restructure — future changes follow the normal `dev → main` flow.

---

## Project Overview

**MediaStream** is a Python/FastAPI-based local network media server that simultaneously exposes media files over multiple protocols:
- **HTTP/HTTPS** — Web UI + REST API (uvicorn, port configured in `.env`)
- **FTP/FTPS** — File transfer (pyftpdlib with TLS)
- **SMB** — Network shares (impacket, custom port 4450)
- **DLNA/UPnP** — Media discovery & streaming for Smart TVs and Quest VR apps (e.g. BigScreen VR)

**Stack:** Python 3.10+, FastAPI, uvicorn, bcrypt, python-jose, impacket, pyftpdlib, PyYAML  
**Storage:** JSON file for user DB, YAML for protocol config  
**Auth:** JWT + bcrypt passwords + NT hashes for SMB

---

## Git Branch Structure

```
main  ← production-ready (default branch on GitHub)
dev   ← active development branch
```

- Repo: `https://github.com/Developer-Geekay/MediaStream.git`
- Develop on `dev`, merge to `main` when ready to release

---

## Files Overview (Critical Paths)

| File | Purpose |
|------|---------|
| `run.py` | Entry point — generates TLS cert, starts uvicorn |
| `app/main.py` | FastAPI app — bootstraps admin, starts FTP/SMB/DLNA |
| `app/config.py` | Config loading (YAML + env) |
| `app/models.py` | User dataclass, JSON persistence |
| `app/auth.py` | bcrypt, JWT, NT hash utilities |
| `app/routers/auth.py` | Login/logout API |
| `app/routers/media.py` | File browsing/upload API |
| `app/routers/users.py` | User management API (admin) |
| `app/services/ftp.py` | FTP/FTPS server |
| `app/services/smb.py` | SMB server (impacket) |
| `app/services/dlna.py` | DLNA/UPnP server |
| `config/config.yaml` | Protocol ports, media paths |
| `.env.example` | Environment variables template |
| `requirements.txt` | Python dependencies |

---

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server
python run.py
```

Default admin credentials: `admin / admin1234` (change after first login)
