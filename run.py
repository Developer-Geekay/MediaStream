#!/usr/bin/env python3
"""
Entry point for MediaStream.
Generates a self-signed TLS certificate (using the 'cryptography' library — no openssl binary needed),
then starts the uvicorn server.

Compatible with Linux, macOS, and Windows.
"""
import os
import sys
from pathlib import Path


def generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Pure-Python TLS cert generation — no openssl binary required."""
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    print(f"Generating self-signed TLS certificate at {cert_path} ...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "mediastream.local"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("mediastream.local"),
                x509.DNSName("localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    print("TLS certificate generated.")


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
