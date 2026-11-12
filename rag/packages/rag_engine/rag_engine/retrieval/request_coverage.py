"""原質問の要求 ID と、明示された未回答の追加検索を追跡する。候補取得は回答可能性と区別する。"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from rag_engine.retrieval.answer_requirements import answer_requirements


@dataclass(frozen=True)
class CoverageBudget:
    """欠けた要求の再検索の予算（handoff §11.3 の「検索の回数・根拠の数・期限」）。"""

    enabled: bool = True
    # 再検索する要求の数（1 要求 1 回）。
    max_queries: int = 2
    # 再検索で回答の材料に足す根拠（親）の数の合計。
    max_chunks: int = 4
    # 再検索の全体の期限（秒）。期限を過ぎたら次の要求の検索を始めない（実行中の 1 回は中断しない）。
    deadline_seconds: float = 10.0
    # 小問ごとの検索をする小問の数（1 小問 1 回）と、小問ごとに回答の材料に足す根拠（親）の数 (#2399)。
    max_subquestions: int = 5
    chunks_per_subquestion: int = 2


@dataclass(frozen=True)
class RequestTarget:
    """元の質問から追跡する要求 1 件。"""

    id: str
    kind: str
    text: str
    terms: tuple[str, ...]
    # 小問の印（小問でなければ空）と、検索に使う文（空なら text。小問は前置き＋小問の文）。
    subquestion: str = ""
    query: str = ""
    # 要求に先行する、質問中で明示された対象・状況。要求の語だけの一致で覆域を確定しない。
    context_terms: tuple[str, ...] = ()


@dataclass
class CoverageUsage:
    """生成前と生成後の再検索で共有する使用量。設定の上限を段ごとにリセットしない。"""

    queries: int = 0
    subquestions: int = 0
    chunks: int = 0
    elapsed_seconds: float = 0.0
    searched_queries: list[str] = field(default_factory=list)

    def remaining(self, budget: CoverageBudget) -> CoverageBudget:
        return replace(
            budget,
            max_queries=max(0, budget.max_queries - self.queries),
            max_subquestions=max(0, budget.max_subquestions - self.subquestions),
            max_chunks=max(0, budget.max_chunks - self.chunks),
            deadline_seconds=max(0.0, budget.deadline_seconds - self.elapsed_seconds),
        )


def _normalize(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _content_terms(raw_terms: Iterable[str]) -> tuple[str, ...]:
    """検索の手がかりに使う語。2 文字以上で、部分の語が揃う複合語（「登録方法」）は部分に任せる。

    分かち書きは複合語とその部分の両方を返す。複合語のまま数えると、資料が「登録」「方法」を別々に書く
    ときに一致しないので、部分の語がある複合語は数えない。
    """
    terms = [t for t in dict.fromkeys(_normalize(term).strip() for term in raw_terms) if len(t) >= 2]
    candidates = set(terms) | {_normalize(t).strip() for t in raw_terms}
    return tuple(t for t in terms if not any(other != t and len(other) >= 2 and other in t for other in candidates))


def coverage_targets(question: str, tokenize: Callable[[str], Sequence[str]]) -> list[RequestTarget]:
    """完全な原質問の追跡行。語は検索の手がかりにだけ使用し、充足性を決めない。"""
    targets = []
    for unit in answer_requirements(question):
        try:
            terms = _content_terms(tokenize(unit["text"]))
        except Exception:  # noqa: BLE001 - 語の取得が失敗しても原質問は失わない。
            terms = ()
        targets.append(RequestTarget(id=unit["id"], kind=unit["kind"], text=unit["text"], terms=terms))
    return targets


def _record_key(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


@dataclass(frozen=True)
class TargetedRetrieval:
    """欠けた要求 1 件の再検索の結果。"""

    request_id: str
    queries: tuple[str, ...]
    parents: tuple[Any, ...]  # ContextParentEvidence（順位順）
    expansion_records: tuple[Any, ...] = ()
    elapsed_ms: int = 0
    # 失敗したときの例外の型の名前（例外の文は入れない。#3284）。
    error: str = ""
    # この要求に確保する根拠の数（小問）。None は欠けた要求の共有の枠（max_chunks）から取る。
    chunk_cap: int | None = None
    reused: bool = False


def select_new_parents(results: Sequence[TargetedRetrieval], existing_keys: Iterable[str], max_chunks: int) -> list[tuple[str, Any]]:
    """再検索の結果から、回答の材料に無い根拠（親）を要求ごとに順番に 1 件ずつ選ぶ。戻り値は (request_id, parent)。

    1 つの要求の結果が予算を使い切らないよう、各要求の最上位から交互に取る。`chunk_cap` のある要求（小問）は
    その件数まで確保し、無い要求（欠けた要求）は合わせて max_chunks 件までにする。
    """
    seen = set(existing_keys)
    queues = [[(result.request_id, parent) for parent in result.parents] for result in results]
    caps = [result.chunk_cap for result in results]
    taken = [0] * len(results)
    shared = 0
    selected: list[tuple[str, Any]] = []

    def open_(index: int) -> bool:
        cap = caps[index]
        return taken[index] < cap if cap is not None else shared < max_chunks

    progress = True
    while progress:
        progress = False
        for index, queue in enumerate(queues):
            if not open_(index):
                continue
            while queue:
                request_id, parent = queue.pop(0)
                key = _record_key(parent.record)
                if not key or key in seen:
                    continue
                seen.add(key)
                selected.append((request_id, parent))
                taken[index] += 1
                if caps[index] is None:
                    shared += 1
                progress = True
                break
    return selected


__all__ = [
    "CoverageBudget",
    "CoverageUsage",
    "RequestTarget",
    "TargetedRetrieval",
    "coverage_targets",
    "select_new_parents",
]
