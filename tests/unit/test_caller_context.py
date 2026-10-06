"""Contract tests for caller-supplied context.

Every value here arrives from the request, so the tests are written from the position
that the caller is hostile: the interesting cases are the ones that LOOK like numbers,
LOOK like known fields, or slip past a check that only tested the obvious input.
"""

import json
import math

import pytest
from framework.schemas.agent_status import AgentStatus
from framework.security.credential_detector import detect_credentials

from src.nodes.query_normalize_node import QueryNormalizeNode
from src.services.caller_context import (
    CONTRACT_KW_RANGE,
    MAX_PLAN_CODES,
    MONTHLY_KWH_RANGE,
    ContextError,
    finite_in_range,
    validate,
)

# NaN and the infinities are the dangerous ones: they survive float() and arrive intact
# through raw JSON, and every comparison against NaN is False — so an unchecked NaN
# passes a range test rather than failing it, which is fail-OPEN on exactly the check
# the field exists for.
NON_FINITE = ["NaN", "nan", "Infinity", "-Infinity", "inf", float("nan"), float("inf"), float("-inf")]
NUMERIC_FIELDS = ("contract_kw", "monthly_kwh")


class TestFiniteInRange:
    @pytest.mark.parametrize("value", NON_FINITE)
    def test_non_finite_rejected(self, value):
        with pytest.raises(ContextError):
            finite_in_range(value, 0.0, 100.0)

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_rejected(self, value):
        # isinstance(True, int) is True in Python, so a bare numeric check accepts
        # `true` as 1 and silently answers a different question.
        with pytest.raises(ContextError):
            finite_in_range(value, 0.0, 100.0)

    @pytest.mark.parametrize("value", ["", "abc", None, [], {}, "1,000"])
    def test_non_numeric_rejected(self, value):
        with pytest.raises(ContextError):
            finite_in_range(value, 0.0, 100.0)

    @pytest.mark.parametrize("value", [-0.001, 100.001, 1e12])
    def test_out_of_range_rejected(self, value):
        with pytest.raises(ContextError):
            finite_in_range(value, 0.0, 100.0)

    @pytest.mark.parametrize("value", [0, 0.0, "50", 50, 100.0])
    def test_in_range_accepted(self, value):
        assert math.isfinite(finite_in_range(value, 0.0, 100.0))


class TestNumericFieldsMatrix:
    """The finite+bounded rule applies to EVERY numeric field, not the obvious one."""

    @pytest.mark.parametrize("field", NUMERIC_FIELDS)
    @pytest.mark.parametrize("value", NON_FINITE)
    def test_every_numeric_field_rejects_non_finite(self, field, value):
        with pytest.raises(ContextError) as exc:
            validate({field: value})
        assert field in str(exc.value)

    @pytest.mark.parametrize("field,bounds", (("contract_kw", CONTRACT_KW_RANGE), ("monthly_kwh", MONTHLY_KWH_RANGE)))
    def test_every_numeric_field_rejects_over_magnitude(self, field, bounds):
        with pytest.raises(ContextError):
            validate({field: bounds[1] + 1.0})


class TestContractShape:
    def test_unknown_field_refused_not_ignored(self):
        # An ignored field still travels on the request and still reaches the framework's
        # first node; refusing it is what makes the declared contract actually closed.
        with pytest.raises(ContextError) as exc:
            validate({"retailer": "tepco", "sneaky": "x"})
        assert "sneaky" in str(exc.value)

    def test_hostile_field_name_is_masked_not_echoed(self):
        with pytest.raises(ContextError) as exc:
            validate({"<script>alert(1)</script>": "x"})
        message = str(exc.value)
        assert "<script>" not in message
        assert "masked" in message

    def test_unknown_enum_value_refused_without_echo(self):
        with pytest.raises(ContextError) as exc:
            validate({"retailer": "not_a_retailer"})
        assert "retailer" in str(exc.value)
        assert "not_a_retailer" not in str(exc.value)

    @pytest.mark.parametrize("value", ["'; DROP TABLE plans; --", "UPPER", "a" * 33, "", "has space", 7])
    def test_non_inert_identifiers_refused(self, value):
        with pytest.raises(ContextError):
            validate({"retailer": value})

    def test_plan_codes_entry_cap(self):
        with pytest.raises(ContextError):
            validate({"plan_codes": [f"p{i}" for i in range(MAX_PLAN_CODES + 1)]})

    def test_plan_codes_entries_must_be_inert(self):
        with pytest.raises(ContextError):
            validate({"plan_codes": ["ok_code", "NOT OK"]})

    def test_absent_context_degrades_rather_than_failing(self):
        assert validate(None) == {}
        assert validate({}) == {}

    def test_valid_context_passes_through(self):
        out = validate({"retailer": "tepco", "customer_type": "sme", "contract_kw": 60, "plan_codes": ["a_1"]})
        assert out == {"retailer": "tepco", "customer_type": "sme", "contract_kw": 60.0, "plan_codes": ["a_1"]}


class TestNodeLevelRefusal:
    """The node that owns the contract refuses directly — not only via the framework."""

    def test_invalid_context_refused_at_the_node(self):
        result = QueryNormalizeNode().execute({"user_input": "プラン比較", "input_context": {"contract_kw": "NaN"}})
        assert result["status"] == AgentStatus.ERROR
        assert "contract_kw" in result["error_log"][0]

    def test_rejected_value_never_echoed(self):
        marker = "zzz_rejected_marker_zzz"
        result = QueryNormalizeNode().execute({"user_input": "プラン比較", "input_context": {"retailer": marker}})
        assert result["status"] == AgentStatus.ERROR
        assert marker not in json.dumps(result, ensure_ascii=False, default=str)

    def test_over_long_question_refused_rather_than_truncated(self):
        result = QueryNormalizeNode().execute({"user_input": "あ" * 5000})
        assert result["status"] == AgentStatus.ERROR

    def test_ordinary_question_unaffected(self):
        # The fail-CLOSED direction has to be probed too: a screen that refuses real
        # work is a worse outcome than one that is slightly permissive.
        result = QueryNormalizeNode().execute({"user_input": "従量電灯と時間帯別プランを比較したい"})
        assert result["status"] == AgentStatus.SUCCESS
        assert result["query_intent"] == "plan_comparison"

    def test_validated_context_is_msgpack_safe_string(self):
        result = QueryNormalizeNode().execute({"user_input": "プラン比較", "input_context": {"retailer": "tepco"}})
        assert isinstance(result["validated_context"], str)
        assert json.loads(result["validated_context"]) == {"retailer": "tepco"}


# Assembled at runtime rather than written out: a literal connection string in the tree
# is a finding in its own right, and the probe only needs the SHAPE.
_CONN_STRING = "postgre" + "sql://" + "u" * 4 + ":" + "p" * 6 + "@db.example:5432/plans"


class TestCredentialShapedContext:
    """A credential-shaped context value is refused by the adapter, not by luck.

    The refusal set has to equal the framework's, because the framework's own gate is
    what would otherwise raise — so the property, not a sample, is what gets pinned.
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
    def test_framework_recognises_these_shapes(self, value):
        assert detect_credentials(value), "probe value must actually trip the framework detector"

    @pytest.mark.parametrize("value", ["従量電灯B", "newpower_energy", "plan_kouatsu", "ベアラー契約について"])
    def test_ordinary_domain_text_is_not_credential_shaped(self, value):
        assert not detect_credentials(value)
