"""送達単位 ID と既存の整数範囲を、履歴互換の引用契約へ変換する。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from types import GenericAlias
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, Field, StrictInt, create_model
from pydantic.json_schema import SkipJsonSchema

LINE_START_DESCRIPTION = (
    "根拠内でコードが付けた「1|」から始まる引用単位の開始番号。PDFの頁・物理行ではない。"
    "番号のない改行も同じ単位に含む。番号が表示されない根拠は null。"
)
LINE_END_DESCRIPTION = (
    "同じ根拠ブロックの引用単位の終了番号。開始番号以上かつ表示された最大番号以下。"
    "単一単位は開始と同じ値。番号なし根拠は開始・終了とも null。"
)

if TYPE_CHECKING:
    from rag_engine.models.llm import GroundedDraft, RequestCoverage, RequestCoverageRange, RequestLinkRange


@dataclass(frozen=True)
class WireRangeBinding:
    """指定範囲の構造上の結果。原文の意味支持を表さない。"""

    lines: str
    reason_code: (
        Literal["citation_range_missing", "citation_range_invalid", "citation_range_out_of_bounds", "citation_extraction_failed"] | None
    ) = None
    whole_text: bool = False


def bind_wire_range(line_start: int | None, line_end: int | None, *, unit_count: int, numbered: bool) -> WireRangeBinding:
    """明示した整数範囲だけを読む。本文から行を推測したり、範囲を切り詰めたりしない。"""
    if unit_count < 1:
        return WireRangeBinding("", "citation_extraction_failed")
    if line_start is None and line_end is None:
        return WireRangeBinding("", "citation_range_missing") if numbered else WireRangeBinding("", whole_text=True)
    if type(line_start) is not int or type(line_end) is not int:
        return WireRangeBinding("", "citation_range_invalid")
    lines = str(line_start) if line_start == line_end else f"{line_start}-{line_end}"
    if line_start < 1 or line_end < 1 or line_start > unit_count or line_end > unit_count:
        return WireRangeBinding(lines, "citation_range_out_of_bounds")
    if line_start > line_end:
        return WireRangeBinding(lines, "citation_range_invalid")
    if not numbered and unit_count == 1:
        # 番号なしで単位が 1 つなら (1,1) は null と同じ本文を指す。経路で扱いを分けないよう、
        # ここで全文の指定へそろえる（#3778）。0・逆順・範囲外は上で拒み、切り詰めない。
        return WireRangeBinding(lines, whole_text=True)
    return WireRangeBinding(lines)


@lru_cache(maxsize=1)
def generation_draft_wire_model() -> type[BaseModel]:
    """既存の項目・草稿のFieldInfoを保ち、生成時だけ引用を整数の契約へ置き換える。"""
    from rag_engine.generation.derivation_wire import GenerationItem
    from rag_engine.models.llm import (
        AnswerPart,
        GroundedApplication,
        GroundedCitation,
        GroundedDraft,
        GroundedItem,
        GuideRequirementCoverage,
        RequestCoverageRange,
        _StrictModel,
    )

    citation = create_model(
        "GroundedCitationWire",
        __base__=_StrictModel,
        evidence_id=(str, deepcopy(GroundedCitation.model_fields["evidence_id"])),
        line_start=(StrictInt | None, Field(description=LINE_START_DESCRIPTION)),
        line_end=(StrictInt | None, Field(description=LINE_END_DESCRIPTION)),
    )
    item_fields: dict[str, Any] = {
        "citations": (GenericAlias(list, citation), deepcopy(GroundedItem.model_fields["citations"])),
        "kind": (
            Literal["operation", "rule", "confirmation", "gap", "derived"],
            Field(
                description="原文の直接記載は operation / rule / confirmation、不足は gap。"
                "計算・条件適用・資料間の組み合わせによる結論は derived。"
            ),
        ),
    }
    item = create_model("GroundedItemWire", __base__=GenerationItem, **item_fields)
    part = create_model(
        "AnswerPartWire", __base__=AnswerPart, application_indexes=(SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True))
    )
    draft_fields: dict[str, Any] = {
        "answer_parts": (GenericAlias(list, part), deepcopy(GroundedDraft.model_fields["answer_parts"])),
        "summary": (
            str,
            Field(description="出力した items の text だけを1〜2文に要約する。新しい結論を加えず、参照を summary_item_indexes に書く。"),
        ),
        "summary_item_indexes": (
            GroundedDraft.model_fields["summary_item_indexes"].annotation,
            Field(
                default_factory=list,
                max_length=40,
                description="summary が要約する items の0始まりの index。直接記載・derived の両方を指せる。",
            ),
        ),
        "request_coverage": (GenericAlias(list, RequestCoverageRange), deepcopy(GroundedDraft.model_fields["request_coverage"])),
        "items": (GenericAlias(list, item), deepcopy(GroundedDraft.model_fields["items"])),
        "applications": (SkipJsonSchema[list[GroundedApplication]], Field(default_factory=list, exclude=True)),
        "summary_application_indexes": (SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True)),
        # 業務ガイドの要件が無い生成には用途が無い。schema に残すとモデルが埋めようとして空白の暴走の起点になる（#4236）。
        "guide_coverage": (SkipJsonSchema[list[GuideRequirementCoverage]], Field(default_factory=list, exclude=True)),
    }
    return create_model("GroundedDraftWire", __base__=GroundedDraft, **draft_fields)


@lru_cache(maxsize=1)
def guided_generation_wire_model() -> type[BaseModel]:
    """業務ガイドの要件を入力で渡す生成だけ、要件ごとの根拠の計画を出力に含める。"""
    from rag_engine.models.llm import GroundedDraft, GuideRequirementCoverage

    fields: dict[str, Any] = {
        "guide_coverage": (GenericAlias(list, GuideRequirementCoverage), deepcopy(GroundedDraft.model_fields["guide_coverage"]))
    }
    return create_model("GuidedGroundedDraftWire", __base__=generation_draft_wire_model(), **fields)


@lru_cache(maxsize=2)
def planned_generation_wire_model(*, guided: bool = False) -> type[BaseModel]:
    """根拠計画を入力で受け取る生成では、その計画を出力 schema から除く。"""
    from rag_engine.models.llm import RequestCoverageRange

    return create_model(
        "PlannedGroundedDraftWire",
        __base__=guided_generation_wire_model() if guided else generation_draft_wire_model(),
        request_coverage=(SkipJsonSchema[list[RequestCoverageRange]], Field(default_factory=list, exclude=True)),
    )


def canonical_request_coverage(
    rows: Sequence[RequestCoverageRange], link_text: Callable[[RequestLinkRange], str], *, unit_catalog: Any = None
) -> list[RequestCoverage]:
    """番号で返した計画を、下流の検査と保存が読む形へ変える。

    linked の原文（link_quote）は ``link_text`` が番号の範囲から切り出す（無効な範囲は空文字で、計画の検査の指摘に任せる）。
    計画に使う根拠（evidence_ids）は direct と linked の行そのものなので、コードが導く。
    """
    from rag_engine.models.llm import RequestCoverage

    def resolve_link(link: Any) -> str:
        if hasattr(link, "unit_ids"):
            if unit_catalog is None:
                raise ValueError("送達した引用単位の対応がありません。")
            return "\n".join(binding.text.strip() for binding in unit_catalog.bind(link.unit_ids))
        return link_text(link)

    result = []
    for row in rows:
        result.append(
            RequestCoverage.model_validate(
                {
                    "request_id": row.request_id,
                    "evidence_scope": [
                        {
                            "evidence_id": scope.evidence_id,
                            "target": scope.target,
                            "relation": scope.relation,
                            "link_quote": resolve_link(scope.link) if scope.relation == "linked" and scope.link is not None else "",
                        }
                        for scope in row.evidence_scope
                    ],
                    "evidence_ids": list(dict.fromkeys(scope.evidence_id for scope in row.evidence_scope if scope.relation != "unrelated")),
                }
            )
        )
    return result


def canonical_generation_draft(
    value: BaseModel, link_text: Callable[[RequestLinkRange], str] = lambda _link: "", *, unit_catalog: Any = None
) -> GroundedDraft:
    """新wireは整数の指定を私有属性へ残す。既存の草稿・履歴のlinesは変更しない。

    ``link_text`` は計画の linked の番号の範囲から原文を切り出す（呼び出し側が送った根拠を知っている）。
    """
    from rag_engine.models.llm import GroundedCitation, GroundedDraft

    if getattr(type(value), "_uses_unit_ids", False):
        if unit_catalog is None:
            raise ValueError("送達した引用単位の対応がありません。")
        raw = value.model_dump(exclude_unset=True)
        if "request_coverage" in raw:
            raw["request_coverage"] = [
                plan.model_dump(exclude_unset=True)
                for plan in canonical_request_coverage(cast(Any, value).request_coverage, link_text, unit_catalog=unit_catalog)
            ]
        bindings = []
        for item in raw.get("items", []):
            unit_bindings = [binding for citation in item.get("citations", []) for binding in unit_catalog.bind(citation["unit_ids"])]
            item["citations"] = [
                {
                    "evidence_id": binding.snapshot.span()["evidence_id"],
                    "lines": str(binding.first + 1) if binding.first == binding.last else f"{binding.first + 1}-{binding.last + 1}",
                }
                for binding in unit_bindings
            ]
            bindings.append(unit_bindings)
        from rag_engine.generation.derivation_wire import split_derivations

        raw, positions = split_derivations(raw)
        bindings = [bindings[index] for index in positions]
        draft = GroundedDraft.model_validate(raw)
        draft._citation_snapshots = unit_catalog.snapshots
        if isinstance(value, GroundedDraft):
            draft._provider_output_text = value._provider_output_text
            draft._collapsed_duplicates = dict(value._collapsed_duplicates)
        for item, unit_bindings in zip(draft.items, bindings, strict=True):
            for citation, binding in zip(item.citations, unit_bindings, strict=True):
                citation._wire_range = (binding.first + 1, binding.last + 1)
                citation._unit_binding = binding
        return draft

    line_limit = next((entry.max_length for entry in GroundedCitation.model_fields["lines"].metadata if hasattr(entry, "max_length")), None)
    wire = generation_draft_wire_model()
    if isinstance(value, GroundedDraft) and not isinstance(value, wire):
        return value
    if not isinstance(value, wire):
        value = wire.model_validate(value.model_dump())
    # 未指定の計画を「空の計画を明示した応答」へ変え、不要な計画の再生成を起こさない。
    raw = value.model_dump(exclude_unset=True)
    if "request_coverage" in raw:
        raw["request_coverage"] = [
            plan.model_dump(exclude_unset=True) for plan in canonical_request_coverage(cast(Any, value).request_coverage, link_text)
        ]
    ranges: list[list[tuple[int | None, int | None]]] = []
    for item in raw.get("items", []):
        own: list[tuple[int | None, int | None]] = []
        converted = []
        for citation in item.get("citations", []):
            start, end = citation["line_start"], citation["line_end"]
            own.append((start, end))
            # 不正範囲も消さず保持する。確定・失敗は実際の根拠の行数と照合して決める。
            lines = "" if start is None or end is None else str(start) if start == end else f"{start}-{end}"
            # 巨大な不正整数で旧文字列の長さ制限に当たり、正常な本文まで失わせない。
            # 元の値は _wire_range に無損失で残し、範囲の検査はその値を使う。
            if line_limit is not None and len(lines) > line_limit:
                lines = ""
            converted.append({"evidence_id": citation["evidence_id"], "lines": lines})
        ranges.append(own)
        if "citations" in item:
            item["citations"] = converted
    from rag_engine.generation.derivation_wire import split_derivations

    raw, positions = split_derivations(raw)
    ranges = [ranges[index] for index in positions]
    draft = GroundedDraft.model_validate(raw)
    if isinstance(value, GroundedDraft):
        draft._provider_output_text = value._provider_output_text
        # 応答の検証で寄せた重複の数（#3849）。寄せた後の値から作り直すので、ここで引き継ぐ。
        draft._collapsed_duplicates = dict(value._collapsed_duplicates)
    for item, own in zip(draft.items, ranges, strict=True):
        for citation, interval in zip(item.citations, own, strict=True):
            citation._wire_range = interval
    return draft
