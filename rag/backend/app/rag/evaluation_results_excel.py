"""品質評価の結果を Excel に出力する。

アップロードした Excel がある評価は、そのファイルに結果の列を足す（#4212。
`evaluation_workbook_with_results`）。無い評価（API で直接投入した評価など）は、結果だけの
ファイルを作る（`evaluation_results_xlsx`）。
"""

from collections.abc import Mapping
from io import BytesIO
from typing import Any

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # type: ignore[import-untyped]
from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]

from app.rag.evaluation_excel import evaluation_case_id, parse_evaluation_workbook
from app.rag.xlsx_append_columns import (
    AppendedColumn,
    CellValue,
    XlsxAppendError,
    append_columns,
)
from app.schemas.evaluation import (
    EvaluationCaseResult,
    EvaluationMetrics,
    EvaluationResultExportRequest,
)

METRICS = (
    ("required_evidence_recall", "required_evidence_recall", "必要な根拠の取得率"),
    ("mrr", "mrr", "正解文書の順位（MRR）"),
    ("answer_correctness", "answer_correctness", "回答の正確性"),
    ("response_decision_accuracy", "response_decision_accuracy", "回答・確認・保留の判断の正しさ"),
    ("precision_at_10", "precision_at_10", "Precision@10（参考）"),
    ("ndcg_at_3", "ndcg_at_3", "NDCG@3（参考）"),
    ("ndcg_at_10", "ndcg_at_10", "NDCG@10（参考）"),
    ("map", "map", "MAP（参考）"),
    ("bleu_1", "bleu_1", "BLEU-1（参考）"),
    ("bleu_2", "bleu_2", "BLEU-2（参考）"),
    ("bleu_4", "bleu_4", "BLEU-4（参考）"),
    ("rouge_1", "rouge_1", "ROUGE-1（参考）"),
    ("rouge_2", "rouge_2", "ROUGE-2（参考）"),
    ("rouge_l", "rouge_l", "ROUGE-L（参考）"),
)
DECISIONS = {"answer": "回答", "clarify": "確認", "hold": "保留"}
# 画面（`evaluation.outcome.*`・`evaluation.failureReason.*`）と同じ文言。無い値は値のまま出す。
OUTCOMES = {
    "answered": "回答",
    "conditional": "条件付きの回答",
    "needs_clarification": "回答前の確認",
    "needs_environment_data": "現場のデータが必要",
    "needs_human": "人への引き継ぎ",
    "insufficient_evidence": "根拠不足（回答を控えた）",
}
FAILURE_REASONS = {
    "retrieval_miss": "正解の文書を取れなかった",
    "partial_recall": "正解の文書の一部だけ取れた",
    "unexpected_answer": "答えるべきでない質問に答えた",
    "unexpected_refusal": "答えるべき質問に答えなかった",
    "answer_keyword_miss": "期待する語が無い",
    "low_groundedness": "根拠に無い語が多い",
    # 次の 3 つは新しい評価では出ない。保存済みの結果に残る理由の表示のために残す（#4781）。
    "claim_unassessed": "主張の支持を確認できていない",
    "unsupported_claim": "根拠のない主張がある",
    "missing_content": "標準回答の必要な項目が抜けている",
    "answer_failed": "標準回答での評価が不合格",
    "answer_evaluation_error": "標準回答で評価できなかった",
    "answer_generation_fallback": "回答文を生成できず参考情報を返した",
    "guardrail_warning": "安全チェックの警告",
    "case_error": "ケースの実行に失敗",
    "unexpected_handling": "対応が期待と違う",
    "step_missing": "手順の不足・順序の誤り",
    "forbidden_action": "危険な操作を提示",
    "condition_missing": "条件に触れていない",
    "evidence_miss": "必要な根拠を取れなかった",
    "known_condition_reasked": "分かっている条件を聞き直した",
    "screen_missing": "案内すべき画面を案内していない",
    "wrong_screen": "別の画面を案内した",
}
CASE_STATUSES = {"success": "成功", "error": "失敗"}
NOT_RUN_LABEL = "未実行"
UNASSESSED_LABEL = "未評価"
ANSWER_RECORD_MISSING_LABEL = "（回答の記録がありません）"
_RESULT_COLUMN_WIDTHS = {"answer": 60, "decision": 20, "status": 12, "failure": 36, "trace": 36}
_ERROR_COLUMN_WIDTH = 48
_METRIC_COLUMN_WIDTH = 14


def _append(sheet: Any, values: list[Any]) -> None:
    sheet.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in values])
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.value = ILLEGAL_CHARACTERS_RE.sub("", cell.value)
            cell.data_type = "s"
        cell.alignment = Alignment(vertical="top", wrap_text=True)


def _format(sheet: Any, widths: list[int]) -> None:
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "1:1"
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def evaluation_results_xlsx(
    metrics: EvaluationMetrics,
    inputs: EvaluationResultExportRequest,
    *,
    partial_of_total: int | None = None,
) -> bytes:
    """全ページ分の結果を出す。文字列は式として評価させない。

    `partial_of_total` は途中までの結果（#4208）の全ケースの数で、概要の末尾に範囲を書く。
    """
    workbook = Workbook()
    overview = workbook.active
    assert overview is not None
    overview.title = "概要"
    groups = [("主条件", inputs.profile_name or "主条件", metrics)]
    if metrics.comparison:
        groups.append(
            (
                "比較対象",
                inputs.compare_profile_name or metrics.comparison.search_answer_profile_id,
                metrics.comparison.metrics,
            )
        )
    _append(overview, ["指標", *[title + ": " + profile for title, profile, _ in groups]])
    _append(overview, ["ケース数", *[result.case_count for _, _, result in groups]])
    for key, _, title in METRICS:
        _append(
            overview,
            [
                title,
                *[
                    getattr(result, key) if getattr(result, key) is not None else "未評価"
                    for _, _, result in groups
                ],
            ],
        )
    if partial_of_total is not None:
        saved = metrics.case_count + (
            metrics.comparison.metrics.case_count if metrics.comparison else 0
        )
        _append(overview, ["途中までの結果", f"全 {partial_of_total} ケースのうち {saved} ケース"])
    rows = {row.case_id: row for row in inputs.rows}
    cases = {case.id: case for case in inputs.cases}
    for title, _profile, result in groups:
        sheet = workbook.create_sheet(title + "の結果")
        _append(
            sheet,
            [
                "ケース ID",
                "質問",
                "期待する根拠",
                "標準回答",
                "必須の回答要点",
                "期待する応答の判断",
                "回答の過程",
                *[label for _, _, label in METRICS],
                "状態",
                "失敗理由",
                "Trace",
                "エラー",
            ],
        )
        for item in result.case_results:
            row = rows.get(item.case_id)
            case = cases.get(item.case_id)
            _append(
                sheet,
                [
                    item.case_id,
                    row.question if row else case.query if case else "",
                    "\n".join(row.evidence) if row else "\n".join(item.relevant_document_ids),
                    (case.standard_answer if case else row.standard_answer_preview if row else "")
                    or "",
                    "\n".join(
                        case.required_answer_points
                        if case
                        else row.required_answer_points or []
                        if row
                        else []
                    ),
                    DECISIONS.get(
                        (
                            case.expected_response_decision
                            if case
                            else row.expected_response_decision
                            if row
                            else None
                        )
                        or "",
                        "未評価",
                    ),
                    row.answer_process or "" if row else "",
                    *[
                        getattr(item, key) if getattr(item, key) is not None else "未評価"
                        for _, key, _ in METRICS
                    ],
                    CASE_STATUSES.get(item.status, item.status),
                    "\n".join(
                        FAILURE_REASONS.get(reason, reason) for reason in item.failure_reasons
                    ),
                    item.trace_id,
                    item.error_message or "",
                ],
            )
            for cell in sheet[sheet.max_row][7:13]:
                cell.number_format = "0.0%"
        _format(sheet, [24, 48, 40, 60, 40, 20, 60, *([20] * 6), 16, 36, 36, 48])
    _format(overview, [36, *([36] * len(groups))])
    for row in overview.iter_rows(min_row=3, min_col=2):
        for cell in row:
            cell.number_format = "0.0%"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _result_columns(prefix: str) -> list[AppendedColumn]:
    return [
        AppendedColumn(f"{prefix}: 回答", width=_RESULT_COLUMN_WIDTHS["answer"]),
        AppendedColumn(f"{prefix}: 応答の判断", width=_RESULT_COLUMN_WIDTHS["decision"]),
        *[
            AppendedColumn(f"{prefix}: {label}", kind="percent", width=_METRIC_COLUMN_WIDTH)
            for _, _, label in METRICS
        ],
        AppendedColumn(f"{prefix}: 状態", width=_RESULT_COLUMN_WIDTHS["status"]),
        AppendedColumn(f"{prefix}: 失敗理由", width=_RESULT_COLUMN_WIDTHS["failure"]),
        AppendedColumn(f"{prefix}: Trace", width=_RESULT_COLUMN_WIDTHS["trace"]),
        AppendedColumn(f"{prefix}: エラー", width=_ERROR_COLUMN_WIDTH),
    ]


def _result_values(
    item: EvaluationCaseResult | None, answers: Mapping[str, str]
) -> list[CellValue]:
    if item is None:
        # 途中までの結果（#4208）で実行していない行。
        blank: list[CellValue] = [None] * (2 + len(METRICS))
        return [*blank, NOT_RUN_LABEL, None, None, None]
    answer: str | None = answers.get(item.trace_id)
    if answer is None and item.status == "success":
        answer = ANSWER_RECORD_MISSING_LABEL
    metrics: list[CellValue] = [
        value if (value := getattr(item, key)) is not None else UNASSESSED_LABEL
        for _, key, _ in METRICS
    ]
    return [
        answer,
        OUTCOMES.get(item.observed_outcome or "", item.observed_outcome),
        *metrics,
        CASE_STATUSES.get(item.status, item.status),
        "\n".join(FAILURE_REASONS.get(reason, reason) for reason in item.failure_reasons) or None,
        item.trace_id,
        item.error_message,
    ]


def evaluation_workbook_with_results(
    data: bytes, metrics: EvaluationMetrics, answers: Mapping[str, str]
) -> bytes:
    """アップロードした Excel のケースの行の右に、結果の列を足したファイルを返す。

    行はケースの ID（行番号から決まる。`evaluation_case_id`）で決め、質問の文字では探さない。結果の
    無い行（途中までの結果）は状態を「未実行」にする。`answers` は trace の ID → 回答の本文。
    """
    parsed = parse_evaluation_workbook(data)
    if parsed.sheet_title is None or not parsed.rows:
        raise XlsxAppendError("ケースのシートを読めません。")
    groups = [("主条件", metrics)]
    if metrics.comparison:
        groups.append(("比較対象", metrics.comparison.metrics))
    columns: list[AppendedColumn] = []
    for title, _result in groups:
        columns.extend(
            _result_columns(f"評価結果（{title}）" if metrics.comparison else "評価結果")
        )
    by_case = [{item.case_id: item for item in result.case_results} for _, result in groups]
    rows: dict[int, list[CellValue]] = {}
    for row in parsed.rows:
        case_id = evaluation_case_id(row.row)
        rows[row.row] = [
            value for results in by_case for value in _result_values(results.get(case_id), answers)
        ]
    return append_columns(data, sheet_title=parsed.sheet_title, columns=columns, rows=rows)
