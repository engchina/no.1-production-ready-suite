"""要求ごとの根拠の覆域と、欠けた要求だけの再検索（#1279、handoff §8.3 / §9）。

質問契約の要求単位（`task_contract.request_units`）ごとに、回答に渡す根拠（context の親と子の本文）が
要求の語をどれだけ含むかを決定的に判定する（覆域の行列）。モデル（LLM）は呼ばない。

- supported: 1 つの根拠が要求の語の `SUPPORTED_RATIO` 以上を含む。
- weak: 最良の根拠が `WEAK_RATIO` 以上・`SUPPORTED_RATIO` 未満を含む。
- missing: どの根拠も `WEAK_RATIO` 未満しか含まない。
- unassessed: 要求から語を取り出せない（判定しない。再検索もしない）。

再検索は corrective / adaptive RAG の「欠けた所だけを狙って取り直す」に当たる。missing と weak の要求ごとに
1 回だけ（missing を先に）、要求の文（＋用語の別名・業務ガイドの検索の手がかり）と、weak の要求では根拠に
現れなかった語だけの文で検索する。最初の検索と同じ検索文は使わない（同じ文の検索は新しい根拠をほぼ返さない）。
1 要求の質問は要求の文が最初の検索の文と同じになるので、手がかりの文か、根拠に現れなかった語の文があるときだけ
検索する（#2810）。作れる検索文が無ければ ``same_query`` として検索しない。
丸数字・(1) などで並べた小問（要求単位の `subquestion`。#2399）は、根拠が無いか一部だけの小問ごとに、前置き＋
小問の文で 1 回ずつ検索し、小問ごとに上位の根拠を `chunks_per_subquestion` 件まで確保する。
どの要求をどの順で何件まで検索するか（追加の検索の枠）は `plan_targeted_retrievals` の 1 か所で数え、
回数・足す根拠の数・時間は `CoverageBudget` で抑える。検索と並べ替え（rerank）は既存の hybrid 検索を使い、
足す根拠は既存の根拠を追い出さない（呼び出し側が併合する）。

lexical の判定は言い換え（資料が別の語で書く）を missing と見誤ることがある。その場合も再検索が 1 回
増えるだけで、回答の材料から既存の根拠は減らない。判定の閾値は評価セットで見直す。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Literal

from rag_engine.retrieval.task_contract import task_contract

COVERAGE_SCHEMA_VERSION = 1
# 根拠が要求の語のこの割合以上を含めば supported、WEAK_RATIO 以上なら weak。
SUPPORTED_RATIO = 0.6
WEAK_RATIO = 0.33
# 1 要求の再検索に足す手がかり（用語の別名・業務ガイドの検索の手がかり）の上限。
MAX_HINTS = 3
# 1 要求の記録に残す根拠の数。
MAX_EVIDENCE_PER_REQUEST = 3

CoverageStatus = Literal["supported", "weak", "missing", "unassessed"]

# 要求単位の文の中の「原文: …」以降は判定に使わない（親の要求の原文の写し）。
_FACET_TARGET = re.compile(r"対象「(.+?)」の(.+?)を個別に回答する")
_DEFINITION_TARGET = re.compile(r"項目「(.+?)」")
# 問い方の語。資料の本文に現れないことが普通で、覆域の判定に使うと根拠があっても割合が下がる。
_QUESTION_FORM_TERMS = frozenset(
    {
        "方法",
        "手順",
        "やり方",
        "仕方",
        "可否",
        "必要",
        "可能",
        "場合",
        "とき",
        "教え",
        "知り",
        "意味",
        "どう",
        "どこ",
        "なぜ",
        "いつ",
        "何",
        "について",
        "ください",
    }
)


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
    """覆域を判定する要求 1 件。"""

    id: str
    kind: str
    text: str
    terms: tuple[str, ...]
    # 小問の印（小問でなければ空）と、検索に使う文（空なら text。小問は前置き＋小問の文）。
    subquestion: str = ""
    query: str = ""


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


def _content_terms(raw_terms: Iterable[str]) -> tuple[str, ...]:
    """判定に使う語。2 文字以上で、問い方の語を除き、部分の語が揃う複合語（「登録方法」）は部分に任せる。

    分かち書きは複合語とその部分の両方を返す。複合語のまま数えると、資料が「登録」「方法」を別々に書く
    ときに一致しないので、部分の語がある複合語は数えない。
    """
    terms = [t for t in dict.fromkeys(_normalize(term).strip() for term in raw_terms) if len(t) >= 2 and t not in _QUESTION_FORM_TERMS]
    candidates = set(terms) | {_normalize(t).strip() for t in raw_terms}
    return tuple(t for t in terms if not any(other != t and len(other) >= 2 and other in t for other in candidates))


def coverage_targets(question: str, tokenize: Callable[[str], Sequence[str]]) -> list[RequestTarget]:
    """質問契約の要求単位から、覆域を判定する要求を返す。語の集合が同じ要求は 1 つにまとめる。"""
    targets: list[RequestTarget] = []
    seen: set[tuple[str, ...]] = set()
    for unit in task_contract(question)["request_units"]:
        focus = _focus_text(unit)
        if not focus:
            continue
        # 小問は印を除いた文で判定し、前置き（対象・状況）を付けた文で検索する（#2399）。
        label = str(unit.get("subquestion") or "")
        body = focus.removeprefix(label).strip() if label else focus
        try:
            raw_terms = tokenize(body)
        except Exception:  # noqa: BLE001 - 分かち書きの失敗は判定しない（unassessed）にする。
            raw_terms = ()
        terms = _content_terms(raw_terms)
        key = tuple(sorted(terms))
        if terms and key in seen:
            continue
        seen.add(key)
        targets.append(
            RequestTarget(
                id=str(unit.get("id") or ""),
                kind=str(unit.get("kind") or "definition"),
                text=focus,
                terms=terms,
                subquestion=label,
                query=" ".join(part for part in (str(unit.get("preamble") or ""), body) if part) if label else "",
            )
        )
    return targets


def _record_key(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


def evidence_texts(context: Any) -> list[tuple[str, str]]:
    """回答に渡す根拠ごとの (key, 正規化した本文)。親の本文に子の本文を足す。"""
    tree = list(getattr(context, "evidence_tree", ()) or ())
    if tree:
        return [
            (
                _record_key(parent.record),
                _normalize(
                    "\n".join(
                        [
                            str(getattr(parent.record, "text", "") or ""),
                            *(str(getattr(child.record, "text", "") or "") for child in parent.children),
                        ]
                    )
                ),
            )
            for parent in tree
        ]
    return [(_record_key(record), _normalize(getattr(record, "text", ""))) for record in getattr(context, "records", ()) or ()]


def assess_targets(targets: Sequence[RequestTarget], evidence: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """要求 × 根拠の覆域を判定する。根拠ごとの割合のうち最良のもので要求の状態を決める。"""
    rows: list[dict[str, Any]] = []
    for target in targets:
        if not target.terms:
            rows.append(
                {
                    "id": target.id,
                    "kind": target.kind,
                    "text": target.text,
                    "terms": [],
                    "status": "unassessed",
                    "best_ratio": 0.0,
                    "matched_terms": [],
                    "evidence_ids": [],
                }
            )
            continue
        scored: list[tuple[float, int, str, list[str]]] = []
        for position, (key, text) in enumerate(evidence):
            matched = [term for term in target.terms if term in text]
            if matched:
                scored.append((len(matched) / len(target.terms), position, key, matched))
        scored.sort(key=lambda item: (-item[0], item[1]))
        best = scored[0][0] if scored else 0.0
        status: CoverageStatus = "supported" if best >= SUPPORTED_RATIO else "weak" if best >= WEAK_RATIO else "missing"
        rows.append(
            {
                "id": target.id,
                "kind": target.kind,
                "text": target.text,
                "terms": list(target.terms),
                "status": status,
                "best_ratio": round(best, 3),
                "matched_terms": scored[0][3] if scored else [],
                "evidence_ids": [key for ratio, _, key, _ in scored if ratio >= WEAK_RATIO and key][:MAX_EVIDENCE_PER_REQUEST],
            }
        )
    for row, target in zip(rows, targets, strict=True):
        if target.subquestion:
            row["subquestion"] = target.subquestion
    return rows


def coverage_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"supported": 0, "weak": 0, "missing": 0, "unassessed": 0}
    for row in rows:
        counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
    return counts


def _needs_search(row: Mapping[str, Any]) -> bool:
    """再検索の対象か。根拠が無い（missing）か一部だけ（weak）の要求（小問は #2399、通常の要求は #2810）。"""
    return row["status"] in {"missing", "weak"}


def _unmatched_terms(row: Mapping[str, Any]) -> tuple[str, ...]:
    """weak の要求で、根拠に現れなかった語（不足の論点）。missing と supported は空。"""
    if row.get("status") != "weak":
        return ()
    matched = set(row.get("matched_terms") or ())
    return tuple(term for term in row.get("terms") or () if term not in matched)


def _query_key(text: str) -> str:
    return re.sub(r"[\s。、．，,.?？!！]+", "", _normalize(text))


def within_extra_budget(rows: Sequence[Mapping[str, Any]], budget: CoverageBudget, remaining: int) -> CoverageBudget:
    """小問ごとの検索と再検索の回数を、追加の検索の枠の残り `remaining`（#2395）の中に収めた予算（小問が先）。"""
    wanted = sum(1 for row in rows if row.get("subquestion") and _needs_search(row))
    subquestions = max(0, min(budget.max_subquestions, wanted, remaining))
    return replace(budget, max_subquestions=subquestions, max_queries=max(0, min(budget.max_queries, remaining - subquestions)))


def re_retrieval_skip_reason(rows: Sequence[Mapping[str, Any]], budget: CoverageBudget) -> str:
    """再検索しない理由。空なら再検索する（根拠の無い要求と、根拠が無いか一部だけの小問）。"""
    if not budget.enabled:
        return "disabled"
    wanted = [row for row in rows if _needs_search(row)]
    if not wanted:
        return "all_covered"
    subquestion_budget = budget.max_subquestions > 0 and budget.chunks_per_subquestion > 0
    request_budget = budget.max_queries > 0 and budget.max_chunks > 0
    if not any(subquestion_budget if row.get("subquestion") else request_budget for row in wanted):
        return "no_budget"
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


def targeted_queries(
    target: RequestTarget, hints: Sequence[str], *, unmatched: Sequence[str] = (), searched: Iterable[str] = ()
) -> tuple[str, ...]:
    """欠けた要求 1 件の検索文。要求の文（小問は前置き＋小問の文）を主軸にし、手がかりがあれば手がかり付きの文を、
    根拠に現れなかった語（``unmatched``）があればその語だけの文を足す。最初の検索の文（``searched``）と同じ文は除く。"""
    text = target.query or target.text
    candidates = (text, " ".join([text, *hints]) if hints else "", " ".join(unmatched))
    done = {_query_key(query) for query in searched}
    queries: dict[str, str] = {}
    for query in candidates:
        key = _query_key(query)
        if key and key not in done:
            queries.setdefault(key, query)
    return tuple(queries.values())


@dataclass(frozen=True)
class PlannedRetrieval:
    """追加の検索 1 回の計画。chunk_cap はこの検索で確保する根拠の数（None は欠けた要求の共有の枠）。"""

    target: RequestTarget
    chunk_cap: int | None = None


def plan_targeted_retrievals(
    rows: Sequence[Mapping[str, Any]], targets: Sequence[RequestTarget], budget: CoverageBudget
) -> tuple[list[PlannedRetrieval], str]:
    """追加の検索の枠を 1 か所で数える。戻り値は (検索する順の計画, 枠で打ち切った理由)。

    小問（根拠が無いか一部）を質問の順に `max_subquestions` 件まで、それぞれ `chunks_per_subquestion` 件を確保して
    先に検索し、次に根拠の無い要求を `max_queries` 件まで検索する（足す根拠は合計 `max_chunks` 件を交互に）。
    要求単位ごとの別の検索を足すときも、この計画に足して同じ期限と根拠の選び方（`select_new_parents`）で数える。
    """
    by_id = {target.id: target for target in targets}
    wanted = [by_id[row["id"]] for row in rows if row["id"] in by_id and _needs_search(row)]
    status = {row["id"]: row["status"] for row in rows}
    subquestions = [target for target in wanted if target.subquestion]
    # 根拠の無い要求を、一部だけの要求より先に検索する（枠が足りないときは missing を優先）。
    requests = sorted((target for target in wanted if not target.subquestion), key=lambda target: status[target.id] != "missing")
    plan: list[PlannedRetrieval] = []
    stop = ""
    if budget.chunks_per_subquestion > 0:
        plan.extend(PlannedRetrieval(target, budget.chunks_per_subquestion) for target in subquestions[: budget.max_subquestions])
        if len(subquestions) > budget.max_subquestions:
            stop = "max_subquestions"
    if budget.max_chunks > 0:
        plan.extend(PlannedRetrieval(target) for target in requests[: budget.max_queries])
        if len(requests) > budget.max_queries:
            stop = stop or "max_queries"
    return plan, stop


@dataclass(frozen=True)
class TargetedRetrieval:
    """欠けた要求 1 件の再検索の結果。"""

    request_id: str
    queries: tuple[str, ...]
    parents: tuple[Any, ...]  # ContextParentEvidence（順位順）
    expansion_records: tuple[Any, ...] = ()
    elapsed_ms: int = 0
    error: str = ""
    # この要求に確保する根拠の数（小問）。None は欠けた要求の共有の枠（max_chunks）から取る。
    chunk_cap: int | None = None


def run_targeted_retrievals(
    rows: Sequence[Mapping[str, Any]],
    targets: Sequence[RequestTarget],
    *,
    budget: CoverageBudget,
    retrieve: Callable[[str, tuple[str, ...]], Any],
    runtime_knowledge: Any = None,
    tokenize: Callable[[str], Sequence[str]],
    searched_queries: Sequence[str] = (),
    clock: Callable[[], float] = monotonic,
) -> tuple[list[TargetedRetrieval], str]:
    """計画（`plan_targeted_retrievals`）の要求ごとに 1 回だけ検索する。戻り値は (結果, 打ち切りの理由)。

    最初の検索の文（``searched_queries``）と違う検索文を作れない要求は検索しない。どの要求も作れなければ
    打ち切りの理由を ``same_query`` にする（#2810）。

    retrieve(request_text, queries) は AnswerContext（evidence_tree と expansion_records を持つ）を返す。
    検索の失敗は要求ごとに記録し、回答は既存の根拠で続ける（失敗と「根拠が無い」を区別する。handoff §9 の 5）。
    """
    plan, stop = plan_targeted_retrievals(rows, targets, budget)
    by_id = {row["id"]: row for row in rows}
    results: list[TargetedRetrieval] = []
    start = clock()
    same_query = 0
    for planned in plan:
        target = planned.target
        if clock() - start >= budget.deadline_seconds:
            stop = "deadline"
            break
        queries = targeted_queries(
            target,
            request_hints(target, runtime_knowledge, tokenize),
            unmatched=_unmatched_terms(by_id.get(target.id, {})),
            searched=searched_queries,
        )
        if not queries:
            same_query += 1
            continue
        began = clock()
        try:
            context = retrieve(target.query or target.text, queries)
        except Exception as exc:  # noqa: BLE001 - 再検索は補助。失敗しても既存の根拠で回答する。
            results.append(
                TargetedRetrieval(
                    target.id,
                    queries,
                    (),
                    (),
                    int((clock() - began) * 1000),
                    f"{type(exc).__name__}: {str(exc)[:200]}",
                    planned.chunk_cap,
                )
            )
            continue
        results.append(
            TargetedRetrieval(
                target.id,
                queries,
                tuple(getattr(context, "evidence_tree", ()) or ()),
                tuple(getattr(context, "expansion_records", ()) or ()),
                int((clock() - began) * 1000),
                chunk_cap=planned.chunk_cap,
            )
        )
    if plan and same_query == len(plan):
        stop = stop or "same_query"
    return results, stop


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
    "COVERAGE_SCHEMA_VERSION",
    "within_extra_budget",
    "CoverageBudget",
    "PlannedRetrieval",
    "RequestTarget",
    "TargetedRetrieval",
    "assess_targets",
    "coverage_counts",
    "coverage_targets",
    "evidence_texts",
    "plan_targeted_retrievals",
    "re_retrieval_skip_reason",
    "request_hints",
    "run_targeted_retrievals",
    "select_new_parents",
    "targeted_queries",
]
