"""Incentive eligibility determination (main step 2).

From the customer segment and the retrieved incentive chunks, determine eligibility for
節電ポイント / RE100 / J-クレジット. The rules themselves live in
``src/services/incentive_rules.py`` as pure functions so they can be tested and reused
independently of the node.
"""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json, to_json
from src.services.incentive_rules import VALID_CUSTOMER_TYPES, evaluate_eligibility


class IncentiveProgramCheckNode(FunctionNode):
    """Compute incentive eligibility from the customer segment and retrieved rules."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        customer_type = state.get("customer_type", "residential")
        if customer_type not in VALID_CUSTOMER_TYPES:
            # The segment is derived internally, never taken raw from the request, so
            # this branch means an internal inconsistency rather than bad caller input.
            emit_trace_event("plan_eligibility_skipped", {"reason": "unknown_segment"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["IncentiveProgramCheckNode: the customer segment is not recognised"],
            }

        incentive_chunks = from_json(state.get("retrieved_incentives"), [])
        eligibility = evaluate_eligibility(customer_type, incentive_chunks)

        emit_trace_event(
            "plan_eligibility_evaluated",
            {
                "segment": customer_type,
                "programs_eligible": sorted(p for p in ("sessuiden_point", "re100", "j_credit") if eligibility.get(p)),
            },
            state,
        )
        return {
            "status": AgentStatus.SUCCESS,
            "eligibility": to_json(eligibility),
        }
