"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic model.
# Graph checkpoints are serialized with msgpack; Pydantic objects and nested
# containers (list[dict] / dict) are not msgpack-safe and corrupt silently rather
# than failing loudly. Extend the base state with primitive fields only. Retrieval
# artifacts that are naturally list/dict are stored JSON-serialized as
# Optional[str] and (de)serialized at the node boundary via to_json / from_json.
# Never add credentials, secrets, or Pydantic models here.

import json
from typing import Any, Literal, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a list/dict State value to a compact, msgpack-safe JSON string.

    Returns ``None`` for ``None`` so the field stays a true ``Optional[str]``.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def from_json(value: Any, default: Any) -> Any:
    """Deserialize a JSON-string State value back to its list/dict form.

    Tolerant by design: ``None``/empty → ``default``; an already-native
    list/dict (e.g. a value supplied directly in a unit test) passes through
    unchanged; a malformed string falls back to ``default``.
    """
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class State(AgentState):
    """Electricity plan comparison & Q&A agent state (ENE-C2-014).

    Flat TypedDict — primitives + JSON-serialized strings only (msgpack-safe).
    Shared fields (user_input, status, session_id, node_history, error_log, ...)
    are inherited from AgentState.  No customer PII / account data and no
    individualized billing amounts are stored here (input masking, output gate);
    the corpus is static and public so no per-request credentials are persisted.
    """

    # --- pre_process: QueryNormalize + CustomerTypeClassify ---
    raw_query: str
    # original natural-language query
    validated_input: str
    # the validated query (== raw_query once normalization passes); drives
    # retrieval + customer-type classification
    query_intent: Literal["plan_comparison", "incentive_eligibility", "enrollment", "general"]
    # coarse intent label driving retrieval + synthesis
    customer_type: Literal["residential", "sme", "corporate", "re100"]
    # classified buyer segment (steers plan retrieval + incentive eligibility)
    validated_context: Optional[str]
    # JSON dict of the caller-supplied context AFTER contract validation. Only
    # validated values travel past pre_process; nothing downstream reads the raw
    # request mapping, so there is one place where the contract is decided.

    # --- main: PlanRulesRetrieve + IncentiveProgramCheck (RAG) ---
    # these retrieval artifacts are list/dict by nature, so they are
    # stored JSON-serialized (Optional[str]) and (de)serialized at the node
    # boundary via to_json / from_json — keeping State msgpack-safe.
    retrieved_plan_rules: Optional[str]
    # JSON list of 電気事業法 + registered plan-terms chunks (id, text, source, citation)
    retrieved_incentives: Optional[str]
    # JSON list of 節電ポイント / RE100 / J-Credit rule chunks
    retrieval_scores: Optional[str]
    # JSON list of per-chunk similarity scores; feeds the low-confidence route gate
    eligibility: Optional[str]
    # JSON dict of incentive eligibility determination keyed by program
    excluded_plan_ids: Optional[str]
    # JSON list of plan ids ruled out by the contract-capacity floor (explains an
    # otherwise silent narrowing of the comparison)
    high_voltage_available: bool
    # True when the supplied contract capacity reaches the high-voltage threshold

    # --- post_process: ComparisonGenerate + ResponseValidate (output gate) ---
    answer: str
    # final answer (plan comparison + eligibility + citations + retailer referral);
    # NEVER an individualized billing/rate projection (hard block at the output gate)
    blocked: bool
    # True when the output gate or a degradation path withheld the answer
    withheld_reason: Optional[str]
    # Stable machine-readable reason for a withheld answer; carries no answer text

    # --- runtime control ---
    error: Optional[str]
    # short-circuit / graceful-degradation flag
