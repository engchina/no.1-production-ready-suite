"""要点と結果を同じ返却単位で受け、内部の参照はコードで構成する。"""

from __future__ import annotations

from copy import deepcopy
from types import GenericAlias
from typing import Annotated, Any, Literal, Union, cast

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import SkipJsonSchema

from rag_engine.generation.citation_locator import EVIDENCE_LABEL, clean_evidence_locators
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog, _unit_coverage_type
from rag_engine.models.llm import MAX_ANSWER_PARTS, GroundedCitation, GroundedDraft


def _answer_before_coverage(schema: dict[str, Any]) -> None:
    """要点の原文選択・方法・結果を、回答全体の関係記録より先に生成する。"""
    properties = schema.get("properties", {})
    if "request_coverage" in properties:
        properties["request_coverage"] = properties.pop("request_coverage")
    if "required" in schema:
        schema["required"] = [name for name in properties if name in schema["required"]]


def answer_parts_model(base: type[BaseModel], catalog: SentUnitCatalog) -> type[BaseModel]:
    """原文の選択と方法・結果を要点ごとに組にし、根拠不足はgapで受ける。"""
    source = catalog.selection_model()
    derived = create_model(
        "SupportedAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        kind=(Literal["answer"], Field(description="引用の根拠に基づく答え。")),
        basis=(source, Field(description="導出に使う資料の事実・規則の原文。")),
        reasoning=(str, Field(min_length=1, max_length=600, description="依存する原文と既知値から導く方法。計算は算式で表す。")),
        result=(str, Field(min_length=1, max_length=600, description="reasoning から導いた、この要点への最終的な答え。途中の値ではない。")),
    )
    gap = create_model(
        "GapAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        kind=(Literal["gap"], Field()),
        text=(str, Field(min_length=1, max_length=600, description="必要な資料・値・前提のうち不足するもの。")),
    )
    part = create_model(
        "RequiredAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        request_id=(str, Field(description="元の request_units の id。背景は答えない。")),
        requirement=(
            str,
            Field(
                min_length=1, max_length=600, description="この要点で答える質問の要求。必要な粒度で複合要求を分け、任意補足を必須にしない。"
            ),
        ),
        answer=(Union.__getitem__((derived, gap)) if catalog.unit_ids else gap, Field()),
    )
    fields: dict[str, Any] = {
        "answer_parts": (GenericAlias(list, part), Field(min_length=1, max_length=MAX_ANSWER_PARTS)),
        "summary_part_indexes": (
            list[Annotated[int, Field(strict=True, ge=0, lt=MAX_ANSWER_PARTS)]],
            Field(default_factory=list, max_length=2, description="冒頭に示す要点のindexを最大2つ選ぶ。別の結論は書かない。"),
        ),
        "items": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        "applications": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        "summary": (SkipJsonSchema[str], Field(default="", exclude=True)),
        "summary_item_indexes": (SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True)),
        "summary_application_indexes": (SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True)),
        "request_coverage": (GenericAlias(list, _unit_coverage_type(catalog)), deepcopy(base.model_fields["request_coverage"])),
    }
    if base.model_fields["request_coverage"].exclude:
        fields["request_coverage"] = (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True))
    model = create_model(
        base.__name__,
        __base__=base,
        __config__=ConfigDict(json_schema_extra=_answer_before_coverage),
        **fields,
    )
    cast(Any, model)._uses_typed_answer_parts = True
    cast(Any, model)._uses_unit_ids = True
    return model


def _without_bound_locators(text: str, selected: list[BoundUnitRange]) -> str:
    """実際に選んだ出典と一致する位置だけの括弧を、既存の出典表示へ一本化する。"""
    citations = [
        GroundedCitation(evidence_id=unit.snapshot.span().get("label") or unit.snapshot.span()["evidence_id"]) for unit in selected
    ]
    source_text = "\n".join(unit.text for unit in selected)
    cleaned = clean_evidence_locators(text, codes=set(EVIDENCE_LABEL.findall(source_text)), citations=citations, source_text=source_text)
    if cleaned.residual or not set(cleaned.labels).issubset({citation.evidence_id for citation in citations}):
        return text
    return cleaned.text


def canonical_answer_parts(
    value: BaseModel, catalog: SentUnitCatalog, coverage: list[dict[str, Any]], *, question: str | None = None
) -> GroundedDraft:
    """選択原文とinlineの導出から、既存APIの草稿と全indexを生成する。"""
    raw = value.model_dump(exclude_unset=True)
    wire_parts = raw.pop("answer_parts", [])
    summary_indexes = raw.pop("summary_part_indexes", [])
    items: list[dict[str, Any]] = []
    bindings: list[BoundUnitRange | None] = []
    source_indexes: dict[tuple[str, str, int, int], int] = {}
    applications: list[dict[str, Any]] = []
    parts = []
    texts = []
    part_sources = []
    part_applications = []
    for position, part in enumerate(wire_parts):
        answer = part["answer"]
        kind = answer["kind"]
        selected = [] if kind == "gap" else list(catalog.bind(answer["basis"]["unit_ids"]))
        indexes = []
        for binding in selected:
            key = (part["request_id"], binding.snapshot.snapshot_id, binding.first, binding.last)
            if key not in source_indexes:
                index = len(items)
                source_indexes[key] = index
                items.append(
                    {
                        "kind": kind if kind != "answer" else "rule",
                        "text": binding.text,
                        "request_id": part["request_id"],
                        "citations": [
                            {
                                "evidence_id": binding.snapshot.span()["evidence_id"],
                                "lines": str(binding.first + 1)
                                if binding.first == binding.last
                                else f"{binding.first + 1}-{binding.last + 1}",
                            }
                        ],
                    }
                )
                bindings.append(binding)
            indexes.append(source_indexes[key])
        app_indexes = []
        if kind == "answer":
            app_indexes = [len(applications)]
            applications.append(
                {
                    "request_id": part["request_id"],
                    "premises": [],
                    "item_indexes": indexes,
                    "reasoning": _without_bound_locators(answer["reasoning"], selected),
                    "result": _without_bound_locators(answer["result"], selected),
                }
            )
            text = applications[-1]["result"]
        elif kind == "gap":
            indexes = [len(items)]
            items.append({"kind": "gap", "request_id": part["request_id"], "text": answer["text"], "citations": []})
            bindings.append(None)
            text = answer["text"]
        else:
            text = "\n".join(items[index]["text"] for index in indexes)
        parts.append(
            {
                "id": f"P{position + 1}",
                "request_id": part["request_id"],
                "text": part["requirement"],
                "result_kind": "derived" if kind == "answer" else "gap",
                "item_indexes": [] if kind == "answer" else indexes,
                "application_indexes": app_indexes,
            }
        )
        texts.append(text)
        part_sources.append(indexes if kind != "answer" else [])
        part_applications.append(app_indexes)
    chosen = list(dict.fromkeys(index for index in summary_indexes if 0 <= index < len(parts) and parts[index]["result_kind"] != "gap"))
    raw.update(
        items=items,
        applications=applications,
        answer_parts=parts,
        request_coverage=coverage,
        summary="\n".join(texts[index] for index in chosen),
        summary_item_indexes=list(dict.fromkeys(source for index in chosen for source in part_sources[index])),
        summary_application_indexes=list(dict.fromkeys(app for index in chosen for app in part_applications[index])),
    )
    draft = GroundedDraft.model_validate(raw)
    if isinstance(value, GroundedDraft):
        draft._provider_output_text = value._provider_output_text
        draft._collapsed_duplicates = dict(value._collapsed_duplicates)
    draft._citation_snapshots = catalog.snapshots
    draft._composed_summary = True
    for item, source_binding in zip(draft.items, bindings, strict=True):
        if source_binding is not None:
            item.citations[0]._unit_binding = source_binding
            item.citations[0]._wire_range = (source_binding.first + 1, source_binding.last + 1)
    return draft
