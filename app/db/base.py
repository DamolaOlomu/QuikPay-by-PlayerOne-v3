"""
app/db/base.py
Declarative base — import this in every model so Alembic can discover them.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models here so Alembic autogenerate picks them up
from app.models import user, kyc, transaction, payment_link, agent, atm  # noqa: F401, E402
from app.models import api_key, request_log, support_ticket  # noqa: F401, E402
from app.providers.mock_bank import models as mock_bank_models  # noqa: F401, E402