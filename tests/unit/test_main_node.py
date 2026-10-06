"""Unit tests for the ``main`` slot orchestrator.

The ``main`` slot is the inline orchestrator ``MainSlotNode``, which runs
PlanRulesRetrieve followed by IncentiveProgramCheck and merges their partial results.
"""

import inspect
import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import MainSlotNode

# The slot calls each sub-node through __call__, which runs the trust gate. In
# production InitializeNode seeds caller_trust_level from the InvocationContext; driving
# a slot directly means seeding it the same way, or every sub-node is denied.
_VERIFIED = {"caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value}


class TestMainSlotNode:
    """Unit tests for the main slot business-logic orchestrator."""

    def setup_method(self):
        self.node = MainSlotNode()

    def test_success_path_retrieves_and_scores_eligibility(self):
        """Main slot runs retrieval then eligibility, merging partial dicts."""
        state = {
            "validated_input": "従量電灯と時間帯別プランを比較したい",
            "customer_type": "residential",
            "node_history": [],
            "error_log": [],
            **_VERIFIED,
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS
        assert json.loads(result["retrieved_plan_rules"])
        assert "eligibility" in result
        assert set(json.loads(result["eligibility"])).issuperset({"sessuiden_point", "re100", "j_credit"})

    def test_corporate_path_eligibility(self):
        state = {
            "validated_input": "高圧電力プランとJ-クレジットについて",
            "customer_type": "corporate",
            "node_history": [],
            "error_log": [],
            **_VERIFIED,
        }
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.SUCCESS
        assert json.loads(result["eligibility"])["j_credit"] is True

    def test_empty_query_short_circuits_error(self):
        """A retrieval error short-circuits before eligibility runs."""
        state = {
            "validated_input": "   ",
            "customer_type": "residential",
            "node_history": [],
            "error_log": [],
            **_VERIFIED,
        }
        result = self.node.execute(state)
        assert result["status"] in (AgentStatus.ERROR, AgentStatus.ERROR.value)

    def test_execute_method_signature(self):
        """The node contract is execute(state) — no alternative entry point."""
        assert hasattr(MainSlotNode, "execute"), "MainSlotNode must implement execute()"
        sig = inspect.signature(MainSlotNode.execute)
        params = list(sig.parameters.keys())
        assert len(params) >= 2, f"execute() must accept (self, state), got: {params}"
        assert params[1] == "state", f"Second parameter must be 'state', got '{params[1]}'"
        assert "_invoke_impl" not in MainSlotNode.__dict__, "_invoke_impl() must not be defined — use execute() instead"
