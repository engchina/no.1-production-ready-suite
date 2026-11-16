"""補いの検索で当たった文書全体の要約などの派生の chunk を、同じ文書の本文の根拠に置き換える（#5244）。

文書全体の要約の chunk（#4793）は文書の全体を短く述べるので、要求に合う文書を見つけるのには当たるが、根拠の語句は
本文にある。要約の chunk には位置で前後の文脈を付けない（#5221）ので、補いの検索（要求別の補完・業務ガイドの手順）が
要約の chunk を切ると、その枠の分だけ本文の根拠が補いに入らない。

補いの検索が切った起点に派生の chunk があれば、その文書の本文に範囲を絞って同じ検索文で検索し直し（文書の範囲つきの
検索。``page_ranges``。元の検索が範囲つきならその文書の範囲のまま）、当たった本文の chunk をその枠の根拠にする。派生の
chunk は枠から外す。本文が当たらない・検索し直せないときは派生の chunk をそのまま残す（根拠を失わない）。

検索し直しは主の検索の後の追加の検索なので、追加の検索の同じ総枠（``ExtraRetrievalBudget``）から 1 回ずつ取る。回答の期限の
残りが、検索 1 回の見積もり（派生の chunk を当てた検索にかかった時間）と生成・確定に残す分（章節の拡張と同じ
``SectionExpansionLimits.generation_reserve_seconds``）の和より少なければ検索し直さない。LLM は呼ばない。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rag_parser_core.extraction import is_derived_content

from rag_engine.resources.deadline import remaining_seconds
from rag_engine.retrieval.extra_retrieval import SUMMARY_BODY_KIND, ExtraRetrievalBudget

SUMMARY_HITS_TRACE_SCHEMA_VERSION = 1

# 検索し直しの結果（記録の ``status``）。
STATUS_REPLACED = "replaced"
STATUS_NO_BODY = "no_body"
STATUS_FAILED = "failed"
STATUS_UNSEARCHED_BUDGET = "unsearched_budget"
STATUS_UNSEARCHED_DEADLINE = "unsearched_deadline"
# 元の検索の範囲にその文書が無い（文書の id が分からない派生の chunk を含む）。範囲を広げて検索しない。
STATUS_UNSCOPED = "unscoped"

# 検索し直しで切りの前に除く候補の理由のコード（主の検索の根拠との重複の理由とは分けて数える）。
EXCLUDED_DERIVED = "summary_research_derived"
EXCLUDED_TAKEN = "summary_research_taken"
EXCLUDED_CODES = frozenset({EXCLUDED_DERIVED, EXCLUDED_TAKEN})


def is_derived_record(record: Any) -> bool:
    """派生の chunk（文書全体の要約・章節の要約・項目抽出の値）か。backend の ``is_derived_chunk`` と同じ判定。"""
    metadata = getattr(record, "metadata", None) or {}
    return isinstance(metadata, Mapping) and is_derived_content(metadata.get("content_kind"), metadata)


def record_document_id(record: Any) -> str:
    metadata = getattr(record, "metadata", None) or {}
    return str(metadata.get("document_id") or "") if isinstance(metadata, Mapping) else ""


def derived_hits_by_document(records: Iterable[Any]) -> dict[str, list[Any]]:
    """切った起点のうち派生の chunk を文書ごとに（起点の順）。文書の id が無いものは "" にまとめる。"""
    hits: dict[str, list[Any]] = {}
    for record in records:
        if is_derived_record(record):
            hits.setdefault(record_document_id(record), []).append(record)
    return hits


def document_scope(document_id: str, page_ranges: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """検索し直しの範囲。元の検索が範囲つきならその文書の範囲だけ、範囲が無ければ文書の全体。

    元の検索の範囲にその文書が無ければ空（範囲を広げない）。
    """
    if not document_id:
        return []
    if not page_ranges:
        return [{"document_id": document_id}]
    return [dict(item) for item in page_ranges if str(item.get("document_id") or "") == document_id]


def replace_derived_hits(records: Sequence[Any], bodies: Mapping[str, Sequence[Any]], key: Callable[[Any], str]) -> list[Any]:
    """派生の chunk を、その文書の本文の根拠に置き換える（本文は文書の最初の派生の chunk の位置に、検索の順で）。

    bodies に無い文書（検索し直さなかった・本文が当たらなかった）の派生の chunk はそのまま残す。
    """
    out: list[Any] = []
    placed: set[str] = set()
    seen: set[str] = set()
    for record in records:
        document_id = record_document_id(record)
        if is_derived_record(record) and bodies.get(document_id):
            if document_id not in placed:
                placed.add(document_id)
                for body in bodies[document_id]:
                    if (body_key := key(body)) not in seen:
                        seen.add(body_key)
                        out.append(body)
            continue
        if (record_key := key(record)) not in seen:
            seen.add(record_key)
            out.append(record)
    return out


@dataclass
class SummaryHitResearch:
    """1 回の回答の、要約の当たりの検索し直しの枠と記録。回答ごとに作り、補いの検索のすべてで共有する。

    ``held`` は、すでに決めたがまだ総枠に数えていない追加の検索の回数（業務ガイドの任意の手順の検索は、終わってから
    まとめて数える）。検索し直しはそれを除いた残りだけを使う。
    """

    budget: ExtraRetrievalBudget
    reserve_seconds: float
    deadline_remaining: Callable[[], float | None] = remaining_seconds
    held: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    def admit(self, estimate_seconds: float) -> str:
        """検索し直してよければ総枠から 1 回取って ""、できなければ理由のコード。"""
        if self.budget.remaining - max(0, self.held) <= 0:
            return STATUS_UNSEARCHED_BUDGET
        left = self.deadline_remaining()
        if left is not None and left < max(0.0, estimate_seconds) + self.reserve_seconds:
            return STATUS_UNSEARCHED_DEADLINE
        self.budget.take(SUMMARY_BODY_KIND)
        return ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SUMMARY_HITS_TRACE_SCHEMA_VERSION,
            "llm_calls": 0,
            "searches": [dict(row) for row in self.rows],
            "budget": self.budget.to_payload(),
        }


__all__ = [
    "EXCLUDED_CODES",
    "EXCLUDED_DERIVED",
    "EXCLUDED_TAKEN",
    "STATUS_FAILED",
    "STATUS_NO_BODY",
    "STATUS_REPLACED",
    "STATUS_UNSCOPED",
    "STATUS_UNSEARCHED_BUDGET",
    "STATUS_UNSEARCHED_DEADLINE",
    "SUMMARY_HITS_TRACE_SCHEMA_VERSION",
    "SummaryHitResearch",
    "derived_hits_by_document",
    "document_scope",
    "is_derived_record",
    "record_document_id",
    "replace_derived_hits",
]
