"""LLM の呼び出しの token の数と秒（#1516）。

回答の工程（生成・監査・抽出）が遅い理由（入力・出力・推論の token の数）を工程の記録（execution_steps）と
log で確かめるため、LLM の応答の ``usage`` を累積する。実行記録は回答の開始で新しい累積（`llm_usage`）を
置き、工程ごとに開始と終了の差（`usage_delta`）を記録する。

抽出は thread で並列に呼ぶ（`contextvars.copy_context`）。累積の object は文脈から参照で引き継がれるので、
足すときは lock を取る。累積が無い文脈（取込・単体の CLI）では何もしない。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

# 累積する値（usage の項目）。秒は呼び出しの往復の時間の和（並列の呼び出しは重なる）。
USAGE_KEYS = ("calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "seconds")


class LlmUsage:
    """1 回の回答の LLM の usage の累積（thread をまたいで共有する）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] = dict.fromkeys(USAGE_KEYS, 0)

    def add(self, values: Mapping[str, float]) -> None:
        with self._lock:
            for key in USAGE_KEYS:
                self._values[key] += float(values.get(key, 0) or 0)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._values)


llm_usage: ContextVar[LlmUsage | None] = ContextVar("rag_engine_llm_usage", default=None)


@contextmanager
def bind_llm_usage(usage: LlmUsage | None = None) -> Iterator[LlmUsage]:
    """この文脈の LLM の usage を新しい累積（または ``usage``）にする。"""
    current = usage or LlmUsage()
    token = llm_usage.set(current)
    try:
        yield current
    finally:
        llm_usage.reset(token)


def _value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def response_usage(response: Any) -> dict[str, int]:
    """Responses API の応答の ``usage`` から、入力・cache に当たった入力・出力・推論の token の数を読む。"""
    usage = _value(response, "usage")
    return {
        "input_tokens": _int(_value(usage, "input_tokens")),
        "cached_tokens": _int(_value(_value(usage, "input_tokens_details"), "cached_tokens")),
        "output_tokens": _int(_value(usage, "output_tokens")),
        "reasoning_tokens": _int(_value(_value(usage, "output_tokens_details"), "reasoning_tokens")),
    }


def record_llm_usage(values: Mapping[str, float]) -> None:
    """この文脈の累積に、1 回の呼び出しの usage（``response_usage`` と ``seconds``）を足す。"""
    usage = llm_usage.get()
    if usage is not None:
        usage.add({**values, "calls": 1})


def usage_snapshot() -> dict[str, float]:
    """この文脈の累積（無ければ空）。"""
    usage = llm_usage.get()
    return usage.snapshot() if usage is not None else {}


def usage_delta(before: Mapping[str, float]) -> dict[str, float]:
    """``before``（`usage_snapshot`）からの増分。呼び出しが無ければ空。秒は小数 1 桁、他は整数。"""
    after = usage_snapshot()
    if not after or after.get("calls", 0) - before.get("calls", 0) <= 0:
        return {}
    delta: dict[str, float] = {}
    for key in USAGE_KEYS:
        diff = after.get(key, 0) - before.get(key, 0)
        delta[key] = round(diff, 1) if key == "seconds" else int(diff)
    return delta


def usage_line(delta: Mapping[str, float]) -> str:
    """利用者向けの 1 行（例: 「入力 12,345 tokens・出力 2,345 tokens（推論 1,800）・120.5 秒」）。空なら空文字。"""
    if not delta:
        return ""
    text = f"入力 {int(delta.get('input_tokens', 0)):,} tokens"
    if delta.get("cached_tokens"):
        text += f"（cache {int(delta['cached_tokens']):,}）"
    text += f"・出力 {int(delta.get('output_tokens', 0)):,} tokens"
    if delta.get("reasoning_tokens"):
        text += f"（推論 {int(delta['reasoning_tokens']):,}）"
    return text + f"・{float(delta.get('seconds', 0)):.1f} 秒"
