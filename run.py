#!/usr/bin/env python3
"""Entry point — generates TLS cert if missing, then starts uvicorn."""
import os
import subprocess
import sys
from pathlib import Path


def generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating self-signed TLS cert at {cert_path} ...")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "3650", "-nodes",
            "-subj", "/CN=mediastream.local",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("TLS cert generated.")


def main():
    cert = Path("./certs/server.crt")
    key = Path("./certs/server.key")
    if not cert.exists() or not key.exists():
        try:
            generate_self_signed_cert(cert, key)
        except Exception as e:
            print(f"Warning: could not generate TLS cert ({e}). FTP TLS will be disabled.")

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
