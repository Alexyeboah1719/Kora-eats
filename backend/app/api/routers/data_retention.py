"""Data retention API endpoints."""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.errors import api_error
from app.core.timeutil import utcnow
from app.models.user import User
from app.services.data_retention import (
    cleanup_expired_retention_data,
    permanently_delete_user,
    soft_delete_user,
)

logger = logging.getLogger(__name__)
router = APIRouter()

RETENTION_MONTHS = 6


@router.post("/account/request-deletion")
def request_account_deletion(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Request account deletion. Data is retained for 6 months before permanent deletion."""
    if current_user.deleted_at is not None:
        raise api_error(400, "already_deleted", "This account is already marked for deletion.")
    
    soft_delete_user(db, str(current_user.id))
    
    return {
        "message": f"Your account has been marked for deletion. Your data will be permanently deleted after {RETENTION_MONTHS} months.",
        "retention_months": RETENTION_MONTHS,
        "deleted_at": utcnow().isoformat(),
    }


@router.post("/admin/cleanup-retention")
def cleanup_retention(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin endpoint to clean up expired retention data."""
    if current_user.role.value != "ADMIN":
        raise api_error(403, "forbidden", "Only admins can perform this action.")
    
    deleted_count = cleanup_expired_retention_data(db)
    
    return {
        "message": f"Cleaned up {deleted_count} expired retention records.",
        "deleted_count": deleted_count,
    }


@router.post("/admin/permanent-delete-user/{user_id}")
def permanent_delete_user_admin(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin endpoint to permanently delete a user immediately (GDPR right to be forgotten)."""
    if current_user.role.value != "ADMIN":
        raise api_error(403, "forbidden", "Only admins can perform this action.")
    
    permanently_delete_user(db, user_id)
    
    return {
        "message": f"User {user_id} has been marked for permanent deletion.",
    }
