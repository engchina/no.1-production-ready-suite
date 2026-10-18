"""回答の過程を書いた表から業務ガイドの下書きとルールの候補を作る（#2397）。

利用者の担当者は、問ごとの「回答の過程」を表（Excel）の 1 セルに、決まった見出しの半構造の文で
書いている（回答の手順・必要な確認事項と分岐・検索する資料と頁・検索の語・補足）。この表の行を、
OCI Enterprise AI で業務ガイドの内容（``SupportGuideContent``）の下書きに直す。

1. Excel の読み込み（``read_process_workbook``）: シートと列（1 行目の見出し）を返し、利用者が
   選んだ列（質問・回答の過程・任意で標準回答）で 2 行目からの行を読む。モデルは呼ばない。
2. 節に分ける（``split_process_sections``）: 回答の過程の文を、言語資源の見出しの語
   （``guide_import_cues.process_headings``。「見出し=項目」）で節に分ける。見出しの語はコードに
   書かない。
3. 構造化（``draft_guides``）: 1 行で LLM を 1 回呼ぶ（共通の上限つき並行の部品。#2004）。応答は
   ``GuideDraftOutput``（Pydantic）で検証し、業務ガイドの内容に直して ``SupportGuideContent`` と
   ``validate_content`` で検証する。
4. 資料名の解決（``resolve_document_name``）: 回答の過程に書いた資料の名前を、検索・回答
   プロファイルのナレッジベースの文書の名前と照合して ``document_id`` にする。解決できない・複数に
   当たる資料は検証のエラー（KB への取り込みを促す）。

下書きは保存しない（画面で行ごとに確かめ、選んだものだけを取り込みの API で下書きにする）。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal, Protocol

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from pr_backend_core.bounded_calls import Attempt
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rag_parser_core.language import LanguagePack

from app.rag.cross_references import document_name_match, title_key
from app.rag.support_guide import has_errors, validate_content
from app.schemas.support_guide import (
    PROCESS_IMPORT_QUESTION_MAX_CHARS,
    PROCESS_IMPORT_TEXT_MAX_CHARS,
    SupportGuideContent,
    SupportGuideIssue,
    SupportGuideProcessColumn,
    SupportGuideProcessItem,
    SupportGuideProcessReference,
    SupportGuideProcessRow,
    SupportGuideProcessSheet,
    SupportGuideProcessWorkbookData,
    SupportGuideRuleCandidate,
)

logger = logging.getLogger(__name__)

PROCESS_WORKBOOK_MAX_BYTES = 10 * 1024 * 1024
# Excel の読み込みで見る行の上限（上限を超えた分は読まない。行の数の数えすぎを止める）。
_WORKBOOK_SCAN_ROWS = 5000
_MAX_ISSUES = 50
ProcessField = Literal["steps", "conditions", "references", "search_terms", "notes"]
PROCESS_FIELDS: tuple[str, ...] = ("steps", "conditions", "references", "search_terms", "notes")


# ---- Excel ----------------------------------------------------------------------------------


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def read_process_workbook(
    data: bytes,
    file_name: str,
    *,
    max_rows: int,
    sheet: str | None = None,
    question_column: int | None = None,
    process_column: int | None = None,
    answer_column: int | None = None,
    start_row: int = 2,
) -> SupportGuideProcessWorkbookData:
    """シートと列を返し、列を選んでいれば ``start_row`` から ``max_rows`` 行を読む。

    1 行目は見出し。質問と回答の過程の両方が空の行は飛ばす。片方だけの行は読めない行として
    ``issues`` に出す。形式の誤り（.xlsx でない・大きすぎる）は ``ValueError``（利用者向けの文）。
    """
    if len(data) > PROCESS_WORKBOOK_MAX_BYTES:
        raise ValueError(
            f"ファイルが大きすぎます（上限 {PROCESS_WORKBOOK_MAX_BYTES // (1024 * 1024)}MB）。"
        )
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - 形式の誤りは利用者へ返す
        raise ValueError(
            "Excel（.xlsx）として読めません。.xlsx の形式で保存してください。"
        ) from exc
    try:
        sheets: list[SupportGuideProcessSheet] = []
        selected: Any = None
        for worksheet in book.worksheets:
            rows = worksheet.iter_rows(values_only=True, max_row=_WORKBOOK_SCAN_ROWS)
            header = list(next(rows, None) or ())
            count = sum(1 for values in rows if any(_cell_text(value) for value in values))
            sheets.append(
                SupportGuideProcessSheet(
                    name=str(worksheet.title),
                    columns=[
                        SupportGuideProcessColumn(
                            index=index,
                            letter=get_column_letter(index + 1),
                            header=_cell_text(value)[:100],
                        )
                        for index, value in enumerate(header)
                    ],
                    row_count=count,
                )
            )
            if sheet is not None and str(worksheet.title) == sheet:
                selected = worksheet
        result = SupportGuideProcessWorkbookData(
            file_name=file_name, sheets=sheets, max_rows=max_rows
        )
        if question_column is None or process_column is None:
            return result
        if selected is None:
            raise ValueError("選んだシートがありません。シートを選び直してください。")
        result.sheet = str(selected.title)
        _read_rows(
            selected,
            result,
            question_column=question_column,
            process_column=process_column,
            answer_column=answer_column,
            start_row=max(2, start_row),
            max_rows=max_rows,
        )
        return result
    finally:
        book.close()


def _read_rows(
    worksheet: Any,
    result: SupportGuideProcessWorkbookData,
    *,
    question_column: int,
    process_column: int,
    answer_column: int | None,
    start_row: int,
    max_rows: int,
) -> None:
    def at(values: Sequence[object], index: int | None) -> str:
        if index is None or index < 0 or index >= len(values):
            return ""
        return _cell_text(values[index])

    for number, values in enumerate(
        worksheet.iter_rows(min_row=start_row, max_row=_WORKBOOK_SCAN_ROWS, values_only=True),
        start=start_row,
    ):
        question = at(values, question_column)
        process = at(values, process_column)
        if not question and not process:
            continue
        if len(result.rows) >= max_rows:
            result.truncated = True
            result.next_start_row = number
            return
        problem = _row_problem(question, process)
        if problem:
            if len(result.issues) < _MAX_ISSUES:
                result.issues.append(f"{number} 行目: {problem}")
            continue
        result.rows.append(
            SupportGuideProcessRow(
                row=number,
                question=question,
                process=process,
                standard_answer=at(values, answer_column)[:PROCESS_IMPORT_TEXT_MAX_CHARS],
            )
        )


def _row_problem(question: str, process: str) -> str:
    if not question:
        return "質問が空です。"
    if not process:
        return "回答の過程が空です。"
    if len(question) > PROCESS_IMPORT_QUESTION_MAX_CHARS:
        return f"質問が長すぎます（{PROCESS_IMPORT_QUESTION_MAX_CHARS} 字まで）。"
    if len(process) > PROCESS_IMPORT_TEXT_MAX_CHARS:
        return f"回答の過程が長すぎます（{PROCESS_IMPORT_TEXT_MAX_CHARS} 字まで）。"
    return ""


# ---- 節に分ける -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessSection:
    """回答の過程の 1 つの節（見出しの無い先頭の文は ``field`` が None）。"""

    field: str | None
    heading: str
    text: str


def process_headings(pack: LanguagePack) -> dict[str, str]:
    """言語資源の見出しの語 → 業務ガイドの項目（``guide_import_cues.process_headings``）。"""
    mapping = pack.mapping("guide_import_cues", "process_headings")
    return {word: field for word, field in mapping.items() if field in PROCESS_FIELDS}


def _is_mark(char: str) -> bool:
    """見出しの飾り（記号・句読点・空白）か。言語に依らない Unicode の分類で決める。"""
    return char.isspace() or unicodedata.category(char)[0] in {"P", "S"}


def _heading(line: str, keys: Sequence[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """行が見出しなら (項目, 見出しの語, 見出しの後ろの文)。

    行の前後の飾り（【】・■・「：」など）を外した文が見出しの語で始まり、その後ろが空か飾りで
    始まるときだけ見出しとみなす（「手順書を開く」の「手順」を見出しにしない）。
    """
    text = unicodedata.normalize("NFKC", line).strip()
    start = 0
    while start < len(text) and _is_mark(text[start]):
        start += 1
    body = text[start:]
    folded = body.casefold()
    for key, word, field in keys:
        if not folded.startswith(key):
            continue
        rest = body[len(key) :]
        if rest and not _is_mark(rest[0]):
            continue
        # 見出しの後ろの閉じ括弧・区切り（「】」「：」）を外す。開き括弧から始まる内容は残す。
        index = 0
        while index < len(rest) and (
            rest[index].isspace() or unicodedata.category(rest[index]) in _HEADING_TAIL
        ):
            index += 1
        return field, word, rest[index:].strip()
    return None


# 見出しの後ろに続く飾りの分類（閉じ括弧・区切りの記号・ダッシュ）。
_HEADING_TAIL = frozenset({"Pe", "Pf", "Po", "Pd"})


def split_process_sections(text: str, headings: Mapping[str, str]) -> list[ProcessSection]:
    """回答の過程の文を、見出しの語の行で節に分ける（見出しが無ければ全体を 1 つの節にする）。"""
    keys = sorted(
        (
            (unicodedata.normalize("NFKC", word).casefold().strip(), word, field)
            for word, field in headings.items()
            if word.strip()
        ),
        key=lambda item: -len(item[0]),
    )
    sections: list[ProcessSection] = []
    field: str | None = None
    heading = ""
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body or field is not None:
            sections.append(ProcessSection(field, heading, body))

    for line in text.splitlines():
        found = _heading(line, keys) if keys else None
        if found is None:
            lines.append(line)
            continue
        flush()
        field, heading, rest = found
        lines = [rest] if rest else []
    flush()
    return [section for section in sections if section.text or section.field]


# ---- LLM の応答の形 -------------------------------------------------------------------------


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DraftStep(_Out):
    id: str = Field(description="手順の id（s1・s2…）。")
    title: str = Field(description="手順の題名（画面・操作が分かる短い文）。")
    purpose: str = Field(default="", description="手順の目的・内容。")
    depends_on: list[str] = Field(default_factory=list, description="先に済ませる手順の id。")
    retrieval_hints: list[str] = Field(
        default_factory=list, description="この手順の資料を検索する語（画面名・機能名）。"
    )
    note: str = Field(default="", description="この手順の注意（補足のうち手順に係るもの）。")


class DraftCondition(_Out):
    id: str = Field(description="条件の id（c1・c2…）。")
    label: str = Field(description="条件の名前。")
    type: Literal["enum", "boolean", "text"] = "enum"
    allowed_values: list[str] = Field(default_factory=list, description="選択肢（enum のとき）。")
    question: str = Field(default="", description="利用者に確かめる問い。")
    handling: Literal["ask", "branch", "handoff"] = Field(
        default="ask", description="分からないとき: ask=確かめる / branch=分岐 / handoff=窓口へ。"
    )


class DraftBranch(_Out):
    condition_id: str
    values: list[str] = Field(
        default_factory=list, description="この値のとき（空なら条件が分からないとき）。"
    )
    goto_step: str = Field(description="進む手順の id。")
    note: str = ""


class DraftReference(_Out):
    document_name: str = Field(description="資料の名前（回答の過程に書いたまま）。")
    pages: str = Field(default="", description="頁（書いたまま。例: 12-14）。")
    section: str = Field(default="", description="章節（書いたまま）。")


class DraftRule(_Out):
    title: str = Field(description="ルールの題名。")
    triggers: list[str] = Field(default_factory=list, description="ルールを当てる質問の語。")
    content: str = Field(description="ルールの内容（回答のときに守ること）。")


class GuideDraftOutput(_Out):
    """LLM の応答（業務ガイドの下書きとルールの候補）。"""

    title: str = Field(description="業務ガイドの題名（利用者がしたいこと）。")
    expected_result: str = Field(description="案内のとおり進めたときの結果。")
    match_terms: list[str] = Field(default_factory=list, description="照合の語（検索の語）。")
    steps: list[DraftStep] = Field(default_factory=list)
    conditions: list[DraftCondition] = Field(default_factory=list)
    branches: list[DraftBranch] = Field(default_factory=list)
    references: list[DraftReference] = Field(default_factory=list)
    handoff_contact: str = Field(default="", description="窓口へ連絡するときの窓口の名前。")
    handoff_conditions: list[str] = Field(default_factory=list, description="窓口へ連絡する条件。")
    rule_candidates: list[DraftRule] = Field(default_factory=list)


GUIDE_DRAFT_RESPONSE_SCHEMA = GuideDraftOutput.model_json_schema()

GUIDE_DRAFT_SYSTEM_PROMPT = """\
あなたは、問い合わせ対応の「回答の過程」を、業務ガイドの下書き（JSON）に直す担当です。
回答の過程に書いてあることだけを使い、書いていない手順・条件・資料・頁を足さないでください。
"""

GUIDE_DRAFT_PROMPT = """\
入力の JSON は、利用者の質問（question）、回答の過程を見出しで分けた節（sections）、任意の標準回答
（standard_answer）です。節の field は次の項目を表します（null は見出しの無い文）。

- steps（回答の手順）: steps に順に入れる。前の手順を済ませてから行う手順は depends_on に前の手順の
  id を入れる。手順の画面名・機能名は retrieval_hints に入れる。
- conditions（必要な確認事項）: 確かめる事項を conditions に、「X なら手順 P」を branches に入れる。
  選択肢が決まっていれば type=enum（2 つ以上）、はい / いいえなら boolean、それ以外は text。
  「窓口へ連絡」のような人へ引き継ぐ場合は handling=handoff にし、handoff_contact に窓口の名前、
  handoff_conditions にその場合を入れる。利用者に聞けば決まる事項は handling=ask と question を
  入れる。
- references（検索する資料と根拠の頁）: 資料の名前は書いたまま document_name に、頁は pages、章節は
  section に入れる。
- search_terms（検索の語）: match_terms と、関係する手順の retrieval_hints に入れる。
- notes（補足）: 1 つの手順に係る注意はその手順の note に入れる。この質問に限らず回答の仕方を決める
  規則（「この種類の質問はこの画面を案内する」「言い切らず可能性にとどめる」「Aと Bを区別する」
  「範囲外は窓口へ」など）は rule_candidates に入れる（triggers はその規則を当てる質問の語）。

title は利用者がしたいことを表す短い名前、expected_result は手順のとおり進めたときの結果にします。
id は手順が s1・s2…、条件が c1・c2… の順の番号にします。
"""


class GuideDraftLlm(Protocol):
    async def generate(
        self,
        prompt: str,
        context: str,
        *,
        system_prompt: str | None = None,
        response_schema: Mapping[str, Any] | None = None,
        response_schema_name: str = ...,
    ) -> str: ...


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_draft_output(raw: str) -> GuideDraftOutput:
    """LLM の応答を検証する（JSON の前後の文は外す）。形が合わなければ ``ValueError``。"""
    match = _JSON_OBJECT.search(str(raw or ""))
    if match is None:
        raise ValueError("応答に JSON がありません。")
    try:
        return GuideDraftOutput.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("応答の JSON が業務ガイドの下書きの形ではありません。") from exc


def draft_context(row: SupportGuideProcessRow, headings: Mapping[str, str]) -> str:
    """LLM に渡す 1 行の入力（質問・節に分けた回答の過程・標準回答）。"""
    sections = split_process_sections(row.process, headings)
    payload = {
        "question": row.question,
        "sections": [
            {"field": section.field, "heading": section.heading, "text": section.text}
            for section in sections
        ],
        "standard_answer": row.standard_answer,
    }
    return json.dumps(payload, ensure_ascii=False)


# ---- 資料名の解決 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeDocument:
    """検索・回答プロファイルのナレッジベースの文書（照合に使う名前）。"""

    document_id: str
    file_name: str


_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def _stem_key(file_name: str) -> str:
    return title_key(_EXTENSION.sub("", unicodedata.normalize("NFKC", file_name).strip()))


def resolve_document_name(
    name: str, documents: Sequence[KnowledgeDocument]
) -> tuple[KnowledgeDocument | None, list[KnowledgeDocument]]:
    """資料の名前に当たる文書（1 つに決まれば文書、決まらなければ None と当たった文書）。

    確かな順に照合し、最初に当たった順位の文書だけを候補にする。
    1. ファイル名（拡張子を除く形を含む）が同じ。
    2. 文書のタイトル・ファイル名の別名と同じ、またはファイル名が資料の名前を含む
       （``document_name_match``。他の文書への参照の解決と同じ規則）。
    3. 資料の名前がファイル名（拡張子を除く）を含む（利用者のファイル名の接頭辞・接尾辞）。最も
       長いファイル名だけを候補にする。
    """
    key = title_key(name)
    stem = _stem_key(name)
    if len(key) < 2:
        return None, []
    exact = [
        item
        for item in documents
        if title_key(item.file_name) == key or (stem and _stem_key(item.file_name) == stem)
    ]
    if exact:
        return (exact[0], []) if len(exact) == 1 else (None, exact)
    ranked: dict[int, list[KnowledgeDocument]] = {}
    for item in documents:
        rank = document_name_match(name, item.file_name)
        if rank is not None:
            ranked.setdefault(rank, []).append(item)
    if ranked:
        best = ranked[min(ranked)]
        return (best[0], []) if len(best) == 1 else (None, best)
    contained = [
        (len(_stem_key(item.file_name)), item)
        for item in documents
        if len(_stem_key(item.file_name)) >= 2 and _stem_key(item.file_name) in key
    ]
    if not contained:
        return None, []
    longest = max(length for length, _ in contained)
    best = [item for length, item in contained if length == longest]
    return (best[0], []) if len(best) == 1 else (None, best)


# ---- 下書きにする ---------------------------------------------------------------------------


def _clean(values: Sequence[str], limit: int, max_chars: int = 200) -> list[str]:
    return list(dict.fromkeys(value.strip()[:max_chars] for value in values if value.strip()))[
        :limit
    ]


def _references(
    output: GuideDraftOutput, documents: Sequence[KnowledgeDocument]
) -> tuple[list[SupportGuideProcessReference], list[SupportGuideIssue]]:
    references: list[SupportGuideProcessReference] = []
    issues: list[SupportGuideIssue] = []
    for index, item in enumerate(output.references[:30]):
        name = item.document_name.strip()
        if not name:
            continue
        found, candidates = resolve_document_name(name, documents)
        if found is not None:
            references.append(
                SupportGuideProcessReference(
                    name=name,
                    pages=item.pages[:64],
                    section=item.section[:200],
                    status="resolved",
                    document_id=found.document_id,
                    file_name=found.file_name,
                )
            )
            continue
        status: Literal["unresolved", "ambiguous"] = "ambiguous" if candidates else "unresolved"
        references.append(
            SupportGuideProcessReference(
                name=name,
                pages=item.pages[:64],
                section=item.section[:200],
                status=status,
                candidates=[candidate.file_name for candidate in candidates[:10]],
            )
        )
        message = (
            f"資料「{name}」に当たる文書が複数あります（"
            + "、".join(candidate.file_name for candidate in candidates[:5])
            + "）。回答の過程の資料の名前を文書の名前に合わせてください。"
            if candidates
            else f"資料「{name}」は、このプロファイルのナレッジベースの文書にありません。"
            "資料をナレッジベースに取り込んでから、もう一度下書きを作ってください。"
        )
        issues.append(
            SupportGuideIssue(
                severity="error",
                code="reference_ambiguous" if candidates else "reference_unresolved",
                path=f"references[{index}]",
                message=message,
            )
        )
    return references, issues


def _content_payload(
    output: GuideDraftOutput,
    question: str,
    references: Sequence[SupportGuideProcessReference],
) -> dict[str, Any]:
    step_ids = {step.id for step in output.steps}
    steps = []
    for step in output.steps[:50]:
        purpose = step.purpose.strip()
        if step.note.strip():
            purpose = f"{purpose}\n注意: {step.note.strip()}" if purpose else f"注意: {step.note}"
        steps.append(
            {
                "id": step.id,
                "title": step.title[:200],
                "purpose": purpose[:2000],
                "depends_on": [item for item in step.depends_on if item in step_ids][:20],
                "retrieval_hints": _clean(step.retrieval_hints, 10),
            }
        )
    conditions = []
    for condition in output.conditions[:30]:
        values = _clean(condition.allowed_values, 20)
        kind = condition.type
        if kind == "enum" and len(values) < 2:
            kind = "text"
        question_text = condition.question.strip()
        if condition.handling == "ask" and not question_text:
            question_text = f"{condition.label}を教えてください。"
        conditions.append(
            {
                "id": condition.id,
                "label": condition.label[:200],
                "type": kind,
                "allowed_values": values if kind == "enum" else [],
                "unknown_handling": condition.handling,
                "question": question_text[:200],
            }
        )
    branches = []
    for index, branch in enumerate(output.branches[:50], start=1):
        values = _clean(branch.values, 20)
        operator = "unknown" if not values else "equals" if len(values) == 1 else "in"
        branches.append(
            {
                "id": f"b{index}",
                "when": {
                    "condition_id": branch.condition_id,
                    "operator": operator,
                    "values": values,
                },
                "goto_step": branch.goto_step,
                "note": branch.note[:200],
            }
        )
    title = output.title.strip() or question
    return {
        "title": title[:200],
        "goal": {
            "expected_result": (output.expected_result.strip() or title)[:2000],
            "intent_examples": [question[:2000]],
            "match_terms": _clean(output.match_terms, 30),
        },
        "conditions": conditions,
        "steps": steps,
        "branches": branches,
        "references": [
            {
                "document_id": item.document_id,
                "title": (item.file_name or item.name)[:200],
                "section_path": [item.section[:200]] if item.section else [],
            }
            for item in references
            if item.document_id
        ],
        "handoff": {
            "contact": output.handoff_contact[:200],
            "conditions": _clean(output.handoff_conditions, 20),
        },
    }


def build_item(
    row: SupportGuideProcessRow,
    output: GuideDraftOutput,
    documents: Sequence[KnowledgeDocument],
) -> SupportGuideProcessItem:
    """LLM の応答を業務ガイドの下書きにし、資料の対応と検証の結果を付ける。"""
    references, issues = _references(output, documents)
    rules = [
        SupportGuideRuleCandidate(
            title=rule.title[:200] or rule.content[:40],
            triggers=_clean(rule.triggers, 32, 160),
            content=rule.content[:2000],
        )
        for rule in output.rule_candidates[:10]
        if rule.content.strip()
    ]
    content: SupportGuideContent | None = None
    try:
        content = SupportGuideContent.model_validate(
            _content_payload(output, row.question, references)
        )
    except ValidationError as error:
        issues.extend(
            SupportGuideIssue(
                severity="error",
                code="invalid_content",
                path=".".join(str(part) for part in item["loc"]),
                message=str(item["msg"]),
            )
            for item in error.errors()[:20]
        )
    if content is not None:
        issues = [*validate_content(content), *issues]
    return SupportGuideProcessItem(
        row=row.row,
        question=row.question,
        valid=content is not None and not has_errors(issues),
        content=content,
        issues=issues,
        references=references,
        rule_candidates=rules,
    )


def failed_item(row: SupportGuideProcessRow, message: str) -> SupportGuideProcessItem:
    """下書きを作れなかった行（LLM の失敗・応答の形の誤り）。"""
    return SupportGuideProcessItem(
        row=row.row,
        question=row.question,
        valid=False,
        issues=[SupportGuideIssue(severity="error", code="draft_failed", path="", message=message)],
    )


DraftCaller = Callable[
    [Sequence[SupportGuideProcessRow], Callable[[SupportGuideProcessRow, Attempt], Awaitable[str]]],
    Awaitable[list[tuple[str | None, str | None]]],
]


async def draft_guides(
    rows: Sequence[SupportGuideProcessRow],
    documents: Sequence[KnowledgeDocument],
    *,
    llm: GuideDraftLlm,
    headings: Mapping[str, str],
    run: DraftCaller,
) -> list[SupportGuideProcessItem]:
    """行ごとに LLM で下書きを作る。

    ``run`` は上限つき並行で呼び、行の順に (応答, 失敗の文) を返す。
    """

    async def once(row: SupportGuideProcessRow, _: Attempt) -> str:
        return await llm.generate(
            GUIDE_DRAFT_PROMPT,
            draft_context(row, headings),
            system_prompt=GUIDE_DRAFT_SYSTEM_PROMPT,
            response_schema=GUIDE_DRAFT_RESPONSE_SCHEMA,
            response_schema_name="support_guide_draft",
        )

    outcomes = await run(rows, once)
    items: list[SupportGuideProcessItem] = []
    for row, (raw, error) in zip(rows, outcomes, strict=True):
        if error is not None or raw is None:
            items.append(failed_item(row, error or "下書きを作れませんでした。"))
            continue
        try:
            output = parse_draft_output(raw)
        except ValueError as exc:
            logger.warning("support guide draft invalid", extra={"row": row.row})
            items.append(failed_item(row, f"下書きを作れませんでした（{exc}）。"))
            continue
        items.append(build_item(row, output, documents))
    return items


__all__ = [
    "GUIDE_DRAFT_PROMPT",
    "GUIDE_DRAFT_RESPONSE_SCHEMA",
    "GUIDE_DRAFT_SYSTEM_PROMPT",
    "PROCESS_WORKBOOK_MAX_BYTES",
    "GuideDraftOutput",
    "KnowledgeDocument",
    "ProcessSection",
    "build_item",
    "draft_context",
    "draft_guides",
    "failed_item",
    "parse_draft_output",
    "process_headings",
    "read_process_workbook",
    "resolve_document_name",
    "split_process_sections",
]
