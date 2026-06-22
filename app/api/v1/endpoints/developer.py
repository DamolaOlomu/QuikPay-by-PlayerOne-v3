"""
app/api/v1/endpoints/developer.py

Developer dashboard endpoints. All require JWT auth (dashboard login).
API key management, usage stats, support tickets, and the overview card.

Router prefix: /developer
Tags: Developer
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_admin, get_current_user
from app.db.session import get_db
from app.models.api_key import KeyEnvironment
from app.models.support_ticket import TicketStatus
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedResponse
from app.schemas.developer import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ApiKeyRevokeResponse,
    DashboardOverview,
    RequestLogResponse,
    SupportTicketCreate,
    SupportTicketResponse,
    SupportTicketUpdate,
    UsageStats,
)
from app.services.api_key_service import ApiKeyService
from app.services.support_ticket_service import SupportTicketService
from app.services.usage_service import UsageService

router = APIRouter(prefix="/developer", tags=["Developer"])


# ── Dashboard overview ────────────────────────────────────────────────────────

@router.get(
    "/overview",
    response_model=APIResponse[DashboardOverview],
    summary="Dashboard overview — key counts, request stats, open tickets, wallet",
)
async def get_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.user_service import UserService
    user_svc = UserService(db)
    balance_data = await user_svc.get_balance(current_user.id)

    svc = UsageService(db)
    overview = await svc.get_dashboard_overview(
        user_id=current_user.id,
        wallet_balance=balance_data["balance"],
        wallet_currency=balance_data["currency"],
    )
    return APIResponse(data=overview)


# ── API Keys ──────────────────────────────────────────────────────────────────

@router.post(
    "/keys",
    response_model=APIResponse[ApiKeyCreatedResponse],
    status_code=201,
    summary="Create an API key — raw key returned once",
    description=(
        "Creates a named API key for the specified environment. "
        "The raw key is returned in `data.raw_key` exactly once — store it securely. "
        "Test keys (`p1t_`) can only call sandbox endpoints. "
        "Live keys (`p1l_`) have access to all endpoints."
    ),
)
async def create_api_key(
    payload: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApiKeyService(db)
    key = await svc.create_key(current_user.id, payload)
    return APIResponse(
        data=key,
        message="API key created. Store the raw key securely — it will not be shown again.",
    )


@router.get(
    "/keys",
    response_model=APIResponse[list[ApiKeyResponse]],
    summary="List all active API keys (raw key never returned)",
)
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApiKeyService(db)
    keys = await svc.list_keys(current_user.id)
    return APIResponse(data=keys)


@router.get(
    "/keys/{key_id}",
    response_model=APIResponse[ApiKeyResponse],
    summary="Get a single API key by ID",
)
async def get_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApiKeyService(db)
    key = await svc.get_key(current_user.id, key_id)
    return APIResponse(data=key)


@router.delete(
    "/keys/{key_id}",
    response_model=APIResponse[ApiKeyRevokeResponse],
    summary="Revoke an API key — immediate, irreversible",
)
async def revoke_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = ApiKeyService(db)
    result = await svc.revoke_key(current_user.id, key_id)
    return APIResponse(data=result, message="API key revoked. Any requests using this key will now fail.")


# ── Usage / Request Logs ──────────────────────────────────────────────────────

@router.get(
    "/usage",
    response_model=APIResponse[UsageStats],
    summary="Aggregated usage stats — daily buckets, overview, top endpoints",
)
async def get_usage(
    days: int = Query(default=30, ge=1, le=90, description="Lookback window in days"),
    environment: Optional[KeyEnvironment] = Query(default=None),
    api_key_id: Optional[str] = Query(default=None, description="Filter to a single API key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = UsageService(db)
    stats = await svc.get_usage_stats(
        user_id=current_user.id,
        days=days,
        environment=environment,
        api_key_id=api_key_id,
    )
    return APIResponse(data=stats)


@router.get(
    "/logs",
    response_model=APIResponse[list[RequestLogResponse]],
    summary="Raw request log — paginated, filterable",
)
async def get_request_logs(
    environment: Optional[KeyEnvironment] = Query(default=None),
    api_key_id: Optional[str] = Query(default=None),
    success: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select, and_
    from app.models.request_log import RequestLog

    filters = [RequestLog.user_id == current_user.id]
    if environment:
        filters.append(RequestLog.environment == environment)
    if api_key_id:
        filters.append(RequestLog.api_key_id == api_key_id)
    if success is not None:
        filters.append(RequestLog.success == success)

    result = await db.execute(
        select(RequestLog)
        .where(and_(*filters))
        .order_by(RequestLog.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    logs = [RequestLogResponse.model_validate(r) for r in result.scalars().all()]
    return APIResponse(data=logs)


# ── Support Tickets ───────────────────────────────────────────────────────────

@router.post(
    "/support",
    response_model=APIResponse[SupportTicketResponse],
    status_code=201,
    summary="Open a support ticket",
)
async def create_ticket(
    payload: SupportTicketCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = SupportTicketService(db)
    ticket = await svc.create(current_user.id, payload)
    return APIResponse(data=ticket, message="Ticket submitted. Our team will respond shortly.")


@router.get(
    "/support",
    response_model=APIResponse[list[SupportTicketResponse]],
    summary="List your support tickets",
)
async def list_tickets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = SupportTicketService(db)
    tickets = await svc.list_for_user(current_user.id)
    return APIResponse(data=tickets)


@router.get(
    "/support/{ticket_id}",
    response_model=APIResponse[SupportTicketResponse],
    summary="Get a support ticket",
)
async def get_ticket(
    ticket_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = SupportTicketService(db)
    ticket = await svc.get(current_user.id, ticket_id)
    return APIResponse(data=ticket)


@router.patch(
    "/support/{ticket_id}",
    response_model=APIResponse[SupportTicketResponse],
    summary="Close your ticket (users) or update status/assignment (admin)",
)
async def update_ticket(
    ticket_id: str,
    payload: SupportTicketUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import UserRole
    is_admin = current_user.role == UserRole.ADMIN
    svc = SupportTicketService(db)
    ticket = await svc.update(ticket_id, payload, actor_id=current_user.id, is_admin=is_admin)
    return APIResponse(data=ticket)


# ── Admin: all tickets ────────────────────────────────────────────────────────

@router.get(
    "/admin/support",
    response_model=APIResponse[list[SupportTicketResponse]],
    summary="[Admin] List all tickets with optional status filter",
)
async def admin_list_tickets(
    status: Optional[TicketStatus] = Query(default=None),
    _admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    svc = SupportTicketService(db)
    tickets = await svc.list_all(status=status)
    return APIResponse(data=tickets)