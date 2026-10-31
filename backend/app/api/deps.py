"""Reusable dependencies. Later phases protect endpoints with one line:
    @router.get("/x", dependencies=[Depends(require_roles(UserRole.ADMIN))])
or  user: User = Depends(get_current_user)"""
import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.cookies import ACCESS_COOKIE
from app.core.errors import api_error
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, UserRole


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else None
    payload = decode_access_token(token) if token else None
    if payload is None:
        raise api_error(401, "not_authenticated", "Please log in.")
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (ValueError, KeyError):
        user = None
    if user is None or not user.is_active:
        raise api_error(401, "not_authenticated", "Please log in.")
    return user


def require_roles(*roles: UserRole):
    """The role is read from the DATABASE (not the token) so changes apply immediately."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise api_error(403, "forbidden", "You don't have permission to do that.")
        return user

    return checker


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """For public endpoints that personalise when logged in (e.g. the home page)."""
    try:
        return get_current_user(request, db)
    except Exception:
        return None
