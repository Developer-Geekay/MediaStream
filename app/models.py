import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime


USERS_DB = Path("./config/users.json")


@dataclass
class User:
    username: str
    hashed_password: str
    role: str = "viewer"  # admin | viewer
    ftp_access: bool = True
    smb_access: bool = True
    dlna_access: bool = True
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    disabled: bool = False


def _load_users() -> dict[str, dict]:
    if USERS_DB.exists():
        with open(USERS_DB) as f:
            return json.load(f)
    return {}


def _save_users(users: dict[str, dict]) -> None:
    USERS_DB.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_DB, "w") as f:
        json.dump(users, f, indent=2)


def get_user(username: str) -> Optional[User]:
    users = _load_users()
    data = users.get(username)
    if data:
        return User(**data)
    return None


def create_user(username: str, hashed_password: str, role: str = "viewer") -> User:
    users = _load_users()
    if username in users:
        raise ValueError(f"User '{username}' already exists")
    user = User(username=username, hashed_password=hashed_password, role=role)
    users[username] = asdict(user)
    _save_users(users)
    return user


def update_user(username: str, **kwargs) -> Optional[User]:
    users = _load_users()
    if username not in users:
        return None
    users[username].update(kwargs)
    _save_users(users)
    return User(**users[username])


def delete_user(username: str) -> bool:
    users = _load_users()
    if username not in users:
        return False
    del users[username]
    _save_users(users)
    return True


def list_users() -> list[User]:
    return [User(**u) for u in _load_users().values()]
