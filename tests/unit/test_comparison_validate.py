"""Unit tests for the ComparisonGenerate and ResponseValidate nodes."""

from framework.schemas.agent_status import AgentStatus

from src.nodes.comparison_generate_node import ComparisonGenerateNode
from src.nodes.response_validate_node import ResponseValidateNode

_PLAN_RULES = [
    {"id": "p1", "text": "従量電灯B: 基本料金 + 従量料金", "source": "従量電灯約款", "citation": "従量電灯約款"},
    {"id": "p2", "text": "時間帯別電灯: 夜間が割安", "source": "時間帯別約款", "citation": "時間帯別約款"},
]
_INCENTIVES = [
    {"id": "i1", "text": "節電ポイント制度", "source": "経産省", "citation": "経産省", "program": "sessuiden_point"},
]
_ELIGIBILITY = {"sessuiden_point": True, "re100": False, "j_credit": False, "details": {}}


class TestComparisonGenerate:
    def test_builds_cited_comparison(self):
        result = ComparisonGenerateNode().execute(
            {
                "retrieved_plan_rules": _PLAN_RULES,
                "retrieved_incentives": _INCENTIVES,
                "eligibility": _ELIGIBILITY,
            }
        )
        assert result["status"] == AgentStatus.SUCCESS
        answer = result["answer"]
        # Citation presence: at least one source label must appear in the output.
        assert "出典" in answer
        assert "従量電灯約款" in answer
        assert "節電ポイント" in answer

    def test_empty_retrieval_blocks(self):
        result = ComparisonGenerateNode().execute(
            {"retrieved_plan_rules": [], "retrieved_incentives": [], "eligibility": {}}
        )
        assert result["answer"] == ""
        assert result["blocked"] is True

    def test_eligible_programs_listed(self):
        result = ComparisonGenerateNode().execute(
            {
                "retrieved_plan_rules": _PLAN_RULES,
                "retrieved_incentives": _INCENTIVES,
                "eligibility": {"sessuiden_point": True, "re100": True, "j_credit": False, "details": {}},
            }
        )
        assert "RE100" in result["answer"]


class TestResponseValidate:
    def test_clean_answer_passes_through(self):
        clean = "【電気料金プラン比較】\n- 従量電灯B(出典: 従量電灯約款)\n"
        result = ResponseValidateNode().execute({"answer": clean})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["blocked"] is False
        assert result["answer"] == clean.strip()

    def test_empty_draft_refused(self):
        result = ResponseValidateNode().execute({"answer": "   "})
        assert result["status"] == AgentStatus.ERROR
        assert result["blocked"] is True
        assert result["error_log"]

    def test_individualized_projection_refused(self):
        # An individualized billing or rate projection must be blocked.
        bad = "あなたの請求額は今月3,200円になります。"
        result = ResponseValidateNode().execute({"answer": bad})
        assert result["status"] == AgentStatus.ERROR
        assert result["blocked"] is True
        # Draft content must NOT be echoed back — only the safe fallback.
        assert "3,200円" not in result["answer"]

    def test_english_projection_refused(self):
        bad = "Your estimated monthly cost is high under this plan."
        result = ResponseValidateNode().execute({"answer": bad})
        assert result["status"] == AgentStatus.ERROR
        assert result["blocked"] is True

    def test_secret_leak_refused(self):
        bad = "プラン比較です。 api_key=sk-secret1234567890abcdef"
        result = ResponseValidateNode().execute({"answer": bad})
        assert result["status"] == AgentStatus.ERROR
        assert result["blocked"] is True
        assert "sk-secret" not in result["answer"]

    def test_jwt_leak_refused(self):
        bad = "token: eyJhbGciOiJIUzI1Ni19.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dummysignature"
        result = ResponseValidateNode().execute({"answer": bad})
        assert result["status"] == AgentStatus.ERROR
        assert result["blocked"] is True
