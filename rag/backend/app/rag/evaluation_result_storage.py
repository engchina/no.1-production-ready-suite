"""品質評価の結果を Oracle の JSON 型の列に収める（#4103）。

`rag_evaluation_jobs.result_json` と `rag_evaluation_runs.result_json` は JSON 型で、1 つの値は
OSON で 32MiB まで（超えると ORA-40604）。結果の大半は各ケースの回答の診断
（`case_results[].diagnostics.answer`）で、ケースの数に比例して上限を超える。

上限を超えそうな結果は、回答の診断だけを外して保存する。指標・判定・ケースごとの得点（画面と
Excel が使う項目）は残す。外した診断は回答の記録（`rag_answer_records.diagnostics_json`）に
`trace_id` で残っている。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.clients.oracle import _json_dumps

logger = logging.getLogger(__name__)

# 保存する JSON の文字列（UTF-8）の上限。OSON の大きさは値の形で変わる（実 DB の測定で、実際の結果に
# 近い形は文字列の約 0.92 倍、短い文字列だけの配列は約 1.2 倍）。1.3 倍でも 32MiB に収まる値にし、
# それでも超える形は job の完了の書き直し（`EvaluationJobService._finish`）が診断を外して通す。
EVALUATION_RESULT_MAX_JSON_BYTES = 24 * 1024 * 1024


def without_answer_diagnostics(result: Mapping[str, Any]) -> dict[str, Any]:
    """各ケースの回答の診断を外した結果を返す（比べたプロファイルの結果も。元の値は変えない）。"""
    compact: dict[str, Any] = {**result, "answer_diagnostics_omitted": True}
    case_results = result.get("case_results")
    if isinstance(case_results, list):
        compact["case_results"] = [_case_without_answer_diagnostics(case) for case in case_results]
    comparison = result.get("comparison")
    if isinstance(comparison, Mapping) and isinstance(comparison.get("metrics"), Mapping):
        compact["comparison"] = {
            **comparison,
            "metrics": without_answer_diagnostics(comparison["metrics"]),
        }
    return compact


def _case_without_answer_diagnostics(case: Any) -> Any:
    if not isinstance(case, Mapping) or not isinstance(case.get("diagnostics"), Mapping):
        return case
    return {**case, "diagnostics": {**case["diagnostics"], "answer": None}}


def fit_evaluation_result(
    result: Mapping[str, Any], *, max_bytes: int = EVALUATION_RESULT_MAX_JSON_BYTES
) -> Mapping[str, Any]:
    """JSON 型の列に収まる結果を返す。上限以下ならそのまま、超えるなら回答の診断を外す。"""
    size = len(_json_dumps(result).encode("utf-8"))
    if size <= max_bytes:
        return result
    logger.warning(
        "evaluation_result_answer_diagnostics_omitted",
        extra={"result_json_bytes": size, "max_json_bytes": max_bytes},
    )
    return without_answer_diagnostics(result)
