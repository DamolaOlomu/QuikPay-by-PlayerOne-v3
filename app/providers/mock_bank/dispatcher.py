"""
app/providers/mock_bank/dispatcher.py
Outbox dispatcher — reads PENDING webhook events and delivers them via HTTP POST.

Call `dispatch_pending()` from:
  • A background task spawned in lifespan (runs every N seconds)
  • A test helper that calls it directly after a transfer

The dispatcher respects a max_retries limit and marks events FAILED after
exhaustion, so the app never loops forever on a dead endpoint.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.providers.mock_bank.models import MockWebhookOutbox, MockWebhookStatus

log = get_logger(__name__)

MAX_RETRIES = 3
TIMEOUT_SECONDS = 5.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def dispatch_pending(db: AsyncSession) -> int:
    """
    Deliver all PENDING outbox events.
    Returns the number of events successfully delivered.
    """
    result = await db.execute(
        select(MockWebhookOutbox).where(
            MockWebhookOutbox.status == MockWebhookStatus.PENDING
        )
    )
    events = list(result.scalars().all())
    delivered = 0

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
        for event in events:
            event.attempts += 1
            event.last_attempt_at = _now()

            if not event.target_url:
                # No webhook URL configured — mark delivered (no-op)
                event.status = MockWebhookStatus.DELIVERED
                event.delivered_at = _now()
                delivered += 1
                log.debug("mock_webhook.no_url", event_id=event.id, event_type=event.event_type)
                continue

            try:
                payload = json.loads(event.payload_json)
                response = await http.post(
                    event.target_url,
                    json=payload,
                    headers={"Content-Type": "application/json", "X-MockBank-Event": event.event_type},
                )
                if response.status_code < 300:
                    event.status = MockWebhookStatus.DELIVERED
                    event.delivered_at = _now()
                    delivered += 1
                    log.info(
                        "mock_webhook.delivered",
                        event_id=event.id,
                        event_type=event.event_type,
                        status_code=response.status_code,
                    )
                else:
                    _handle_failure(event, f"HTTP {response.status_code}")
            except Exception as exc:
                _handle_failure(event, str(exc))

    await db.commit()
    return delivered


def _handle_failure(event: MockWebhookOutbox, error: str) -> None:
    event.error = error
    if event.attempts >= MAX_RETRIES:
        event.status = MockWebhookStatus.FAILED
        log.warning(
            "mock_webhook.exhausted",
            event_id=event.id,
            event_type=event.event_type,
            error=error,
        )
    else:
        log.warning(
            "mock_webhook.retry",
            event_id=event.id,
            event_type=event.event_type,
            attempt=event.attempts,
            error=error,
        )
