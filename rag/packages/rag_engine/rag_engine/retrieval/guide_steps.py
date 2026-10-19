"""業務ガイドの手順ごとの検索と、手順が参照する資料の頁の固定（#2395。静的な多段）。

照合した業務ガイドの手順（分かっている条件で外れた手順は呼び出し側が除く）ごとに検索し、手順の根拠を
回答の材料に確保する。手順 A の後に要る手順 B のように、質問の文だけでは上位に出ない手順の根拠を取るため。

- 検索文は「質問 + 手順の検索の手がかり」（手がかりが無ければ手順の題名）。
- 手順が資料と頁（`references`）を持てば、その頁に絞った検索も行い、当たった根拠は検索の順位に関係なく
  回答の材料に入れる（`reference_max_chunks` まで）。
- 回数は追加の検索の枠（`extra_retrieval.ExtraRetrievalBudget`）から取り、手順ごとの検索の上限を超えない。
  まず各手順に 1 回（参照があれば頁に絞った検索）を割り当て、残りで参照のある手順の絞らない検索をする。

検索と並べ替えは既存の hybrid 検索を使い、モデル（LLM）は呼ばない。足す根拠は既存の根拠を追い出さない
（呼び出し側が併合する）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Literal

GUIDE_STEP_TRACE_SCHEMA_VERSION = 1
# 1 手順の検索文に足す手がかりの上限。
MAX_STEP_HINTS = 5

SearchKind = Literal["step", "reference"]


@dataclass(frozen=True)
class GuidePageRange:
    page_start: int
    page_end: int

    def to_payload(self) -> dict[str, int]:
        return {"page_start": self.page_start, "page_end": self.page_end}


@dataclass(frozen=True)
class GuideStepReference:
    document_id: str
    title: str = ""
    # 空なら文書の全体。
    page_ranges: tuple[GuidePageRange, ...] = ()

    def filters(self) -> list[dict[str, Any]]:
        """検索の頁の範囲の絞り込み（`page_ranges` の形。頁が無ければ文書の全体）。"""
        if not self.page_ranges:
            return [{"document_id": self.document_id}]
        return [{"document_id": self.document_id, **item.to_payload()} for item in self.page_ranges]

    def to_payload(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "page_ranges": [item.to_payload() for item in self.page_ranges],
        }


@dataclass(frozen=True)
class GuideStep:
    step_id: str
    title: str
    retrieval_hints: tuple[str, ...] = ()
    references: tuple[GuideStepReference, ...] = ()
    # 回答の充足で必須の手順か（条件が決まらない分岐の行き先とその後続は False。#2491）。
    required: bool = True

    def hints(self) -> tuple[str, ...]:
        return self.retrieval_hints[:MAX_STEP_HINTS] or ((self.title,) if self.title else ())

    def page_filters(self) -> list[dict[str, Any]]:
        return [item for reference in self.references for item in reference.filters()]


@dataclass(frozen=True)
class GuidePlan:
    """回答に使う業務ガイドの手順（分岐で残った手順だけ）。"""

    guide_id: str
    revision: int
    steps: tuple[GuideStep, ...] = ()

    @property
    def optional_step_ids(self) -> tuple[str, ...]:
        """回答の充足で必須に数えない手順の id（#2491）。"""
        return tuple(step.step_id for step in self.steps if not step.required)

    @classmethod
    def from_payload(cls, guide_id: str, revision: int, steps: Iterable[Mapping[str, Any]]) -> GuidePlan | None:
        """呼び出し側の手順の一覧（id・title・required・retrieval_hints・references）から作る。手順が無ければ None。"""
        parsed: list[GuideStep] = []
        for raw in steps:
            if not isinstance(raw, Mapping) or not str(raw.get("id") or "").strip():
                continue
            references = tuple(
                GuideStepReference(
                    document_id=str(item.get("document_id")),
                    title=str(item.get("title") or ""),
                    page_ranges=tuple(
                        GuidePageRange(int(r["page_start"]), int(r.get("page_end") or r["page_start"]))
                        for r in item.get("page_ranges") or ()
                        if isinstance(r, Mapping) and r.get("page_start")
                    ),
                )
                for item in raw.get("references") or ()
                if isinstance(item, Mapping) and str(item.get("document_id") or "").strip()
            )
            parsed.append(
                GuideStep(
                    step_id=str(raw["id"]).strip(),
                    title=str(raw.get("title") or "").strip(),
                    retrieval_hints=tuple(str(h).strip() for h in raw.get("retrieval_hints") or () if str(h).strip()),
                    references=references,
                    required=raw.get("required") is not False,
                )
            )
        return cls(guide_id=guide_id, revision=int(revision), steps=tuple(parsed)) if parsed else None


@dataclass(frozen=True)
class StepSearch:
    """手順 1 件の検索 1 回の結果。"""

    step_id: str
    kind: SearchKind
    text: str
    queries: tuple[str, ...]
    parents: tuple[Any, ...]  # ContextParentEvidence（順位順）
    expansion_records: tuple[Any, ...] = ()
    elapsed_ms: int = 0
    error: str = ""


def step_queries(question: str, step: GuideStep) -> tuple[str, tuple[str, ...]]:
    """手順の検索の (主の検索文, 検索文の列)。主は「質問 + 手がかり」、手がかりだけの文も足す。"""
    hints = " ".join(step.hints())
    text = " ".join(part for part in (question.strip(), hints) if part)
    return text, tuple(dict.fromkeys(q for q in (text, hints) if q))


def planned_searches(plan: GuidePlan, limit: int) -> list[tuple[GuideStep, SearchKind]]:
    """検索する (手順, 種類) を順に ``limit`` 件まで。

    各手順に 1 回（参照があれば頁に絞った検索、無ければ絞らない検索）を先に割り当て、残りで参照のある手順の
    絞らない検索をする（後ろの手順の検索が、前の手順の 2 回目に押し出されないようにする）。
    """
    first: list[tuple[GuideStep, SearchKind]] = [(step, "reference" if step.references else "step") for step in plan.steps]
    second: list[tuple[GuideStep, SearchKind]] = [(step, "step") for step in plan.steps if step.references]
    return [*first, *second][: max(0, limit)]


def run_step_searches(
    question: str,
    plan: GuidePlan,
    *,
    limit: int,
    deadline_seconds: float,
    retrieve: Callable[[str, tuple[str, ...], list[dict[str, Any]]], Any],
    clock: Callable[[], float] = monotonic,
) -> tuple[list[StepSearch], str]:
    """手順ごとに検索する。戻り値は (結果, 打ち切りの理由)。

    retrieve(text, queries, page_ranges) は AnswerContext（evidence_tree と expansion_records を持つ）を返す。
    page_ranges が空なら絞らない検索。検索の失敗は手順ごとに記録し、回答は既存の根拠で続ける。
    """
    searches = planned_searches(plan, limit)
    wanted = len(plan.steps) + sum(1 for step in plan.steps if step.references)
    stop = "max_queries" if len(searches) < wanted else ""
    results: list[StepSearch] = []
    start = clock()
    for step, kind in searches:
        if clock() - start >= deadline_seconds:
            stop = "deadline"
            break
        text, queries = step_queries(question, step)
        page_ranges = step.page_filters() if kind == "reference" else []
        began = clock()
        try:
            context = retrieve(text, queries, page_ranges)
        except Exception as exc:  # noqa: BLE001 - 手順の検索は補助。失敗しても既存の根拠で回答する。
            results.append(
                StepSearch(
                    step.step_id, kind, text, queries, (), (), int((clock() - began) * 1000), f"{type(exc).__name__}: {str(exc)[:200]}"
                )
            )
            continue
        results.append(
            StepSearch(
                step.step_id,
                kind,
                text,
                queries,
                tuple(getattr(context, "evidence_tree", ()) or ()),
                tuple(getattr(context, "expansion_records", ()) or ()),
                int((clock() - began) * 1000),
            )
        )
    return results, stop


def _key(parent: Any) -> str:
    record = getattr(parent, "record", None)
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


@dataclass(frozen=True)
class StepSelection:
    """手順の検索の結果から選んだ根拠。"""

    # (step_id, 種類, parent)。回答の材料に無く、足す根拠。
    added: tuple[tuple[str, SearchKind, Any], ...]
    # 手順 id → その手順の根拠の key（既存の根拠のうち手順の検索で当たったものと、足した根拠）。
    step_sources: Mapping[str, tuple[str, ...]]


def select_step_parents(
    results: Sequence[StepSearch],
    existing_keys: Iterable[str],
    *,
    chunks_per_step: int,
    reference_max_chunks: int,
) -> StepSelection:
    """手順ごとの検索の結果から、手順の根拠を選ぶ。

    - 絞らない検索: 各検索の上位 ``chunks_per_step`` 件を手順の根拠にする（既にある根拠はそのまま手順に紐づけ、
      無い根拠を足す）。
    - 頁に絞った検索: 参照の頁の根拠は順位に関係なく入れる。各検索の上位から手順の間で交互に、合計
      ``reference_max_chunks`` 件まで（既にある根拠は数えずに手順に紐づける）。
    """
    existing = set(existing_keys)
    seen = set(existing)
    added: list[tuple[str, SearchKind, Any]] = []
    sources: dict[str, list[str]] = {}

    def link(step_id: str, key: str) -> None:
        keys = sources.setdefault(step_id, [])
        if key not in keys:
            keys.append(key)

    per_step = max(0, int(chunks_per_step))
    for result in results:
        if result.kind != "step":
            continue
        taken = 0
        for parent in result.parents:
            if taken >= per_step:
                break
            key = _key(parent)
            if not key:
                continue
            taken += 1
            link(result.step_id, key)
            if key not in seen:
                seen.add(key)
                added.append((result.step_id, "step", parent))

    queues = [list(result.parents) for result in results if result.kind == "reference"]
    owners = [result.step_id for result in results if result.kind == "reference"]
    pinned = 0
    limit = max(0, int(reference_max_chunks))
    while pinned < limit and any(queues):
        for index, queue in enumerate(queues):
            while queue:
                parent = queue.pop(0)
                key = _key(parent)
                if not key:
                    continue
                link(owners[index], key)
                if key in seen:
                    continue
                seen.add(key)
                added.append((owners[index], "reference", parent))
                pinned += 1
                break
            if pinned >= limit:
                break
    return StepSelection(added=tuple(added), step_sources={step_id: tuple(keys) for step_id, keys in sources.items()})


__all__ = [
    "GUIDE_STEP_TRACE_SCHEMA_VERSION",
    "GuidePageRange",
    "GuidePlan",
    "GuideStep",
    "GuideStepReference",
    "StepSearch",
    "StepSelection",
    "planned_searches",
    "run_step_searches",
    "select_step_parents",
    "step_queries",
]
