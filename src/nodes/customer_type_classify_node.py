"""Customer-segment classification (pre_process step 2)."""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.caller_context import CUSTOMER_TYPES

# Rule-based classification signals, checked most-specific first
# (re100 -> corporate -> sme -> residential).
_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("re100", ("re100", "j-credit", "jクレジット", "再エネ100", "carbon", "脱炭素", "グリーン電力")),
    (
        "corporate",
        ("corporate", "法人", "企業", "工場", "事業所", "高圧", "特別高圧", "energy manager", "エネルギー管理"),
    ),
    ("sme", ("sme", "中小", "小規模", "商店", "店舗", "低圧電力", "業務用")),
)


class CustomerTypeClassifyNode(FunctionNode):
    """Classify the requester into a customer segment.

    A ``customer_type`` supplied in the caller context wins — it has already been
    validated against the enum upstream, so this node consumes it as trusted rather
    than re-deriving the check from raw request data.

    Falls back to keyword classification, and to ``residential`` (the broadest
    consumer segment) when no signal is found. Contract capacity is a stronger
    signal than wording: a high-voltage-scale contract is not a household.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        try:
            context = json.loads(state.get("validated_context") or "{}")
        except (TypeError, ValueError):
            context = {}

        explicit = context.get("customer_type")
        if explicit in CUSTOMER_TYPES:
            emit_trace_event("plan_customer_type_resolved", {"source": "caller_context", "segment": explicit}, state)
            return {"status": AgentStatus.SUCCESS, "customer_type": explicit}

        text = (state.get("validated_input") or state.get("raw_query") or state.get("user_input") or "").lower()
        customer_type = "residential"
        source = "default"
        for segment, signals in _SIGNALS:
            if any(s in text for s in signals):
                customer_type, source = segment, "question_keywords"
                break

        contract_kw = context.get("contract_kw")
        if customer_type == "residential" and isinstance(contract_kw, (int, float)) and contract_kw >= 50.0:
            customer_type, source = "corporate", "contract_capacity"

        emit_trace_event("plan_customer_type_resolved", {"source": source, "segment": customer_type}, state)
        return {"status": AgentStatus.SUCCESS, "customer_type": customer_type}
