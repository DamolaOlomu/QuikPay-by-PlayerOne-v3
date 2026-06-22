"""
app/services/usage_service.py

Aggregates RequestLog data for the developer dashboard.
All queries are scoped to a single user_id and an optional environment filter.

Query strategy
──────────────
For daily buckets we use SQL date-truncation (works on PostgreSQL).
For SQLite (dev), we fall back to Python-side aggregation.
The composite index ix_request_logs_user_created makes these fast.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import KeyEnvironment
from app.models.request_log import RequestLog
from app.schemas.developer import (
    DailyUsageBucket,
    DashboardOverview,
    UsageByEndpoint,
    UsageOverview,
    UsageStats,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UsageService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_usage_stats(
        self,
        user_id: str,
        days: int = 30,
        environment: Optional[KeyEnvironment] = None,
        api_key_id: Optional[str] = None,
    ) -> UsageStats:
        since = _now() - timedelta(days=days)

        filters = [
            RequestLog.user_id == user_id,
            RequestLog.created_at >= since,
        ]
        if environment:
            filters.append(RequestLog.environment == environment)
        if api_key_id:
            filters.append(RequestLog.api_key_id == api_key_id)

        # ── Fetch raw rows (efficient for ≤ 30 days at 100 req/min) ──────────
        result = await self.db.execute(
            select(
                RequestLog.created_at,
                RequestLog.status_code,
                RequestLog.duration_ms,
                RequestLog.path,
                RequestLog.method,
                RequestLog.success,
            ).where(and_(*filters))
        )
        rows = result.all()

        # ── Overview ──────────────────────────────────────────────────────────
        total = len(rows)
        successful = sum(1 for r in rows if r.success)
        failed = total - successful
        success_rate = round((successful / total * 100) if total else 0.0, 2)
        latencies = [r.duration_ms for r in rows]
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        p99_latency: Optional[float] = None
        if latencies:
            sorted_lat = sorted(latencies)
            p99_idx = max(0, int(len(sorted_lat) * 0.99) - 1)
            p99_latency = float(sorted_lat[p99_idx])

        overview = UsageOverview(
            total_requests=total,
            successful_requests=successful,
            failed_requests=failed,
            success_rate=success_rate,
            avg_latency_ms=avg_latency,
            p99_latency_ms=p99_latency,
        )

        # ── Daily buckets ─────────────────────────────────────────────────────
        daily_map: dict[str, dict] = defaultdict(
            lambda: {"total": 0, "successful": 0, "failed": 0, "latencies": []}
        )
        for r in rows:
            date_str = r.created_at.strftime("%Y-%m-%d")
            bucket = daily_map[date_str]
            bucket["total"] += 1
            if r.success:
                bucket["successful"] += 1
            else:
                bucket["failed"] += 1
            bucket["latencies"].append(r.duration_ms)

        daily = [
            DailyUsageBucket(
                date=date_str,
                total=b["total"],
                successful=b["successful"],
                failed=b["failed"],
                avg_latency_ms=round(
                    sum(b["latencies"]) / len(b["latencies"]) if b["latencies"] else 0.0, 2
                ),
            )
            for date_str, b in sorted(daily_map.items())
        ]

        # ── By endpoint ───────────────────────────────────────────────────────
        endpoint_map: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"total": 0, "errors": 0}
        )
        for r in rows:
            key = (r.method, r.path)
            endpoint_map[key]["total"] += 1
            if not r.success:
                endpoint_map[key]["errors"] += 1

        by_endpoint = sorted(
            [
                UsageByEndpoint(
                    method=method,
                    path=path,
                    total=v["total"],
                    error_rate=round(
                        (v["errors"] / v["total"] * 100) if v["total"] else 0.0, 2
                    ),
                )
                for (method, path), v in endpoint_map.items()
            ],
            key=lambda x: x.total,
            reverse=True,
        )[:20]  # top 20 endpoints

        return UsageStats(overview=overview, daily=daily, by_endpoint=by_endpoint)

    async def get_dashboard_overview(
        self, user_id: str, wallet_balance: float, wallet_currency: str
    ) -> DashboardOverview:
        from app.models.api_key import ApiKey, KeyStatus

        # Active key counts
        key_result = await self.db.execute(
            select(ApiKey.environment, func.count(ApiKey.id))
            .where(ApiKey.user_id == user_id, ApiKey.status == KeyStatus.ACTIVE)
            .group_by(ApiKey.environment)
        )
        key_counts = {env: count for env, count in key_result.all()}
        test_keys = key_counts.get(KeyEnvironment.TEST, 0)
        live_keys = key_counts.get(KeyEnvironment.LIVE, 0)

        # Request counts
        now = _now()
        since_30d = now - timedelta(days=30)
        since_7d = now - timedelta(days=7)

        count_result = await self.db.execute(
            select(
                func.count(RequestLog.id),
                func.sum(
                    func.cast(RequestLog.created_at >= since_7d, type_=None)
                ),
            ).where(
                RequestLog.user_id == user_id,
                RequestLog.created_at >= since_30d,
            )
        )
        row = count_result.one()
        requests_30d = int(row[0] or 0)
        requests_7d = int(row[1] or 0)

        # Success rate last 7d
        rate_result = await self.db.execute(
            select(
                func.count(RequestLog.id),
                func.sum(func.cast(RequestLog.success, type_=None)),
            ).where(
                RequestLog.user_id == user_id,
                RequestLog.created_at >= since_7d,
            )
        )
        rate_row = rate_result.one()
        total_7d = int(rate_row[0] or 0)
        success_7d = int(rate_row[1] or 0)
        success_rate_7d = round((success_7d / total_7d * 100) if total_7d else 100.0, 2)

        # Open tickets
        from app.models.support_ticket import SupportTicket, TicketStatus
        ticket_result = await self.db.execute(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.user_id == user_id,
                SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]),
            )
        )
        open_tickets = int(ticket_result.scalar() or 0)

        return DashboardOverview(
            active_api_keys=test_keys + live_keys,
            test_keys=test_keys,
            live_keys=live_keys,
            requests_last_30d=requests_30d,
            requests_last_7d=requests_7d,
            success_rate_last_7d=success_rate_7d,
            open_tickets=open_tickets,
            wallet_balance=wallet_balance,
            wallet_currency=wallet_currency,
        )