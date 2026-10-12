"""回答生成の実行記録。工程の開始・詳細・終了を呼出単位で記録し、利用者向けに整形する。"""

from __future__ import annotations

from time import monotonic
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Callable, Iterator, Mapping, Sequence
from rag_engine.dependencies import llm_call_count
from rag_engine.resources.llm_usage import LlmUsage, llm_usage as _llm_usage, usage_delta, usage_snapshot
from rag_engine.generation.answer_models import QUESTION_DISPLAY_METADATA_SEPARATOR, extract_original_question


@dataclass
class _ExecutionStep:
    """一工程の詳細と終了状態。失敗や早期停止を成功と区別して記録する。"""

    name: str
    status: str = ""
    hidden: bool = False

    def add(self, *details: str) -> None:
        """詳細をその場で追加し、入れ子工程との前後関係を保持する。"""
        lines = _execution_lines.get()
        if lines is not None:
            for detail in details:
                lines.extend(f"  {line}" for line in detail.splitlines())

    def result(self, *details: str) -> None:
        """この工程で分かったこと。最初の行に「結果:」を付ける。"""
        values = [line for detail in details for line in detail.splitlines() if line.strip()]
        if values:
            self.add("結果: " + values[0], *("      " + value for value in values[1:]))

    def impact(self, text: str) -> None:
        """この結果が後続の処理をどう変えるか。変えない場合は書かない。"""
        if text:
            self.add("影響: " + text)

    def hide(self) -> None:
        """利用者に伝える内容がない工程は記録から省く（異常終了した場合は省かない）。"""
        self.hidden = True


# Gradio の並行リクエスト間で実行記録が混ざらないよう、呼び出し単位で分離する。
_structured_steps: ContextVar[list[dict[str, Any]] | None] = ContextVar("rag_engine_steps", default=None)

_execution_lines: ContextVar[list[str] | None] = ContextVar("answer_execution_lines", default=None)

# 工程の開始・終了を呼び出し側へ知らせる(進捗の表示。#593)。引数は (工程名, "started" /
# "success" / "error", 経過秒)。通知の失敗は回答を止めない。
StepListener = Callable[[str, str, float], None]
_step_listener: ContextVar[StepListener | None] = ContextVar("answer_step_listener", default=None)


@contextmanager
def bind_step_listener(listener: StepListener | None) -> Iterator[None]:
    """この呼び出しの中の工程の開始・終了を ``listener`` へ知らせる。"""
    token = _step_listener.set(listener)
    try:
        yield
    finally:
        _step_listener.reset(token)


# 工程ごとに差分を記録する累積の計測値（DB の SQL の回数・行数など。#1490）。呼び出し側が渡す関数は
# その時点の累積値（数値）を返し、工程の記録には終了時と開始時の差を足す。失敗しても回答を止めない。
StepMetrics = Callable[[], Mapping[str, int | float]]
_step_metrics: ContextVar[StepMetrics | None] = ContextVar("answer_step_metrics", default=None)


@contextmanager
def bind_step_metrics(provider: StepMetrics | None) -> Iterator[None]:
    """この呼び出しの中の工程の記録に、``provider`` の累積値の差分を足す。"""
    token = _step_metrics.set(provider)
    try:
        yield
    finally:
        _step_metrics.reset(token)


def _step_metrics_snapshot() -> dict[str, int | float]:
    provider = _step_metrics.get()
    if provider is None:
        return {}
    try:
        values = provider()
    except Exception:  # noqa: BLE001 - 計測は補助。回答の処理を止めない。
        return {}
    return {key: value for key, value in values.items() if isinstance(value, int | float) and not isinstance(value, bool)}


def _step_metrics_delta(before: Mapping[str, int | float]) -> dict[str, int | float]:
    after = _step_metrics_snapshot()
    delta: dict[str, int | float] = {}
    for key, value in after.items():
        diff = value - before.get(key, 0)
        delta[key] = round(diff, 3) if isinstance(diff, float) else diff
    return delta


def _notify_step(name: str, outcome: str, elapsed: float) -> None:
    listener = _step_listener.get()
    if listener is None:
        return
    try:
        listener(name, outcome, elapsed)
    except Exception:  # noqa: BLE001 - 進捗の通知は補助。回答の処理を止めない。
        pass


class AnswerExecutionError(RuntimeError):
    """失敗時も完了済み工程と中断箇所を質問欄へ返すための例外。"""

    def __init__(self, message: str, question_display: str):
        """元のエラー本文と、保存前の実行記録を保持する。"""
        super().__init__(message)
        self.question_display = question_display


@contextmanager
def _execution_step(name: str, purpose: str = ""):
    """工程境界で記録し、例外時には終了状態を「エラーで中断」にする。

    purpose は利用者向けの「何のための処理か」。工程名だけでは分からない場合に付ける。
    """
    steps = _structured_steps.get()
    index = len(steps) if steps is not None else 0
    start = monotonic()
    llm_before = llm_call_count.get()
    usage_before = usage_snapshot()
    metrics_before = _step_metrics_snapshot()
    if steps is not None:
        steps.append({"name": name, "status": "running"})
    lines = _execution_lines.get()
    step = _ExecutionStep(name)
    first_line = len(lines) if lines is not None else 0
    if lines is not None:
        lines.extend(["", f"{name} 開始"])
        if purpose:
            lines.append(f"  目的: {purpose}")
    _notify_step(name, "started", 0.0)
    failed = False
    try:
        yield step
    except Exception:
        failed = True
        step.status = "エラーで中断"
        raise
    finally:
        elapsed = monotonic() - start
        _notify_step(name, "error" if failed else "success", elapsed)
        llm_calls = llm_call_count.get() - llm_before
        if steps is not None:
            steps[index] = {
                "name": name,
                "status": step.status or "complete",
                "elapsed_seconds": elapsed,
                "llm_calls": llm_calls,
                **_step_metrics_delta(metrics_before),
            }
            # LLM の token の数（入力・出力・推論）と呼び出しの秒の和。遅い工程の理由を確かめる（#1516）。
            if usage := usage_delta(usage_before):
                steps[index]["llm_usage"] = usage
        if lines is not None:
            if step.hidden and not step.status:
                del lines[first_line:]
            else:
                suffix = f"（{step.status}）" if step.status else ""
                # 所要時間と LLM 呼出回数は終了行の末尾に付け、整形時に見出しへ移す（_END_METRICS）。
                lines.append(f"{name} 終了{suffix} @{elapsed:.1f}s/{llm_calls}")


def format_question_display(original_question: str, execution_lines: Sequence[str]) -> str:
    """原質問に工程境界・対応番号付きの記録を付加する。記録自体の順序は変更しない。"""
    if not execution_lines:
        return original_question
    return "\n".join(
        [
            original_question,
            "",
            QUESTION_DISPLAY_METADATA_SEPARATOR,
            *_format_execution_boundaries(execution_lines),
        ]
    ).strip()


_END_METRICS = re.compile(r" @([\d.]+)s/(\d+)$")


def _format_execution_boundaries(execution_lines: Sequence[str]) -> list[str]:
    """工程を番号付きの節（1. / 6.1 / 6.1.1）と字下げで表示する。字下げ済みの詳細文は工程として解釈しない。

    見出しは工程ごとに1回だけ出し、LLM を呼んだ工程・時間のかかった工程には見出しに
    「［LLM n 回 / x.x 秒］」を添える。正常に終わった工程に終了行は付けず、中止・失敗など
    利用者が知るべき状態だけを「状態:」として示す。詳細も状態もない工程は表示しない。
    末尾に LLM 呼出回数と所要時間の合計を出す（計測のある工程が 1 つもなければ出さない）。
    """
    root: dict[str, Any] = {"name": "", "items": [], "status": "", "elapsed": 0.0, "llm": 0}
    stack = [root]
    for line in execution_lines:
        if not line.strip():
            continue
        if not line[0].isspace() and line.endswith(" 開始"):
            node = {"name": line.removesuffix(" 開始"), "items": [], "status": "", "elapsed": None, "llm": 0}
            stack[-1]["items"].append(node)
            stack.append(node)
        elif len(stack) > 1 and line.startswith(stack[-1]["name"] + " 終了"):
            node = stack.pop()
            metrics = _END_METRICS.search(line)
            if metrics:
                node["elapsed"], node["llm"] = float(metrics.group(1)), int(metrics.group(2))
                line = line[: metrics.start()]
            status = re.search(r"終了（(.+)）$", line)
            node["status"] = status.group(1) if status else ""
        else:
            stack[-1]["items"].append((line[2:] if line.startswith("  ") else line).rstrip())
    # 中断時は終了行がない。どこで止まったかが分かるよう、開いたままの工程に状態を付ける。
    for node in stack[1:]:
        node["status"] = node["status"] or "中断"

    rendered: list[str] = []

    def metrics_label(node: dict[str, Any]) -> str:
        parts = []
        if node["llm"]:
            parts.append(f"LLM {node['llm']} 回")
        if node["elapsed"] is not None and node["elapsed"] >= 0.1:
            parts.append(f"{node['elapsed']:.1f} 秒")
        return f" ［{' / '.join(parts)}］" if parts else ""

    def render(node: dict[str, Any], number: str, depth: int) -> None:
        indent = "   " * depth
        heading = f"{number}." if depth == 0 else number  # 最上位は「1.」、入れ子は「1.2」「1.2.3」
        rendered.extend(["", f"{indent}{heading} {node['name']}{metrics_label(node)}"])
        body = indent + "   "
        children = 0
        for item in node["items"]:
            if isinstance(item, str):
                rendered.append(body + item)
            elif item["items"] or item["status"]:
                children += 1
                render(item, f"{number}.{children}", depth + 1)
        if node["status"]:
            rendered.append(f"{body}状態: {node['status']}")

    count = 0
    for item in root["items"]:
        if isinstance(item, str):
            rendered.append(item)
        elif item["items"] or item["status"]:
            count += 1
            render(item, str(count), 0)
    measured = [item for item in root["items"] if isinstance(item, dict) and item["elapsed"] is not None]
    if measured:
        rendered.extend(["", f"合計: LLM {sum(item['llm'] for item in measured)} 回 / {sum(item['elapsed'] for item in measured):.1f} 秒"])
    return rendered


def _record_answer_execution(function):
    """結果に実行順の記録を付け、例外でも記録を保持して呼び出し元へ伝える。"""

    @wraps(function)
    def wrapped(question, *args, **kwargs):
        lines: list[str] = []
        steps: list[dict[str, Any]] = []
        step_token = _structured_steps.set(steps)
        token = _execution_lines.set(lines)
        llm_token = llm_call_count.set(0)
        usage_token = _llm_usage.set(LlmUsage())
        try:
            result = function(question, *args, **kwargs)
            return replace(result, question_display=format_question_display(result.original_question, lines), execution_steps=tuple(steps))
        except Exception as exc:
            if not lines:
                raise
            display = format_question_display(extract_original_question(question), lines)
            error = AnswerExecutionError(str(exc), display)
            error.execution_steps = tuple(steps)
            raise error from exc
        finally:
            _llm_usage.reset(usage_token)
            llm_call_count.reset(llm_token)
            _execution_lines.reset(token)
            _structured_steps.reset(step_token)

    return wrapped
