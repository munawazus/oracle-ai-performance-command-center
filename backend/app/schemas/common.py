from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict


def _serialize_utc_datetime(dt: datetime) -> str:
    """Every datetime this platform stores is conceptually UTC (see app/models/base.py's
    utcnow()), but SQLite round-trips a timezone-aware value as naive - Pydantic's default
    serialization of a naive datetime omits any offset, and a JS client's `new Date(iso)`
    then silently misreads it as local time (confirmed live: a Health page "5h ago" was
    actually under a minute old, 5 hours being exactly the viewer's UTC offset). Forcing the
    "Z" suffix once, here, fixes every API response rather than every individual call site -
    the same class of fix as `ensure_aware()` in app/models/base.py, applied at the boundary
    instead of at each read.

    Uses `json_encoders` (soft-deprecated in Pydantic v2, removed in v3) rather than a
    per-field serializer, since it applies to every existing and future datetime field with
    no per-schema changes - a real trade-off, revisit this when upgrading to Pydantic v3."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_encoders={datetime: _serialize_utc_datetime})


class ErrorResponse(BaseModel):
    detail: str
