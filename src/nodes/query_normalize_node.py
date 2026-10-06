"""Query normalization and caller-context validation (pre_process step 1)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.caller_context import ContextError, to_json, validate

# Coarse intent tags (rule-based routing label; retrieval still runs on the full query).
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "incentive_eligibility": (
        "節電ポイント",
        "re100",
        "j-credit",
        "jクレジット",
        "incentive",
        "eligib",
        "補助",
        "適格",
    ),
    "enrollment": ("enroll", "申し込", "申込", "切り替え", "切替", "switch", "契約", "加入"),
    "plan_comparison": ("compare", "比較", "プラン", "plan", "rate", "料金", "tariff", "従量", "時間帯"),
}

# A question longer than this is refused rather than truncated: truncation would answer
# a different question than the one asked, silently.
MAX_QUERY_CHARS = 4000


class QueryNormalizeNode(FunctionNode):
    """Validate the caller's question and context, then tag a coarse intent.

    Refuses empty input, over-long input, and any caller context that fails the
    contract in ``src/services/caller_context.py``. Refusal messages name the field
    that failed and never repeat the value that failed — an error log is an output
    channel, and echoing a rejected value there hands the caller a way to place
    arbitrary text into it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        user_input = state.get("user_input", "")
        if not isinstance(user_input, str) or not user_input.strip():
            emit_trace_event("plan_query_rejected", {"reason": "empty_input"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["QueryNormalizeNode: the question is empty"],
            }
        if len(user_input) > MAX_QUERY_CHARS:
            emit_trace_event("plan_query_rejected", {"reason": "input_too_long"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QueryNormalizeNode: the question exceeds {MAX_QUERY_CHARS} characters"],
            }

        try:
            validated_context = validate(state.get("input_context"))
        except ContextError as exc:
            # str(exc) is built by caller_context and names fields only.
            emit_trace_event("plan_context_rejected", {"reason": "contract_violation"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": [f"QueryNormalizeNode: {exc}"],
            }

        raw_query = user_input.strip()
        lowered = raw_query.lower()
        query_intent = "general"
        for intent, keywords in _INTENT_KEYWORDS.items():
            if any(k in lowered for k in keywords):
                query_intent = intent
                break

        emit_trace_event(
            "plan_query_normalized",
            {
                "intent": query_intent,
                "query_chars": len(raw_query),
                # Field NAMES only — never the values the caller supplied.
                "context_fields": sorted(validated_context),
            },
            state,
        )
        return {
            "status": AgentStatus.SUCCESS,
            "raw_query": raw_query,
            "validated_input": raw_query,
            "query_intent": query_intent,
            "validated_context": to_json(validated_context),
        }
