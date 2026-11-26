from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """Uniform error body: {"detail": {"code": "...", "message": "..."}}.
    The frontend shows `message`; `code` lets it react (e.g. email_not_verified)."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
