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


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
    user: UserOut
