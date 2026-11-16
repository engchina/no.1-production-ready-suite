"""全文検索の超限を原要求の単位で補う。根拠の十分性は判断しない。

要求ごとの検索は、主の検索と同じく「検索 → rerank → 下限 → 文書ごとの上限 → ``top_k`` で切る」だけを行う（#5173）。
ただし下限の後、文書ごとの上限の前に、主の検索の根拠にすでにある候補を除き、切りの枠を新しい根拠に使う（#5175。
業務ガイドの手順の検索と同じ判定。除いた数は要求ごとの ``covered_candidates``）。
``top_k`` は、足す根拠の上限（``RAG_REQUEST_COVERAGE_MAX_CHUNKS``）を要求の数で割った件数（切り上げ）と、上限の残りの
小さい方。切った根拠は呼び出し元が主の検索の根拠へ合わせる（``expansion_merge`` の規則。主の検索の根拠を外さない）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any

from rag_engine.dependencies import report_degraded
from rag_engine.retrieval.extra_retrieval import ExtraRetrievalBudget
from rag_engine.retrieval.request_coverage import CoverageBudget, CoverageUsage, coverage_targets
from rag_engine.retrieval.summary_hits import is_derived_record
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizationResult, normalize_text_search_text

OVERFLOW_KIND = "query_overflow"


@dataclass(frozen=True)
class OverflowSearch:
    request_id: str
    query: str
    context: Any


def _key(query: str) -> str:
    return " ".join(normalize_text_search_text(query).split()).rstrip("。?？!！")


def _record_key(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


def search_overflow_requests(
    question: str,
    *,
    tokenize: Callable[[str], TextSearchTokenizationResult],
    retrieve: Callable[[str, int], Any],
    extra_budget: ExtraRetrievalBudget,
    coverage_budget: CoverageBudget,
    usage: CoverageUsage,
    searched_queries: Sequence[str] = (),
    existing_keys: Iterable[str] = (),
    allow_split: bool = True,
    initial_query_overflowed: bool = False,
    clock: Callable[[], float] = monotonic,
) -> tuple[list[OverflowSearch], dict[str, Any]]:
    """元の ID・前置きを保った検索を、追加検索の同じ回数・期限の中で行う。

    ``retrieve(query, top_k)`` は ``top_k`` で切った起点（records）を返す。``existing_keys`` は主の検索の根拠の key で、
    新しい根拠の数（上限の使用量）にはそれ以外だけを数える。
    """
    if not allow_split:
        return [], {"schema_version": 1, "triggered": False, "skipped": "original_query_only", "requests": [], "llm_calls": 0}
    try:
        original = tokenize(question)
    except Exception as exc:  # noqa: BLE001 - 語の抽出の失敗で既存根拠を失わない。
        report_degraded("query_overflow", exc)
        return [], {"schema_version": 1, "triggered": False, "skipped": "tokenization_failed", "requests": [], "llm_calls": 0}
    trace: dict[str, Any] = {
        "schema_version": 1,
        "triggered": original.truncated or initial_query_overflowed,
        "initial_query_overflowed": initial_query_overflowed,
        "assessment": "not_performed",
        "llm_calls": 0,
        "original_query_chars": len(original.oracle_text_query),
        "requests": [],
    }
    if not trace["triggered"]:
        return [], trace
    targets = [target for target in coverage_targets(question, lambda text: tokenize(text).tokens) if "." not in target.id]
    started = clock()
    rows = trace["requests"]
    results: list[OverflowSearch] = []
    initial_keys = {_key(query) for query in (*searched_queries, *usage.searched_queries)}
    seen = {key for key in existing_keys if key}
    share = -(-max(0, coverage_budget.max_chunks) // max(1, len(targets)))
    cache: dict[str, Any] = {}
    deadline = usage.remaining(coverage_budget).deadline_seconds
    for target in targets:
        query = target.query or target.text
        try:
            tokens = tokenize(query)
        except Exception as exc:  # noqa: BLE001 - 未検索を根拠なしと区別する。
            report_degraded("query_overflow", exc)
            rows.append({"request_id": target.id, "query": query, "status": "unsearched_tokenization_failed"})
            continue
        row: dict[str, Any] = {
            "request_id": target.id,
            "query": query,
            "oracle_text_query": tokens.oracle_text_query,
            "query_chars": len(tokens.oracle_text_query),
            "omitted_tokens": list(tokens.truncated_tokens),
            "omitted_protected_tokens": list(tokens.omitted_protected_tokens),
            "answerability": "unassessed",
        }
        rows.append(row)
        key = _key(query)
        if key in cache:
            result = cache[key]
            row.update(status="reused" if result is not None else "reused_failed", candidates=len(getattr(result, "records", ()) or ()))
            if result is not None:
                results.append(OverflowSearch(target.id, query, result))
            continue
        if key in initial_keys:
            row["status"] = "already_searched"
            continue
        # 1 要求と共有条件だけでも識別子が収まらない場合、条件を勝手に組み替えて検索しない。
        if tokens.omitted_protected_tokens or not tokens.oracle_text_query:
            row["status"] = "unsearched_query_overflow"
            continue
        if coverage_budget.max_chunks <= usage.chunks:
            row["status"] = "unsearched_evidence_budget"
            continue
        if extra_budget.remaining <= 0:
            row["status"] = "unsearched_budget"
            continue
        if deadline <= 0 or clock() - started >= deadline:
            row["status"] = "unsearched_deadline"
            continue
        top_k = max(1, min(share, coverage_budget.max_chunks - usage.chunks))
        extra_budget.take(OVERFLOW_KIND)
        usage.queries += 1
        usage.searched_queries.append(query)
        cache[key] = None
        try:
            result = retrieve(query, top_k)
        except Exception as exc:  # noqa: BLE001 - 失敗を根拠なしにせず記録し、既存の検索結果を維持する。
            row.update(status="failed", error_type=type(exc).__name__, top_k=top_k)
            continue
        cache[key] = result
        records = list(getattr(result, "records", ()) or ())
        fresh = [record_key for record in records if (record_key := _record_key(record)) and record_key not in seen]
        seen.update(fresh)
        # 本文の後に文書の説明として残した要約などの派生の chunk は、足す根拠の上限に数えない（#5244）。
        usage.chunks += sum(1 for record in records if _record_key(record) in fresh and not is_derived_record(record))
        count = len(records)
        row.update(
            status="candidate_found" if count else "no_candidates",
            candidates=count,
            top_k=top_k,
            new_candidates=len(fresh),
            covered_candidates=len(getattr(result, "covered_candidates", ()) or ()),
        )
        results.append(OverflowSearch(target.id, query, result))
    usage.elapsed_seconds += max(0.0, clock() - started)
    trace["budget"] = extra_budget.to_payload()
    trace["elapsed_ms"] = int(max(0.0, clock() - started) * 1000)
    return results, trace
