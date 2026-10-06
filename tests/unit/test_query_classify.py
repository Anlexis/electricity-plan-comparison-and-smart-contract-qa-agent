"""Unit tests for ENE-C2-014 pre_process nodes (impl #3): QueryNormalize + CustomerTypeClassify."""

import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.customer_type_classify_node import CustomerTypeClassifyNode
from src.nodes.query_normalize_node import QueryNormalizeNode


def _ctx(**fields) -> dict:
    """Build the state key that carries ALREADY-VALIDATED caller context.

    Downstream nodes never read the raw request mapping — the contract is decided once,
    in QueryNormalize — so tests for those nodes supply the validated form.
    """
    return {"validated_context": json.dumps(fields)}


class TestQueryNormalize:
    def test_empty_input_errors(self):
        result = QueryNormalizeNode().execute({"user_input": "   "})
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"]

    def test_valid_input_normalizes_and_tags_general(self):
        result = QueryNormalizeNode().execute({"user_input": "  こんにちは  "})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["raw_query"] == "こんにちは"
        assert result["query_intent"] == "general"

    def test_incentive_intent_tagged(self):
        result = QueryNormalizeNode().execute({"user_input": "節電ポイントの適格条件は？"})
        assert result["query_intent"] == "incentive_eligibility"

    def test_plan_comparison_intent_tagged(self):
        result = QueryNormalizeNode().execute({"user_input": "従量電灯と時間帯別プランを比較したい"})
        assert result["query_intent"] == "plan_comparison"

    def test_unknown_retailer_rejected_s1(self):
        # SC-01: malformed/unknown retailer selector is refused before any retrieval.
        result = QueryNormalizeNode().execute(
            {"user_input": "プラン比較", "input_context": {"retailer": "'; DROP TABLE"}}
        )
        assert result["status"] == AgentStatus.ERROR

    def test_known_retailer_accepted(self):
        result = QueryNormalizeNode().execute(
            {"user_input": "プラン比較", "input_context": {"retailer": "newpower_energy"}}
        )
        assert result["status"] == AgentStatus.SUCCESS


class TestCustomerTypeClassify:
    def test_defaults_to_residential(self):
        result = CustomerTypeClassifyNode().execute({"validated_input": "電気料金プランについて教えて"})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["customer_type"] == "residential"

    def test_re100_signal(self):
        # BL-02
        result = CustomerTypeClassifyNode().execute({"validated_input": "RE100対応のグリーン電力プランは？"})
        assert result["customer_type"] == "re100"

    def test_corporate_signal(self):
        result = CustomerTypeClassifyNode().execute({"validated_input": "工場の高圧電力プランを比較したい"})
        assert result["customer_type"] == "corporate"

    def test_explicit_customer_type_wins(self):
        result = CustomerTypeClassifyNode().execute({"validated_input": "anything", **_ctx(customer_type="sme")})
        assert result["customer_type"] == "sme"

    def test_invalid_customer_type_refused_at_the_contract_boundary(self):
        # The enum check belongs to the one node that owns the caller contract, so an
        # invalid segment never reaches classification at all.
        result = QueryNormalizeNode().execute({"user_input": "プラン比較", "input_context": {"customer_type": "bogus"}})
        assert result["status"] == AgentStatus.ERROR
        assert "customer_type" in result["error_log"][0]
        assert "bogus" not in result["error_log"][0], "a rejected value must not be echoed back"

    def test_high_contract_capacity_reclassifies_from_residential(self):
        # A high-voltage-scale contract outweighs household wording.
        result = CustomerTypeClassifyNode().execute(
            {"validated_input": "電気料金プランについて教えて", **_ctx(contract_kw=120.0)}
        )
        assert result["customer_type"] == "corporate"
