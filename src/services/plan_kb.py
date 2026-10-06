"""AgentCore Platform v1.0 — ENE-C2-014 electricity-plan knowledge-base interface.

A small in-code corpus. The retrieval node depends ONLY on the ``PlanKnowledgeBase``
interface (``retrieve_plan_rules`` / ``retrieve_incentives``), so an embedding-backed
store can replace this one without any change to node logic.

All chunks are static, public regulatory and plan-terms text — no personal data, no
credentials, and no per-customer billing figures are held here.
"""

from __future__ import annotations

from typing import Any

# Customer segments that may receive each chunk. ``None`` = available to all segments.
# Each chunk: id, text, source (citation label), the customer segments it applies to, and
# ``min_kw`` — the minimum contract capacity in kW at which the plan becomes available
# (``None`` = no capacity floor). This is what makes a supplied contract capacity change
# the answer rather than merely being accepted and ignored.
_PLAN_RULE_CHUNKS: "tuple[dict[str, Any], ...]" = (
    {
        "id": "denkijigyoho-17",
        "text": "電気事業法第17条: 小売電気事業者は、正当な理由がない限り、一般の需要に応ずる電気の供給を拒んではならない。",
        "source": "電気事業法 第17条",
        "segments": None,
        "min_kw": None,
    },
    {
        "id": "plan-juuryou-toukou",
        "text": "従量電灯B/C: 基本料金 + 三段階の従量料金。標準的な家庭・小規模事業者向けの規制料金プラン。",
        "source": "小売プラン約款 — 従量電灯",
        "segments": ("residential", "sme"),
        "min_kw": None,
    },
    {
        "id": "plan-jikantai-betsu",
        "text": "時間帯別電灯(オール電化向け): 夜間料金が割安。昼夜の使用比率で従量電灯より有利になる場合がある。",
        "source": "小売プラン約款 — 時間帯別電灯",
        "segments": ("residential",),
        "min_kw": None,
    },
    {
        "id": "plan-kouatsu-business",
        "text": "高圧電力プラン: 契約電力50kW以上の事業所向け。デマンド料金制で、最大需要電力により基本料金が決まる。",
        "source": "小売プラン約款 — 高圧電力",
        "segments": ("corporate", "sme"),
        "min_kw": 50.0,
    },
    {
        "id": "plan-green-re100",
        "text": "実質再生可能エネルギー100%プラン: 非化石証書により再エネ比率100%相当。RE100 報告に利用可能。",
        "source": "小売プラン約款 — グリーン電力",
        "segments": ("re100", "corporate"),
        "min_kw": None,
    },
)

_INCENTIVE_CHUNKS: "tuple[dict[str, Any], ...]" = (
    {
        "id": "incentive-sessuiden",
        "text": "節電ポイント(電気・ガス価格激変緩和対策事業の後継): 需要逼迫時の節電量に応じてポイントを付与。家庭・事業者が対象。",
        "source": "経済産業省 — 節電プログラム促進事業",
        "program": "sessuiden_point",
        "segments": None,
    },
    {
        "id": "incentive-re100",
        "text": "RE100: 事業活動で消費する電力を100%再生可能エネルギーで賄うことを目標とする国際イニシアチブ。加盟は法人単位。",
        "source": "RE100 技術要件",
        "program": "re100",
        "segments": ("re100", "corporate"),
    },
    {
        "id": "incentive-j-credit",
        "text": "J-クレジット制度: 省エネ設備導入や再エネ利用による温室効果ガス排出削減量を国が認証。クレジットは売却・カーボンオフセットに利用可能。",
        "source": "J-クレジット制度 実施要綱",
        "program": "j_credit",
        "segments": ("corporate", "re100", "sme"),
    },
)

_VALID_SEGMENTS = frozenset({"residential", "sme", "corporate", "re100"})


def _segment_match(chunk: "dict[str, Any]", customer_type: str) -> bool:
    segments = chunk.get("segments")
    return segments is None or customer_type in segments


class PlanKnowledgeBase:
    """In-code corpus. Replace the body with an embedding-backed store to scale it.

    The public method shapes (query, customer_type, retailer_id -> list[dict])
    are the stable contract; node code must not reach past them.
    """

    def __init__(
        self,
        plan_chunks: "tuple[dict[str, Any], ...]" = _PLAN_RULE_CHUNKS,
        incentive_chunks: "tuple[dict[str, Any], ...]" = _INCENTIVE_CHUNKS,
    ) -> None:
        self._plan_chunks = plan_chunks
        self._incentive_chunks = incentive_chunks

    @staticmethod
    def _score(query: str, text: str) -> float:
        """Deterministic lexical overlap score in [0, 1] (stand-in for cosine similarity)."""
        q_tokens = {t for t in query.lower().split() if t}
        if not q_tokens:
            return 0.5  # neutral baseline when the query has no usable tokens
        hits = sum(1 for t in q_tokens if t in text.lower())
        return round(min(1.0, 0.4 + 0.2 * hits), 4)

    def retrieve_plan_rules(
        self, query: str, customer_type: str, retailer_id: str | None = None, top_k: int = 4
    ) -> "list[dict[str, Any]]":
        """Return plan-terms / 電気事業法 chunks filtered by customer segment."""
        segment = customer_type if customer_type in _VALID_SEGMENTS else "residential"
        out: "list[dict[str, Any]]" = []
        for chunk in self._plan_chunks:
            if not _segment_match(chunk, segment):
                continue
            out.append(
                {
                    "id": chunk["id"],
                    "text": chunk["text"],
                    "source": chunk["source"],
                    "citation": chunk["source"],
                    "retailer_id": retailer_id,
                    "min_kw": chunk.get("min_kw"),
                    "score": self._score(query, chunk["text"] + " " + chunk["source"]),
                }
            )
        out.sort(key=lambda c: c["score"], reverse=True)
        return out[:top_k]

    def retrieve_incentives(self, query: str, customer_type: str, top_k: int = 3) -> "list[dict[str, Any]]":
        """Return 節電ポイント / RE100 / J-Credit rule chunks filtered by segment."""
        segment = customer_type if customer_type in _VALID_SEGMENTS else "residential"
        out: "list[dict[str, Any]]" = []
        for chunk in self._incentive_chunks:
            if not _segment_match(chunk, segment):
                continue
            out.append(
                {
                    "id": chunk["id"],
                    "text": chunk["text"],
                    "source": chunk["source"],
                    "citation": chunk["source"],
                    "program": chunk["program"],
                    "score": self._score(query, chunk["text"] + " " + chunk["source"]),
                }
            )
        out.sort(key=lambda c: c["score"], reverse=True)
        return out[:top_k]
