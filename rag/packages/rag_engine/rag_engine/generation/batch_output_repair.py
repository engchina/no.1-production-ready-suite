"""分割読取の契約違反を、原文を含めない診断と再試行指示へ変える。"""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from rag_engine.resources.structured_failures import structured_failure_details

_PROTOCOL_CODES = {"batch_quote_not_in_input", "batch_read_coverage_incomplete"}


def batch_validation_detail(error: Exception, schema: Mapping[str, Any]) -> dict[str, Any]:
    """共有の匿名診断へ、分割読取の固定プロトコル違反だけを添える。"""
    detail = {"schema": "BatchReading", **structured_failure_details(error, schema)}
    if isinstance(error, ValidationError):
        rows = error.errors(include_input=False, include_url=False)
        for safe, row in zip(detail.get("validation_errors", []), rows, strict=False):
            # msg / input / 任意の ctx は渡さず、既知の固定コードだけを採用する。
            protocol_error = (row.get("ctx") or {}).get("error")
            if isinstance(protocol_error, ValueError) and protocol_error.args in [(code,) for code in _PROTOCOL_CODES]:
                safe["protocol_code"] = protocol_error.args[0]
    return detail


def batch_repair_prompt(original: str, detail: Mapping[str, Any]) -> str:
    """失敗した欄の指摘を同じ入力の後へ付け、全文を読み直した応答を求める。"""
    guidance = [
        "前の読取結果は指定形式の検査を通りませんでした。次の指摘を直し、同じ入力全体について完全な読取結果を返してください。",
    ]
    errors = detail.get("validation_errors", [])
    if any("read_receipts" in entry["loc"] or entry.get("protocol_code") == "batch_read_coverage_incomplete" for entry in errors):
        guidance.append(
            "read_receipts は入力の全 evidence_id を記録してください。"
            "本文を実際に確認した入力だけを true とし、確認できない入力を確認済みに変えないでください。"
        )
    if any("quote" in entry["loc"] or entry.get("protocol_code") == "batch_quote_not_in_input" for entry in errors):
        guidance.append(
            "各 findings の evidence_id と quote を対応する入力原文に照合し、"
            "連続して存在する原文を quote に使ってください。原文を補作しないでください。"
        )
    return (
        original
        + "\n\n## Step 4: 読取結果の形式を修正する\n"
        + "\n".join(guidance)
        + "\n"
        + json.dumps({"validation_errors": errors}, ensure_ascii=False)
    )
