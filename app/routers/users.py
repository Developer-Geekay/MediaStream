from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional

from app.auth import require_admin, hash_password, get_current_user
from app.models import create_user, delete_user, list_users, update_user, get_user, User
from app.services import smb as smb_service

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    ftp_access: bool = True
    smb_access: bool = True
    dlna_access: bool = True

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ("admin", "viewer"):
            raise ValueError("role must be 'admin' or 'viewer'")
        return v

    @field_validator("username")
    @classmethod
    def valid_username(cls, v):
        if not v.isalnum() or len(v) < 2:
            raise ValueError("username must be alphanumeric and at least 2 chars")
        return v.lower()

    @field_validator("password")
    @classmethod
    def strong_password(cls, v):
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    ftp_access: Optional[bool] = None
    smb_access: Optional[bool] = None
    dlna_access: Optional[bool] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None


@router.get("/")
async def get_users(_: User = Depends(require_admin)):
    users = list_users()
    return [
        {
            "username": u.username,
            "role": u.role,
            "ftp_access": u.ftp_access,
            "smb_access": u.smb_access,
            "dlna_access": u.dlna_access,
            "disabled": u.disabled,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.post("/")
async def add_user(req: CreateUserRequest, _: User = Depends(require_admin)):
    try:
        user = create_user(req.username, hash_password(req.password), req.role)
        update_user(req.username, ftp_access=req.ftp_access, smb_access=req.smb_access, dlna_access=req.dlna_access)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"User '{req.username}' created", "username": req.username}


@router.patch("/{username}")
async def update_user_endpoint(username: str, req: UpdateUserRequest, admin: User = Depends(require_admin)):
    if not get_user(username):
        raise HTTPException(status_code=404, detail="User not found")
    updates = {k: v for k, v in req.model_dump().items() if v is not None and k != "password"}
    if req.password:
        if len(req.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        updates["hashed_password"] = hash_password(req.password)
    update_user(username, **updates)
    return {"message": f"User '{username}' updated"}


@router.delete("/{username}")
async def remove_user(username: str, admin: User = Depends(require_admin)):
    if username == admin.username:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if not delete_user(username):
        raise HTTPException(status_code=404, detail="User not found")
    smb_service.remove_smb_user(username)
    return {"message": f"User '{username}' deleted"}
