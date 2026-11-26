from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record(db: Session, action: str, user_id=None, ip: str | None = None, detail: str | None = None) -> None:
    """Adds an audit row to the current transaction. The caller commits."""
    db.add(AuditLog(user_id=user_id, action=action, ip_address=ip, detail=detail))
