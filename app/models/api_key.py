"""
app/models/api_key.py

Named, revocable API keys scoped to a user and environment.
Replaces the single `api_key_hash` column on the User model.

Key lifecycle
─────────────
  ACTIVE   → REVOKED   (user revokes)
  ACTIVE   → EXPIRED   (TTL exceeded — checked at auth time, not a cron job)

The raw key is returned ONCE at creation and never stored.
Only the SHA-256 hash is persisted (same pattern as the existing api_key_hash).

Key format (from security.py):  p1p_<48 hex chars>
We add an environment prefix so callers can tell keys apart visually:
  Test:  p1t_<48 hex chars>
  Live:  p1l_<48 hex chars>
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Enum as SAEnum, DateTime, Index, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, ULIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class KeyEnvironment(str, Enum):
    TEST = "test"
    LIVE = "live"


class KeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ApiKey(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        Index("ix_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_api_keys_prefix", "prefix"),
    )

    # Owner
    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Identity
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), nullable=False)  # first 8 chars — shown in UI
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # SHA-256 hex

    # Scope
    environment: Mapped[KeyEnvironment] = mapped_column(
        SAEnum(KeyEnvironment, name="keyenvironment"), nullable=False, default=KeyEnvironment.TEST
    )

    # State
    status: Mapped[KeyStatus] = mapped_column(
        SAEnum(KeyStatus, name="keystatus"), nullable=False, default=KeyStatus.ACTIVE
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Usage tracking (lightweight — full log is in RequestLog)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="api_keys")
    request_logs: Mapped[list["RequestLog"]] = relationship(  # noqa: F821
        back_populates="api_key", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} prefix={self.prefix} env={self.environment} status={self.status}>"