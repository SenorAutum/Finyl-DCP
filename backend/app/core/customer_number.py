"""
Customer numbers — the short reference a borrower types as the M-Pesa paybill
"Account Number" (WP2).

Format: 7 digits = 6 random digits (100000-999999) + 1 Luhn check digit.

  * Unique per DCP (tenant), not platform-wide: every DCP has its own paybill,
    so the paybill shortcode already tells us which DCP the money belongs to.
  * Random rather than sequential, so the next customer's number cannot be
    guessed from your own and no shared counter is needed under concurrency.
  * The Luhn check digit rejects any single mistyped digit and most swapped
    adjacent digits, so a typo lands in the review queue instead of silently
    crediting another customer.

One number per borrower, used for every loan they ever take, so it never
changes between loans and can be printed on cards / SMS once.
"""
from __future__ import annotations

import re
import secrets

CUSTOMER_NUMBER_LENGTH = 7
_PAYLOAD_MIN = 100000
_PAYLOAD_SPAN = 900000  # 100000..999999
_MAX_ATTEMPTS = 50

_NON_DIGITS = re.compile(r"\D")


def luhn_check_digit(payload: str) -> int:
    """Luhn check digit for a string of digits (the digit to append)."""
    total = 0
    double = True  # the rightmost payload digit is doubled (check digit follows it)
    for ch in reversed(payload):
        d = int(ch)
        if double:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        double = not double
    return (10 - total % 10) % 10


def is_valid(value) -> bool:
    """True when `value` is a well-formed customer number (7 digits, Luhn-valid)."""
    s = normalise(value)
    if s is None or len(s) != CUSTOMER_NUMBER_LENGTH:
        return False
    return luhn_check_digit(s[:-1]) == int(s[-1])


def normalise(value) -> str | None:
    """Strip spaces/dashes etc. Returns the digit string, or None when the input
    contains letters (then it cannot be a customer number)."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace("-", "")
    if not s or _NON_DIGITS.search(s):
        return None
    return s


def generate_candidate() -> str:
    payload = str(_PAYLOAD_MIN + secrets.randbelow(_PAYLOAD_SPAN))
    return payload + str(luhn_check_digit(payload))


def generate_unique(exists) -> str:
    """Generate a number for which `exists(candidate)` is False.

    `exists` is a callable checking uniqueness inside the borrower's tenant. With
    900,000 possible numbers per DCP a clash is rare; we retry a bounded number
    of times and fail loudly rather than loop forever on a nearly-full space."""
    for _ in range(_MAX_ATTEMPTS):
        cand = generate_candidate()
        if not exists(cand):
            return cand
    raise RuntimeError("Could not allocate a unique customer number for this DCP")
