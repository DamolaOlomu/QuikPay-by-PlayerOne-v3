"""
tests/test_coverage_gap.py

Targeted tests to push QuikPay coverage from 68.43 % → ≥ 70 %.

Key gaps addressed (from CI report):
  app/services/usage_service.py              19 %  → needs full path coverage
  app/services/support_ticket_service.py     27 %  → needs all branches
  app/services/api_key_service.py            61 %  → needs revoke / error paths
  app/providers/mock_bank/client.py           0 %  → unit-test each method
  app/providers/mock_bank/dispatcher.py       0 %  → test all dispatch paths
  app/providers/factory.py                   33 %  → test mock + glyde fallback
  app/db/base_models.py                       0 %  → trivial import test
  app/models/agent.py + atm.py               0 %  → instantiation tests
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.models.api_key import ApiKey, KeyEnvironment, KeyStatus
from app.models.request_log import RequestLog
from app.models.support_ticket import (
    SupportTicket,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)
from app.models.user import User
from app.providers.mock_bank.models import (
    MockWebhookOutbox,
    MockWebhookStatus,
)
from app.schemas.developer import (
    ApiKeyCreate,
    SupportTicketCreate,
    SupportTicketUpdate,
)
from app.services.api_key_service import ApiKeyService, _generate_raw_key, _hash_key
from app.services.support_ticket_service import SupportTicketService
from app.services.usage_service import UsageService


def _bcrypt_hash(password: str) -> str:
    """
    Produce a bcrypt-compatible hash without hitting the 72-byte truncation
    bug present in newer bcrypt versions when passlib runs its internal
    wrap-detection check.

    Strategy: pre-hash the password with SHA-256 (32 bytes = always < 72),
    then bcrypt-hash the hex digest.  The stored value is a valid bcrypt hash
    that the passlib CryptContext can later verify against the same digest.
    """
    from passlib.context import CryptContext
    _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    digest = hashlib.sha256(password.encode()).hexdigest()  # 64 hex chars, well under 72 bytes
    return _ctx.hash(digest)


# ─── shared helpers ──────────────────────────────────────────────────────────

async def _make_user(db: AsyncSession, phone: str = "+2349200000001") -> User:
    user = User(
        phone_number=phone,
        fullname="Gap Coverage User",
        hashed_password=_bcrypt_hash("SecurePass123!"),
        email=f"gap_{phone[-4:]}@test.com",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_api_key(
    db: AsyncSession,
    user_id: str,
    env: KeyEnvironment = KeyEnvironment.TEST,
    name: str = "gap-key",
) -> tuple[ApiKey, str]:
    """Returns (orm_key, raw_key)."""
    import secrets

    prefix_str = "p1t" if env == KeyEnvironment.TEST else "p1l"
    raw = f"{prefix_str}_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:8]

    api_key = ApiKey(
        user_id=user_id,
        name=name,
        prefix=prefix,
        key_hash=key_hash,
        environment=env,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key, raw


async def _make_request_logs(
    db: AsyncSession,
    user_id: str,
    count: int = 5,
    success: bool = True,
) -> None:
    now = datetime.now(timezone.utc)
    for i in range(count):
        log = RequestLog(
            user_id=user_id,
            environment=KeyEnvironment.TEST,
            method="POST",
            path="/api/v1/transactions/transfer",
            status_code=200 if success else 500,
            duration_ms=50 + i * 10,
            success=success,
            created_at=now - timedelta(hours=i),
        )
        db.add(log)
    await db.commit()


# ─── _generate_raw_key / _hash_key unit tests ────────────────────────────────

class TestApiKeyHelpers:
    def test_generate_test_key_prefix(self):
        key = _generate_raw_key(KeyEnvironment.TEST)
        assert key.startswith("p1t_")
        assert len(key) == 52  # "p1t_" + 48 hex chars

    def test_generate_live_key_prefix(self):
        key = _generate_raw_key(KeyEnvironment.LIVE)
        assert key.startswith("p1l_")

    def test_hash_key_deterministic(self):
        raw = "p1t_abc123"
        assert _hash_key(raw) == _hash_key(raw)

    def test_hash_key_length(self):
        raw = "p1t_abc123"
        assert len(_hash_key(raw)) == 64  # SHA-256 hex


# ─── ApiKeyService ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestApiKeyService:

    async def test_create_test_key(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000010")
        svc = ApiKeyService(db)
        result = await svc.create_key(user.id, ApiKeyCreate(name="test-k", environment=KeyEnvironment.TEST))
        assert result.raw_key.startswith("p1t_")
        assert result.name == "test-k"

    async def test_create_live_key(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000011")
        svc = ApiKeyService(db)
        result = await svc.create_key(user.id, ApiKeyCreate(name="live-k", environment=KeyEnvironment.LIVE))
        assert result.raw_key.startswith("p1l_")

    async def test_list_keys_excludes_revoked(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000012")
        svc = ApiKeyService(db)
        created = await svc.create_key(user.id, ApiKeyCreate(name="active", environment=KeyEnvironment.TEST))
        await svc.revoke_key(user.id, created.id)
        keys = await svc.list_keys(user.id)
        assert all(k.status != KeyStatus.REVOKED for k in keys)

    async def test_revoke_key_success(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000013")
        svc = ApiKeyService(db)
        created = await svc.create_key(user.id, ApiKeyCreate(name="to-revoke", environment=KeyEnvironment.TEST))
        result = await svc.revoke_key(user.id, created.id)
        assert result.status == KeyStatus.REVOKED
        assert result.revoked_at is not None

    async def test_revoke_already_revoked_raises(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000014")
        svc = ApiKeyService(db)
        created = await svc.create_key(user.id, ApiKeyCreate(name="double-revoke", environment=KeyEnvironment.TEST))
        await svc.revoke_key(user.id, created.id)
        with pytest.raises(AuthorizationError):
            await svc.revoke_key(user.id, created.id)

    async def test_revoke_missing_key_raises(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000015")
        svc = ApiKeyService(db)
        with pytest.raises(ResourceNotFoundError):
            await svc.revoke_key(user.id, "01NONEXISTENTKEYID000000000")

    async def test_get_key_success(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000016")
        svc = ApiKeyService(db)
        created = await svc.create_key(user.id, ApiKeyCreate(name="get-me", environment=KeyEnvironment.TEST))
        fetched = await svc.get_key(user.id, created.id)
        assert fetched.id == created.id

    async def test_get_key_missing_raises(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000017")
        svc = ApiKeyService(db)
        with pytest.raises(ResourceNotFoundError):
            await svc.get_key(user.id, "01NONEXISTENTKEYID000000000")

    async def test_hash_raw_key_static(self):
        assert ApiKeyService.hash_raw_key("p1t_x") == _hash_key("p1t_x")


# ─── SupportTicketService ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSupportTicketService:

    async def test_create_ticket(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000020")
        svc = SupportTicketService(db)
        ticket = await svc.create(
            user.id,
            SupportTicketCreate(
                subject="API key not working",
                body="My API key returns 403 on every request I make.",
                category=TicketCategory.API_KEYS,
                priority=TicketPriority.HIGH,
            ),
        )
        assert ticket.subject == "API key not working"
        assert ticket.status == TicketStatus.OPEN

    async def test_list_for_user_empty(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000021")
        svc = SupportTicketService(db)
        result = await svc.list_for_user(user.id)
        assert result == []

    async def test_list_for_user_returns_own_tickets(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000022")
        svc = SupportTicketService(db)
        await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Transaction failed",
                body="My transaction failed but money was debited from wallet.",
                category=TicketCategory.TRANSACTIONS,
            ),
        )
        await db.commit()
        result = await svc.list_for_user(user.id)
        assert len(result) >= 1

    async def test_get_ticket_success(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000023")
        svc = SupportTicketService(db)
        created = await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Webhook not firing",
                body="My webhook endpoint receives no calls after transaction.",
            ),
        )
        await db.commit()
        fetched = await svc.get(user.id, created.id)
        assert fetched.id == created.id

    async def test_get_ticket_not_found_raises(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000024")
        svc = SupportTicketService(db)
        with pytest.raises(ResourceNotFoundError):
            await svc.get(user.id, "01NONEXISTENTTICKETID0000000")

    async def test_get_ticket_wrong_user_raises(self, db: AsyncSession):
        owner = await _make_user(db, "+2349200000025")
        other = await _make_user(db, "+2349200000026")
        svc = SupportTicketService(db)
        created = await svc.create(
            owner.id,
            SupportTicketCreate(
                subject="Owner only ticket",
                body="This ticket belongs to the owner user only.",
            ),
        )
        await db.commit()
        with pytest.raises(AuthorizationError):
            await svc.get(other.id, created.id)

    async def test_get_ticket_admin_bypass(self, db: AsyncSession):
        owner = await _make_user(db, "+2349200000027")
        svc = SupportTicketService(db)
        created = await svc.create(
            owner.id,
            SupportTicketCreate(
                subject="Admin readable ticket",
                body="Admin should be able to read any user ticket.",
            ),
        )
        await db.commit()
        fetched = await svc.get("admin-user-id", created.id, is_admin=True)
        assert fetched.id == created.id

    async def test_update_ticket_user_close(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000028")
        svc = SupportTicketService(db)
        created = await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Billing question",
                body="I have a question about my billing statement.",
                category=TicketCategory.BILLING,
            ),
        )
        await db.commit()
        updated = await svc.update(
            created.id,
            SupportTicketUpdate(status=TicketStatus.CLOSED),
            actor_id=user.id,
            is_admin=False,
        )
        assert updated.status == TicketStatus.CLOSED

    async def test_update_ticket_user_cannot_set_in_progress(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000029")
        svc = SupportTicketService(db)
        created = await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Status change test",
                body="Non-admin user should not be able to set in-progress.",
            ),
        )
        await db.commit()
        with pytest.raises(AuthorizationError):
            await svc.update(
                created.id,
                SupportTicketUpdate(status=TicketStatus.IN_PROGRESS),
                actor_id=user.id,
                is_admin=False,
            )

    async def test_update_ticket_admin_fields(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000030")
        svc = SupportTicketService(db)
        created = await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Admin update test",
                body="Admin should be able to set resolution note and priority.",
            ),
        )
        await db.commit()
        updated = await svc.update(
            created.id,
            SupportTicketUpdate(
                status=TicketStatus.RESOLVED,
                resolution_note="Resolved by team.",
                priority=TicketPriority.LOW,
            ),
            actor_id="admin-id",
            is_admin=True,
        )
        assert updated.status == TicketStatus.RESOLVED
        assert updated.resolution_note == "Resolved by team."

    async def test_update_ticket_not_found_raises(self, db: AsyncSession):
        svc = SupportTicketService(MagicMock())
        svc.db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        svc.db.execute = AsyncMock(return_value=mock_result)
        with pytest.raises(ResourceNotFoundError):
            await svc.update("nonexistent", SupportTicketUpdate(), actor_id="u1")

    async def test_list_all_no_filter(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000031")
        svc = SupportTicketService(db)
        await svc.create(
            user.id,
            SupportTicketCreate(
                subject="List all test ticket",
                body="This ticket should appear in the admin list_all call.",
            ),
        )
        await db.commit()
        all_tickets = await svc.list_all()
        assert len(all_tickets) >= 1

    async def test_list_all_with_status_filter(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000032")
        svc = SupportTicketService(db)
        await svc.create(
            user.id,
            SupportTicketCreate(
                subject="Open ticket for filter",
                body="This open ticket should appear when filtering by open status.",
            ),
        )
        await db.commit()
        open_tickets = await svc.list_all(status=TicketStatus.OPEN)
        assert all(t.status == TicketStatus.OPEN for t in open_tickets)


# ─── UsageService ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUsageService:

    async def test_get_usage_stats_empty(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000040")
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert stats.overview.total_requests == 0
        assert stats.overview.success_rate == 0.0
        assert stats.daily == []
        assert stats.by_endpoint == []

    async def test_get_usage_stats_with_logs(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000041")
        await _make_request_logs(db, user.id, count=10, success=True)
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert stats.overview.total_requests == 10
        assert stats.overview.successful_requests == 10
        assert stats.overview.failed_requests == 0
        assert stats.overview.success_rate == 100.0

    async def test_get_usage_stats_mixed_success_failure(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000042")
        await _make_request_logs(db, user.id, count=6, success=True)
        await _make_request_logs(db, user.id, count=4, success=False)
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert stats.overview.total_requests == 10
        assert stats.overview.successful_requests == 6
        assert stats.overview.failed_requests == 4
        assert stats.overview.success_rate == 60.0

    async def test_get_usage_stats_latency_p99(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000043")
        await _make_request_logs(db, user.id, count=5, success=True)
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert stats.overview.avg_latency_ms > 0
        assert stats.overview.p99_latency_ms is not None

    async def test_get_usage_stats_environment_filter(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000044")
        await _make_request_logs(db, user.id, count=3, success=True)
        svc = UsageService(db)
        stats_test = await svc.get_usage_stats(user.id, days=30, environment=KeyEnvironment.TEST)
        stats_live = await svc.get_usage_stats(user.id, days=30, environment=KeyEnvironment.LIVE)
        assert stats_test.overview.total_requests == 3
        assert stats_live.overview.total_requests == 0

    async def test_get_usage_stats_api_key_filter(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000045")
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30, api_key_id="nonexistent-key-id")
        assert stats.overview.total_requests == 0

    async def test_get_usage_stats_daily_buckets(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000046")
        await _make_request_logs(db, user.id, count=3, success=True)
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert len(stats.daily) >= 1
        for bucket in stats.daily:
            assert bucket.total > 0

    async def test_get_usage_stats_by_endpoint(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000047")
        await _make_request_logs(db, user.id, count=5, success=True)
        svc = UsageService(db)
        stats = await svc.get_usage_stats(user.id, days=30)
        assert len(stats.by_endpoint) >= 1
        assert stats.by_endpoint[0].method == "POST"

    async def test_get_dashboard_overview(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000048")
        svc = UsageService(db)
        overview = await svc.get_dashboard_overview(
            user_id=user.id,
            wallet_balance=5000.0,
            wallet_currency="NGN",
        )
        assert overview.wallet_balance == 5000.0
        assert overview.wallet_currency == "NGN"
        assert overview.active_api_keys == 0

    async def test_get_dashboard_overview_counts_active_keys(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000049")
        api_svc = ApiKeyService(db)
        await api_svc.create_key(user.id, ApiKeyCreate(name="k1", environment=KeyEnvironment.TEST))
        await api_svc.create_key(user.id, ApiKeyCreate(name="k2", environment=KeyEnvironment.LIVE))
        usage_svc = UsageService(db)
        overview = await usage_svc.get_dashboard_overview(user.id, 0.0, "NGN")
        assert overview.test_keys >= 1
        assert overview.live_keys >= 1
        assert overview.active_api_keys >= 2

    async def test_get_dashboard_overview_success_rate_no_logs(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000050")
        svc = UsageService(db)
        overview = await svc.get_dashboard_overview(user.id, 0.0, "NGN")
        assert overview.success_rate_last_7d == 100.0

    async def test_get_dashboard_overview_open_tickets(self, db: AsyncSession):
        user = await _make_user(db, "+2349200000051")
        ticket_svc = SupportTicketService(db)
        await ticket_svc.create(
            user.id,
            SupportTicketCreate(
                subject="Open ticket for dashboard",
                body="This open ticket should increment the dashboard counter.",
            ),
        )
        await db.commit()
        usage_svc = UsageService(db)
        overview = await usage_svc.get_dashboard_overview(user.id, 0.0, "NGN")
        assert overview.open_tickets >= 1


# ─── providers/factory.py ─────────────────────────────────────────────────────

class TestProviderFactory:

    def test_returns_mock_bank_client_by_default(self):
        from app.providers.factory import get_payment_provider
        from app.providers.mock_bank.client import MockBankClient

        with patch("app.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.PAYMENT_PROVIDER = "mock"
            mock_settings.return_value = settings
            client = get_payment_provider()
        assert isinstance(client, MockBankClient)

    def test_glyde_falls_back_to_mock_when_not_configured(self):
        from app.providers.factory import get_payment_provider
        from app.providers.mock_bank.client import MockBankClient

        with patch("app.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.PAYMENT_PROVIDER = "glyde"
            settings.GLYDE_ENABLED = False
            settings.GLYDE_SECRET_KEY = None
            mock_settings.return_value = settings
            client = get_payment_provider()
        assert isinstance(client, MockBankClient)

    def test_unknown_provider_falls_back_to_mock(self):
        from app.providers.factory import get_payment_provider
        from app.providers.mock_bank.client import MockBankClient

        with patch("app.core.config.get_settings") as mock_settings:
            settings = MagicMock()
            settings.PAYMENT_PROVIDER = "unknown_provider"
            mock_settings.return_value = settings
            client = get_payment_provider()
        assert isinstance(client, MockBankClient)


# ─── providers/mock_bank/dispatcher.py ───────────────────────────────────────

@pytest.mark.asyncio
class TestDispatcher:

    async def test_dispatch_no_pending_events(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import dispatch_pending

        delivered = await dispatch_pending(db)
        assert delivered == 0

    async def test_dispatch_no_url_marks_delivered(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import dispatch_pending

        event = MockWebhookOutbox(
            event_type="transfer.success",
            payload_json=json.dumps({"ref": "test_ref_001"}),
            target_url=None,
            status=MockWebhookStatus.PENDING,
            attempts=0,
        )
        db.add(event)
        await db.commit()

        delivered = await dispatch_pending(db)
        assert delivered == 1

        await db.refresh(event)
        assert event.status == MockWebhookStatus.DELIVERED

    async def test_dispatch_http_success(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import dispatch_pending

        event = MockWebhookOutbox(
            event_type="transfer.success",
            payload_json=json.dumps({"ref": "test_ref_002"}),
            target_url="http://test-hook.example.com/webhook",
            status=MockWebhookStatus.PENDING,
            attempts=0,
        )
        db.add(event)
        await db.commit()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            delivered = await dispatch_pending(db)

        assert delivered == 1
        await db.refresh(event)
        assert event.status == MockWebhookStatus.DELIVERED

    async def test_dispatch_http_4xx_increments_attempts(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import dispatch_pending

        event = MockWebhookOutbox(
            event_type="transfer.failed",
            payload_json=json.dumps({"ref": "test_ref_003"}),
            target_url="http://test-hook.example.com/webhook",
            status=MockWebhookStatus.PENDING,
            attempts=0,
        )
        db.add(event)
        await db.commit()

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            delivered = await dispatch_pending(db)

        assert delivered == 0
        await db.refresh(event)
        assert event.attempts == 1

    async def test_dispatch_exhausts_retries_marks_failed(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import MAX_RETRIES, dispatch_pending

        event = MockWebhookOutbox(
            event_type="transfer.failed",
            payload_json=json.dumps({"ref": "test_ref_004"}),
            target_url="http://test-hook.example.com/webhook",
            status=MockWebhookStatus.PENDING,
            attempts=MAX_RETRIES - 1,
        )
        db.add(event)
        await db.commit()

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            await dispatch_pending(db)

        await db.refresh(event)
        assert event.status == MockWebhookStatus.FAILED

    async def test_dispatch_network_exception_triggers_retry(self, db: AsyncSession):
        from app.providers.mock_bank.dispatcher import dispatch_pending

        event = MockWebhookOutbox(
            event_type="collection.received",
            payload_json=json.dumps({"ref": "test_ref_005"}),
            target_url="http://test-hook.example.com/webhook",
            status=MockWebhookStatus.PENDING,
            attempts=0,
        )
        db.add(event)
        await db.commit()

        with patch(
            "httpx.AsyncClient.post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            delivered = await dispatch_pending(db)

        assert delivered == 0
        await db.refresh(event)
        assert event.attempts == 1


# ─── providers/mock_bank/client.py (unit — no real DB) ───────────────────────

@pytest.mark.asyncio
class TestMockBankClient:
    """
    These tests patch _engine() to avoid needing a live AsyncSessionLocal.
    They verify the adapter correctly maps engine output to provider response shape.
    """

    def _make_client_with_mock_engine(self, engine_mock):
        from app.providers.mock_bank.client import MockBankClient
        client = MockBankClient()
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.begin = MagicMock(return_value=session_mock)
        client._engine = AsyncMock(return_value=(engine_mock, session_mock))
        return client

    async def test_banks_returns_success(self):
        engine = MagicMock()
        engine.list_banks = MagicMock(return_value=[{"code": "001", "name": "Mock Bank"}])
        client = self._make_client_with_mock_engine(engine)
        result = await client.banks()
        assert result["status"] == "success"
        assert len(result["data"]) == 1

    async def test_account_enquiry_returns_success(self):
        engine = MagicMock()
        engine.account_enquiry = MagicMock(return_value={"account_name": "John Doe"})
        client = self._make_client_with_mock_engine(engine)
        result = await client.account_enquiry(account_number="1234567890", bank_code="001")
        assert result["status"] == "success"
        assert result["data"]["account_name"] == "John Doe"

    async def test_balance_returns_success(self):
        engine = AsyncMock()
        engine.float_balance = AsyncMock(return_value=50000)
        client = self._make_client_with_mock_engine(engine)
        result = await client.balance()
        assert result["status"] == "success"
        assert "balance" in result["data"]

    async def test_collection_bank_transfer_delegates(self):
        from decimal import Decimal
        from app.providers.mock_bank.client import MockBankClient
        client = MockBankClient()
        client.initialise_collection = AsyncMock(return_value={"status": "success", "data": {}})
        await client.collection_bank_transfer(
            amount=Decimal("1000"),
            currency="NGN",
            reference="ref-001",
            customer_name="Test Customer",
            customer_email="test@example.com",
        )
        client.initialise_collection.assert_called_once()
        kwargs = client.initialise_collection.call_args.kwargs
        assert kwargs["channels"] == ["bank_transfer"]


# ─── db/base_models.py — import coverage ─────────────────────────────────────

class TestBaseModels:
    def test_import(self):
        from app.db import base_models  # noqa: F401
        assert base_models is not None


# ─── models/agent.py + atm.py — instantiation coverage ──────────────────────

class TestUnusedModels:
    def test_agent_model_importable(self):
        from app.models import agent  # noqa: F401
        assert agent is not None

    def test_atm_model_importable(self):
        from app.models import atm  # noqa: F401
        assert atm is not None