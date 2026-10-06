"""Containment tests for the output gate.

Returning an error status is not containment. The graph resolves the caller-visible
output by falling through a chain of candidate fields, and a falsy value ACTIVATES that
fallback rather than suppressing it — so a gate that blanks the answer to an empty string
ships precisely what it meant to withhold. These tests assert the two properties that
actually hold the boundary: the gate clears every carrier of the answer, and the graph
refuses to surface answer text that no gate ever cleared for release.
"""

import json

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials

from src.nodes.response_validate_node import (
    _OUTPUT_BEARING_FIELDS,
    ResponseValidateNode,
    _cleared_payload,
    _violation_kinds,
)

_VERIFIED = {"caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value}
# Assembled at runtime rather than written out: a literal connection string in the tree
# is a finding in its own right, and the probe only needs the SHAPE.
_CONN_STRING = "red" + "is://" + "u" * 4 + ":" + "p" * 6 + "@cache.example:6379/0"
_DRAFT_MARKER = "DRAFT_CONTENT_THAT_MUST_NOT_SHIP"
_VIOLATING_DRAFT = f"あなたの請求額は今月4,500円になります。{_DRAFT_MARKER}"


def _populated_state(draft: str) -> dict:
    """State shaped the way the pipeline actually builds it, with every carrier filled."""
    return {
        "answer": draft,
        "retrieved_plan_rules": json.dumps([{"id": "p1", "text": draft}]),
        "retrieved_incentives": json.dumps([{"id": "i1", "text": draft}]),
        "retrieval_scores": json.dumps([0.9]),
        "eligibility": json.dumps({"sessuiden_point": True, "details": {"note": draft}}),
        "excluded_plan_ids": json.dumps(["plan_x"]),
        **_VERIFIED,
    }


class TestGateClearsEveryCarrier:
    def test_violation_clears_all_output_bearing_fields(self):
        result = ResponseValidateNode().execute(_populated_state(_VIOLATING_DRAFT))
        assert result["status"] == AgentStatus.ERROR
        blob = json.dumps(result, ensure_ascii=False, default=str)
        assert _DRAFT_MARKER not in blob, "the withheld draft must not survive in ANY field"
        for field in _OUTPUT_BEARING_FIELDS:
            assert field in result, f"{field} carries answer content and must be cleared"

    def test_cleared_answer_is_truthy(self):
        # An empty string here would be falsy and would re-activate the very fallback
        # this gate exists to suppress.
        assert _cleared_payload()["answer"]
        result = ResponseValidateNode().execute(_populated_state(_VIOLATING_DRAFT))
        assert result["answer"], "a falsy notice would activate the output fallback"

    def test_inventory_guard_matches_the_cleared_payload(self):
        assert set(_cleared_payload()) == set(_OUTPUT_BEARING_FIELDS)

    def test_violation_message_names_kinds_not_content(self):
        result = ResponseValidateNode().execute(_populated_state(_VIOLATING_DRAFT))
        message = " ".join(result["error_log"])
        assert "individual" in message or "billing" in message or "monetary" in message
        assert _DRAFT_MARKER not in message
        assert "4,500" not in message

    def test_clean_draft_is_released(self):
        # The control: a refuse-everything gate would pass every test above.
        clean = "【電気料金プラン比較】\n- 従量電灯B(出典: 約款)"
        result = ResponseValidateNode().execute({"answer": clean, **_VERIFIED})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["answer"] == clean
        assert result["blocked"] is False


class TestDetectorParity:
    """The gate must never be narrower than the framework's own detector.

    A value the framework recognises and this gate misses does not merely slip through:
    the framework raises after the node returns, and the wrapper discards the node's
    whole delta — including the clearing. A detector gap is therefore a containment
    bypass, so the property is pinned rather than a handful of samples.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "Bearer abcdefghij0123456789",
            "sk_live_" + "abcdefghij0123456789",
            "sk-abcdefghij0123456789abcd",
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "AKIAIOSFODNN7EXAMPLE",
            _CONN_STRING,
        ],
    )
    def test_gate_refuses_everything_the_framework_refuses(self, value):
        draft = f"プラン比較の結果です。{value}"
        assert detect_credentials(draft), "probe must actually trip the framework detector"
        assert _violation_kinds(draft), "the gate must refuse whatever the framework would"
        result = ResponseValidateNode().execute({"answer": draft, **_VERIFIED})
        assert result["status"] == AgentStatus.ERROR
        assert value not in json.dumps(result, ensure_ascii=False, default=str)

    def test_ordinary_domain_text_still_released(self):
        draft = "従量電灯Bとグリーン電力プランの比較(出典: 約款)"
        assert not _violation_kinds(draft)
