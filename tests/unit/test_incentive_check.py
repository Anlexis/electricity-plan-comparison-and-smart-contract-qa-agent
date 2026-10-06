"""Unit tests for ENE-C2-014 IncentiveProgramCheck node + rule functions (impl #5)."""

import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.incentive_program_check_node import IncentiveProgramCheckNode
from src.services import incentive_rules

# Full incentive chunk set (as PlanRulesRetrieve would supply).
_ALL_INCENTIVES = [
    {"program": "sessuiden_point", "text": "節電ポイント", "source": "経産省"},
    {"program": "re100", "text": "RE100", "source": "RE100技術要件"},
    {"program": "j_credit", "text": "J-クレジット", "source": "J-クレジット制度"},
]


def _check(customer_type, incentives=None):
    return IncentiveProgramCheckNode().execute(
        {
            "customer_type": customer_type,
            "retrieved_incentives": incentives if incentives is not None else _ALL_INCENTIVES,
        }
    )


class TestIncentiveProgramCheck:
    def test_residential_path(self):
        result = _check("residential")
        assert result["status"] == AgentStatus.SUCCESS
        elig = json.loads(result["eligibility"])
        assert elig["sessuiden_point"] is True
        assert elig["re100"] is False  # corporate-only initiative
        assert elig["j_credit"] is False  # individuals cannot register
        assert "re100" in elig["details"]

    def test_energy_manager_corporate_path(self):
        # "energy_manager" maps to the corporate segment in this template.
        result = _check("corporate")
        elig = json.loads(result["eligibility"])
        assert elig["sessuiden_point"] is True
        assert elig["re100"] is True
        assert elig["j_credit"] is True

    def test_re100_corporate_path(self):
        result = _check("re100")
        elig = json.loads(result["eligibility"])
        assert elig["re100"] is True
        assert elig["j_credit"] is True

    def test_sme_path_no_re100_but_j_credit(self):
        result = _check("sme")
        elig = json.loads(result["eligibility"])
        assert elig["re100"] is False
        assert elig["j_credit"] is True

    def test_no_kb_evidence_means_not_eligible(self):
        # Eligibility requires retrieved evidence — empty incentives → all False.
        result = _check("corporate", incentives=[])
        elig = json.loads(result["eligibility"])
        assert elig["sessuiden_point"] is False
        assert elig["re100"] is False
        assert elig["j_credit"] is False

    def test_invalid_customer_type_errors(self):
        result = _check("bogus")
        assert result["status"] == AgentStatus.ERROR
        assert result["error_log"]


class TestIncentiveRuleFunctions:
    """The rule functions are reusable energy-domain assets — test them directly."""

    def test_sessuiden_requires_evidence(self):
        assert incentive_rules.sessuiden_point_eligible("residential", _ALL_INCENTIVES) is True
        assert incentive_rules.sessuiden_point_eligible("residential", []) is False

    def test_re100_corporate_only(self):
        assert incentive_rules.re100_eligible("corporate", _ALL_INCENTIVES) is True
        assert incentive_rules.re100_eligible("residential", _ALL_INCENTIVES) is False
        assert incentive_rules.re100_eligible("sme", _ALL_INCENTIVES) is False

    def test_j_credit_excludes_residential(self):
        assert incentive_rules.j_credit_eligible("residential", _ALL_INCENTIVES) is False
        assert incentive_rules.j_credit_eligible("sme", _ALL_INCENTIVES) is True

    def test_evaluate_eligibility_shape(self):
        out = incentive_rules.evaluate_eligibility("corporate", _ALL_INCENTIVES)
        assert set(out.keys()) == {"sessuiden_point", "re100", "j_credit", "details"}
        assert isinstance(out["details"], dict)
