"""
app/models/request_log.py

Append-only log of every authenticated API request.
Written by RequestLogMiddleware AFTER the response is sent (background task)
so it never adds latency to the critical path.

Retention: keep 90 days by default (purge via a scheduled task / pg_partman).
For dashboard usage charts, aggregate into daily buckets — do NOT scan this
table directly for date-range queries without an index on (api_key_id, created_at).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import String, Integer, Index, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, ULIDPrimaryKeyMixin
from app.models.api_key import KeyEnvironment

from sqlalchemy import Enum as SAEnum

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.user import User


class RequestLog(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "request_logs"
    __table_args__ = (
        # Primary access patterns for the dashboard
        Index("ix_request_logs_api_key_created", "api_key_id", "created_at"),
        Index("ix_request_logs_user_created", "user_id", "created_at"),
        Index("ix_request_logs_created_at", "created_at"),
    )

    # Auth context
    user_id: Mapped[Optional[str]] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[Optional[str]] = mapped_column(
        String(26), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )

    # Environment at time of request (denormalised for fast filtering)
    environment: Mapped[KeyEnvironment] = mapped_column(
        String(10), nullable=False, default=KeyEnvironment.TEST
    )

    # Request
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True, index=True)

    # Response
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)  # rounded to int
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)  # status_code < 400

    # Error details (only set when success=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # IP / agent (optional, for abuse detection)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)   # IPv6 max 45
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    api_key: Mapped[Optional["ApiKey"]] = relationship(back_populates="request_logs")
    user: Mapped[Optional["User"]] = relationship()

    def __repr__(self) -> str:
        return f"<RequestLog {self.method} {self.path} {self.status_code}>"
