# app/db/base_models.py
"""
app/db/base_models.py
Imports every model so they register on Base.metadata.

Import this ONLY from alembic/env.py (for autogenerate). Do NOT import it
from app/db/base.py itself — that causes a circular import when a model
(e.g. api_key.py) is loaded directly by application code (dependencies.py)
before this file's own imports finish, since app.db.base would re-trigger
loading the same still-incomplete module.
"""
from app.models import user, kyc, transaction, payment_link, agent, atm  # noqa: F401
from app.models import api_key, request_log, support_ticket  # noqa: F401
from app.providers.mock_bank import models as mock_bank_models  # noqa: F401