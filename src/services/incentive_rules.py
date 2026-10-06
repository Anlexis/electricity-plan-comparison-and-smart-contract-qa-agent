"""AgentCore Platform v1.0 — ENE-C2-014 incentive eligibility rule functions.

Reusable rule functions for 節電ポイント / RE100 / J-クレジット eligibility. Kept out of the
node class body so they can be unit-tested and reused independently by other energy-domain
agents. Pure functions: (customer_type, retrieved incentive chunks) → bool / dict, with no
side effects.

Eligibility paths differ by customer_type:
  - residential : 節電ポイント yes; RE100 no (corporate initiative); J-Credit limited
  - sme         : 節電ポイント yes; RE100 no; J-Credit yes (省エネ設備)
  - corporate   : all three available
  - re100       : all three; RE100 is the primary path
"""

from __future__ import annotations

from typing import Any

VALID_CUSTOMER_TYPES = ("residential", "sme", "corporate", "re100")


def _has_program(incentive_chunks: "list[dict[str, Any]]", program: str) -> bool:
    """True when the KB retrieved a chunk for the given program (evidence present)."""
    return any(c.get("program") == program for c in (incentive_chunks or []))


def sessuiden_point_eligible(customer_type: str, incentive_chunks: "list[dict[str, Any]]") -> bool:
    """節電ポイント: open to households and businesses that registered for the program."""
    if customer_type not in VALID_CUSTOMER_TYPES:
        return False
    # Demand-response point program is broadly available; require KB evidence.
    return _has_program(incentive_chunks, "sessuiden_point")


def re100_eligible(customer_type: str, incentive_chunks: "list[dict[str, Any]]") -> bool:
    """RE100: corporate-only initiative (membership is per legal entity)."""
    if customer_type not in ("corporate", "re100"):
        return False
    return _has_program(incentive_chunks, "re100")


def j_credit_eligible(customer_type: str, incentive_chunks: "list[dict[str, Any]]") -> bool:
    """J-Credit: for entities able to certify GHG reductions (省エネ設備 / 再エネ).

    Residential consumers generally cannot register J-Credit projects individually.
    """
    if customer_type == "residential":
        return False
    if customer_type not in VALID_CUSTOMER_TYPES:
        return False
    return _has_program(incentive_chunks, "j_credit")


def evaluate_eligibility(customer_type: str, incentive_chunks: "list[dict[str, Any]]") -> "dict[str, Any]":
    """Aggregate the three rule functions into the ``eligibility`` state value.

    Shape: ``{"sessuiden_point": bool, "re100": bool, "j_credit": bool, "details": {...}}``
    """
    sessuiden = sessuiden_point_eligible(customer_type, incentive_chunks)
    re100 = re100_eligible(customer_type, incentive_chunks)
    j_credit = j_credit_eligible(customer_type, incentive_chunks)

    details: dict[str, str] = {}
    if not re100 and customer_type in ("residential", "sme"):
        details["re100"] = "RE100は法人単位のイニシアチブのため、当該顧客区分は対象外です。"
    if not j_credit and customer_type == "residential":
        details["j_credit"] = "J-クレジットは個人の家庭単位での登録は一般に困難です。"

    return {
        "sessuiden_point": sessuiden,
        "re100": re100,
        "j_credit": j_credit,
        "details": details,
    }
