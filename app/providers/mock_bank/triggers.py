"""
app/providers/mock_bank/triggers.py
Deterministic outcome rules for the mock bank.

Use these in tests / dev to force specific results without randomness:

  Amount triggers (transfer & collection):
    *.00  (e.g. 100.00)  → always SUCCESS
    *.01                 → always FAILED  (generic failure)
    *.02                 → always PENDING (stays pending forever — test timeouts)
    *.03                 → SUCCESS after simulated delay (1 s in tests)

  Account-number prefix triggers (account enquiry):
    "0000……"  → NOT_FOUND
    "1111……"  → account name returned normally
    anything  → resolved normally (first 3 digits = bank code lookup)

  Reference prefix triggers:
    "FAIL_"  → always FAILED regardless of amount
    "PEND_"  → always PENDING
    "DUP_"   → IdempotencyConflictError simulation

All other amounts/refs behave randomly with a 90% success rate.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional


class SimulatedOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"


def outcome_for_transfer(
    amount: Decimal,
    reference: str,
) -> SimulatedOutcome:
    """Determine the simulated outcome for an outbound transfer."""
    # Reference prefix takes priority
    ref_upper = reference.upper()
    if ref_upper.startswith("FAIL_"):
        return SimulatedOutcome.FAILED
    if ref_upper.startswith("PEND_"):
        return SimulatedOutcome.PENDING

    # Amount-based magic
    kobo = int((amount * 100).quantize(Decimal("1"))) % 100
    if kobo == 0:
        return SimulatedOutcome.SUCCESS
    if kobo == 1:
        return SimulatedOutcome.FAILED
    if kobo == 2:
        return SimulatedOutcome.PENDING

    # Default: 90% success
    import secrets
    return SimulatedOutcome.SUCCESS if secrets.randbelow(10) < 9 else SimulatedOutcome.FAILED


def outcome_for_collection(
    amount: Decimal,
    reference: str,
) -> SimulatedOutcome:
    """Determine the simulated outcome for an inbound collection."""
    return outcome_for_transfer(amount, reference)


def failure_reason_for(outcome: SimulatedOutcome, reference: str) -> Optional[str]:
    if outcome != SimulatedOutcome.FAILED:
        return None
    if "INSUF" in reference.upper():
        return "Insufficient funds in source account"
    if "INVALID" in reference.upper():
        return "Invalid destination account"
    return "Transaction declined by mock bank"
