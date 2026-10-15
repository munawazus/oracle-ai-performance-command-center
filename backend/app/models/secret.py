from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class Secret(UUIDPkMixin, TimestampMixin, Base):
    """Backing store for the local encrypted SecretProvider. Holds ciphertext only.

    No API route ever reads this table's plaintext back out to a client. Swappable for a
    HashiCorp Vault / cloud KMS-backed SecretProvider without changing call sites.
    """

    __tablename__ = "secrets"

    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
