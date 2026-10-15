from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

VALID_EDITION_HINTS = {"SE2", "EE", "UNKNOWN"}
MAX_TAGS = 20
MAX_TAG_LENGTH = 50


def _normalize_tags(tags: list[str]) -> list[str]:
    """Lowercase/trim/dedupe (order-preserving) so tag filtering is consistent regardless
    of how a tag was originally typed - "Prod" and "prod" are the same fleet filter."""
    if len(tags) > MAX_TAGS:
        raise ValueError(f"a database may have at most {MAX_TAGS} tags")
    seen: dict[str, None] = {}
    for raw in tags:
        tag = raw.strip().lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ValueError(f"tag '{raw}' exceeds {MAX_TAG_LENGTH} characters")
        seen[tag] = None
    return list(seen.keys())


class CreateDatabaseRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    service_name: str = Field(min_length=1, max_length=200)
    edition_hint: str = "UNKNOWN"
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list)

    @field_validator("edition_hint")
    @classmethod
    def validate_edition(cls, value: str) -> str:
        if value not in VALID_EDITION_HINTS:
            raise ValueError(f"edition_hint must be one of {sorted(VALID_EDITION_HINTS)}")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return _normalize_tags(value)


class UpdateDatabaseRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    service_name: str | None = Field(default=None, min_length=1, max_length=200)
    edition_hint: str | None = None
    is_active: bool | None = None
    username: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=1, max_length=500)
    tags: list[str] | None = None

    @field_validator("edition_hint")
    @classmethod
    def validate_edition(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_EDITION_HINTS:
            raise ValueError(f"edition_hint must be one of {sorted(VALID_EDITION_HINTS)}")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _normalize_tags(value)


class DatabaseResponse(ApiModel):
    """Never includes username/password/secret_ref - registration metadata only."""

    id: str
    name: str
    host: str
    port: int
    service_name: str
    edition_hint: str
    is_active: bool
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class DatabaseGroupResponse(ApiModel):
    id: str
    name: str
    description: str
    database_count: int = 0
    created_at: datetime
    updated_at: datetime


class CreateDatabaseGroupRequest(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)


class UpdateDatabaseGroupRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class AssignGroupsRequest(ApiModel):
    """Replace-the-full-set semantics - see DatabaseGroupRepository.set_memberships."""

    group_ids: list[str] = Field(default_factory=list)


class BulkCreateDatabasesRequest(ApiModel):
    databases: list[CreateDatabaseRequest] = Field(min_length=1, max_length=100)


class BulkCreateResult(ApiModel):
    index: int
    status: str  # "created" | "failed"
    database: DatabaseResponse | None = None
    error: str | None = None


class BulkCreateDatabasesResponse(ApiModel):
    results: list[BulkCreateResult]
    created_count: int
    failed_count: int


class TestConnectionResponse(ApiModel):
    ok: bool
    category: str | None
    message: str


class CapabilityProfileResponse(ApiModel):
    detected_version: str | None
    detected_edition: str | None
    is_rac: bool | None
    statspack_available: bool | None
    diagnostics_tuning_pack_licensed_confirmed: bool
    privilege_report: dict[str, bool] | None
    last_probed_at: datetime | None
    cpu_count: int | None
