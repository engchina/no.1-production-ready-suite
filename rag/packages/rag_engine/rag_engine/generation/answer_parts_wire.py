"""要点と結果を同じ返却単位で受け、内部の参照はコードで構成する。"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from types import GenericAlias
from typing import Annotated, Any, Literal, Union, cast

from annotated_types import MaxLen
from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic.json_schema import SkipJsonSchema

from rag_engine.generation.citation_locator import EVIDENCE_LABEL, clean_evidence_locators
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog
from rag_engine.models.llm import MAX_ANSWER_PARTS, AnswerPart, GroundedCitation, GroundedDraft
from rag_engine.retrieval.table_header_lines import span_header_lines

# 要点の要求の種類（requirement_type）の閉じた語彙（#4586・#4631）。導出（計算・区分・条件の当てはめ）が要る種類と、原文の
# 記載で答えられる種類を分ける。種類の判定はモデルの構造化出力だけが要点ごとに行い、コードは値の集合を比べるだけで語の意味を
# 読まない。質問全体の 1 つの宣言にしない（要求が複数ある質問で、一部の語が計算らしいだけで記載の要点まで導出にしない）。
DERIVATION_QUESTION_TYPES = ("calculation", "classification", "condition_check")
STATED_QUESTION_TYPES = ("fact", "definition", "procedure", "comparison", "other")
DERIVATION_TYPE_DESCRIPTION = (
    "この要点の要求の答えの種類。calculation=値の計算・集計、classification=値や条件から区分・等級を決める、"
    "condition_check=質問の事実を規則の条件に当てはめて可否・該当を決める。答えは answer 型か、導けなければ gap 型。"
)
STATED_TYPE_DESCRIPTION = (
    "この要点の要求の答えの種類。fact=資料に書かれた事実・値、definition=語・項目の意味、procedure=手順・操作、"
    "comparison=複数の対象の比較、other=それ以外。"
)


# 表の見出しの行だけを basis に選んだ source 型の要点を作り直すときに、生成の user の入力の後ろへ足す部品（#4592）。
# 見出しの行かは表の chunk の分割の記録で決まり（``retrieval.table_header_lines``）、この部品は違反と直し方だけを書く。
HEADER_ONLY_SOURCE_PROMPT = (
    "## Step 6: 表の見出しの行だけを示した要点を作り直す\n"
    "- header_only_requests の要求の要点は、前回の出力で source 型の basis に表の見出しの行（列名の行）だけを選び、"
    "表のデータの行を選んでいなかった。見出しの行だけでは答えにならない。\n"
    "- その要点の basis に、答えが書かれた表のデータの行の単位を選ぶ。根拠に答えが書かれた行が無ければ gap 型に書く。\n"
    "- 応答 schema どおりの完全な草稿を返す。\n"
)


def _without_default(schema: dict[str, Any]) -> None:
    schema.pop("default", None)


def answer_parts_model(
    base: type[BaseModel],
    catalog: SentUnitCatalog,
    *,
    requirements: Collection[str] = (),
    request_ids: Sequence[str] = (),
) -> type[BaseModel]:
    """要点の直接原文・導出の方法と結果・根拠不足を別の役割で受ける。

    要点は要求の種類（``requirement_type``）を答えの型より先に選び、種類で答えの型が決まる（#4631）。導出の種類
    （計算・区分・条件の当てはめ）の要点は source 型を選べず、記載の種類の要点は source・answer・gap から選ぶ。
    原文の写しの導出は従来どおり ``canonical_answer_parts`` が直接の記載に戻す。
    ``requirements`` を渡すと、要点の requirement はその文の写しだけを受ける（作り直しでは 1 回目の出力の要点の文。
    作り直しで要求の名前を別の文・識別子に変えさせない）。
    ``request_ids`` はその回の要求の id。要求ごとの根拠の計画はモデルに書かせず、``canonical_answer_parts`` が要点の
    basis から要求ごとに 1 件だけ導く（``basis_request_plans``。#4611）。
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
        kind=(cast(Any, Literal["gap"]), Field()),
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
    part: Any
    if catalog.unit_ids:
        # 要点ごとに要求の種類を答えの型より先に選ばせ、種類を union の判別子にする（#4631）。質問全体の 1 つの宣言と
        # 要点の型が食い違う出力（#4586 の「導出が要るのに原文の提示だけ」と、#4631 の「記載の要点を導出に作り直す」）を
        # schema で起こさない。種類の無い出力（録画など）は記載の要点として読む。
        derivation_part = create_model(
            "DerivationAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            requirement_type=(cast(Any, Literal)[DERIVATION_QUESTION_TYPES], Field(description=DERIVATION_TYPE_DESCRIPTION)),
            answer=(cast(Any, Union.__getitem__((derived, gap))), Field()),
        )
        stated_part = create_model(
            "StatedAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            # 既定値は種類の無い録画を読むためだけ。モデルに渡す schema には出さない（strict schema は全欄が必須）。
            requirement_type=(
                cast(Any, Literal)[STATED_QUESTION_TYPES],
                Field(default="other", description=STATED_TYPE_DESCRIPTION, json_schema_extra=_without_default),
            ),
            answer=(cast(Any, Union.__getitem__((direct, derived, gap))), Field()),
        )
        part = Union.__getitem__((derivation_part, stated_part))
    else:
        part = create_model(
            "RequiredAnswerPart",
            __config__=ConfigDict(extra="forbid"),
            **common,
            answer=(cast(Any, gap), Field()),
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
        # 要求ごとの根拠の計画は schema に出さない（#4611）。basis に選んだ根拠が答えに使う根拠なので、計画はコードが
        # basis から導く。モデルに計画の行を書かせると、basis と同じ根拠を二重に書かせ（暴走の位置。#4594）、basis の
        # 根拠に unrelated を書いて自分の答えを計画の外にする（#4613）。
        "request_coverage": (SkipJsonSchema[list[Any]], Field(default_factory=list, exclude=True)),
        # 質問の種類は要点ごとの requirement_type から ``canonical_answer_parts`` が作る（#4631）。モデルに質問全体の
        # 宣言を別に書かせない（要点の種類と食い違う 2 つ目の宣言になる）。
        "question_type": (SkipJsonSchema[list[str]], Field(default_factory=list, exclude=True)),
    }
    model = create_model(base.__name__, __base__=base, **fields)
    cast(Any, model)._uses_typed_answer_parts = True
    cast(Any, model)._uses_unit_ids = True
    cast(Any, model)._request_ids = tuple(dict.fromkeys(request_ids))
    return model


def _header_only(selected: Sequence[BoundUnitRange]) -> bool:
    """選んだ単位がすべて、根拠に記録した表の見出しの行（データの行を 1 つも含まない）か。

    見出しの行かは、表の chunk の分割の記録と一致した行の原文との一致だけで決める（語の意味は読まない）。
    """
    if not selected:
        return False
    for binding in selected:
        headers = span_header_lines(binding.snapshot.span())
        text = str(binding.snapshot.span().get("text") or "")
        units = binding.snapshot.units[binding.first : binding.last + 1]
        if not headers or any(text[start:end].strip() not in headers for start, end in units):
            return False
    return True


def header_only_source_parts(value: BaseModel, catalog: SentUnitCatalog) -> list[str]:
    """source 型の要点の basis が表の見出しの行だけのとき、その要求 ID を返す（#4592）。

    見出しの行は列名で、行の値を示さない。見出しの行だけを答えの原文にした要点は、表の値に答えていない。
    原文の写しだけの導出（``_copies_one_original``）は直接の記載になるので、同じく数える。
    """
    if not getattr(type(value), "_uses_typed_answer_parts", False):
        return []
    found = []
    for part in value.model_dump(exclude_unset=True).get("answer_parts") or []:
        answer = part["answer"]
        if answer["kind"] == "gap":
            continue
        selected = list(catalog.bind(answer["basis"]["unit_ids"]))
        if answer["kind"] == "answer" and not _copies_one_original(_without_bound_locators(answer["result"], selected), selected):
            continue
        if _header_only(selected):
            found.append(str(part["request_id"]))
    return list(dict.fromkeys(found))


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


def _formal_text(text: str) -> str:
    """空白（改行・全角空白を含む）だけを除く。表記・語の意味は変えない。"""
    return "".join(text.split())


def _copies_one_original(result: str, selected: list[BoundUnitRange]) -> bool:
    """導出の結果が、選んだ1つの原文範囲の写しだけで成り立つかを形式で確かめる。

    次をすべて満たすときだけ真にする（文字列の包含の検査で、語の意味は判定しない）。
    1. basis が1つの文書の連続した1範囲だけを選ぶ（ほかの原文を前提・条件に使っていない）。
    2. 空白を除いた結果が、空白を除いたその範囲の原文に含まれる。
    3. 範囲の両端の単位を外すと含まれなくなる（写しに要らない単位を選んでいない）。
    結果が原文に無い計算・組合せ・条件の当てはめは 2 を満たさず、導出のまま独立の検証を通る。
    """
    if len(selected) != 1:
        return False
    target = _formal_text(result)
    binding = selected[0]
    if not target or target not in _formal_text(binding.text):
        return False
    if binding.first == binding.last:
        return True
    narrower = (
        BoundUnitRange(binding.snapshot, binding.first + 1, binding.last),
        BoundUnitRange(binding.snapshot, binding.first, binding.last - 1),
    )
    return all(target not in _formal_text(candidate.text) for candidate in narrower)


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


def canonical_answer_parts(
    value: BaseModel,
    catalog: SentUnitCatalog,
    *,
    question: str | None = None,
) -> GroundedDraft:
    """直接選択した原文とinlineの導出から、既存APIの草稿と全indexを生成する。

    要求ごとの根拠の計画は要点の basis から導く（``basis_request_plans``。#4611）。質問の種類（``question_type``）は
    要点ごとに選んだ要求の種類を出現順に重複なく並べる（#4631。保存・表示の形は従来どおり）。
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
    plan_basis: list[tuple[str, list[BoundUnitRange]]] = []
    for position, part in enumerate(wire_parts):
        answer = part["answer"]
        kind = answer["kind"]
        if kind not in {"source", "answer", "gap"}:
            raise ValueError("回答要点の役割を確認できません。")
        selected = [] if kind == "gap" else list(catalog.bind(answer["basis"]["unit_ids"]))
        if kind == "answer" and _copies_one_original(_without_bound_locators(answer["result"], selected), selected):
            # 原文の写しだけの結果は、選んだ ID の原文を直接の記載にする。モデルの文は表示しない。
            kind = "source"
        # 表の見出しの行だけを示す要点は答えとして受けない（#4592）。見出しの行を確認できた事項に出さず、欠けた要点にする。
        header_only = kind == "source" and _header_only(selected)
        if header_only:
            selected = []
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
        elif header_only:  # 見出しの行だけの要点。答えの文は無い。
            text = ""
        else:  # source は選んだ原文だけを直接提示する。
            text = "\n".join(items[index]["text"] for index in indexes)
        parts.append(
            {
                "id": f"P{position + 1}",
                "request_id": part["request_id"],
                "text": part["requirement"],
                "result_kind": "derived" if header_only else "direct" if kind == "source" else "derived" if kind == "answer" else "gap",
                "item_indexes": [] if kind == "answer" else indexes,
                "application_indexes": app_indexes,
            }
        )
        texts.append(text)
        part_sources.append(indexes if kind != "answer" and not header_only else [])
        part_applications.append(app_indexes)
    _attach_basis_quotes(parts, applications)
    chosen = list(
        dict.fromkeys(
            index for index in summary_indexes if 0 <= index < len(parts) and texts[index] and parts[index]["result_kind"] != "gap"
        )
    )
    raw.update(
        question_type=list(dict.fromkeys(str(part["requirement_type"]) for part in wire_parts if part.get("requirement_type"))),
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
