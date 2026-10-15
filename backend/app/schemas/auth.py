from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import ApiModel


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class AccessTokenResponse(ApiModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class CurrentUserResponse(ApiModel):
    id: str
    email: str
    display_name: str
    roles: list[str]
    permissions: list[str]
