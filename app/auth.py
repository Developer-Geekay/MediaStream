from __future__ import annotations

import binascii
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from app.config import settings
from app.models import get_user, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
ALGORITHM = "HS256"


def _prepare(password: str) -> bytes:
    # Pre-hash with SHA-256 so passwords > 72 bytes are handled safely,
    # then base64-encode to keep the result null-byte-free.
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode())
    except Exception:
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode()


def compute_nt_hash(password: str) -> str:
    """Compute NTLM NT hash from a plain-text password (needed for SMB auth)."""
    try:
        from impacket import ntlm
        return binascii.hexlify(ntlm.compute_nthash(password)).decode()
    except Exception:
        try:
            h = hashlib.new("md4", password.encode("utf-16-le"))
            return h.hexdigest()
        except ValueError:
            return ""


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


def authenticate_user(username: str, password: str) -> Optional[User]:
    user = get_user(username)
    if not user or user.disabled:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def validate_token(raw: str) -> Optional[User]:
    """Validate a raw JWT string and return the User, or None if invalid."""
    try:
        payload = jwt.decode(raw, settings.secret_key, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            return None
        user = get_user(username)
        return user if user and not user.disabled else None
    except JWTError:
        return None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc
    user = get_user(username)
    if user is None or user.disabled:
        raise credentials_exc
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
