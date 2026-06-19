"""
app/providers/factory.py
Returns the active PaymentProviderClient based on the PAYMENT_PROVIDER env var.

Supported values:
  mock   → MockBankClient  (default; no external deps)
  glyde  → GlydeClient
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def get_payment_provider():
    """Return a fully-initialised provider client for the current environment."""
    settings = get_settings()
    provider = getattr(settings, "PAYMENT_PROVIDER", "mock").lower()

    if provider == "glyde":
        if not settings.GLYDE_ENABLED or not settings.GLYDE_SECRET_KEY:
            log.warning("glyde.not_configured — falling back to mock provider")
            provider = "mock"
        else:
            from app.integrations.glyde import GlydeClient
            log.info("provider.glyde")
            return GlydeClient()

    # Default / explicit mock
    from app.providers.mock_bank.client import MockBankClient
    log.info("provider.mock_bank")
    return MockBankClient()
