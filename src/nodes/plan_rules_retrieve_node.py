"""Plan-rule and incentive retrieval (main step 1).

Retrieves over the energy corpus (電気事業法, retail plan terms, incentive rules) and
then applies the caller's validated context as a real filter: contract capacity decides
whether high-voltage plans are applicable at all, and an explicit plan-code selection
narrows the comparison to the plans the caller asked about.

The node depends only on the ``PlanKnowledgeBase`` interface, so the corpus backing it
can change without touching this logic.
"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import to_json
from src.services.caller_context import HIGH_VOLTAGE_MIN_KW
from src.services.plan_kb import PlanKnowledgeBase


class PlanRulesRetrieveNode(FunctionNode):
    """Retrieve candidate plan rules and incentive chunks, filtered by caller context."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, kb: PlanKnowledgeBase | None = None) -> None:
        # The knowledge base is injected so a different corpus can be supplied without
        # changing node logic; it defaults to the bundled one.
        self._kb = kb or PlanKnowledgeBase()

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        query = state.get("validated_input") or state.get("raw_query") or state.get("user_input") or ""
        if not isinstance(query, str) or not query.strip():
            emit_trace_event("plan_retrieval_skipped", {"reason": "no_query"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["PlanRulesRetrieveNode: there is no question to retrieve on"],
            }

        try:
            context: dict[str, Any] = json.loads(state.get("validated_context") or "{}")
        except (TypeError, ValueError):
            context = {}

        customer_type = state.get("customer_type", "residential")
        retailer_id = context.get("retailer")
        contract_kw = context.get("contract_kw")
        plan_codes = context.get("plan_codes")

        plan_rules = self._kb.retrieve_plan_rules(query, customer_type, retailer_id)
        incentives = self._kb.retrieve_incentives(query, customer_type)

        excluded: list[str] = []

        # Contract capacity is a hard applicability rule, not a ranking hint: a plan with
        # a capacity floor above the caller's contract cannot be offered to them at all.
        if contract_kw is not None:
            keep = []
            for chunk in plan_rules:
                floor = chunk.get("min_kw")
                if floor is not None and contract_kw < floor:
                    excluded.append(chunk["id"])
                else:
                    keep.append(chunk)
            plan_rules = keep

        # An explicit plan selection narrows the comparison to what was asked about.
        if plan_codes:
            wanted = set(plan_codes)
            plan_rules = [c for c in plan_rules if c["id"].replace("-", "_") in wanted]

        scores = [c["score"] for c in plan_rules] + [c["score"] for c in incentives]

        emit_trace_event(
            "plan_retrieval_complete",
            {
                "plan_chunks": len(plan_rules),
                "incentive_chunks": len(incentives),
                "excluded_by_capacity": len(excluded),
                "capacity_filter_applied": contract_kw is not None,
                "plan_code_filter_applied": bool(plan_codes),
            },
            state,
        )

        return {
            "status": AgentStatus.SUCCESS,
            "retrieved_plan_rules": to_json(plan_rules),
            "retrieved_incentives": to_json(incentives),
            "retrieval_scores": to_json(scores),
            "excluded_plan_ids": to_json(excluded),
            # A capacity floor that excluded everything is a real, explainable answer
            # ("no plan matches this contract"), so it must not be confused with the
            # "we retrieved nothing" degradation path.
            "high_voltage_available": bool(contract_kw is not None and contract_kw >= HIGH_VOLTAGE_MIN_KW),
        }
