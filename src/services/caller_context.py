"""Validated caller-supplied context for the electricity-plan Q&A agent.

The agent accepts an optional ``input_context`` mapping alongside the natural-language
question. Everything in it is caller-controlled and therefore hostile until proven
otherwise, so this module is the single place that decides what may enter the pipeline:

* the accepted field set is a closed whitelist — an unknown field is refused, never
  silently ignored, because an ignored field still travels on the request and a silent
  drop gives the caller no signal that their input had no effect;
* every numeric field is parsed by :func:`finite_in_range`, which rejects booleans,
  non-numerics, NaN and +/-Infinity, and out-of-range magnitudes. NaN matters
  specifically: it parses through ``float()`` and arrives intact through raw JSON, and
  every comparison against it evaluates False — so an unchecked NaN silently passes a
  threshold test rather than failing it;
* every string that can reach the rendered answer is locked to an inert identifier
  alphabet, so caller text cannot become output;
* validation errors name the FIELD and never echo the value.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

# Retail electricity suppliers this template can filter plan terms for.
KNOWN_RETAILERS: frozenset[str] = frozenset(
    {
        "newpower_energy",
        "tepco",
        "kepco",
        "chubu",
        "tohoku",
        "kyushu",
        "chugoku",
        "shikoku",
        "hokkaido",
        "hokuriku",
        "okinawa",
    }
)

CUSTOMER_TYPES: tuple[str, ...] = ("residential", "sme", "corporate", "re100")

# Caller strings that can reach the rendered answer are restricted to this alphabet.
# `_` is included deliberately: plan codes are rendered in lower_snake form, and a
# guard that omits the separator character the identifiers actually use is not a guard.
INERT_IDENTIFIER = re.compile(r"^[a-z0-9_]{1,32}$")

# Structural caps — a list field is a size amplifier unless it is bounded.
MAX_PLAN_CODES = 16
# Contract capacity in kW. The upper bound is far above any retail contract; it exists
# to bound the value, not to model the market.
CONTRACT_KW_RANGE = (0.0, 100_000.0)
# Monthly consumption in kWh.
MONTHLY_KWH_RANGE = (0.0, 10_000_000.0)

ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "retailer",
        "customer_type",
        "contract_kw",
        "monthly_kwh",
        "plan_codes",
        # Seeded by the Marketplace runner on every invocation, not caller-supplied.
        # Accepted and ignored: this template has no conversational follow-up behaviour,
        # so the history carries nothing this pipeline consumes -- but rejecting it as
        # "unknown" refused every single invocation, since the runner always sends it.
        "conversation_history",
    }
)

# Contract capacity at or above which high-voltage plans become available.
HIGH_VOLTAGE_MIN_KW = 50.0


class ContextError(ValueError):
    """Raised when caller context fails validation. Message names fields only."""


def finite_in_range(value: Any, lo: float, hi: float) -> float:
    """Parse *value* as a finite float within [lo, hi], or raise.

    Rejects bool (``isinstance(True, int)`` is True in Python, so a bare numeric check
    would accept ``true`` as 1), anything non-numeric, NaN, +/-Infinity, and
    out-of-range magnitudes. Raises :class:`ContextError` with no value echoed.
    """
    if isinstance(value, bool):
        raise ContextError("must be a number")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except (TypeError, ValueError):
            raise ContextError("must be a number") from None
    else:
        raise ContextError("must be a number")
    if not math.isfinite(parsed):
        raise ContextError("must be a finite number")
    if not (lo <= parsed <= hi):
        raise ContextError(f"must be between {lo:g} and {hi:g}")
    return parsed


def _inert(value: Any) -> str:
    if not isinstance(value, str) or not INERT_IDENTIFIER.match(value):
        raise ContextError("must match [a-z0-9_]{1,32}")
    return value


def validate(raw: Any) -> dict[str, Any]:
    """Validate a caller ``input_context`` mapping into the accepted contract.

    Returns the validated mapping (absent fields simply absent — the pipeline then
    degrades to its baseline behaviour rather than inventing a value).

    Raises :class:`ContextError` naming the offending field, never its value.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ContextError("input_context must be a mapping")

    unknown = sorted(set(raw) - ALLOWED_FIELDS)
    if unknown:
        # Field NAMES are caller data too — only echo one that is itself inert.
        shown = [n if isinstance(n, str) and INERT_IDENTIFIER.match(n) else "<masked>" for n in unknown[:5]]
        raise ContextError(f"unknown input_context field(s): {', '.join(shown)}")

    out: dict[str, Any] = {}
    for field, checker in _FIELD_CHECKERS:
        if field not in raw or raw[field] is None:
            continue
        try:
            out[field] = checker(raw[field])
        except ContextError as exc:
            raise ContextError(f"input_context.{field}: {exc}") from None
    return out


def _enum(value: str, allowed: "frozenset[str] | set[str]") -> str:
    if value not in allowed:
        raise ContextError("is not a recognised value")
    return value


def _retailer(value: Any) -> str:
    return _enum(_inert(value), KNOWN_RETAILERS)


def _customer_type(value: Any) -> str:
    return _enum(_inert(value), set(CUSTOMER_TYPES))


def _contract_kw(value: Any) -> float:
    return finite_in_range(value, *CONTRACT_KW_RANGE)


def _monthly_kwh(value: Any) -> float:
    return finite_in_range(value, *MONTHLY_KWH_RANGE)


def _plan_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ContextError("must be a list")
    if len(value) > MAX_PLAN_CODES:
        raise ContextError(f"must contain at most {MAX_PLAN_CODES} entries")
    return [_inert(v) for v in value]


_FIELD_CHECKERS: "tuple[tuple[str, Callable[[Any], Any]], ...]" = (
    ("retailer", _retailer),
    ("customer_type", _customer_type),
    ("contract_kw", _contract_kw),
    ("monthly_kwh", _monthly_kwh),
    ("plan_codes", _plan_codes),
)


def to_json(validated: dict[str, Any]) -> str:
    """Serialize the validated context for msgpack-safe State transport."""
    return json.dumps(validated, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
