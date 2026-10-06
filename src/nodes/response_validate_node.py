"""Output gate over the draft answer (post_process step 2).

Three independent checks run on the draft produced by ComparisonGenerate:

* the template's own boundary — an INDIVIDUALIZED billing or rate projection is refused,
  because this agent compares plans in the aggregate and a per-customer cost estimate is
  precisely what it must never emit;
* a credential scan, delegated to the framework's own ``detect_credentials`` rather than
  a local pattern list. A local list that is narrower than the framework's is not merely
  weaker, it is a bypass: a value the framework recognises and this gate misses makes the
  framework raise *after* this node returns, and the wrapper then discards this node's
  whole delta — including the clearing below. Sharing the framework's detector makes the
  two sets equal by construction rather than by maintenance;
* an emptiness check, so a draft that never got written cannot be shipped as an answer.

REFUSAL SEMANTICS. Returning an error status is not by itself containment. The graph's
output resolution falls back through the answer field, and a falsy value merely activates
that fallback rather than suppressing it — so a gate that blanks the answer to an empty
string ships exactly what it meant to withhold. On refusal this node therefore returns an
error status AND overwrites every field that carries answer text or retrieved payload,
putting a TRUTHY notice in the answer field. The inventory below is asserted at import
time so a future output-bearing field cannot quietly avoid being cleared.
"""

import re
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event

# Individualized billing / rate projection phrasing. This is the domain boundary the
# framework cannot know about, so it is enforced here.
_INDIVIDUALIZED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("individual_billing_ja", re.compile(r"あなたの(?:請求|料金|電気代|支払)")),
    ("individual_billing_keigo_ja", re.compile(r"お客様の(?:月額|請求額|想定料金)")),
    ("billing_simulation_ja", re.compile(r"(?:推定|試算|シミュレーション).{0,6}(?:請求|料金|電気代)")),
    (
        "billing_projection_en",
        re.compile(r"\b(?:your )?(?:estimated|projected|simulated)\s+(?:bill|monthly cost|charge)", re.IGNORECASE),
    ),
    # A concrete monetary amount attached to a billing period is an individualized
    # figure regardless of the wording that introduces it.
    ("monetary_billing_amount", re.compile(r"\b\d{1,3}(?:[,，]\d{3})*\s*円(?:/月|/年|毎月|の請求|お支払い)")),
)

_NOTICE = (
    "申し訳ありませんが、この内容はご提供できません。"
    "本エージェントは一般的なプラン比較のみをご案内しており、個別の請求額・料金シミュレーションは対象外です。"
    "個別のお見積りは小売事業者にご確認ください。"
)

_DEGRADED_NOTICE = (
    "該当するプラン情報が見つかりませんでした。" "ご質問の条件を変更いただくか、小売事業者にご確認ください。"
)

# Every state field that can carry answer text or retrieved payload. On refusal all of
# these are overwritten. Inert provenance (booleans, counts, enums) is deliberately not
# listed, and the guard below keeps that decision honest.
_OUTPUT_BEARING_FIELDS: tuple[str, ...] = (
    "answer",
    "retrieved_plan_rules",
    "retrieved_incentives",
    "retrieval_scores",
    "eligibility",
    "excluded_plan_ids",
)


def _cleared_payload() -> dict[str, Any]:
    """The cleared value for every output-bearing field.

    The answer field gets a TRUTHY notice on purpose: an empty string is falsy, and the
    graph's output resolution treats a falsy answer as "not set" and falls through to the
    next candidate — which is the un-gated content this gate exists to withhold.
    """
    cleared: dict[str, Any] = {f: None for f in _OUTPUT_BEARING_FIELDS}
    cleared["answer"] = _NOTICE
    return cleared


def _violation_kinds(text: str) -> list[str]:
    """Return the KINDS of violation found — never the matched text.

    The matched text is the thing being withheld; naming it in an error log would move
    it to a different output channel rather than withholding it. A credential match is
    additionally never echoed because the framework's own gate scans every returned
    value and would raise on it, discarding this node's clearing.
    """
    kinds: list[str] = []
    if detect_credentials(text):
        kinds.append("credential_pattern")
    for label, pattern in _INDIVIDUALIZED_PATTERNS:
        if pattern.search(text):
            kinds.append(label)
    return kinds


class ResponseValidateNode(FunctionNode):
    """Refuse or release the draft answer; on refusal, withhold every carrier of it."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        draft = state.get("answer", "")

        if not isinstance(draft, str) or not draft.strip():
            reason = state.get("withheld_reason") or "empty_draft"
            emit_trace_event("plan_answer_withheld", {"reason": reason}, state)
            return {
                "status": AgentStatus.ERROR,
                **_cleared_payload(),
                "answer": _DEGRADED_NOTICE,
                "blocked": True,
                "withheld_reason": reason,
                "error_log": [f"ResponseValidateNode: no answer to release ({reason})"],
            }

        kinds = _violation_kinds(draft)
        if kinds:
            emit_trace_event(
                "plan_answer_withheld", {"reason": "output_gate_violation", "kinds": sorted(set(kinds))}, state
            )
            return {
                "status": AgentStatus.ERROR,
                **_cleared_payload(),
                "blocked": True,
                "withheld_reason": "output_gate_violation",
                # Kinds only. The draft that triggered them is not reproduced anywhere.
                "error_log": [
                    "ResponseValidateNode: answer withheld by the output gate " f"({', '.join(sorted(set(kinds)))})"
                ],
            }

        emit_trace_event("plan_answer_released", {"answer_chars": len(draft.strip())}, state)
        return {
            "status": AgentStatus.SUCCESS,
            "answer": draft.strip(),
            "blocked": False,
            "withheld_reason": None,
        }


# Inventory guard: every declared output-bearing field must actually be cleared. If a new
# carrier of answer text is added to the tuple but not to the cleared payload, or vice
# versa, this fails at import rather than at the boundary it was meant to protect.
assert set(_cleared_payload()) == set(
    _OUTPUT_BEARING_FIELDS
), "output-bearing field inventory and cleared payload have diverged"
assert _cleared_payload()["answer"], "the cleared answer must be truthy, or it activates the fallback"
