"""Graph composition for the electricity-plan Q&A agent.

A retrieval-augmented question-answering agent built directly on the framework base
graph. The backbone exposes three slots, and this agent fills each with a small inline
orchestrator that runs an ordered pair of nodes and merges their partial results:

  pre_process  : QueryNormalize → CustomerTypeClassify
  main         : PlanRulesRetrieve → IncentiveProgramCheck
  post_process : ComparisonGenerate → ResponseValidate   (the output gate)

Each orchestrator short-circuits when a sub-node returns an error status, so a refusal
raised anywhere in the chain propagates intact and the backbone routes the run straight
to finalization rather than continuing to build on a rejected result.
"""

import pathlib
from typing import Any, ClassVar

import yaml
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.query_normalize_node import QueryNormalizeNode
from src.nodes.customer_type_classify_node import CustomerTypeClassifyNode
from src.nodes.plan_rules_retrieve_node import PlanRulesRetrieveNode
from src.nodes.incentive_program_check_node import IncentiveProgramCheckNode
from src.nodes.comparison_generate_node import ComparisonGenerateNode
from src.nodes.response_validate_node import ResponseValidateNode
from src.schemas.state import State


_RUNTIME_CONFIG_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def load_runtime_config() -> "dict[str, Any]":
    """Load runtime parameters from ``config/config.yaml`` (max_retry, timeout_s).

    The standalone entry point passes the result to the constructor. Without that the
    graph would be built with an empty config and every declared runtime value would be
    silently replaced by a framework default — the file would look authoritative and
    control nothing. A missing or unreadable file degrades to ``{}``, which is the same
    documented default behaviour, just arrived at honestly.
    """
    try:
        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _is_error(result: "dict[str, Any]") -> bool:
    status = result.get("status")
    # status may be the enum or its .value ("error") depending on the caller.
    return status in (AgentStatus.ERROR, AgentStatus.ERROR.value)


class _SequentialSlotNode(FunctionNode):
    """Run an ordered list of sub-nodes inline, merging partial dicts.

    Each sub-node sees the state as the previous one left it, and:
      - if the INCOMING state already carries an error (a refusal raised in an
        earlier slot), this slot is a no-op pass-through — the backbone always
        runs initialize→pre_process→main→route, so a downstream slot must NOT
        overwrite an upstream refusal; and
      - it stops at the first sub-node that returns ERROR so the refusal status
        is preserved for the backbone's routing decision.
    Returns the accumulated partial dict (not the whole state).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    _sub_nodes: "tuple[FunctionNode, ...]" = ()

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        # Honor an upstream refusal: pass it through unchanged. The backbone always runs
        # every slot, so without this a downstream slot would overwrite the refusal that
        # an earlier one produced.
        if _is_error(state):
            emit_trace_event(
                "plan_slot_skipped",
                {"slot": self.__class__.__name__, "reason": "upstream_error"},
                state,
            )
            return {
                "status": state.get("status"),
                "blocked": state.get("blocked", True),
            }

        merged = dict(state)
        accumulated: "dict[str, Any]" = {}
        ran: list[str] = []
        for node in self._sub_nodes:
            result = node(merged)  # __call__ -> trust gate + execute()
            ran.append(node.__class__.__name__)
            merged = {**merged, **result}
            accumulated = {**accumulated, **result}
            if _is_error(result):
                break
        emit_trace_event(
            "plan_slot_complete",
            {"slot": self.__class__.__name__, "sub_nodes_run": ran},
            state,
        )
        return accumulated


class PreProcessSlotNode(_SequentialSlotNode):
    """pre_process slot: QueryNormalize → CustomerTypeClassify."""

    def __init__(self) -> None:
        self._sub_nodes = (QueryNormalizeNode(), CustomerTypeClassifyNode())


class MainSlotNode(_SequentialSlotNode):
    """main slot: PlanRulesRetrieve → IncentiveProgramCheck."""

    def __init__(self) -> None:
        self._sub_nodes = (PlanRulesRetrieveNode(), IncentiveProgramCheckNode())


class PostProcessSlotNode(_SequentialSlotNode):
    """post_process slot: ComparisonGenerate → ResponseValidate (the output gate)."""

    def __init__(self) -> None:
        self._sub_nodes = (ComparisonGenerateNode(), ResponseValidateNode())


class ElectricityPlanQAAgent(AgentBaseGraph):
    """Electricity plan comparison & Q&A agent (ENE-C2-014)."""

    @property
    def name(self) -> str:
        return "ElectricityPlanQAAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode
        self._nodes["pre_process"] = PreProcessSlotNode()
        self._nodes["main"] = MainSlotNode()
        self._nodes["post_process"] = PostProcessSlotNode()

    def get_output(self, state: "dict[str, Any]") -> "dict[str, Any]":
        """Resolve the caller-visible envelope.

        The base implementation resolves output as "first non-empty candidate" with no
        regard for status, which means an error envelope still carries whatever answer
        text happens to be sitting in state. That is unsafe here: the post_process slot
        merges each sub-node's partial as it goes, so if the draft is written and a later
        step then fails, the draft is still in state when the run ends.

        So the answer is surfaced only when the run succeeded, or when the output gate
        itself produced a deliberate refusal notice — which it signals by setting
        ``withheld_reason``. Any other non-success outcome (an upstream error, a trust
        denial, an exception anywhere in the slot) resolves to no output at all, because
        in those cases nothing has attested that the text in state was ever cleared for
        release.
        """
        status = state.get("status")
        succeeded = status in (AgentStatus.SUCCESS, AgentStatus.SUCCESS.value)
        gate_notice = state.get("withheld_reason")

        if succeeded or gate_notice:
            output = state.get("answer") or state.get("formatted_output")
        else:
            output = state.get("formatted_output") or None

        return {
            "output": output,
            "status": status,
            "blocked": state.get("blocked", False),
            "withheld_reason": gate_notice,
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }


# Alias kept so that code importing the generic name ``Graph`` continues to resolve to
# this agent.
Graph = ElectricityPlanQAAgent
