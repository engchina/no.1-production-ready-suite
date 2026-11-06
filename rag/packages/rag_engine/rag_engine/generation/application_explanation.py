"""論理の審査で valid とした推導ごとに、利用者に示す説明を独立した短い段で作る（#4393）。

説明の段に渡すのは原質問と、その推導が当てはめた資料の原文だけ（明示の回答言語の指定があるときだけ、その原文の
引用も渡す）。審査済みの結論・生成の推導の文・要求は渡さない。照らす相手が入力に無いので、説明が入力の写しや
「結論と一致します」という自己確認の文にならない。

表示するかはコードが形式だけで決める（意味の判定はしない。語の一覧・正規表現の語彙を持たない）:

- 空でない。
- 既定の言語（明示の指定が無い）では、Unicode の文字名でかな（平仮名・片仮名）を 1 字以上含み、入力の原文に
  逐字で無い Latin の 3 語以上の連なりを含まない。
- 審査済みの結論の数値（Unicode の 10 進数字の連なり。3 桁区切りを除く）がすべて説明にある。
- 本文の出典行の印で始まる行を含まない（画面が出典行を数えて根拠に結ぶため）。

不成立・呼び出しの失敗なら説明だけを隠す。結論と資料の原文はそのまま出し、生成の推導の文へ戻さない。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable, Sequence
from functools import lru_cache
from time import monotonic
from typing import Any, TypeGuard

from pr_backend_core.observability.model_purpose import call_with_model_purpose
from pydantic import BaseModel, ConfigDict, Field, create_model

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.application_reason import propagates
from rag_engine.generation.applications import CheckedApplication
from rag_engine.generation.input_budget import ensure_model_input
from rag_engine.generation.response_language import response_language_contract
from rag_engine.models.llm import ResponseLanguage
from rag_engine.resources.deadline import check_cancelled, remaining_seconds

# 既定の回答言語（response_language_contract の既定 "ja"）の、説明の段への書き方。
DEFAULT_LANGUAGE_LABEL = "日本語"
KEY_QUESTION = "質問"
KEY_SOURCES = "資料の原文"
KEY_LANGUAGE = "回答言語の指定"
# 入力の原文に無い Latin の語がこの数以上連なれば、既定の言語の説明ではないとみなして隠す。
LATIN_RUN_WORDS = 3
_THOUSANDS_SEPARATORS = (",", "，")


def language_label(instruction: str | None) -> str:
    return DEFAULT_LANGUAGE_LABEL if instruction is None else f"「{KEY_LANGUAGE}」が求める言語"


def explanation_system(instruction: str | None) -> str:
    label = language_label(instruction)
    return "\n".join(
        [
            "## Step 1: 入力を読む",
            f"入力の「{KEY_QUESTION}」は利用者の質問、「{KEY_SOURCES}」は資料から引いた原文です。"
            + ("" if instruction is None else f"「{KEY_LANGUAGE}」は、質問のうち回答の言語を指定した原文です。"),
            "",
            "## Step 2: 説明を書く",
            f"質問への答えが、質問の条件と{KEY_SOURCES}の値からどう導かれるかを、利用者に示す{label}の文で書きます。"
            "計算を含むときは、使った値と式と途中の結果を示します。"
            f"質問と{KEY_SOURCES}に無い値・条件を足しません。"
            "原文の数値・記号・固有名詞・SQL は原文のまま書きます。",
            "",
            "## Step 3: 出力する",
            "explanation だけを持つ JSON を返します。",
        ]
    )


class _ExplanationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


@lru_cache(maxsize=2)
def _explanation_model(label: str) -> type[BaseModel]:
    return create_model(
        "ApplicationExplanation",
        __base__=_ExplanationBase,
        explanation=(str, Field(description=f"質問への答えが質問と{KEY_SOURCES}からどう導かれるかを示す、{label}の説明文。")),
    )


def is_explanation_schema(schema: Any) -> TypeGuard[type[BaseModel]]:
    return isinstance(schema, type) and issubclass(schema, _ExplanationBase)


def explanation_schema(instruction: str | None) -> type[BaseModel]:
    return _explanation_model(language_label(instruction))


def explanation_prompt(question: str, sources: Sequence[str], instruction: str | None) -> str:
    value: dict[str, Any] = {KEY_QUESTION: question, KEY_SOURCES: list(sources)}
    if instruction is not None:
        value[KEY_LANGUAGE] = instruction
    return json.dumps(value, ensure_ascii=False, indent=2)


def application_sources(entry: CheckedApplication) -> list[str]:
    """推導が当てはめた資料の原文（監査の items の quote と同じ値）。同じ原文は 1 回だけ渡す。"""
    texts = [str(item.item.quote or item.item.text or "").strip() for item in entry.entries]
    return [text for text in dict.fromkeys(texts) if text]


# ---- 表示の門（形式の検査だけ） -----------------------------------------------------------


def _name(ch: str) -> str:
    return unicodedata.name(ch, "")


def _is_kana(ch: str) -> bool:
    return _name(ch).startswith(("HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA"))


def _is_latin_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L") and "LATIN" in _name(ch).split(" LETTER")[0]


def _is_word_char(ch: str) -> bool:
    return _is_latin_letter(ch) or unicodedata.category(ch) == "Nd" or ch in "_'’-"


def _breaks_run(ch: str) -> bool:
    """Latin 以外の字（Letter）と改行で連なりを切る。空白・記号・数字は連なりの内側に置ける。"""
    return ch in "\r\n" or (unicodedata.category(ch).startswith("L") and not _is_latin_letter(ch))


def latin_runs(text: str) -> list[tuple[str, int]]:
    """Latin の字で始まる語の連なり（原文の部分文字列, 語数）。"""
    runs: list[tuple[str, int]] = []
    start: int | None = None
    end = 0
    words = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _is_latin_letter(ch):
            j = i
            while j < n and _is_word_char(text[j]):
                j += 1
            if start is None:
                start = i
            end, words, i = j, words + 1, j
            continue
        if _breaks_run(ch):
            if start is not None:
                runs.append((text[start:end], words))
            start, words = None, 0
        i += 1
    if start is not None:
        runs.append((text[start:end], words))
    return runs


def _three_digit_group(text: str, start: int) -> bool:
    group = text[start : start + 3]
    after = text[start + 3 : start + 4]
    return len(group) == 3 and all(unicodedata.category(c) == "Nd" for c in group) and not (after and unicodedata.category(after) == "Nd")


def number_runs(text: str) -> list[str]:
    """Unicode の 10 進数字（一般カテゴリ Nd）の連なりを ASCII の数字列で返す。直後が 3 桁の数字の桁区切りは除く。"""
    runs: list[str] = []
    current = ""
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Nd":
            current += str(unicodedata.decimal(ch))
        elif ch in _THOUSANDS_SEPARATORS and current and _three_digit_group(text, i + 1):
            continue
        else:
            if current:
                runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


def display_gate(explanation: Any, *, question: str, sources: Sequence[str], conclusion: str, default_language: bool) -> dict[str, Any]:
    """説明を表示してよいかを形式だけで決める。``reason`` は隠した理由のコード。"""
    if not isinstance(explanation, str) or not explanation.strip():
        return {"status": "hidden", "reason": "empty"}
    text = explanation.strip()
    if default_language:
        if not any(_is_kana(ch) for ch in text):
            return {"status": "hidden", "reason": "no_kana"}
        joined = "\n".join([question, *sources, conclusion])
        if any(words >= LATIN_RUN_WORDS and run not in joined for run, words in latin_runs(text)):
            return {"status": "hidden", "reason": "latin_run_not_in_inputs"}
    have = set(number_runs(text))
    if any(run not in have for run in number_runs(conclusion)):
        return {"status": "hidden", "reason": "conclusion_numbers_missing"}
    prefix = answer_labels().citation_prefix
    if prefix and any(line.strip().startswith(prefix) for line in text.splitlines()):
        return {"status": "hidden", "reason": "citation_marker_in_text"}
    return {"status": "confirmed", "text": text}


# ---- 説明の段 ---------------------------------------------------------------------------


def explanation_target(entry: CheckedApplication) -> bool:
    """論理の審査で valid とし、本文に出す推導だけに説明を作る。"""
    return entry.published and entry.assessment.get("status") == "valid"


def explain_applications(
    question: str,
    checked: Sequence[CheckedApplication],
    response_language: ResponseLanguage | None,
    *,
    parse_text: Callable[..., Any],
    settings: Any,
    provider_id: str | None,
) -> dict[str, Any]:
    """valid な推導ごとに 1 回、説明の段を呼び、表示の門を通った説明だけを ``presentation`` に置く。"""
    targets = [entry for entry in checked if explanation_target(entry)]
    for entry in checked:
        if not explanation_target(entry):
            entry.presentation = {"status": "not_requested", "reason": "logic_not_valid"}
    if not targets:
        return {"status": "not_applicable", "calls": 0}
    contract = response_language_contract(question, response_language or ResponseLanguage())
    diagnostic: dict[str, Any] = {"status": "completed", "calls": 0, "language": dict(contract), "prompt_chars": []}
    if contract["status"] != "confirmed":
        for entry in targets:
            entry.presentation = {"status": "hidden", "reason": "response_language_unconfirmed"}
        diagnostic.update(status="not_applicable", results=[dict(entry.presentation) for entry in targets])
        return diagnostic
    instruction = contract["instruction"]
    system = explanation_system(instruction)
    schema = explanation_schema(instruction)
    started = monotonic()
    for entry in targets:
        check_cancelled()
        left = remaining_seconds()
        if left is not None and left <= 0:
            entry.presentation = {"status": "unrecorded", "reason": "deadline"}
            diagnostic["status"] = "incomplete"
            continue
        sources = application_sources(entry)
        prompt = explanation_prompt(question, sources, instruction)
        diagnostic["prompt_chars"].append(len(system) + len(prompt))
        try:
            ensure_model_input(settings, system, prompt, schema.model_json_schema(), provider_id=provider_id)
            diagnostic["calls"] += 1
            output = call_with_model_purpose("answer.generate", parse_text, system, prompt, settings, schema, provider_id=provider_id)
        except Exception as exc:
            if propagates(exc):
                raise
            entry.presentation = {"status": "unrecorded", "error_type": type(exc).__name__}
            diagnostic["status"] = "incomplete"
            continue
        gate = display_gate(
            getattr(output, "explanation", None),
            question=question,
            sources=sources,
            conclusion=entry.application.result,
            default_language=instruction is None,
        )
        entry.presentation = (
            {"status": "confirmed", "reasoning": gate["text"], "language": contract["language"]}
            if gate["status"] == "confirmed"
            else {"status": "hidden", "reason": gate["reason"]}
        )
    diagnostic.update(seconds=round(monotonic() - started, 3), results=[dict(entry.presentation) for entry in targets])
    return diagnostic
