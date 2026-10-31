import re
import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints, model_validator

# Emails are stored/compared lowercase so "A@x.com" and "a@x.com" are one account.
Email = Annotated[EmailStr, AfterValidator(str.lower)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


def check_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(value) > 128:
        raise ValueError("Password must be at most 128 characters")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain at least one letter and one number")
    return value


Password = Annotated[str, AfterValidator(check_password_strength)]


class RegisterRequest(BaseModel):
    full_name: FullName
    email: Email
    password: Password
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class VerifyOtpRequest(BaseModel):
    email: Email
    code: str = Field(pattern=r"^\d{6}$")


class ResendOtpRequest(BaseModel):
    email: Email


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=10)


class ForgotPasswordRequest(BaseModel):
    email: Email


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: Password
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_email_verified: bool


class AuthResponse(BaseModel):
    user: UserOut


class MessageResponse(BaseModel):
    message: str


class AuthConfig(BaseModel):
    google_client_id: str | None
