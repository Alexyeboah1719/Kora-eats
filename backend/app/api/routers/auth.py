from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.rate_limit import client_ip
from app.core.cookies import REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.core.rate_limit import rate_limit
from app.db.session import get_db
from app.models.user import User
from app.schemas import auth as s
from app.services import auth_service as svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip(request: Request) -> str | None:
    return client_ip(request)


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _login_response(result: svc.Session_, response: Response) -> s.AuthResponse:
    user, access, refresh = result
    set_auth_cookies(response, access, refresh)
    return s.AuthResponse(user=svc.to_user_out(user))


@router.get("/config", response_model=s.AuthConfig)
def auth_config() -> s.AuthConfig:
    """Public values the frontend needs (the Google client ID is public by design)."""
    return s.AuthConfig(google_client_id=get_settings().google_client_id or None)


@router.post("/register", response_model=s.MessageResponse, status_code=201,
             dependencies=[Depends(rate_limit("register", 10, 3600))])
def register(data: s.RegisterRequest, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    svc.register(db, data, background, _ip(request))
    return s.MessageResponse(message="Account created. We sent a verification code to your email.")


@router.post("/verify-otp", response_model=s.AuthResponse,
             dependencies=[Depends(rate_limit("verify_otp", 20, 600))])
def verify_otp(data: s.VerifyOtpRequest, request: Request, response: Response,
               background: BackgroundTasks, db: Session = Depends(get_db)):
    return _login_response(svc.verify_otp(db, data, background, _ip(request), _ua(request)), response)


@router.post("/resend-otp", response_model=s.MessageResponse,
             dependencies=[Depends(rate_limit("resend_otp", 10, 3600))])
def resend_otp(data: s.ResendOtpRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    svc.resend_otp(db, data.email, background)
    return s.MessageResponse(message="If that account needs verification, a new code is on its way.")


@router.post("/login", response_model=s.AuthResponse, dependencies=[Depends(rate_limit("login", 10, 300))])
def login(data: s.LoginRequest, request: Request, response: Response,
          background: BackgroundTasks, db: Session = Depends(get_db)):
    return _login_response(svc.login(db, data, background, _ip(request), _ua(request)), response)


@router.post("/google", response_model=s.AuthResponse, dependencies=[Depends(rate_limit("google", 20, 300))])
def google(data: s.GoogleLoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    return _login_response(svc.google_login(db, data.credential, _ip(request), _ua(request)), response)


@router.post("/refresh", response_model=s.AuthResponse, dependencies=[Depends(rate_limit("refresh", 60, 300))])
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    return _login_response(svc.refresh(db, request.cookies.get(REFRESH_COOKIE), _ua(request)), response)


@router.post("/logout", response_model=s.MessageResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    svc.logout(db, request.cookies.get(REFRESH_COOKIE))
    clear_auth_cookies(response)
    return s.MessageResponse(message="Logged out.")


@router.post("/forgot-password", response_model=s.MessageResponse,
             dependencies=[Depends(rate_limit("forgot", 5, 900))])
def forgot_password(data: s.ForgotPasswordRequest, request: Request,
                    background: BackgroundTasks, db: Session = Depends(get_db)):
    svc.forgot_password(db, data, background, _ip(request))
    # Same answer whether or not the email exists.
    return s.MessageResponse(message="If an account exists for that email, we've sent a reset link.")


@router.post("/reset-password", response_model=s.MessageResponse,
             dependencies=[Depends(rate_limit("reset", 10, 900))])
def reset_password(data: s.ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    svc.reset_password(db, data, _ip(request))
    return s.MessageResponse(message="Password updated. You can now log in.")


@router.get("/me", response_model=s.UserOut)
def me(user: User = Depends(get_current_user)):
    return svc.to_user_out(user)
