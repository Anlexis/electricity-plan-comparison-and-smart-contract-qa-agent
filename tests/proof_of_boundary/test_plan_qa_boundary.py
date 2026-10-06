"""Proof-of-Boundary — the two guardrails that define THIS agent.

Complements the generic boundary tests (import isolation, state safety) with:

  - a malformed or injected retailer selector is refused during pre-processing, before
    any retrieval runs, so the injection never reaches the corpus;
  - an individualized billing or rate projection is refused at the output gate, and the
    draft content is never echoed back to the caller.

These drive the real slot orchestrators the graph uses, so they prove the boundary holds
through the whole node chain rather than for one node in isolation.
"""

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import (
    PreProcessSlotNode,
    PostProcessSlotNode,
    ElectricityPlanQAAgent,
)


def _err(result: dict) -> bool:
    return result.get("status") in (AgentStatus.ERROR, AgentStatus.ERROR.value)


# QueryNormalizeNode requires VERIFIED_EXTERNAL trust. The framework seeds
# caller_trust_level from the InvocationContext at initialization; when driving a
# slot directly we seed it the same way so the trust gate admits the call.
_VERIFIED = {"caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value}


class TestInjectionBoundary:
    def test_injected_retailer_refused_before_retrieval(self):
        # The enum guard fires during pre-processing; main must never see the injection.
        pre = PreProcessSlotNode().execute(
            {"user_input": "プラン比較したい", "input_context": {"retailer": "'; DROP TABLE plans; --"}, **_VERIFIED}
        )
        assert _err(pre), "an injected retailer selector must be refused"
        # customer_type classification must not have run after the refusal.
        assert "customer_type" not in pre

    def test_valid_retailer_passes_pre_process(self):
        pre = PreProcessSlotNode().execute(
            {"user_input": "従量電灯プランを比較", "input_context": {"retailer": "newpower_energy"}, **_VERIFIED}
        )
        assert pre["status"] == AgentStatus.SUCCESS
        assert pre["customer_type"] in ("residential", "sme", "corporate", "re100")

    def test_empty_input_refused(self):
        pre = PreProcessSlotNode().execute({"user_input": "   ", **_VERIFIED})
        assert _err(pre)

    def test_anonymous_caller_denied_by_trust_gate(self):
        # Trust gate: QueryNormalize requires VERIFIED_EXTERNAL; an ANONYMOUS
        # caller (no caller_trust_level seeded) is refused before normalization.
        pre = PreProcessSlotNode().execute(
            {"user_input": "プラン比較", "input_context": {"retailer": "newpower_energy"}}
        )
        assert _err(pre)


class TestOutputGateBoundary:
    def test_individualized_projection_refused_through_the_slot(self):
        # The projection is injected on the DATA path — a retrieved chunk carrying it —
        # so ComparisonGenerate renders it and the gate is what must refuse. Injecting it
        # straight into the draft would be overwritten by synthesis and would prove
        # nothing about the slot.
        post = PostProcessSlotNode().execute(
            {
                "retrieved_plan_rules": [
                    {
                        "id": "p1",
                        "text": "従量電灯B。あなたの請求額は今月4,500円になります。",
                        "source": "約款",
                        "citation": "約款",
                    }
                ],
                "retrieved_incentives": [],
                "eligibility": {"sessuiden_point": True, "re100": False, "j_credit": False, "details": {}},
                **_VERIFIED,
            }
        )
        assert _err(post), "an individualized billing projection must be refused"
        assert post["blocked"] is True
        assert "4,500円" not in post["answer"], "draft content must not be echoed back"
        assert post["answer"], "a refusal still owes the caller an explanation"

    def test_clean_aggregate_comparison_passes(self):
        post = PostProcessSlotNode().execute(
            {
                "retrieved_plan_rules": [
                    {"id": "p1", "text": "従量電灯B: 基本料金 + 従量料金", "source": "約款", "citation": "約款"}
                ],
                "retrieved_incentives": [],
                "eligibility": {"sessuiden_point": True, "re100": False, "j_credit": False, "details": {}},
                **_VERIFIED,
            }
        )
        assert post["status"] == AgentStatus.SUCCESS
        assert post["blocked"] is False
        assert "出典" in post["answer"]


class TestAgentComposition:
    def test_agent_inherits_l1_agent_base_graph(self):
        # PB / S-0: agent must inherit the L1 base (not a standalone class).
        from framework.graph.agent_base_graph import AgentBaseGraph

        assert issubclass(ElectricityPlanQAAgent, AgentBaseGraph)

    def test_agent_registers_three_domain_slots(self):
        agent = ElectricityPlanQAAgent()
        agent.register_nodes()
        for slot in ("pre_process", "main", "post_process"):
            assert agent._nodes[slot] is not None, f"slot {slot} must be wired"


class TestEndToEndBoundary:
    """Drive the compiled L1 graph so refusals are proven through route() → finalize."""

    def _agent(self):
        from framework.schemas.invocation_context import InvocationContext

        agent = ElectricityPlanQAAgent()
        agent.compile()
        return agent, InvocationContext

    def test_happy_path_returns_cited_comparison(self):
        agent, InvocationContext = self._agent()
        ctx = InvocationContext(session_id="pb-ok", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = agent.invoke(
            "従量電灯と時間帯別プランを比較したい",
            ctx=ctx,
            input_context={"retailer": "newpower_energy", "customer_type": "residential"},
        )
        assert out["status"] == AgentStatus.SUCCESS
        assert out["blocked"] is False
        assert "出典" in (out["output"] or ""), "output must carry at least one citation"

    def test_injection_refused_through_routing(self):
        # The refusal raised during pre-processing must survive the fixed backbone
        # (main and post are skipped) and reach finalization as an error status —
        # never as a successful comparison.
        agent, InvocationContext = self._agent()
        ctx = InvocationContext(session_id="pb-inj", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        out = agent.invoke(
            "プラン比較",
            ctx=ctx,
            input_context={"retailer": "'; DROP TABLE plans; --"},
        )
        assert out["status"] in (AgentStatus.ERROR, AgentStatus.ERROR.value)
        # IncentiveProgramCheck must not have run after the refusal.
        assert "IncentiveProgramCheckNode" not in out["node_history"]
