"""Proof-of-Boundary — what the caller actually receives when a run does not succeed.

Deliberately imports only the agent and its corpus interface: nothing that the output
gate's internals expose. That keeps the module loadable against ANY version of the gate,
which is what makes it usable as a regression probe — a test module that cannot import
against the pre-fix code reports a collection error, and a collection error is not a
failure, so it would silently read as "the old code passed too".
"""

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import ElectricityPlanQAAgent

_DRAFT_MARKER = "DRAFT_CONTENT_THAT_MUST_NOT_SHIP"


class TestEnvelopeContainment:
    """Drive the compiled graph: a non-success run must not surface unattested text."""

    def _agent(self):
        agent = ElectricityPlanQAAgent()
        agent.compile()
        return agent

    def test_gate_refusal_surfaces_the_notice_not_the_draft(self, monkeypatch):
        import src.nodes.plan_rules_retrieve_node as retrieve_module
        from src.services.plan_kb import PlanKnowledgeBase

        drifted = (
            {
                "id": "plan-drift",
                "text": f"あなたの請求額は 12,345 円/月 です。{_DRAFT_MARKER}",
                "source": "corpus",
                "segments": None,
                "min_kw": None,
            },
        )

        class DriftedKB(PlanKnowledgeBase):
            def __init__(self) -> None:
                super().__init__(plan_chunks=drifted, incentive_chunks=())

        def use_drifted_corpus(self, kb=None):
            # Fault injected on the DATA path (a drifted corpus), never on the gate —
            # patching the gate would only test the patch.
            self._kb = DriftedKB()

        monkeypatch.setattr(retrieve_module.PlanRulesRetrieveNode, "__init__", use_drifted_corpus)

        agent = self._agent()
        out = agent.invoke(
            "従量電灯プランを比較して",
            ctx=InvocationContext(session_id="contain", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        )
        blob = json.dumps(out, ensure_ascii=False, default=str)
        assert out["status"] in (AgentStatus.ERROR, AgentStatus.ERROR.value)
        assert _DRAFT_MARKER not in blob, "the un-gated draft must not reach the caller"
        assert "12,345" not in blob
        assert "Traceback" not in blob
        assert out["output"], "a refusal still owes the caller an explanation"

    def test_unattested_answer_is_not_surfaced_on_failure(self, monkeypatch):
        """A post-process step that FAILS leaves the draft in state; it must not ship.

        The slot merges each sub-node's partial as it goes, so a failure after the draft
        was written leaves the draft behind. Nothing has attested it was cleared for
        release, so the envelope must withhold it.
        """

        import src.nodes.response_validate_node as gate_module

        def explode(self, state):
            raise RuntimeError("post-process failure after the draft was written")

        monkeypatch.setattr(gate_module.ResponseValidateNode, "execute", explode)

        agent = self._agent()
        out = agent.invoke(
            "従量電灯と時間帯別プランを比較したい",
            ctx=InvocationContext(session_id="contain-raise", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        )
        assert out["status"] in (AgentStatus.ERROR, AgentStatus.ERROR.value)
        assert out["output"] is None, "answer text no gate released must not reach the caller"
        assert "出典" not in json.dumps(out, ensure_ascii=False, default=str)

    def test_clean_run_still_produces_its_real_answer(self):
        agent = self._agent()
        out = agent.invoke(
            "従量電灯と時間帯別プランを比較したい",
            ctx=InvocationContext(session_id="contain-ok", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL),
        )
        assert out["status"] in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value)
        assert "出典" in out["output"]
        assert (
            "ResponseValidateNode" in out["node_history"]
        ), "the gate must be on the path, or a pass proves nothing about it"
