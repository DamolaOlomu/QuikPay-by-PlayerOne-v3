# app/db/base.py
"""
app/db/base.py
Declarative base — import this in every model so Alembic can discover them.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass