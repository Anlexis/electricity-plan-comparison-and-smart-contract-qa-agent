"""Aggregate plan-comparison synthesis (post_process step 1).

Builds a cited comparison of the retrieved plan terms, the incentive programmes the
customer segment qualifies for, and — when the caller supplied a contract capacity or a
consumption figure — which plans that actually rules in or out.

The template's standing boundary is that it produces an AGGREGATE comparison and never an
individualized billing or rate projection. Consumption is therefore reported as a usage
BAND rather than a figure, and no monetary amount is ever computed: a per-customer cost
estimate is the one thing this agent must not produce, so it is not produced here and the
output gate independently refuses it downstream.
"""

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.schemas.state import from_json
from src.services.caller_context import HIGH_VOLTAGE_MIN_KW

_PROGRAM_LABELS = {
    "sessuiden_point": "節電ポイント",
    "re100": "RE100",
    "j_credit": "J-クレジット",
}

# Consumption bands. Reporting a band rather than the caller's own figure keeps the
# answer aggregate, which is the invariant this template exists to hold.
_USAGE_BANDS: tuple[tuple[float, str], ...] = (
    (300.0, "月間300kWh未満(小規模)"),
    (700.0, "月間300〜700kWh(標準的な家庭)"),
    (2000.0, "月間700〜2,000kWh(大規模家庭・小規模事業所)"),
    (float("inf"), "月間2,000kWh以上(事業所規模)"),
)


def _usage_band(monthly_kwh: float) -> str:
    for ceiling, label in _USAGE_BANDS:
        if monthly_kwh < ceiling:
            return label
    return _USAGE_BANDS[-1][1]


class ComparisonGenerateNode(FunctionNode):
    """Build the cited comparison answer from retrieved candidates and eligibility."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: "dict[str, Any]") -> "dict[str, Any]":
        plan_rules = from_json(state.get("retrieved_plan_rules"), [])
        incentives = from_json(state.get("retrieved_incentives"), [])
        eligibility = from_json(state.get("eligibility"), {})
        excluded = from_json(state.get("excluded_plan_ids"), [])
        try:
            context: dict[str, Any] = json.loads(state.get("validated_context") or "{}")
        except (TypeError, ValueError):
            context = {}

        if not plan_rules and not incentives:
            # Nothing to synthesize from. This is a real outcome that the caller must be
            # told about explicitly — an empty answer returned as a success would look
            # like "there is nothing to say" rather than "we found nothing to say it
            # from", and the two need different follow-up.
            emit_trace_event("plan_comparison_degraded", {"reason": "no_retrieval_evidence"}, state)
            return {
                "status": AgentStatus.SUCCESS,
                "answer": "",
                "blocked": True,
                "withheld_reason": "no_retrieval_evidence",
            }

        lines: list[str] = ["【電気料金プラン比較】"]
        for chunk in plan_rules:
            citation = chunk.get("citation") or chunk.get("source") or chunk.get("id", "出典不明")
            lines.append(f"- {chunk.get('text', '')}(出典: {citation})")

        contract_kw = context.get("contract_kw")
        if contract_kw is not None:
            lines.append("")
            if state.get("high_voltage_available"):
                lines.append(
                    f"【契約電力】ご申告の契約電力は高圧区分の目安({HIGH_VOLTAGE_MIN_KW:g}kW)以上のため、"
                    "高圧電力プランが選択可能です。"
                )
            else:
                lines.append(
                    f"【契約電力】ご申告の契約電力は高圧区分の目安({HIGH_VOLTAGE_MIN_KW:g}kW)未満のため、"
                    "高圧電力プランは対象外です。"
                )
            if excluded:
                lines.append(f"- 契約電力の条件により比較対象から除外したプラン: {len(excluded)}件")

        monthly_kwh = context.get("monthly_kwh")
        if monthly_kwh is not None:
            lines.append("")
            lines.append(f"【使用量区分】{_usage_band(float(monthly_kwh))}")
            lines.append("※ 使用量区分は一般的な傾向の説明であり、料金の試算ではありません。")

        eligible_programs = [_PROGRAM_LABELS[p] for p in ("sessuiden_point", "re100", "j_credit") if eligibility.get(p)]
        if eligible_programs:
            lines.append("")
            lines.append(f"【利用可能な優遇制度】{', '.join(eligible_programs)}")
        for chunk in incentives:
            citation = chunk.get("citation") or chunk.get("source") or chunk.get("id", "出典不明")
            lines.append(f"- {chunk.get('text', '')}(出典: {citation})")

        details = eligibility.get("details") or {}
        for note in details.values():
            lines.append(f"- 補足: {note}")

        lines.append("")
        lines.append("※ 本回答は一般的なプラン比較です。個別のご契約・お見積りは小売事業者にご確認ください。")

        emit_trace_event(
            "plan_comparison_generated",
            {
                "plan_chunks_rendered": len(plan_rules),
                "incentive_chunks_rendered": len(incentives),
                "capacity_reported": contract_kw is not None,
                "usage_band_reported": monthly_kwh is not None,
            },
            state,
        )
        return {
            "status": AgentStatus.SUCCESS,
            "answer": "\n".join(lines),
            "blocked": False,
            "withheld_reason": None,
        }
