"""推導の独立監査の判定に、利用者に示す理由を付ける段（判定は変えない）。

独立監査（``application_review.review_applications``）のモデルは、判定（valid）と判定に使った
evidence_id だけを返す。この段は、質問・結論・確定した判定の日本語のラベル・引用した文と、
コードが evidence_id から切り出した原文だけの日本語の入力から、理由の文を 1 つ作る。
生成の reasoning・英語の文・内部 ID は入力に入れない。

表示の門は形式だけを確かめる（空でない・かなを含む・入力に無い英字 3 語以上の連なりが無い・
入力に無い内部 ID が無い）。通らない理由・作れなかった理由は定型の文に置き換え、判定は変えない。
語の意味の判定（正規表現・語の一覧）はここに置かない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import CancelledError
from time import monotonic
from typing import Any

from pr_backend_core.observability.model_purpose import call_with_model_purpose
from pydantic import Field
from rag_parser_core.language import current_pack
from rag_parser_core.language.text import range_class

from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, ensure_model_input
from rag_engine.models.llm import ApplicationVerdictAudit, _StrictModel
from rag_engine.resources.deadline import DeadlineExceeded, current_deadline, deadline_cut_short
from rag_engine.resources.runtime import resolve_prompt

APPLICATION_REASON_SYSTEM_PROMPT = (
    "## Step 1: 入力を読む\n"
    "「質問」は利用者の質問、「結論」は質問への回答の一部、「判定」はその結論を確かめた確定の結果、"
    "「原文」は判定に使った資料の原文です。「引用元」が「はい」の原文は、結論が引用した資料です。\n"
    "\n"
    "## Step 2: 理由を書く\n"
    "判定の理由を、利用者に示す日本語の 1〜2 文で書きます。"
    "結論が引用した資料の原文が示す対象・条件・版と、質問の対象・条件の関係を示します。"
    "判定を変えず、質問と原文に無い事実を足しません。資料名・見出し・原文の語は原文のまま書きます。\n"
    "\n"
    "## Step 3: 出力する\n"
    "reason だけを持つ JSON を返します。"
)

# 確定した判定を、理由の段の入力に渡す日本語のラベル。
VERDICT_LABELS = {
    "valid": "結論は引用した資料に照らして適用できる",
    "invalid": "結論は引用した資料に照らして適用できない",
}
# 表示の門を通らない・作れなかった理由の代わりに示す定型の文（英語の文へは戻さない）。
HIDDEN_REASON = "判定の理由を表示できません。"
# 入力に無い英字の連なりとして隠す語数。
LATIN_RUN_WORDS = 3

_WORD = r"[A-Za-z][A-Za-z0-9_'’-]*"
_LATIN_RUN = re.compile(_WORD + r"(?:[ \t,.;:!?()*=+×/0-9-]+" + _WORD + r")*")
_LATIN_WORDS = re.compile(_WORD)
# 内部 ID の形（根拠の番号・単位の ID・要求の番号）。
_INTERNAL_ID = re.compile(r"(?<![A-Za-z0-9_-])(?:evidence-\d+|U[0-9a-f]{24}|E\d+|Q\d+)(?![A-Za-z0-9_-])")


# docstring は schema の description としてモデルに渡るため置かない（検証した schema の形）。
class AuditReason(_StrictModel):
    reason: str = Field(description="判定の理由を利用者に示す、日本語の 1〜2 文。")


def propagates(exc: BaseException) -> bool:
    """権限・取消・期限切れは、監査の失敗として握らずに呼び出し元へ返す。"""
    deadline = current_deadline()
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return (
        isinstance(exc, (PermissionError, CancelledError))
        or status in {401, 403}
        or (deadline is not None and (deadline.cancelled or (deadline.expired and not deadline_cut_short())))
        or (isinstance(exc, DeadlineExceeded) and not deadline_cut_short())
    )


def _evidence(inputs: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["evidence_id"]): row
        for document in inputs.get("documents") or ()
        for row in document.get("evidence") or ()
        if row.get("evidence_id")
    }


def reason_inputs(inputs: Mapping[str, Any], index: int, status: str, basis_ids: Sequence[str]) -> dict[str, Any]:
    """理由の段の入力。原文は引用の束縛と判定に使った evidence_id からコードが切り出す。"""
    evidence = _evidence(inputs)
    application = next(row for row in inputs["applications"] if row["index"] == index)
    items = [inputs["items"][position] for position in application["item_indexes"]]
    cited = list(
        dict.fromkeys(
            str(binding.get("evidence_id"))
            for item in items
            for binding in item.get("citation_bindings") or ()
            if str(binding.get("evidence_id")) in evidence
        )
    )
    ordered = cited + [evidence_id for evidence_id in dict.fromkeys(basis_ids) if evidence_id in evidence and evidence_id not in cited]
    return {
        "質問": inputs["question"],
        "結論": application["result"],
        "判定": VERDICT_LABELS[status],
        "引用した文": [item.get("quote") or item.get("text") for item in items],
        "原文": [
            {
                "資料": str(evidence[evidence_id].get("source") or ""),
                "見出し": " / ".join(str(part) for part in evidence[evidence_id].get("section_path") or ()),
                "本文": str(evidence[evidence_id].get("text") or ""),
                "引用元": "はい" if evidence_id in cited else "いいえ",
            }
            for evidence_id in ordered
        ],
    }


def reason_input_texts(payload: Mapping[str, Any]) -> list[str]:
    """理由に逐字で現れてよい入力の文（質問・結論・引用した文・資料名・見出し・本文）。"""
    return [
        payload["質問"],
        payload["結論"],
        *payload["引用した文"],
        *(f"{row['資料']}\n{row['見出し']}\n{row['本文']}" for row in payload["原文"]),
    ]


def internal_ids(inputs: Mapping[str, Any]) -> list[str]:
    """監査の入力が持つ内部 ID（根拠・出典・単位・要求の ID）。"""
    found: list[str] = []
    for row in _evidence(inputs).values():
        found += [str(row.get("evidence_id") or ""), str(row.get("source_id") or "")]
    for item in inputs.get("items") or ():
        for binding in item.get("citation_bindings") or ():
            found += [str(binding.get("evidence_id") or ""), str(binding.get("source_id") or ""), *map(str, binding.get("unit_ids") or ())]
    found += [str(row.get("request_id") or "") for row in inputs.get("applications") or ()]
    return [value for value in dict.fromkeys(found) if value]


def _kana() -> re.Pattern[str]:
    pack = current_pack()
    return re.compile(range_class(pack.words("search_terms", "hiragana_chars") + pack.words("search_terms", "katakana_chars")))


def latin_runs_not_in_inputs(text: str, inputs: Sequence[str]) -> list[str]:
    joined = "\n".join(inputs)
    found = []
    for match in _LATIN_RUN.finditer(text):
        run = match.group(0).strip(" \t,.;:!?")
        if len(_LATIN_WORDS.findall(run)) >= LATIN_RUN_WORDS and run not in joined:
            found.append(run)
    return found


def display_gate(reason: Any, inputs: Sequence[str], ids: Iterable[str] = ()) -> dict[str, Any]:
    """理由を表示してよいかの形式の検査。意味は判定しない。"""
    if not isinstance(reason, str) or not reason.strip():
        return {"status": "hidden", "why": "empty"}
    if not _kana().search(reason):
        return {"status": "hidden", "why": "no_kana"}
    if runs := latin_runs_not_in_inputs(reason, inputs):
        return {"status": "hidden", "why": "latin_run_not_in_inputs", "runs": runs}
    joined = "\n".join(inputs)
    marks = [*_INTERNAL_ID.findall(reason), *(value for value in ids if value in reason)]
    if marks := [mark for mark in dict.fromkeys(marks) if mark not in joined]:
        return {"status": "hidden", "why": "internal_id", "marks": marks}
    return {"status": "shown", "text": reason}


def explain_verdicts(
    inputs: Mapping[str, Any],
    audit: ApplicationVerdictAudit | None,
    *,
    parse_text: Callable[..., Any],
    settings: Any,
    provider_id: str | None,
) -> tuple[dict[int, str], dict[str, Any]]:
    """判定が 1 件だけ確定した application ごとに理由を 1 回作る。返す理由は表示の門を通ったものか定型の文。"""
    reasons: dict[int, str] = {}
    trace: dict[str, Any] = {"calls": 0, "results": []}
    if audit is None:
        return reasons, trace
    evidence = _evidence(inputs)
    ids = internal_ids(inputs)
    system = resolve_prompt("application.reasonSystem", APPLICATION_REASON_SYSTEM_PROMPT)
    unknown_basis: dict[int, list[str]] = {}
    for application in inputs["applications"]:
        index = int(application["index"])
        rows = [row for row in audit.application_reviews if row.index == index]
        if len(rows) != 1:
            continue
        verdict = rows[0]
        basis = list(dict.fromkeys(verdict.basis_evidence_ids))
        if unknown := [evidence_id for evidence_id in basis if evidence_id not in evidence]:
            unknown_basis[index] = unknown
        payload = reason_inputs(inputs, index, "valid" if verdict.valid else "invalid", basis)
        prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        started = monotonic()
        result: dict[str, Any] = {"index": index, "prompt_chars": len(system) + len(prompt)}
        parsed = None
        try:
            ensure_model_input(settings, system, prompt, AuditReason.model_json_schema(), provider_id=provider_id)
            trace["calls"] += 1
            parsed = call_with_model_purpose(
                "answer.audit_reason", parse_text, system, prompt, settings, AuditReason, provider_id=provider_id
            )
        except Exception as exc:
            if propagates(exc):
                raise
            result["error_type"] = type(exc).__name__
        if parsed is not None:
            gate = display_gate(parsed.reason, reason_input_texts(payload), ids)
        elif result.get("error_type") == GenerationInputBudgetExceeded.__name__:
            gate = {"status": "hidden", "why": "input_budget_exceeded"}
        else:
            gate = {"status": "hidden", "why": "reason_step_failed"}
        reasons[index] = gate["text"] if gate["status"] == "shown" else HIDDEN_REASON
        result.update({key: value for key, value in gate.items() if key != "text"}, seconds=round(monotonic() - started, 3))
        trace["results"].append(result)
    if unknown_basis:
        trace["unknown_basis_ids"] = {str(index): values for index, values in unknown_basis.items()}
    return reasons, trace
