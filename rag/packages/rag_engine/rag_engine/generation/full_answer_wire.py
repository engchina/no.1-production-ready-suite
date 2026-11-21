"""完成した回答本文と引用位置を受け、本文の意味で公開を判定しない。"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
)

from rag_engine.generation.answer_envelope import AnswerOutcome
from rag_engine.generation.answer_table import table_markdown
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog, unit_label
from rag_engine.models.llm import AnswerTable, AnswerTableRow, GroundedCitation, GroundedDraft, GroundedItem, ResponseLanguage
from rag_engine.resources.structured_output_retry import FINAL_ATTEMPT_CONTEXT_KEY
from rag_engine.retrieval.character_forms import format_characters

# 回答種別（response_kind）の各値の定義。本文が質問の要求にどう応じたかで定める。
RESPONSE_KIND_DEFINITIONS: dict[AnswerOutcome, str] = {
    "answered": "すべての要求に資料で答えた。",
    "conditional": "一部の要求に答え、答えられない部分や前提の条件を本文で示した。",
    "insufficient_evidence": (
        "要求に答える記載が資料に無く、答えを示せない。資料に記載が無いことの説明や、関連する記載の紹介だけの本文もこれにする。"
    ),
    "needs_clarification": "質問の意味を利用者に確かめる必要がある。",
    "needs_environment_data": "答えに実環境の値・記録の確認が要る。",
    "needs_human": "人の判断が要る。",
}
RESPONSE_KIND_DESCRIPTION = "本文が質問の要求にどう応じたかの種別。次の値から選ぶ。\n" + "\n".join(
    f"  - {kind}: {definition}" for kind, definition in RESPONSE_KIND_DEFINITIONS.items()
)


class AnswerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        description=(
            "表示する行名。質問で尋ねられた項目・対象だけを短く名付ける。数値比較は対象名に適用範囲を添える。"
            "同じ語を含んでいても異なる属性は独立した行として扱う。"
        )
    )
    sources: list[str] = Field(
        description="この行が答える質問に直接必要な原文ラベルだけを先に選ぶ。別の対象・別の属性にしか答えない行は選ばない。"
    )
    cells: list[str] = Field(
        description=(
            "行名に対する値のみ。複数列の表では先頭列は label なので、二列目から順に記入する。段落と一列表は一セル。"
            "独立した別の事実を補足としてセルに混ぜず、別行で要否を判断する。引用は sources だけ。"
        )
    )

    required: bool = Field(
        description=(
            "セルを埋めてから判断する。この行を取り除くと利用者の求めた値・判断・操作またはその必要条件・例外が欠ける場合だけ true。"
            "項目ごとの判断で答えが揃う場合、その判断をまとめ直した総論は false。周辺知識や別属性の規則も false。"
        )
    )


class AnswerTableBody(BaseModel):
    """回答ごとに一組のセルを受け、表または段落を組み立てる。"""

    model_config = ConfigDict(extra="forbid")

    headers: list[str] = Field(
        description="数値比較は比較対象ごとに一行、各測定項目を列とする。対象の適用範囲は行名に一度だけ添える。異なる判断・操作・確認事項が並ぶ質問には「項目・答え」の二列を使う。単独の説明や指定形式は空配列。"
    )
    rows: list[AnswerRow] = Field(
        min_length=1,
        description=(
            "質問から求める項目名を拾い、それだけを埋める。見出しなしは一行一セル。単純な可否の答えは「可」か「不可」の一語で止める。不可の後に禁止文を添えない。条件付きなら条件を添える。"
            "複数項目の断定を尋ねたときは各項目に直接答える。資料で決まらない欄は「不明（不足する情報）」で完結し、別の属性の規則で埋めない。資料が区分を分けていなければ対象名に『区分なし』に相当する短句だけを添える。"
            "特定対象の可否・理由も尋ねられたら専用の列を設け、該当行だけに制約を書く。他の行は空欄。必要な条件・否定・例外を該当するセルに含める。"
        ),
    )

    def markdown(self) -> str:
        rows = []
        for row in self.rows:
            if not row.required:
                continue
            cells = [row.label, *row.cells] if len(self.headers) > 1 else list(row.cells)
            if len(cells) != (len(self.headers) or 1):
                raise ValueError("行名は label に、残りの列の値だけを cells に書き、セル数を見出しの数に合わせてください。")
            cells[-1] += "".join(dict.fromkeys(row.sources))
            rows.append(AnswerTableRow(cells=cells))
        if not self.headers:
            return "\n\n".join(row.cells[0].strip() for row in rows if row.cells[0].strip())
        return table_markdown(AnswerTable(columns=self.headers, rows=rows)) if rows else ""


class FullAnswerWire(BaseModel):
    """本文はモデルが完成させ、種別は同じ呼び出しの付帯情報にする。"""

    model_config = ConfigDict(extra="forbid")
    _provider_output_text: str | None = PrivateAttr(default=None)
    _uses_unit_ids = True
    _uses_full_answer = True

    response_language: ResponseLanguage = Field(default_factory=ResponseLanguage)
    answer_body: str = Field(
        min_length=1,
        description=(
            "各要求に一度だけ答える。複数の対象の値や状態の判定を尋ねる質問には表を選び、"
            "可否の理由・適用範囲を該当行にまとめる。"
            "それ以外は尋ねられた項目に絞る。必要な条件・否定・例外を残す。利用者の形式指定を優先する。"
        ),
    )

    @field_validator("answer_body", mode="before", json_schema_input_type=AnswerTableBody)
    @classmethod
    def render_body(cls, value: Any) -> Any:
        # モデルには構造化した入力を示し、保存済みの文字列や段落の再検証には従来の本文を使う。
        return AnswerTableBody.model_validate(value).markdown() if isinstance(value, dict) else value

    # 各値の定義はこの項目の説明だけに書き、生成の指示の Step では繰り返さない。adapter が説明を指示の最後の
    # Step にして全モデルに送る（#5230・#5238）。
    response_kind: AnswerOutcome = Field(description=RESPONSE_KIND_DESCRIPTION)
    confidence: Literal["high", "medium", "low"] = Field(description="回答の確かさの自己申告。")


# 印の角括弧として読む文字（半角・全角・隅付き括弧。#5091）。
_OPEN = "[\\[［【]"
_CLOSE = "[\\]］】]"


@functools.cache
def _padding() -> str:
    """括弧の内側と印の間に許す字: 改行を除く Unicode の空白（全角の空白を含む）と書式文字（ZWSP・BOM など）の連続（#5186）。"""
    return rf"(?:[^\S\n]|[{format_characters()}])*"


def citation_marker(catalog: SentUnitCatalog) -> re.Pattern[str] | None:
    """本文の引用の印: この入力の根拠のラベル（``[E2]``）か単位のラベル（``[E2.1]``）を角括弧で囲んだもの。

    印はモデルが入力の根拠で見たラベルそのもので、別の番号の体系を持たない（#5078）。今回送っていない根拠の
    ラベルは印にしない（本文の字として残す）。自然言語の意味は解析しない。

    角括弧は文字の種類だけを形式として揃え、全角の ``［］`` と ``【】`` も同じ印として読む（#5091）。括弧の内側の
    前後の空白と書式文字（``[ E2.1 ]``・ゼロ幅の空白の入った印）も形式として読み飛ばす（#5186）。ラベルの照合は変えない。
    """
    if not catalog.block_labels:
        return None
    blocks = "|".join(re.escape(label) for label in sorted(catalog.block_labels, key=len, reverse=True))
    pad = _padding()
    return re.compile(rf"{_OPEN}{pad}({blocks})(?:\.([1-9][0-9]*))?{pad}{_CLOSE}")


def _marker_run(marker: re.Pattern[str]) -> re.Pattern[str]:
    """続けて書いた印（``[E2.1][E2.2]``）は 1 か所の引用として、隣り合う単位を 1 つの出典の行にまとめる。"""
    return re.compile(rf"(?:{marker.pattern})(?:{_padding()}(?:{marker.pattern}))*")


# 文の中の丸括弧の 1 組（入れ子にしない）と、その前の改行を除く空白（#5231）。印の角括弧は含まない。
_ROUND_GROUP = r"[^\S\n]*[（(][^（）()\n]*[）)]"


def _has_text(value: str) -> bool:
    """字（文字・数字・結合文字）を含むか。空白・区切りの記号・書式文字だけなら持たない（字種だけを見る）。"""
    return any(unicodedata.category(char)[0] in "LNM" for char in value)


def _cited_labels(run: str, marker: re.Pattern[str], catalog: SentUnitCatalog) -> tuple[str, ...]:
    """印の並びが指す単位のラベル。根拠のラベルはその根拠のすべての単位を指す。"""
    labels: list[str] = []
    for match in marker.finditer(run):
        block, index = match.group(1), match.group(2)
        labels.extend(catalog.block_units(block) if index is None else [unit_label(block, int(index))])
    return tuple(dict.fromkeys(labels))


# 資料で答えたと宣言した種別。本文に引用の印を求める（#5391）。
CITED_RESPONSE_KINDS: frozenset[AnswerOutcome] = frozenset({"answered", "conditional"})
CITATION_MISSING_MESSAGE = (
    "answer_body に根拠のラベルが 1 つもありません。根拠に基づく各説明の後に、根拠の各行に示したラベルを書いてください。"
)


class CitationMissing(ValueError):
    """資料で答えた本文に、引用の印が 1 つも無い（#5391）。本文は固定の指摘で、モデルの出力の文を持たない。"""

    def __init__(self) -> None:
        super().__init__(CITATION_MISSING_MESSAGE)


def citation_repair_step() -> str:
    """再試行の入力の後に付ける指摘の Step（番号は呼び出し側が送る指示の続きに振り直す）。"""
    return f"## Step 1: 引用のラベルを書く\n- {CITATION_MISSING_MESSAGE}\n"


def citation_missing_error(error: Exception) -> bool:
    """構造化出力の検証の失敗に、引用の印の欠落が含まれるか。"""
    if not isinstance(error, ValidationError):
        return False
    return any(
        isinstance((row.get("ctx") or {}).get("error"), CitationMissing) for row in error.errors(include_input=False, include_url=False)
    )


def lacks_citation(body: str, response_kind: str, catalog: SentUnitCatalog) -> bool:
    """資料で答えた種別の本文に、今回送った根拠を指す印が 1 つも無いか（根拠の目録が空なら求めない）。"""
    marker = citation_marker(catalog)
    return marker is not None and response_kind in CITED_RESPONSE_KINDS and marker.search(body) is None


def full_answer_model(catalog: SentUnitCatalog) -> type[BaseModel]:
    """引用は本文の印だけで受ける。印が指せるのは、この入力に実際に提示した根拠と単位だけ。

    資料で答えた種別（``answered``・``conditional``）の本文に印が 1 つも無ければ、形式の不合格として既存の再試行に
    渡す（#5391）。最後の試行（検証の context の ``FINAL_ATTEMPT_CONTEXT_KEY``）では不合格にせず本文を保持し、
    呼び出し側が引用の欠落として扱う。
    """
    marker = citation_marker(catalog)

    def known_units(value: str) -> str:
        if marker is None:
            return value
        unknown = [
            match.group(0)
            for match in marker.finditer(value)
            if match.group(2) is not None and unit_label(match.group(1), int(match.group(2))) not in catalog.unit_labels
        ]
        if unknown:
            raise ValueError(
                f"answer_body の {'、'.join(dict.fromkeys(unknown))} は今回の根拠に無い行のラベルです。"
                "引用には根拠の各行に示したラベルか、根拠のラベルをそのまま書いてください。"
            )
        return value

    def cites_evidence(value: Any, info: ValidationInfo) -> Any:
        if (info.context or {}).get(FINAL_ATTEMPT_CONTEXT_KEY):
            return value
        if lacks_citation(value.answer_body, value.response_kind, catalog):
            raise CitationMissing()
        return value

    validators: dict[str, Any] = {
        "known_units": field_validator("answer_body")(known_units),
        "cites_evidence": model_validator(mode="after")(cites_evidence),
    }
    return create_model("FullAnswerWire", __base__=FullAnswerWire, __validators__=validators)


def canonical_full_answer(value: BaseModel, catalog: SentUnitCatalog) -> GroundedDraft:
    """本文の印が指す単位を既存の保存形式へ結ぶ。分類・引用数による本文の削除や書換えはしない。"""
    raw = value.model_dump()
    body = str(raw["answer_body"])
    marker = citation_marker(catalog)
    bindings: list[BoundUnitRange] = []
    groups: dict[str, tuple[int, ...]] = {}
    if marker is not None:
        for run in _marker_run(marker).finditer(body):
            labels = _cited_labels(run.group(0), marker, catalog)
            key = ",".join(labels)
            if key in groups:
                continue
            selected = catalog.bind(labels)
            groups[key] = tuple(range(len(bindings), len(bindings) + len(selected)))
            bindings.extend(selected)
    items: list[GroundedItem] = []
    for binding in bindings:
        citation = GroundedCitation(evidence_id=binding.snapshot.span()["evidence_id"])
        citation._unit_binding = binding
        citation._wire_range = (binding.first + 1, binding.last + 1)
        citation.lines = str(binding.first + 1) if binding.first == binding.last else f"{binding.first + 1}-{binding.last + 1}"
        items.append(GroundedItem(kind="rule", text=binding.text, citations=[citation]))
    draft = GroundedDraft(items=items, summary="", confidence=raw["confidence"], response_language=raw["response_language"])
    draft._response_kind = draft._declared_response_kind = raw["response_kind"]
    draft._citation_snapshots = catalog.snapshots
    draft._provider_output_text = getattr(value, "_provider_output_text", None)
    draft._full_answer = {"body": body, "groups": groups, "catalog": catalog}
    return draft


def render_full_answer(draft: GroundedDraft, checked: Sequence[Any]) -> Any:
    """引用マーカーだけを既存の根拠行へ変換する。本文の主張はそのまま返す。

    印は段落・表の後ろの出典の行にして本文から外す。丸括弧の中の印は、外すと括弧や接続の字だけが残るので
    （「資料（[E1] および [E2]）」）、括弧の中の字種だけで扱いを決める（#5231）: 印のほかに字が無い括弧は
    括弧ごと外し、字がある括弧では印を出典の名前（出典の行と同じ書き方）に置き換える。どちらも出典の行と
    引用の結び付きは変えない。文の意味（印を名詞として書いたか）は判定しない。
    """
    from rag_engine.generation import grounded

    full = draft._full_answer
    assert full is not None
    entries = {entry.draft_index: entry for entry in checked if entry.span is not None}
    lines: list[dict[str, Any]] = []

    body = cast(str, full["body"])
    catalog = cast(SentUnitCatalog, full["catalog"])
    marker = citation_marker(catalog)
    if marker is None or not marker.search(body):
        return grounded.RenderedAnswer(body, [])
    run_pattern = _marker_run(marker)
    token = re.compile(rf"(?P<group>{_ROUND_GROUP})|{run_pattern.pattern}")
    sources = grounded.sent_evidence_sources(catalog)
    output: list[str] = []
    pending: list[Any] = []

    def append_references() -> None:
        # 同じ本文行・表内で繰り返した同じ根拠だけを束ね、各引用の位置に対応する子をすべて残す。
        grouped: dict[tuple[str, str], list[Any]] = {}
        for entry in pending:
            label = grounded._cited_line(entry)
            key = (label, entry.span["evidence_id"])
            grouped.setdefault(key, []).append(entry)
        for (label, _), group in grouped.items():
            output.append(label)
            lines.append(grounded.citation_line_ref(group))
        pending.clear()

    in_table = False
    for line in body.splitlines():
        table_line = line.lstrip().startswith("|")
        if in_table and not table_line:
            append_references()
        in_table = table_line

        def cite(run: str) -> None:
            key = ",".join(_cited_labels(run, marker, catalog))
            pending.extend(entries[index] for index in full["groups"].get(key, ()) if index in entries)

        def source_names(match: re.Match[str]) -> str:
            cite(match.group(0))
            blocks = dict.fromkeys(found.group(1) for found in marker.finditer(match.group(0)))
            return "、".join(sources[block].strip() for block in blocks if block in sources) or match.group(0)

        def replace_marker(match: re.Match[str]) -> str:
            if match.group("group") is None:
                cite(match.group(0))
                return ""
            group = match.group(0)
            if not run_pattern.search(group):
                return group
            if not _has_text(run_pattern.sub("", group)):
                for run in run_pattern.finditer(group):
                    cite(run.group(0))
                return ""
            return run_pattern.sub(source_names, group)

        # 表の行や文末の句読点を途中で割らず、原文参照だけを行・表の後ろへ出す。
        displayed = token.sub(replace_marker, line)
        output.append(displayed)
        if displayed.strip().startswith(grounded.answer_labels().citation_prefix):
            # 本文に書かれた出典の見た目は、選択した ID に結ばない。後続の正式な引用番号だけを保つ。
            lines.append({})
        if not table_line:
            append_references()
    append_references()
    return grounded.RenderedAnswer("\n".join(output), lines)
