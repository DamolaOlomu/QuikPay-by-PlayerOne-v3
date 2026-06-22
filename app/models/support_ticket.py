"""
app/models/support_ticket.py

Support tickets raised by dashboard users.
Simple linear model: open → in_progress → resolved / closed.
No threading — for a proper threaded reply chain, add a TicketReply table later.
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import String, Text, Enum as SAEnum, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, ULIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketCategory(str, Enum):
    API_KEYS = "api_keys"
    TRANSACTIONS = "transactions"
    WEBHOOKS = "webhooks"
    ACCOUNT = "account"
    BILLING = "billing"
    BUG = "bug"
    OTHER = "other"


class SupportTicket(ULIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_user_id", "user_id"),
        Index("ix_support_tickets_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    # Content
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Classification
    category: Mapped[TicketCategory] = mapped_column(
        SAEnum(TicketCategory, name="ticketcategory"), nullable=False, default=TicketCategory.OTHER
    )
    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(TicketPriority, name="ticketpriority"), nullable=False, default=TicketPriority.MEDIUM
    )

    # State
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus, name="ticketstatus"), nullable=False, default=TicketStatus.OPEN
    )

    # Admin fields
    assigned_to: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)  # admin user_id
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Reference to a specific API resource the ticket is about (optional)
    related_resource_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    related_resource_id: Mapped[Optional[str]] = mapped_column(String(26), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        return f"<SupportTicket id={self.id} status={self.status} subject={self.subject[:40]!r}>"