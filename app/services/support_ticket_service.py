"""
app/services/support_ticket_service.py
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.models.support_ticket import SupportTicket, TicketStatus
from app.models.user import UserRole
from app.schemas.developer import SupportTicketCreate, SupportTicketResponse, SupportTicketUpdate


class SupportTicketService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, user_id: str, payload: SupportTicketCreate) -> SupportTicketResponse:
        ticket = SupportTicket(
            user_id=user_id,
            subject=payload.subject,
            body=payload.body,
            category=payload.category,
            priority=payload.priority,
            related_resource_type=payload.related_resource_type,
            related_resource_id=payload.related_resource_id,
        )
        self.db.add(ticket)
        await self.db.flush()
        return SupportTicketResponse.model_validate(ticket)

    async def list_for_user(self, user_id: str) -> list[SupportTicketResponse]:
        result = await self.db.execute(
            select(SupportTicket)
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.created_at.desc())
        )
        return [SupportTicketResponse.model_validate(t) for t in result.scalars().all()]

    async def get(self, user_id: str, ticket_id: str, is_admin: bool = False) -> SupportTicketResponse:
        result = await self.db.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ResourceNotFoundError("Ticket not found.")
        if not is_admin and ticket.user_id != user_id:
            raise AuthorizationError("You do not have access to this ticket.")
        return SupportTicketResponse.model_validate(ticket)

    async def update(
        self, ticket_id: str, payload: SupportTicketUpdate, actor_id: str, is_admin: bool = False
    ) -> SupportTicketResponse:
        result = await self.db.execute(
            select(SupportTicket).where(SupportTicket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if not ticket:
            raise ResourceNotFoundError("Ticket not found.")
        if not is_admin and ticket.user_id != actor_id:
            raise AuthorizationError("You do not have access to this ticket.")

        # Non-admins can only close their own open tickets
        if not is_admin:
            if payload.status and payload.status not in (TicketStatus.CLOSED,):
                raise AuthorizationError("Users may only close their own tickets.")

        if payload.status is not None:
            ticket.status = payload.status
        if is_admin:
            if payload.assigned_to is not None:
                ticket.assigned_to = payload.assigned_to
            if payload.resolution_note is not None:
                ticket.resolution_note = payload.resolution_note
            if payload.priority is not None:
                ticket.priority = payload.priority

        return SupportTicketResponse.model_validate(ticket)

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def list_all(
        self, status: TicketStatus | None = None
    ) -> list[SupportTicketResponse]:
        q = select(SupportTicket).order_by(SupportTicket.created_at.desc())
        if status:
            q = q.where(SupportTicket.status == status)
        result = await self.db.execute(q)
        return [SupportTicketResponse.model_validate(t) for t in result.scalars().all()]