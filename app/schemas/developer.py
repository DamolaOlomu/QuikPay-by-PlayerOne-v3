"""
app/schemas/developer.py

Pydantic schemas for the developer dashboard:
  - API key CRUD
  - Request log / usage
  - Support tickets
  - Dashboard overview
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.api_key import KeyEnvironment, KeyStatus
from app.models.support_ticket import TicketCategory, TicketPriority, TicketStatus


# ── API Keys ──────────────────────────────────────────────────────────────────

class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["Production server"])
    environment: KeyEnvironment = KeyEnvironment.TEST
    expires_at: Optional[datetime] = Field(
        None, description="Optional expiry. Omit for non-expiring keys."
    )


class ApiKeyResponse(BaseModel):
    """Returned for list/get operations. Raw key is NEVER included here."""
    id: str
    name: str
    prefix: str
    environment: KeyEnvironment
    status: KeyStatus
    last_used_at: Optional[datetime]
    request_count: int
    expires_at: Optional[datetime]
    created_at: datetime
    revoked_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    """
    Returned ONCE at creation only — includes the raw key.
    The client must store this; it cannot be retrieved again.
    """
    raw_key: str


class ApiKeyRevokeResponse(BaseModel):
    id: str
    status: KeyStatus
    revoked_at: datetime


# ── Request Logs ──────────────────────────────────────────────────────────────

class RequestLogResponse(BaseModel):
    id: str
    api_key_id: Optional[str]
    environment: KeyEnvironment
    method: str
    path: str
    status_code: int
    duration_ms: int
    success: bool
    error_code: Optional[str]
    request_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class UsageOverview(BaseModel):
    """Aggregate stats for the dashboard overview card."""
    total_requests: int
    successful_requests: int
    failed_requests: int
    success_rate: float                    # 0.0–100.0
    avg_latency_ms: float
    p99_latency_ms: Optional[float]


class DailyUsageBucket(BaseModel):
    date: str                              # ISO date "YYYY-MM-DD"
    total: int
    successful: int
    failed: int
    avg_latency_ms: float


class UsageByEndpoint(BaseModel):
    path: str
    method: str
    total: int
    error_rate: float


class UsageStats(BaseModel):
    overview: UsageOverview
    daily: list[DailyUsageBucket]
    by_endpoint: list[UsageByEndpoint]


# ── Dashboard Overview ────────────────────────────────────────────────────────

class DashboardOverview(BaseModel):
    """Top-level summary shown on the dashboard home page."""
    active_api_keys: int
    test_keys: int
    live_keys: int

    # Last 30 days
    requests_last_30d: int
    requests_last_7d: int
    success_rate_last_7d: float

    # Open support tickets
    open_tickets: int

    # Wallet (passed through from existing balance endpoint)
    wallet_balance: float
    wallet_currency: str


# ── Support Tickets ───────────────────────────────────────────────────────────

class SupportTicketCreate(BaseModel):
    subject: str = Field(..., min_length=5, max_length=255)
    body: str = Field(..., min_length=20)
    category: TicketCategory = TicketCategory.OTHER
    priority: TicketPriority = TicketPriority.MEDIUM
    related_resource_type: Optional[str] = None
    related_resource_id: Optional[str] = None

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Ticket body cannot be blank.")
        return v


class SupportTicketUpdate(BaseModel):
    """Admin-only update."""
    status: Optional[TicketStatus] = None
    assigned_to: Optional[str] = None
    resolution_note: Optional[str] = None
    priority: Optional[TicketPriority] = None


class SupportTicketResponse(BaseModel):
    id: str
    subject: str
    body: str
    category: TicketCategory
    priority: TicketPriority
    status: TicketStatus
    assigned_to: Optional[str]
    resolution_note: Optional[str]
    related_resource_type: Optional[str]
    related_resource_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}