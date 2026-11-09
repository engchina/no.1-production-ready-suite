"""分割読取の契約違反を、原文を含めない診断と再試行指示へ変える。"""

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from rag_engine.generation.citation_wire import bind_wire_range
from rag_engine.resources.structured_failures import failure_summary_fields, longest_validation_errors, structured_failure_details

RANGE_INVALID_CODE = "batch_range_invalid"
UNIT_INVALID_CODE = "batch_unit_invalid"
_PROTOCOL_CODES = {RANGE_INVALID_CODE}


class BatchRangeInvalid(ValueError):
    """指定した番号の範囲が入力に無い。検証の例外の本文は固定のコードで、匿名の診断だけを持つ（#4090）。

    ``reading`` は検査した読取結果。再試行の後も残ったときに、有効な範囲の findings だけを呼び出し側が使う。
    """

    def __init__(self, diagnostic: Mapping[str, Any], *, reading: Any = None) -> None:
        super().__init__(RANGE_INVALID_CODE)
        self.diagnostic = dict(diagnostic)
        self.reading = reading


def batch_range_invalid(error: Exception) -> BatchRangeInvalid | None:
    """構造化出力の検証の失敗が番号の範囲の不合格だけのとき、その例外。ほかの違反が混ざれば None。"""
    if not isinstance(error, ValidationError):
        return None
    causes = [(row.get("ctx") or {}).get("error") for row in error.errors(include_input=False, include_url=False)]
    return causes[0] if len(causes) == 1 and isinstance(causes[0], BatchRangeInvalid) else None


_LINE_LABEL = re.compile(r"(B\d+)-(\d+)")


def parse_line_range(start_line: str, end_line: str) -> tuple[str, int, int]:
    """行頭の番号の組から、入力の番号と行の範囲。形が違えば行を 0 にする（``range_error_reason`` が拒む）。"""
    start, end = _LINE_LABEL.fullmatch(start_line.strip()), _LINE_LABEL.fullmatch(end_line.strip())
    if start is None or end is None:
        return "", 0, 0
    if start.group(1) != end.group(1):
        return "", -1, -1
    return start.group(1), int(start.group(2)), int(end.group(2))


def range_error_reason(start_line: str, end_line: str, unit_counts: Mapping[str, int]) -> str | None:
    """番号の範囲の不合格の理由。有効なら None。"""
    evidence_id, first, last = parse_line_range(start_line, end_line)
    if first == 0:
        return "line_label_invalid"
    if first < 0:
        return "range_across_inputs"
    if evidence_id not in unit_counts:
        return "evidence_id_unknown"
    code = bind_wire_range(first, last, unit_count=unit_counts[evidence_id], numbered=True).reason_code
    return {"citation_range_out_of_bounds": "range_out_of_bounds", "citation_range_invalid": "range_inverted"}.get(code or "", code)




def batch_validation_detail(error: Exception, schema: Mapping[str, Any]) -> dict[str, Any]:
    """共有の匿名診断へ、分割読取の固定プロトコル違反だけを添える。"""
    detail = {"schema": "BatchReading", **structured_failure_details(error, schema)}
    if isinstance(error, ValidationError):
        rows = error.errors(include_input=False, include_url=False)
        for safe, row in zip(detail.get("validation_errors", []), rows, strict=False):
            # msg / input / 任意の ctx は渡さず、既知の固定コードだけを採用する。
            location = row.get("loc", ())
            if (
                row.get("type") == "literal_error"
                and len(location) == 4
                and location[0] == "findings"
                and isinstance(location[1], int)
                and location[2] == "unit_ids"
                and isinstance(location[3], int)
            ):
                safe["protocol_code"] = UNIT_INVALID_CODE
            protocol_error = (row.get("ctx") or {}).get("error")
            if isinstance(protocol_error, ValueError) and protocol_error.args in [(code,) for code in _PROTOCOL_CODES]:
                safe["protocol_code"] = protocol_error.args[0]
            if isinstance(protocol_error, BatchRangeInvalid):
                safe["range_mismatch"] = protocol_error.diagnostic
    return detail


def batch_failure_summary(receipt: Mapping[str, Any], error: Exception) -> dict[str, Any]:
    """失敗した組の類別・件数・容量（質問・原文を持たない）。詳しい記録は検索の工程の ``generation_evidence_batch``。"""
    attempts = receipt.get("validation_failures") or []
    # 前の試行の検証の失敗の後に別の理由（stream の失敗など）で終わったなら、最後の失敗の理由を残す。
    last = attempts[-1] if attempts and isinstance(error, ValidationError) else structured_failure_details(error)
    errors = last.get("validation_errors", ())
    code = next((row["protocol_code"] for row in errors if row.get("protocol_code")), None)
    mismatch = next((row["range_mismatch"] for row in errors if row.get("range_mismatch")), None)
    return {
        "category": code or last["category"],
        **failure_summary_fields(last),
        "batch_index": receipt["index"],
        "input_count": receipt["input_count"],
        "input_chars": receipt["input_chars"],
        "invalid_attempts": len(attempts),
        **({"unmatched_count": mismatch["unmatched_count"], "unmatched_reasons": mismatch["reasons"]} if mismatch else {}),
    }


def repair_feedback_upper_bound(unit_counts: Mapping[str, int]) -> dict[str, Any]:
    """``batch_repair_prompt`` の指摘が最も長くなる診断。組を分けるときに、この指摘の入る余裕を確保する（#4229）。"""
    return {
        "validation_errors": [
            *({**row, "protocol_code": RANGE_INVALID_CODE} for row in longest_validation_errors()),
            {
                "range_mismatch": {
                    "unit_counts": dict(unit_counts),
                    "input_count": len(unit_counts),
                    "reasons": {"evidence_id_unknown": 1},
                }
            },
        ]
    }


def batch_repair_prompt(original: str, detail: Mapping[str, Any]) -> str:
    """失敗した欄の指摘を同じ入力の後へ付け、全文を読み直した応答を求める。"""
    guidance = [
        "前の読取結果は指定形式の検査を通りませんでした。次の指摘を直し、同じ入力全体について完全な読取結果を返してください。",
    ]
    errors = detail.get("validation_errors", [])
    if any(entry.get("protocol_code") == UNIT_INVALID_CODE for entry in errors):
        guidance.append(
            "unit_ids には、同じ入力の原文の隣に表示された引用単位 ID だけを選んでください。存在しない ID を作らないでください。"
        )
    mismatches = [entry["range_mismatch"] for entry in errors if entry.get("range_mismatch")]
    if mismatches:
        guidance.append(
            "各 findings の start_line と end_line には、同じ入力の行頭の番号（例: B1-3）をそのまま、開始以上の終了で指定してください。"
        )
        guidance.extend(
            f"{key} の行は {key}-1〜{key}-{count} です。" for mismatch in mismatches for key, count in mismatch["unit_counts"].items()
        )
        guidance.extend(
            f"evidence_id は B1〜B{mismatch['input_count']} です。"
            for mismatch in mismatches
            if mismatch["reasons"].get("evidence_id_unknown")
        )
    # 照合の診断（range_mismatch）の件数はモデルに渡さない。指摘の形は検査の型・位置・固定コードと、上の番号の範囲だけ。
    shown = [{key: value for key, value in entry.items() if key != "range_mismatch"} for entry in errors]
    return (
        original
        + "\n\n## Step 4: 読取結果の形式を修正する\n"
        + "\n".join(guidance)
        + "\n"
        + json.dumps({"validation_errors": shown}, ensure_ascii=False)
    )
