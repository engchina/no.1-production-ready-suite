"""要求ごとの根拠の覆域と、欠けた要求だけの再検索（#1279、handoff §8.3 / §9）。

質問契約の要求単位（`task_contract.request_units`）ごとに、回答に渡す根拠（context の親と子の本文）が
要求の語をどれだけ含むかを決定的に判定する（覆域の行列）。モデル（LLM）は呼ばない。

- supported: 1 つの根拠が要求の語の `SUPPORTED_RATIO` 以上を含む。
- weak: 最良の根拠が `WEAK_RATIO` 以上・`SUPPORTED_RATIO` 未満を含む（再検索しない）。
- missing: どの根拠も `WEAK_RATIO` 未満しか含まない。
- unassessed: 要求から語を取り出せない（判定しない。再検索もしない）。

再検索は corrective / adaptive RAG の「欠けた所だけを狙って取り直す」に当たる。判定した要求が 2 つ以上あり、
missing の要求があるときだけ、missing の要求ごとに 1 回だけ、要求の文（＋用語の別名・業務ガイドの検索の
手がかり）で検索する。1 要求の質問は、最初の検索がすでにその要求の文で検索しているので再検索しない。
回数・足す根拠の数・時間は `CoverageBudget` で抑える。検索と並べ替え（rerank）は既存の hybrid 検索を使い、
足す根拠は既存の根拠を追い出さない（呼び出し側が併合する）。

lexical の判定は言い換え（資料が別の語で書く）を missing と見誤ることがある。その場合も再検索が 1 回
増えるだけで、回答の材料から既存の根拠は減らない。判定の閾値は評価セットで見直す。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from rag_engine.retrieval.task_contract import task_contract

COVERAGE_SCHEMA_VERSION = 1
# 根拠が要求の語のこの割合以上を含めば supported、WEAK_RATIO 以上なら weak。
SUPPORTED_RATIO = 0.6
WEAK_RATIO = 0.34
# 1 要求の再検索に足す手がかり（用語の別名・業務ガイドの検索の手がかり）の上限。
MAX_HINTS = 3
# 1 要求の記録に残す根拠の数。
MAX_EVIDENCE_PER_REQUEST = 3

CoverageStatus = Literal["supported", "weak", "missing", "unassessed"]

# 要求単位の文の中の「原文: …」以降は判定に使わない（親の要求の原文の写し）。
_FACET_TARGET = re.compile(r"対象「(.+?)」の(.+?)を個別に回答する")
_DEFINITION_TARGET = re.compile(r"項目「(.+?)」")


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


@dataclass(frozen=True)
class RequestTarget:
    """覆域を判定する要求 1 件。"""

    id: str
    kind: str
    text: str
    terms: tuple[str, ...]


def _normalize(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _focus_text(unit: Mapping[str, Any]) -> str:
    """要求単位から、判定と再検索に使う文を返す。対象にしない単位は空。"""
    kind = str(unit.get("kind") or "")
    text = " ".join(str(unit.get("text") or "").split())
    unit_id = str(unit.get("id") or "")
    if kind == "context":
        return ""
    if kind in {"request", "desired_outcome"}:
        return text
    if kind == "explicit_facet" and ".R" in unit_id:
        # 「AとBを追加」の対象ごとの子要求。親の文の判定では一方だけで足りてしまうので、対象ごとに判定する。
        match = _FACET_TARGET.search(text)
        return f"{match.group(1)}の{match.group(2)}" if match else ""
    if not kind and ".D" in unit_id:
        match = _DEFINITION_TARGET.search(text)
        return f"{match.group(1)}の意味" if match else ""
    # 「操作方法」「影響」などの論点の子要求は親の文と同じ語で判定が変わらないので、親の判定に任せる。
    return ""


def coverage_targets(question: str, tokenize: Callable[[str], Sequence[str]]) -> list[RequestTarget]:
    """質問契約の要求単位から、覆域を判定する要求を返す。語の集合が同じ要求は 1 つにまとめる。"""
    targets: list[RequestTarget] = []
    seen: set[tuple[str, ...]] = set()
    for unit in task_contract(question)["request_units"]:
        focus = _focus_text(unit)
        if not focus:
            continue
        try:
            raw_terms = tokenize(focus)
        except Exception:  # noqa: BLE001 - 分かち書きの失敗は判定しない（unassessed）にする。
            raw_terms = ()
        terms = tuple(dict.fromkeys(t for t in (_normalize(term).strip() for term in raw_terms) if len(t) >= 2))
        key = tuple(sorted(terms))
        if terms and key in seen:
            continue
        seen.add(key)
        targets.append(RequestTarget(id=str(unit.get("id") or ""), kind=str(unit.get("kind") or "definition"),
                                     text=focus, terms=terms))
    return targets


def _record_key(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


def evidence_texts(context: Any) -> list[tuple[str, str]]:
    """回答に渡す根拠ごとの (key, 正規化した本文)。親の本文に子の本文を足す。"""
    tree = list(getattr(context, "evidence_tree", ()) or ())
    if tree:
        return [(_record_key(parent.record),
                 _normalize("\n".join([str(getattr(parent.record, "text", "") or ""),
                                       *(str(getattr(child.record, "text", "") or "") for child in parent.children)])))
                for parent in tree]
    return [(_record_key(record), _normalize(getattr(record, "text", ""))) for record in getattr(context, "records", ()) or ()]


def assess_targets(targets: Sequence[RequestTarget], evidence: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """要求 × 根拠の覆域を判定する。根拠ごとの割合のうち最良のもので要求の状態を決める。"""
    rows: list[dict[str, Any]] = []
    for target in targets:
        if not target.terms:
            rows.append({"id": target.id, "kind": target.kind, "text": target.text, "terms": [],
                         "status": "unassessed", "best_ratio": 0.0, "matched_terms": [], "evidence_ids": []})
            continue
        scored: list[tuple[float, int, str, list[str]]] = []
        for position, (key, text) in enumerate(evidence):
            matched = [term for term in target.terms if term in text]
            if matched:
                scored.append((len(matched) / len(target.terms), position, key, matched))
        scored.sort(key=lambda item: (-item[0], item[1]))
        best = scored[0][0] if scored else 0.0
        status: CoverageStatus = ("supported" if best >= SUPPORTED_RATIO else "weak" if best >= WEAK_RATIO else "missing")
        rows.append({
            "id": target.id, "kind": target.kind, "text": target.text, "terms": list(target.terms),
            "status": status, "best_ratio": round(best, 3),
            "matched_terms": scored[0][3] if scored else [],
            "evidence_ids": [key for ratio, _, key, _ in scored if ratio >= WEAK_RATIO and key][:MAX_EVIDENCE_PER_REQUEST],
        })
    return rows


def coverage_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"supported": 0, "weak": 0, "missing": 0, "unassessed": 0}
    for row in rows:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    return counts


def re_retrieval_skip_reason(rows: Sequence[Mapping[str, Any]], budget: CoverageBudget) -> str:
    """再検索しない理由。空なら missing の要求を再検索する。"""
    if not budget.enabled:
        return "disabled"
    if budget.max_queries <= 0 or budget.max_chunks <= 0:
        return "no_budget"
    assessed = [row for row in rows if row["status"] != "unassessed"]
    if len(assessed) < 2:
        # 1 要求の質問は、最初の検索がその要求の文で検索している。同じ文での再検索は新しい根拠をほぼ返さない。
        return "single_request"
    if not any(row["status"] == "missing" for row in assessed):
        return "all_covered"
    return ""


def request_hints(target: RequestTarget, runtime_knowledge: Any, tokenize: Callable[[str], Sequence[str]]) -> list[str]:
    """要求の再検索に足す手がかり。要求の語を含む用語の別名と、要求の語を共有する規則（業務ガイド）の手がかり。"""
    if runtime_knowledge is None:
        return []
    focus = _normalize(target.text)
    terms = set(target.terms)
    hints: list[str] = []
    for term in getattr(runtime_knowledge, "matched_terms", ()) or ():
        labels = [str(label) for label in term.labels() if str(label).strip()]
        if any(_normalize(label) in focus for label in labels):
            hints.extend(label for label in labels if _normalize(label) not in focus)
    for rule in getattr(runtime_knowledge, "matched_rules", ()) or ():
        for trigger in getattr(rule, "triggers", ()) or ():
            trigger = str(trigger).strip()
            if not trigger or _normalize(trigger) in focus:
                continue
            try:
                trigger_terms = {_normalize(t).strip() for t in tokenize(trigger)}
            except Exception:  # noqa: BLE001 - 手がかりは補助。
                continue
            if trigger_terms & terms:
                hints.append(trigger)
    return list(dict.fromkeys(hints))[:MAX_HINTS]


def targeted_queries(target: RequestTarget, hints: Sequence[str]) -> tuple[str, ...]:
    """欠けた要求 1 件の検索文。要求の文を主軸にし、手がかりがあれば手がかり付きの文を足す。"""
    return tuple(dict.fromkeys(q for q in (target.text, " ".join([target.text, *hints]) if hints else "") if q))


@dataclass(frozen=True)
class TargetedRetrieval:
    """欠けた要求 1 件の再検索の結果。"""

    request_id: str
    queries: tuple[str, ...]
    parents: tuple[Any, ...]  # ContextParentEvidence（順位順）
    expansion_records: tuple[Any, ...] = ()
    elapsed_ms: int = 0
    error: str = ""


def run_targeted_retrievals(
    rows: Sequence[Mapping[str, Any]],
    targets: Sequence[RequestTarget],
    *,
    budget: CoverageBudget,
    retrieve: Callable[[str, tuple[str, ...]], Any],
    runtime_knowledge: Any = None,
    tokenize: Callable[[str], Sequence[str]],
    clock: Callable[[], float] = monotonic,
) -> tuple[list[TargetedRetrieval], str]:
    """missing の要求ごとに 1 回だけ検索する。戻り値は (結果, 打ち切りの理由)。

    retrieve(request_text, queries) は AnswerContext（evidence_tree と expansion_records を持つ）を返す。
    検索の失敗は要求ごとに記録し、回答は既存の根拠で続ける（失敗と「根拠が無い」を区別する。handoff §9 の 5）。
    """
    by_id = {target.id: target for target in targets}
    missing = [by_id[row["id"]] for row in rows if row["status"] == "missing" and row["id"] in by_id]
    results: list[TargetedRetrieval] = []
    stop = ""
    start = clock()
    for index, target in enumerate(missing):
        if index >= budget.max_queries:
            stop = "max_queries"
            break
        if clock() - start >= budget.deadline_seconds:
            stop = "deadline"
            break
        queries = targeted_queries(target, request_hints(target, runtime_knowledge, tokenize))
        began = clock()
        try:
            context = retrieve(target.text, queries)
        except Exception as exc:  # noqa: BLE001 - 再検索は補助。失敗しても既存の根拠で回答する。
            results.append(TargetedRetrieval(target.id, queries, (), (), int((clock() - began) * 1000),
                                             f"{type(exc).__name__}: {str(exc)[:200]}"))
            continue
        results.append(TargetedRetrieval(
            target.id, queries, tuple(getattr(context, "evidence_tree", ()) or ()),
            tuple(getattr(context, "expansion_records", ()) or ()), int((clock() - began) * 1000)))
    return results, stop


def select_new_parents(results: Sequence[TargetedRetrieval], existing_keys: Iterable[str],
                       max_chunks: int) -> list[tuple[str, Any]]:
    """再検索の結果から、回答の材料に無い根拠（親）を要求ごとに順番に 1 件ずつ選ぶ（合計 max_chunks まで）。

    1 つの要求の結果が予算を使い切らないよう、各要求の最上位から交互に取る。戻り値は (request_id, parent)。
    """
    seen = set(existing_keys)
    queues = [[(result.request_id, parent) for parent in result.parents] for result in results]
    selected: list[tuple[str, Any]] = []
    while len(selected) < max_chunks and any(queues):
        for queue in queues:
            while queue:
                request_id, parent = queue.pop(0)
                key = _record_key(parent.record)
                if not key or key in seen:
                    continue
                seen.add(key)
                selected.append((request_id, parent))
                break
            if len(selected) >= max_chunks:
                break
    return selected


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "CoverageBudget",
    "RequestTarget",
    "TargetedRetrieval",
    "assess_targets",
    "coverage_counts",
    "coverage_targets",
    "evidence_texts",
    "re_retrieval_skip_reason",
    "request_hints",
    "run_targeted_retrievals",
    "select_new_parents",
    "targeted_queries",
]
