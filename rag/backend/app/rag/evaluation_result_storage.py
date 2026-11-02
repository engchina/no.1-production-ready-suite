"""品質評価の結果の保存の内容（#4103・#4107）。

`rag_evaluation_jobs.result_json` と `rag_evaluation_runs.result_json` には、各ケースの回答の診断
（`case_results[].diagnostics.answer`）を入れない。診断は質問・回答・根拠の文を、固定の項目だけで
なく工程の表示の文の中にも持つため、項目を選んで外す方法では原文を保存しないことを保証できない。
結果の大半を占める診断を外すので、JSON 型の列の上限（1 つの値は OSON で 32MiB）にも収まる。

指標・判定・ケースごとの得点（画面と Excel が使う項目）は残す。質問の原文は評価セットと回答の
記録に、診断は回答の記録（`rag_answer_records.diagnostics_json`）に `trace_id` で残る。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def without_answer_diagnostics(result: Mapping[str, Any]) -> dict[str, Any]:
    """各ケースの回答の診断を外した結果を返す（比べたプロファイルの結果も。元の値は変えない）。

    ケースの結果が無い結果（判定の校正など）は、外すものが無いので内容を変えない。
    """
    compact: dict[str, Any] = dict(result)
    case_results = result.get("case_results")
    if isinstance(case_results, list):
        compact["answer_diagnostics_omitted"] = True
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
