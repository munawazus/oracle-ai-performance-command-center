from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class DatabaseGroup(UUIDPkMixin, TimestampMixin, Base):
    """A named, organization-defined collection of databases (master plan Sec 35 Phase 22:
    "DB groups") - e.g. "Production", "APAC region". A database may belong to zero or more
    groups; membership is a separate many-to-many table (DatabaseGroupMembership) rather than
    a single group_id column, since the master plan doesn't constrain a database to one
    group."""

    __tablename__ = "database_groups"

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class DatabaseGroupMembership(Base):
    """Many-to-many join, same composite-primary-key pattern as app/models/rbac.py's
    UserRole/RolePermission - no separate id column needed for a pure association row."""

    __tablename__ = "database_group_memberships"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("database_groups.id", ondelete="CASCADE"), primary_key=True)
