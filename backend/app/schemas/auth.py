"""Request and response bodies for /auth endpoints.

Validation here gives users instant, specific feedback (422 VALIDATION_ERROR
with a message per field). The same rules apply on web and mobile because
both call these endpoints.
"""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.domain.validation import (
    PASSWORD_MAX_LENGTH,
    check_password_strength,
    normalize_phone,
)
from app.schemas.user import UserOut

Platform = Literal["web", "mobile"]


class RegisterRequest(BaseModel):
    email: EmailStr
    phone: str = Field(max_length=30, examples=["0300 1234567"])
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    # "admin" is deliberately NOT allowed here (spec section 5.1).
    role: Literal["provider", "receiver", "volunteer"]

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())  # trim and collapse inner spaces
        if len(cleaned) < 2:
            raise ValueError("Please enter your full name.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @model_validator(mode="after")
    def strong_password(self) -> "RegisterRequest":
        check_password_strength(self.password, email=self.email)
        return self


class LoginRequest(BaseModel):
    # One field accepts either the email address or the phone number.
    identifier: str = Field(min_length=3, max_length=254, examples=["ali@example.com"])
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    # web: refresh token goes into an httpOnly cookie (JavaScript can't read it)
    # mobile: refresh token is returned in the JSON body (app keeps it in SecureStore)
    platform: Platform = "web"
    device_label: str | None = Field(default=None, max_length=100, examples=["Samsung A54"])


class RefreshRequest(BaseModel):
    # Mobile sends the token here; the website sends nothing (the cookie is used).
    refresh_token: str | None = Field(default=None, max_length=200)


class LogoutRequest(RefreshRequest):
    pass


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    platform: Platform = "web"

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value: str) -> str:
        return check_password_strength(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds until the access token expires
    # Only filled for mobile. For the website it is null: the token is in the cookie.
    refresh_token: str | None = None
    user: UserOut
