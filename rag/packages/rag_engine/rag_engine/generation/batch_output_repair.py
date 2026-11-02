"""分割読取の契約違反を、原文を含めない診断と再試行指示へ変える。"""

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from rag_engine.resources.structured_failures import structured_failure_details
from rag_engine.retrieval.character_forms import fold_text, fold_width

_PROTOCOL_CODES = {"batch_quote_not_in_input", "batch_read_coverage_incomplete"}
_QUOTE_NOT_IN_INPUT = "batch_quote_not_in_input"
_MAX_MISMATCH_SAMPLES = 8
_ELISION_MARKS = ("…", "...", "・・・", "‥")
_LITERAL_ESCAPES = ("\\n", "\\t", "\\r", '\\"', "\\u")


class BatchQuoteMismatch(ValueError):
    """quote が入力の原文に無い。検証の例外の本文は固定のコードで、匿名の診断だけを持つ（#4090）。"""

    def __init__(self, diagnostic: Mapping[str, Any]) -> None:
        super().__init__(_QUOTE_NOT_IN_INPUT)
        self.diagnostic = dict(diagnostic)


def _compact(value: str, fold: Callable[[Any], str] = fold_width) -> str:
    """引用の照合（``find_width_folded``）と同じく、文字の幅と空白を同一視した形。"""
    return "".join(fold(value).split())


def _longest_edge_match(target: str, text: str, *, suffix: bool = False) -> int:
    """``target`` の先頭（``suffix`` なら末尾）から ``text`` に連続して現れる字数。短い部分が必ず先に現れるので二分探索できる。"""
    low, high = 0, len(target)
    while low < high:
        middle = (low + high + 1) // 2
        if (target[-middle:] if suffix else target[:middle]) in text:
            low = middle
        else:
            high = middle - 1
    return low


def _unmatched_quote(evidence_id: str, quote: str, inputs: Mapping[str, str], originals: Mapping[str, str]) -> dict[str, Any]:
    """照合できなかった 1 件の性質。字数と真偽だけで、引用も原文も持たない。``inputs`` は ``_compact`` にした入力。"""
    target = _compact(quote)
    own = inputs.get(evidence_id)
    joined = "".join(inputs.values())
    others = sum(target in text for key, text in inputs.items() if key != evidence_id) if target else 0
    if own is None:
        reason = "evidence_id_unknown"
    elif others:
        reason = "quote_in_other_input"
    elif target and target in joined:
        reason = "quote_across_inputs"
    else:
        reason = "quote_not_found"
    compared = own if own is not None else joined
    original = originals.get(evidence_id)
    return {
        "reason": reason,
        "quote_chars": len(target),
        "input_chars": len(own) if own is not None else 0,
        "other_input_matches": others,
        "prefix_match_chars": _longest_edge_match(target, compared),
        "suffix_match_chars": _longest_edge_match(target, compared, suffix=True),
        "elision_mark": any(mark in quote for mark in _ELISION_MARKS),
        "literal_escape": any(mark in quote for mark in _LITERAL_ESCAPES),
        "symbol_fold_match": original is not None and bool(target) and _compact(quote, fold_text) in _compact(original, fold_text),
    }


def quote_mismatch_diagnostic(unmatched: Sequence[tuple[str, str]], originals: Mapping[str, str]) -> dict[str, Any]:
    """照合できなかった findings の理由の件数と先頭の数件の性質（モデルの実出力を保存せずに切り分ける。#4090）。"""
    inputs = {key: _compact(text) for key, text in originals.items()}
    rows = [_unmatched_quote(evidence_id, quote, inputs, originals) for evidence_id, quote in unmatched]
    return {
        "unmatched_count": len(rows),
        "reasons": dict(Counter(row["reason"] for row in rows)),
        "samples": rows[:_MAX_MISMATCH_SAMPLES],
    }


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
            if isinstance(protocol_error, BatchQuoteMismatch):
                safe["quote_mismatch"] = protocol_error.diagnostic
    return detail


def batch_failure_summary(receipt: Mapping[str, Any], error: Exception) -> dict[str, Any]:
    """失敗した組の類別・件数・容量（質問・原文を持たない）。詳しい記録は検索の工程の ``generation_evidence_batch``。"""
    attempts = receipt.get("validation_failures") or []
    last = attempts[-1] if attempts else structured_failure_details(error)
    errors = last.get("validation_errors", ())
    code = next((row["protocol_code"] for row in errors if row.get("protocol_code")), None)
    mismatch = next((row["quote_mismatch"] for row in errors if row.get("quote_mismatch")), None)
    return {
        "category": code or last["category"],
        "batch_index": receipt["index"],
        "input_count": receipt["input_count"],
        "input_chars": receipt["input_chars"],
        "invalid_attempts": len(attempts),
        **({"unmatched_count": mismatch["unmatched_count"], "unmatched_reasons": mismatch["reasons"]} if mismatch else {}),
    }


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
    if any("quote" in entry["loc"] or entry.get("protocol_code") == _QUOTE_NOT_IN_INPUT for entry in errors):
        guidance.append(
            "各 findings の evidence_id と quote を対応する入力原文に照合し、"
            "連続して存在する原文を quote に使ってください。原文を補作しないでください。"
        )
    # 照合の診断（quote_mismatch）はモデルに渡さない。指摘の形は検査の型・位置・固定コードだけ。
    shown = [{key: value for key, value in entry.items() if key != "quote_mismatch"} for entry in errors]
    return (
        original
        + "\n\n## Step 4: 読取結果の形式を修正する\n"
        + "\n".join(guidance)
        + "\n"
        + json.dumps({"validation_errors": shown}, ensure_ascii=False)
    )
