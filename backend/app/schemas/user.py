from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import ApiModel
from app.services.user_service import VALID_ROLE_NAMES


class CreateUserRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    roles: list[str] = Field(default_factory=list)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, roles: list[str]) -> list[str]:
        invalid = set(roles) - VALID_ROLE_NAMES
        if invalid:
            raise ValueError(f"Unknown role(s): {', '.join(sorted(invalid))}")
        return roles


class UpdateUserStatusRequest(ApiModel):
    is_active: bool


class UpdateUserRolesRequest(ApiModel):
    roles: list[str]

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, roles: list[str]) -> list[str]:
        invalid = set(roles) - VALID_ROLE_NAMES
        if invalid:
            raise ValueError(f"Unknown role(s): {', '.join(sorted(invalid))}")
        return roles


class UserResponse(ApiModel):
    id: str
    email: str
    display_name: str
    is_active: bool
    roles: list[str]
    created_at: datetime
