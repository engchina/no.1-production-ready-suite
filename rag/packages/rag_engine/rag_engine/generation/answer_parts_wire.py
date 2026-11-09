"""要点と結果を同じ返却単位で受け、内部の参照はコードで構成する。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from types import GenericAlias
from typing import Annotated, Any, Literal, Union, cast

from annotated_types import MaxLen
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import SkipJsonSchema

from rag_engine.generation.citation_content import structure_only_quote
from rag_engine.generation.citation_locator import EVIDENCE_LABEL, clean_evidence_locators
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog
from rag_engine.models.llm import MAX_ANSWER_PARTS, AnswerPart, GroundedCitation, GroundedDraft

# 質問の種類（question_type）の閉じた語彙（#4586）。導出（計算・区分・条件の当てはめ）が要る種類と、原文の記載で
# 答えられる種類を分ける。種類の判定はモデルの構造化出力だけが行い、コードは値の集合を比べるだけで語の意味を読まない。
DERIVATION_QUESTION_TYPES = ("calculation", "classification", "condition_check")
STATED_QUESTION_TYPES = ("fact", "definition", "procedure", "comparison", "other")
QUESTION_TYPE_DESCRIPTION = (
    "質問が求める答えの種類（複数可）。calculation=値の計算・集計、classification=値や条件から区分・等級を決める、"
    "condition_check=質問の事実を規則の条件に当てはめて可否・該当を決める、fact=資料に書かれた事実・値、"
    "definition=語・項目の意味、procedure=手順・操作、comparison=複数の対象の比較、other=それ以外。"
)


# source 型だけの出力を作り直すときに、生成の user の入力の後ろへ足す部品（#4586）。何が違反でどう直すかだけを書く。
# 既定の user の入力（Step 6 まで）の続きの番号で書き、組み立てる時に ``continue_prompt_steps`` で振り直す。
# 表の見出しの行だけを basis に選んだ source 型の要点を作り直すときに、生成の user の入力の後ろへ足す部品（#4592）。
# 見出しの行かは表の chunk の分割の記録で決まり（``retrieval.table_header_lines``）、この部品は違反と直し方だけを書く。
def _without_default(schema: dict[str, Any]) -> None:
    schema.pop("default", None)


def _question_type_first(schema: dict[str, Any]) -> None:
    """質問の種類（question_type）を要点より先に生成させる（strict schema の欄の順は生成の順）。

    導出（計算・区分・条件の当てはめ）が要るかの判断を、要点の型（source / answer / gap）を選ぶ前に出させる。要点の後に
    書くと、種類の判断は要点の型に影響しない（#4586 の source 型だけの出力の作り直しが増える）。
    """
    properties = schema.get("properties", {})
    if "question_type" not in properties:
        return
    order = [name for name in properties if name != "question_type"]
    order.insert(order.index("answer_parts") if "answer_parts" in order else 0, "question_type")
    schema["properties"] = {name: properties[name] for name in order}
    if "required" in schema:
        schema["required"] = [name for name in order if name in schema["required"]]


def answer_parts_model(
    base: type[BaseModel],
    catalog: SentUnitCatalog,
    *,
    direct_source: bool = True,
    requirements: Collection[str] = (),
    request_ids: Sequence[str] = (),
    subquestions: Sequence[str] = (),
) -> type[BaseModel]:
    """要点の直接原文・導出の方法と結果・根拠不足を別の役割で受ける。

    ``direct_source=False`` は source 型を受けない契約（導出が要る質問で要点がすべて source だった出力の作り直し。
    #4586）。原文の写しの導出は従来どおり ``canonical_answer_parts`` が直接の記載に戻す。
    ``requirements`` を渡すと、要点の requirement はその文の写しだけを受ける（作り直しでは 1 回目の出力の要点の文。
    不足の文に出る要求の名前を、作り直しで別の文・識別子に変えさせない。#4586）。
    ``request_ids`` はその回の要求の id。要求ごとの根拠の計画はモデルに書かせず、``canonical_answer_parts`` が要点の
    basis から要求ごとに 1 件だけ導く（``basis_request_plans``。#4611）。
    ``subquestions`` は原質問の小問の印。要点をどの小問に答えるかで結び、冒頭の結論に小問が抜けないように
    ``canonical_answer_parts`` が小問ごとに補う（#4755）。
    """
    source = catalog.selection_model()
    direct = create_model(
        "SourceAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        kind=(cast(Any, Literal["source"]), Field(description="basis の原文をそのまま答えとして示す。")),
        basis=(source, Field(description="答えとして示す原文の単位だけ。")),
    )
    derived = create_model(
        "SupportedAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        kind=(cast(Any, Literal["answer"]), Field(description="原文と既知値から計算・条件適用・組合せで導く答え。")),
        basis=(source, Field(description="導出に使う資料の事実・規則の原文。")),
        reasoning=(str, Field(min_length=1, max_length=600, description="依存する原文と既知値から導く方法。計算は算式で表す。")),
        result=(str, Field(min_length=1, max_length=600, description="reasoning から導いた、この要点への最終的な答え。途中の値ではない。")),
    )
    gap = create_model(
        "GapAnswerPart",
        __config__=ConfigDict(extra="forbid"),
        kind=(cast(Any, Literal["gap"]), Field(description="根拠から答えられない要点だけに使う。")),
        text=(str, Field(min_length=1, max_length=600, description="必要な資料・値・前提のうち不足するもの。")),
    )
    common: dict[str, Any] = {
        "request_id": (str, Field(description="元の request_units の id。背景は答えない。")),
        "requirement": (
            cast(Any, Literal)[tuple(dict.fromkeys(requirements))] if requirements else str,
            Field(description="前回の出力の要点の requirement から、この要点で答える要求をそのまま選ぶ。")
            if requirements
            else Field(
                min_length=1, max_length=600, description="この要点で答える質問の要求。必要な粒度で複合要求を分け、任意補足を必須にしない。"
            ),
        ),
    }
    if subquestions:
        # 既定値は小問の欄の無い録画を読むためだけ。モデルに渡す schema には出さない（strict schema は全欄が必須）。
        common["subquestion"] = (
            cast(Any, Literal)[tuple(subquestions)],
            Field(default="", description="この要点が答える小問の印を、原質問の表記のまま選ぶ。", json_schema_extra=_without_default),
        )
    part: Any
    if direct_source and catalog.unit_ids:
        # 要点の役割（導出か記載か）を答えの型より先に決めさせ、導出の要点では source 型を選べなくする（#4611。#4586 の
        # 「導出が要るのに原文の提示だけ」を schema で起こさない）。記載の要点は従来どおり 3 つの型から選ぶ。mode の無い
        # 出力（録画など）は記載の要点として読む。
        derivation_part = create_model(
            "DerivationAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            mode=(
                cast(Any, Literal["derivation"]),
                Field(description="計算・区分・条件の当てはめで答えを導く要点。答えは answer 型か、導けなければ gap 型。"),
            ),
            answer=(cast(Any, Union.__getitem__((derived, gap))), Field()),
        )
        stated_part = create_model(
            "StatedAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            # 既定値は mode の無い録画を読むためだけ。モデルに渡す schema には出さない（strict schema は全欄が必須）。
            mode=(
                cast(Any, Literal["stated"]),
                Field(default="stated", description="資料の記載がそのまま答えになる要点。", json_schema_extra=_without_default),
            ),
            answer=(cast(Any, Union.__getitem__((direct, derived, gap))), Field()),
        )
        part = Union.__getitem__((derivation_part, stated_part))
    else:
        part = create_model(
            "RequiredAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            answer=(cast(Any, Union.__getitem__((derived, gap)) if catalog.unit_ids else gap), Field()),
        )
    question_types = cast(Any, Literal)[(*DERIVATION_QUESTION_TYPES, *STATED_QUESTION_TYPES)]
    fields: dict[str, Any] = {
        "answer_parts": (
            GenericAlias(list, part),
            Field(min_length=1, max_length=MAX_ANSWER_PARTS, description="要点ごとに 1 件。要点と結果を別々に重複して生成しない。"),
        ),
        "summary_part_indexes": (
            list[Annotated[int, Field(strict=True, ge=0, lt=MAX_ANSWER_PARTS)]],
            Field(default_factory=list, max_length=2, description="冒頭に示す要点のindexを最大2つ選ぶ。別の結論は書かない。"),
        ),
        "items": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        "applications": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        "summary": (SkipJsonSchema[str], Field(default="", exclude=True)),
        "summary_item_indexes": (SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True)),
        "summary_application_indexes": (SkipJsonSchema[list[int]], Field(default_factory=list, exclude=True)),
        # 要求ごとの根拠の計画は schema に出さない（#4611）。basis に選んだ根拠が答えに使う根拠なので、計画はコードが
        # basis から導く。モデルに計画の行を書かせると、basis と同じ根拠を二重に書かせ（暴走の位置。#4594）、basis の
        # 根拠に unrelated を書いて自分の答えを計画の外にする（#4613）。
        "request_coverage": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        "question_type": (
            GenericAlias(list, question_types),
            Field(
                default_factory=list,
                max_length=len(DERIVATION_QUESTION_TYPES) + len(STATED_QUESTION_TYPES),
                description=QUESTION_TYPE_DESCRIPTION,
            ),
        ),
    }
    model = create_model(base.__name__, __base__=base, __config__=ConfigDict(json_schema_extra=_question_type_first), **fields)
    cast(Any, model)._uses_typed_answer_parts = True
    cast(Any, model)._uses_unit_ids = True
    cast(Any, model)._request_ids = tuple(dict.fromkeys(request_ids))
    cast(Any, model)._subquestions = tuple(subquestions)
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


def _attach_basis_quotes(parts: list[dict[str, Any]], applications: list[dict[str, Any]]) -> None:
    """導出の要点に、依存する basis の原文の items を引用として結ぶ（結論の言い換えで条件が消えても原文を示す）。

    引用は ID から切り出した原文の items だけで、結論の公開は従来どおり導出の独立の検証が決める。
    同じ要求で直接の要点がすでに示す item は重ねない。要約の依存（summary_*_indexes）は変えない。
    """
    shown: dict[str, set[int]] = {}
    for part in parts:
        if part["result_kind"] == "direct":
            shown.setdefault(part["request_id"], set()).update(part["item_indexes"])
    limit = next((rule.max_length for rule in AnswerPart.model_fields["item_indexes"].metadata if isinstance(rule, MaxLen)), None)
    for part in parts:
        if part["result_kind"] != "derived" or not part["application_indexes"]:
            continue
        quotes = [
            index
            for app in part["application_indexes"]
            for index in applications[app]["item_indexes"]
            if index not in shown.get(part["request_id"], set())
        ]
        quotes = list(dict.fromkeys(quotes))
        # 要点の参照の上限を超える選択は、引用を結ばずに従来の導出の形のままにする（本文の原文の節には残る）。
        if limit is None or len(quotes) <= limit:
            part["item_indexes"] = quotes


def basis_request_plans(request_ids: Sequence[str], basis: Sequence[tuple[str, Sequence[BoundUnitRange]]]) -> list[dict[str, Any]]:
    """要点の basis から、要求ごとの根拠の計画（``RequestCoverage`` の形）を 1 件ずつ導く（#4611）。

    ``basis`` は答えとして受けた要点（source・answer）の要求 ID と、その basis の単位の組。basis に選んだ根拠は、その
    要求で答えに使う根拠なので relation=direct の行にする。gap の要点・答えとして受けない見出しの行だけの要点は渡さない。
    行の名前は根拠の表示 ID（``_label_spans`` が引ける label）の 1 つの形だけにする。``request_ids`` （その回の要求）は
    basis が無くても計画を 1 件ずつ持ち、要点にだけ現れた要求 ID は後ろに足す。
    """
    labels: dict[str, list[str]] = {request_id: [] for request_id in request_ids}
    for request_id, selected in basis:
        rows = labels.setdefault(request_id, [])
        for binding in selected:
            span = binding.snapshot.span()
            label = str(span.get("label") or span["evidence_id"])
            if label not in rows:
                rows.append(label)
    return [
        {
            "request_id": request_id,
            "evidence_scope": [{"evidence_id": label, "target": "", "relation": "direct", "link_quote": ""} for label in rows],
            "evidence_ids": list(rows),
        }
        for request_id, rows in labels.items()
    ]


def _summary_parts(
    chosen: Sequence[int],
    parts: Sequence[Mapping[str, Any]],
    texts: Sequence[str],
    structure_only: Sequence[bool],
    labels: Sequence[str],
    subquestions: Sequence[str],
) -> list[int]:
    """冒頭に示す要点。見出しだけの要点を除き（他が無ければ残す）、結論のある小問が必ず 1 行は載るようにする。"""
    answered = [index for index in range(len(parts)) if texts[index] and parts[index]["result_kind"] != "gap" and not structure_only[index]]
    picked = list(
        dict.fromkeys(index for index in chosen if 0 <= index < len(parts) and texts[index] and parts[index]["result_kind"] != "gap")
    )
    selected = [index for index in picked if index in answered] or picked
    if not subquestions:
        return selected
    covered = {labels[index] for index in selected}
    for label in subquestions:
        options = [index for index in answered if labels[index] == label]
        if label not in covered and options:
            # 導いた結論を原文の提示より優先する（読み手が先に欲しいのは答え）。
            selected.append(min(options, key=lambda index: (parts[index]["result_kind"] != "derived", index)))
    order = {label: position for position, label in enumerate(subquestions)}
    return sorted(selected, key=lambda index: (order.get(labels[index], len(order)), index))


def canonical_answer_parts(
    value: BaseModel,
    catalog: SentUnitCatalog,
    *,
    question: str | None = None,
) -> GroundedDraft:
    """直接選択した原文とinlineの導出から、既存APIの草稿と全indexを生成する。

    要求ごとの根拠の計画は要点の basis から導く（``basis_request_plans``。#4611）。
    """
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
    part_structure_only = []
    part_labels = []
    plan_basis: list[tuple[str, list[BoundUnitRange]]] = []
    for position, part in enumerate(wire_parts):
        answer = part["answer"]
        kind = answer["kind"]
        if kind not in {"source", "answer", "gap"}:
            raise ValueError("回答要点の役割を確認できません。")
        selected = [] if kind == "gap" else list(catalog.bind(answer["basis"]["unit_ids"]))
        if selected:
            plan_basis.append((part["request_id"], selected))
        indexes = []
        for binding in selected:
            key = (part["request_id"], binding.snapshot.snapshot_id, binding.first, binding.last)
            if key not in source_indexes:
                index = len(items)
                source_indexes[key] = index
                items.append(
                    {
                        "kind": "rule",
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
        else:  # source は選んだ原文だけを直接提示する。
            text = "\n".join(items[index]["text"] for index in indexes)
        parts.append(
            {
                "id": f"P{position + 1}",
                "request_id": part["request_id"],
                "text": part["requirement"],
                "result_kind": "direct" if kind == "source" else "derived" if kind == "answer" else "gap",
                "item_indexes": [] if kind == "answer" else indexes,
                "application_indexes": app_indexes,
            }
        )
        texts.append(text)
        part_structure_only.append(
            kind == "source" and bool(selected) and all(structure_only_quote(binding.text, binding.snapshot.span()) for binding in selected)
        )
        part_labels.append(str(part.get("subquestion") or ""))
        part_sources.append(indexes if kind != "answer" else [])
        part_applications.append(app_indexes)
    _attach_basis_quotes(parts, applications)
    chosen = _summary_parts(summary_indexes, parts, texts, part_structure_only, part_labels, getattr(type(value), "_subquestions", ()))
    raw.update(
        items=items,
        applications=applications,
        answer_parts=parts,
        request_coverage=basis_request_plans(getattr(type(value), "_request_ids", ()), plan_basis),
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
