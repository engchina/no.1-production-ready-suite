"""品質評価の評価リクエストの Excel（#2065）。

画面の「評価リクエスト」は、テンプレートの Excel（1 行 = 1 つの評価のケース）を読み、評価の job の
API（`POST /api/evaluation/jobs/run`）のケース（`EvaluationCase`）にする。列は次の 3 つ。

- 質問（必須）: 利用者が尋ねる文（Ragas の `user_input`、Azure AI の `query`）。
- 期待する根拠（任意）: 回答の根拠になるべき資料（Ragas の `reference_contexts`、TREC の qrels）。
  1 行に 1 つ、`<ファイル名>` の後に `|` で区切って、任意でページ（`p.12` / `p.12-14`）・章節
  （`章節: <見出し>`）・本文の抜粋（`抜粋: <本文の語句>`）を書く。ファイル名は選んだナレッジベースの
  文書の名前と完全に一致させる（NFKC・大小文字・前後の空白は無視）。
- 標準回答（任意）: 期待する回答（Ragas の `reference`、Azure AI の `ground_truth`）。

期待する根拠と標準回答の両方が空の行は、資料に答えが無い質問（回答を控えるのが正しい）として扱う
（`EvaluationCase.expects_answer`）。

照合の規則:
- ファイル名だけの根拠は正解の文書（`relevant_document_ids`）にし、正解文書の再現率・順位で測る。
- ページ・章節・抜粋のある根拠は、正解の文書に加えて必要な根拠（`required_evidence`）にし、回答の
  引用にその条件をすべて満たす chunk があるかで測る（`evaluation.evidence_matches_chunk`）。

読み込みは 2 段で、構造の検証（`parse_evaluation_workbook`。ナレッジベースを見ない）と、ファイル名の
解決（`build_evaluation_cases`。選んだナレッジベースの文書で引く）に分ける。どちらの誤りも行・列・
理由の形（`EvaluationImportIssue`）で返し、1 つでもあればケースを作らない。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

# openpyxl は rag-engine[excel] が入れる（新しい依存は足さない）。
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]
from openpyxl.comments import Comment  # type: ignore[import-untyped]
from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.schemas.evaluation import (
    EVIDENCE_LABEL_MAX_CHARS,
    EVIDENCE_SECTION_MAX_CHARS,
    EVIDENCE_TEXT_MAX_CHARS,
    STANDARD_ANSWER_MAX_CHARS,
    EvaluationCase,
    EvaluationEvidence,
    EvaluationImportIssue,
)

# アップロードの上限。評価は 1 行ごとに回答を生成するため、行数は job の時間の上限
# （`evaluation_job_time_limit_seconds`）に収まる量にする。
EVALUATION_EXCEL_MAX_BYTES = 10 * 1024 * 1024
EVALUATION_EXCEL_MAX_ROWS = 200
# 1 行の質問の長さの上限（文字）。
EVALUATION_QUESTION_MAX_CHARS = 2000
# 1 つのセルの「期待する根拠」の行数の上限（`EvaluationCase.required_evidence` の上限と同じ）。
EVALUATION_EVIDENCE_MAX_LINES = 30
# 画面に返す誤りの件数の上限（それより多い分は件数だけを返す）。
EVALUATION_IMPORT_MAX_ISSUES = 100
# ページの番号の上限（Excel の書き間違いを止める）。
_MAX_PAGE = 100_000

CASES_SHEET_TITLE = "評価リクエスト"
GUIDE_SHEET_TITLE = "記入の説明"
QUESTION_HEADER = "質問"
EVIDENCE_HEADER = "期待する根拠"
STANDARD_ANSWER_HEADER = "標準回答"
HEADERS = (QUESTION_HEADER, EVIDENCE_HEADER, STANDARD_ANSWER_HEADER)
TEMPLATE_FILE_NAME = "evaluation_request_template.xlsx"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 見出しの別名（業界の評価データの列名でも読めるようにする。比較は NFKC・小文字・空白なし）。
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "question": (QUESTION_HEADER, "question", "query", "user_input", "質問文"),
    "evidence": (
        EVIDENCE_HEADER,
        "根拠",
        "正解の根拠",
        "reference_contexts",
        "contexts",
        "context",
        "evidence",
    ),
    "standard_answer": (
        STANDARD_ANSWER_HEADER,
        "回答",
        "期待する回答",
        "reference",
        "ground_truth",
        "expected_answer",
        "standard_answer",
    ),
}
_COLUMN_LABELS = {
    "question": QUESTION_HEADER,
    "evidence": EVIDENCE_HEADER,
    "standard_answer": STANDARD_ANSWER_HEADER,
}
# 見出しの末尾の注記（「質問（必須）」など）は無視する。
_HEADER_NOTE = re.compile(r"[（(][^）)]*[）)]\s*$")
_PART_SEPARATOR = re.compile(r"\s*[|｜]\s*")
_PAGE_PATTERN = re.compile(
    r"(?:p\.?|pp\.?|page|ページ|頁)?\s*(\d+)\s*(?:[-~〜～–—]\s*(\d+))?\s*(?:ページ|頁)?",
    re.IGNORECASE,
)
_SECTION_PREFIXES = ("章節", "見出し", "section")
_EXCERPT_PREFIXES = ("抜粋", "本文", "excerpt", "text")


@dataclass(frozen=True)
class EvidenceSpec:
    """「期待する根拠」の 1 行を読んだもの（ファイル名はまだ文書 ID にしていない）。"""

    line: str
    file_name: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    text: str | None = None

    @property
    def has_conditions(self) -> bool:
        return self.page_start is not None or self.section is not None or self.text is not None


@dataclass(frozen=True)
class EvaluationExcelRow:
    """Excel の 1 行（構造の検証を通ったもの）。"""

    row: int
    question: str
    evidence: tuple[EvidenceSpec, ...] = ()
    standard_answer: str | None = None

    @property
    def case_id(self) -> str:
        return evaluation_case_id(self.row)


@dataclass(frozen=True)
class EvaluationDocumentRef:
    """ファイル名の解決に使う、ナレッジベースの文書の参照。"""

    document_id: str
    file_name: str
    superseded_by_file_name: str | None = None
    superseded: bool = False


@dataclass
class EvaluationExcelParse:
    """Excel の構造の検証の結果。"""

    rows: list[EvaluationExcelRow] = field(default_factory=list)
    issues: list[EvaluationImportIssue] = field(default_factory=list)


def evaluation_case_id(row: int) -> str:
    """Excel の行番号から作るケースの id（結果の表で行に戻せるようにする）。"""
    return f"row-{row}"


def normalize_file_name(value: str) -> str:
    """ファイル名の比較の形（NFKC・前後の空白なし・大小文字を区別しない）。"""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _normalized_header(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _HEADER_NOTE.sub("", text).strip()
    return re.sub(r"\s+", "", text).casefold()


_ALIAS_LOOKUP = {
    _normalized_header(alias): key for key, aliases in _HEADER_ALIASES.items() for alias in aliases
}


def _cell_text(value: object) -> str:
    """セルの値を文字にする（数値・日付も文字にする。改行は LF にそろえる）。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def parse_evaluation_workbook(data: bytes) -> EvaluationExcelParse:
    """Excel の構造（見出し・必須・長さ・行数・根拠の書き方）を検証して行を読む。

    シートは「評価リクエスト」（無ければ最初のシート）。1 行目が見出し、2 行目からがケース。
    すべての列が空の行は飛ばす。誤りは行・列・理由で返す（`rows` は誤りの無い行だけ）。
    """
    result = EvaluationExcelParse()
    if len(data) > EVALUATION_EXCEL_MAX_BYTES:
        result.issues.append(
            _file_issue(
                f"ファイルが大きすぎます（上限 {EVALUATION_EXCEL_MAX_BYTES // (1024 * 1024)}MB）。"
                "行を分けて複数のファイルにしてください。"
            )
        )
        return result
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - 形式の誤りは利用者へ返す
        result.issues.append(
            _file_issue("Excel（.xlsx）として読めません。テンプレートの形式で保存してください。")
        )
        return result
    try:
        sheet = _cases_sheet(book)
        if sheet is None:
            result.issues.append(_file_issue("シートがありません。"))
            return result
        rows = sheet.iter_rows(values_only=True)
        first: tuple[object, ...] | None = next(rows, None)
        header = tuple(first or ())
        columns = _header_columns(header)
        if "question" not in columns:
            result.issues.append(
                EvaluationImportIssue(
                    row=1,
                    column=None,
                    message=(
                        f"1 行目に「{QUESTION_HEADER}」の見出しがありません。"
                        "テンプレートの見出し（質問・期待する根拠・標準回答）を使ってください。"
                    ),
                )
            )
            return result
        data_rows = 0
        for number, values in enumerate(rows, start=2):
            cells = {key: _cell_text(_at(values, index)) for key, index in columns.items()}
            if not any(cells.values()):
                continue
            data_rows += 1
            if data_rows > EVALUATION_EXCEL_MAX_ROWS:
                result.issues.append(
                    _file_issue(
                        f"評価のケースは {EVALUATION_EXCEL_MAX_ROWS} 行までです。"
                        "ファイルを分けて評価してください。"
                    )
                )
                result.rows.clear()
                return result
            row, issues = _parse_row(number, cells)
            result.issues.extend(issues)
            if row is not None:
                result.rows.append(row)
        if data_rows == 0:
            result.issues.append(
                _file_issue("評価のケースが 1 行もありません。2 行目から質問を書いてください。")
            )
        return result
    finally:
        book.close()


def _cases_sheet(book: Any) -> Any:
    for sheet in book.worksheets:
        if str(sheet.title).strip() == CASES_SHEET_TITLE:
            return sheet
    for sheet in book.worksheets:
        if str(sheet.title).strip() != GUIDE_SHEET_TITLE:
            return sheet
    return None


def _header_columns(header: Sequence[object]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, value in enumerate(header):
        key = _ALIAS_LOOKUP.get(_normalized_header(value))
        if key and key not in found:
            found[key] = index
    return found


def _at(values: Sequence[object], index: int) -> object:
    return values[index] if index < len(values) else None


def _file_issue(message: str) -> EvaluationImportIssue:
    return EvaluationImportIssue(row=None, column=None, message=message)


def _parse_row(
    number: int, cells: Mapping[str, str]
) -> tuple[EvaluationExcelRow | None, list[EvaluationImportIssue]]:
    issues: list[EvaluationImportIssue] = []

    def issue(column: str, message: str) -> None:
        issues.append(
            EvaluationImportIssue(row=number, column=_COLUMN_LABELS[column], message=message)
        )

    question = cells.get("question", "")
    if not question:
        issue("question", "質問を入力してください。")
    elif len(question) > EVALUATION_QUESTION_MAX_CHARS:
        issue(
            "question",
            f"質問は {EVALUATION_QUESTION_MAX_CHARS} 文字以内にしてください"
            f"（{len(question)} 文字）。",
        )
    standard_answer = cells.get("standard_answer", "") or None
    if standard_answer is not None and len(standard_answer) > STANDARD_ANSWER_MAX_CHARS:
        issue(
            "standard_answer",
            f"標準回答は {STANDARD_ANSWER_MAX_CHARS} 文字以内にしてください"
            f"（{len(standard_answer)} 文字）。",
        )
    lines = [line.strip() for line in cells.get("evidence", "").split("\n") if line.strip()]
    if len(lines) > EVALUATION_EVIDENCE_MAX_LINES:
        issue(
            "evidence",
            f"期待する根拠は {EVALUATION_EVIDENCE_MAX_LINES} 行までにしてください"
            f"（{len(lines)} 行）。",
        )
        lines = []
    evidence: list[EvidenceSpec] = []
    for index, line in enumerate(lines, start=1):
        parsed = parse_evidence_line(line)
        if isinstance(parsed, str):
            issue("evidence", f"{index} 行目: {parsed}")
        else:
            evidence.append(parsed)
    if issues:
        return None, issues
    return (
        EvaluationExcelRow(
            row=number,
            question=question,
            evidence=tuple(evidence),
            standard_answer=standard_answer,
        ),
        [],
    )


def parse_evidence_line(line: str) -> EvidenceSpec | str:
    """「期待する根拠」の 1 行を読む。読めなければ理由の文を返す。

    形: `<ファイル名> | p.12 | 章節: <見出し> | 抜粋: <本文の語句>`
    （ファイル名の後は任意・順不同）。
    """
    if len(line) > EVIDENCE_LABEL_MAX_CHARS:
        return f"1 行は {EVIDENCE_LABEL_MAX_CHARS} 文字以内にしてください。"
    head, *parts = _PART_SEPARATOR.split(line.strip())
    file_name = head.strip()
    if not file_name:
        return "先頭に文書のファイル名を書いてください（例: 経費精算規程.pdf | p.3）。"
    if _labelled_value(file_name, _SECTION_PREFIXES + _EXCERPT_PREFIXES) is not None or (
        _page_range(file_name) is not None
    ):
        return (
            "先頭に文書のファイル名を書いてください"
            "（ページ・章節・抜粋はファイル名の後に書きます）。"
        )
    page: tuple[int, int | None] | None = None
    section: str | None = None
    text: str | None = None
    for raw in parts:
        part = raw.strip()
        if not part:
            continue
        if (value := _labelled_value(part, _SECTION_PREFIXES)) is not None:
            if section is not None:
                return "章節は 1 行に 1 つにしてください（複数の章節は行を分けて書きます）。"
            if not value:
                return "「章節:」の後に見出しを書いてください。"
            if len(value) > EVIDENCE_SECTION_MAX_CHARS:
                return f"章節は {EVIDENCE_SECTION_MAX_CHARS} 文字以内にしてください。"
            section = value
        elif (value := _labelled_value(part, _EXCERPT_PREFIXES)) is not None:
            if text is not None:
                return "抜粋は 1 行に 1 つにしてください（複数の抜粋は行を分けて書きます）。"
            if not value:
                return "「抜粋:」の後に本文の語句を書いてください。"
            if len(value) > EVIDENCE_TEXT_MAX_CHARS:
                return f"抜粋は {EVIDENCE_TEXT_MAX_CHARS} 文字以内にしてください。"
            text = value
        elif (pages := _page_range(part)) is not None:
            if page is not None:
                return "ページは 1 行に 1 つにしてください（範囲は p.12-14 のように書きます）。"
            start, end = pages
            if start < 1 or start > _MAX_PAGE or (end is not None and end > _MAX_PAGE):
                return f"ページ「{part}」は 1 以上の番号にしてください。"
            if end is not None and end < start:
                return f"ページ「{part}」の範囲は、小さい番号から書いてください（例: p.12-14）。"
            page = (start, end if end != start else None)
        else:
            return (
                f"「{part}」を読めません。ページは「p.12」「p.12-14」、章節は「章節: 見出し」、"
                "本文は「抜粋: 本文の語句」と書いてください。"
            )
    return EvidenceSpec(
        line=line.strip(),
        file_name=file_name,
        page_start=page[0] if page else None,
        page_end=page[1] if page else None,
        section=section,
        text=text,
    )


def _labelled_value(part: str, prefixes: Iterable[str]) -> str | None:
    """`<接頭辞>: <値>` の値（接頭辞が違えば None）。コロンは全角も受け付ける。"""
    normalized = unicodedata.normalize("NFKC", part)
    for prefix in prefixes:
        if normalized.casefold().startswith(prefix.casefold()):
            rest = normalized[len(prefix) :].lstrip()
            if rest.startswith(":"):
                return rest[1:].strip()
    return None


def _page_range(part: str) -> tuple[int, int | None] | None:
    match = _PAGE_PATTERN.fullmatch(unicodedata.normalize("NFKC", part).strip())
    if match is None:
        return None
    end = match.group(2)
    return int(match.group(1)), int(end) if end is not None else None


def build_evaluation_cases(
    rows: Sequence[EvaluationExcelRow],
    documents: Sequence[EvaluationDocumentRef],
) -> tuple[list[EvaluationCase], list[EvaluationImportIssue]]:
    """読んだ行を評価のケースにする（根拠のファイル名を、選んだナレッジベースの文書 ID にする）。

    ファイル名は完全一致（`normalize_file_name`）。新しい版に置き換えた文書（旧版）は検索の対象外の
    ため、今有効な版があればそれを使い、旧版しか無ければ誤りにする。同じ名前の有効な文書が複数あると
    どれを正解にするか決められないため誤りにする。
    """
    by_name: dict[str, list[EvaluationDocumentRef]] = {}
    for document in documents:
        by_name.setdefault(normalize_file_name(document.file_name), []).append(document)
    cases: list[EvaluationCase] = []
    issues: list[EvaluationImportIssue] = []
    for row in rows:
        relevant: list[str] = []
        required: list[EvaluationEvidence] = []
        row_issues: list[EvaluationImportIssue] = []
        for index, spec in enumerate(row.evidence, start=1):
            resolved, error = _resolve_document(spec.file_name, by_name)
            if resolved is None:
                row_issues.append(
                    EvaluationImportIssue(
                        row=row.row, column=EVIDENCE_HEADER, message=f"{index} 行目: {error}"
                    )
                )
                continue
            if resolved not in relevant:
                relevant.append(resolved)
            if spec.has_conditions:
                required.append(
                    EvaluationEvidence(
                        id=f"e{index}",
                        document_id=resolved,
                        text=spec.text,
                        page_start=spec.page_start,
                        page_end=spec.page_end,
                        section=spec.section,
                        label=spec.line,
                    )
                )
        if row_issues:
            issues.extend(row_issues)
            continue
        try:
            cases.append(
                EvaluationCase(
                    id=row.case_id,
                    query=row.question,
                    relevant_document_ids=relevant,
                    required_evidence=required,
                    standard_answer=row.standard_answer,
                )
            )
        except ValidationError as exc:
            issues.append(
                EvaluationImportIssue(row=row.row, column=None, message=_validation_message(exc))
            )
    return cases, issues


def _resolve_document(
    file_name: str, by_name: Mapping[str, Sequence[EvaluationDocumentRef]]
) -> tuple[str | None, str]:
    """ファイル名の文書 ID と、解決できないときの理由（解決できたら空）。"""
    candidates = by_name.get(normalize_file_name(file_name), ())
    if not candidates:
        return None, (
            f"「{file_name}」は選んだナレッジベースの文書にありません"
            "（ファイル名を完全に一致させてください）。"
        )
    current = [item for item in candidates if not item.superseded]
    if not current:
        newer = next(
            (item.superseded_by_file_name for item in candidates if item.superseded_by_file_name),
            None,
        )
        hint = f"新しい版（{newer}）" if newer else "新しい版"
        return None, (
            f"「{file_name}」は{hint}に置き換えた文書です。新しい版のファイル名を書いてください。"
        )
    ids = list(dict.fromkeys(item.document_id for item in current))
    if len(ids) > 1:
        return None, (
            f"「{file_name}」という名前の文書が {len(ids)} 件あります。"
            "同じ名前の文書が 1 件になるナレッジベースを選んでください。"
        )
    return ids[0], ""


def _validation_message(exc: ValidationError) -> str:
    messages = [str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors()]
    return (
        "".join(message for message in messages if message) or "この行を評価のケースにできません。"
    )


# --- 書き出し（テンプレートと、フィードバックから作る 1 行） ------------------------------------

_HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
_HEADER_NOTES = {
    QUESTION_HEADER: "必須。利用者が尋ねる文を 1 セルに 1 つ書きます。",
    EVIDENCE_HEADER: (
        "任意。回答の根拠になるべき資料を 1 行に 1 つ書きます（セルの中で改行して複数）。"
        "形: ファイル名 | p.12 | 章節: 見出し | 抜粋: 本文の語句（ファイル名の後は任意）。"
    ),
    STANDARD_ANSWER_HEADER: "任意。期待する回答を書きます。回答をこの文と比べて採点します。",
}
_COLUMN_WIDTHS = (48, 56, 64)

# 「記入の説明」のシート（列の説明・書き方・例）。例は一般の社内規程の合成の内容。
_GUIDE_ROWS: tuple[tuple[str, ...], ...] = (
    ("列", "必須 / 任意", "意味", "書き方"),
    (
        QUESTION_HEADER,
        "必須",
        "利用者が尋ねる文。業界の評価データの question / query / user_input にあたります。",
        f"1 セルに 1 つ。{EVALUATION_QUESTION_MAX_CHARS} 文字まで。",
    ),
    (
        EVIDENCE_HEADER,
        "任意",
        "回答の根拠になるべき資料の場所と内容（正解の文書・ページ・章節・本文の抜粋）。"
        "reference_contexts / qrels にあたります。",
        "1 行に 1 つの根拠（セルの中で改行して複数。30 行まで）。"
        "先頭に文書のファイル名（選ぶナレッジベースの文書の名前と完全に一致）を書き、"
        "続けて「|」で区切って、任意でページ「p.12」「p.12-14」、章節「章節: 見出し」、"
        f"本文の抜粋「抜粋: 本文の語句」（{EVIDENCE_TEXT_MAX_CHARS} 文字まで）を書きます。",
    ),
    (
        STANDARD_ANSWER_HEADER,
        "任意",
        "期待する回答。reference / ground_truth にあたります。",
        f"{STANDARD_ANSWER_MAX_CHARS} 文字まで。書くと、回答をこの文と比べて採点します"
        "（標準回答での合格・主張の裏付け・必要な内容の網羅）。",
    ),
    (),
    ("照合の規則",),
    (
        "ファイル名だけ",
        "",
        "その文書が回答に引用されたか（正解文書の再現率・順位）で測ります。",
        "例: 経費精算規程.pdf",
    ),
    (
        "ページ・章節・抜粋つき",
        "",
        "引用の中に、書いた条件をすべて満たす箇所があるか（必要な根拠の再現率）でも測ります。"
        "ページは範囲が重なれば一致、章節は見出しの一部が一致、抜粋は本文に含まれれば一致です"
        "（全角・半角・大小文字・空白の違いは無視）。",
        "例: 経費精算規程.pdf | p.3-4 | 章節: 申請の手続き | 抜粋: 部門長が承認する",
    ),
    (
        "根拠も標準回答も空",
        "",
        "資料に答えが無い質問として、回答を控えたか（回答を控える判断の正しさ）を測ります。",
        "例: 社員食堂の来月の献立を教えてください。",
    ),
    (),
    ("記入例", "", "", ""),
    (QUESTION_HEADER, EVIDENCE_HEADER, STANDARD_ANSWER_HEADER, ""),
    (
        "経費精算の締め日はいつですか？",
        "経費精算規程.pdf | p.3",
        "毎月 25 日が締め日で、過ぎた分は翌月の精算になります。",
        "",
    ),
    (
        "出張の宿泊費の上限を教えてください。",
        "出張旅費規程.docx | 章節: 宿泊費\n出張旅費規程.docx | 抜粋: 1 泊あたりの上限",
        "",
        "",
    ),
    ("社員食堂の来月の献立を教えてください。", "", "", ""),
    (),
    (
        "上限",
        "",
        f"1 ファイル {EVALUATION_EXCEL_MAX_ROWS} 行・"
        f"{EVALUATION_EXCEL_MAX_BYTES // (1024 * 1024)}MB まで。",
        "",
    ),
    (
        "注意",
        "",
        f"ケースは「{CASES_SHEET_TITLE}」のシートの 2 行目から書きます。"
        "1 行目の見出しは変えないでください。"
        "すべての列が空の行は飛ばします。",
        "",
    ),
)


def template_xlsx() -> bytes:
    """評価リクエストのテンプレート（見出しだけのケースのシートと「記入の説明」のシート）。"""
    return evaluation_cases_xlsx([])


def evaluation_cases_xlsx(rows: Sequence[tuple[str, Sequence[str], str | None]]) -> bytes:
    """評価リクエストの Excel（テンプレートと同じ形）。`rows` は（質問, 根拠の行, 標準回答）。"""
    book = Workbook()
    sheet = book.active or book.create_sheet()
    sheet.title = CASES_SHEET_TITLE
    sheet.append(list(HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
        cell.comment = Comment(_HEADER_NOTES[str(cell.value)], "RAG")
    for question, evidence, standard_answer in rows:
        sheet.append([question, "\n".join(evidence), standard_answer or ""])
    for column, width in zip("ABC", _COLUMN_WIDTHS, strict=True):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    guide = book.create_sheet(GUIDE_SHEET_TITLE)
    for values in _GUIDE_ROWS:
        guide.append(list(values))
    for cell in guide[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
    for row in guide.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.value in {"照合の規則", "記入例", "上限", "注意"}:
                cell.font = Font(bold=True)
    for column, width in zip("ABCD", (24, 16, 64, 72), strict=True):
        guide.column_dimensions[column].width = width
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def evidence_line(file_name: str, page: int | None = None) -> str:
    """根拠の 1 行（フィードバックの引用から作る）。"""
    return f"{file_name} | p.{page}" if page else file_name
