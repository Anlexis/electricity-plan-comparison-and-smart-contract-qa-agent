"""Unit tests for the PlanRulesRetrieve node and the bundled corpus."""

import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.plan_rules_retrieve_node import PlanRulesRetrieveNode
from src.services.plan_kb import PlanKnowledgeBase


class TestPlanRulesRetrieve:
    def test_residential_retrieval_returns_cited_chunks(self):
        result = PlanRulesRetrieveNode().execute(
            {"validated_input": "従量電灯プランを比較したい", "customer_type": "residential"}
        )
        assert result["status"] == AgentStatus.SUCCESS
        plan_rules = json.loads(result["retrieved_plan_rules"])
        assert plan_rules, "expected at least one plan-rule chunk"
        # Every chunk must carry a citation/source for downstream attribution.
        for chunk in plan_rules:
            assert chunk["citation"]
            assert chunk["source"]
            assert "score" in chunk
        assert json.loads(result["retrieval_scores"])

    def test_customer_type_filters_corporate_only_plan(self):
        # 高圧電力 plan is corporate/sme only — must NOT surface for a residential customer.
        residential = PlanRulesRetrieveNode().execute({"validated_input": "プラン", "customer_type": "residential"})
        res_ids = {c["id"] for c in json.loads(residential["retrieved_plan_rules"])}
        assert "plan-kouatsu-business" not in res_ids

        corporate = PlanRulesRetrieveNode().execute({"validated_input": "高圧電力プラン", "customer_type": "corporate"})
        corp_ids = {c["id"] for c in json.loads(corporate["retrieved_plan_rules"])}
        assert "plan-kouatsu-business" in corp_ids

    def test_re100_customer_gets_green_plan_and_incentives(self):
        result = PlanRulesRetrieveNode().execute({"validated_input": "RE100対応プラン", "customer_type": "re100"})
        plan_ids = {c["id"] for c in json.loads(result["retrieved_plan_rules"])}
        incentive_programs = {c["program"] for c in json.loads(result["retrieved_incentives"])}
        assert "plan-green-re100" in plan_ids
        assert "re100" in incentive_programs

    def test_retailer_id_threaded_into_chunks(self):
        result = PlanRulesRetrieveNode().execute(
            {
                "validated_input": "従量電灯",
                "customer_type": "residential",
                "validated_context": json.dumps({"retailer": "newpower_energy"}),
            }
        )
        assert all(c["retailer_id"] == "newpower_energy" for c in json.loads(result["retrieved_plan_rules"]))

    def test_empty_query_errors(self):
        result = PlanRulesRetrieveNode().execute({"validated_input": "   ", "customer_type": "residential"})
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"]

    def test_no_retrieval_yields_empty_candidate_sets(self):
        # An empty corpus yields no candidates. Retrieval reports that plainly; deciding
        # what the caller is told about it belongs to the synthesis step, so this node
        # does not pre-empt that decision by flagging the run blocked.
        empty_kb = PlanKnowledgeBase(plan_chunks=(), incentive_chunks=())
        result = PlanRulesRetrieveNode(kb=empty_kb).execute(
            {"validated_input": "anything", "customer_type": "residential"}
        )
        assert result["status"] == AgentStatus.SUCCESS
        assert json.loads(result["retrieved_plan_rules"]) == []
        assert json.loads(result["retrieved_incentives"]) == []

    def test_kb_interface_is_abstracted(self):
        # PB: node depends on the PlanKnowledgeBase interface, swappable for Phase 2.
        custom = PlanKnowledgeBase(
            plan_chunks=(
                {"id": "x1", "text": "custom plan", "source": "custom-src", "segments": None, "min_kw": None},
            ),
            incentive_chunks=(),
        )
        result = PlanRulesRetrieveNode(kb=custom).execute({"validated_input": "plan", "customer_type": "residential"})
        assert [c["id"] for c in json.loads(result["retrieved_plan_rules"])] == ["x1"]
