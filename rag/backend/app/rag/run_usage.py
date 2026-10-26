"""1 回の実行のモデルの利用量（実行履歴の概要の「モデル」「token」「モデルの呼び出し」。#3419）。

集計の元は、その実行の trace（`operation_trace`）の span の知らせ（`SpanUpdate`）だけで、別の
計測は持たない。数えるのは LLM の呼び出しの span（`gen_ai.chat`。回答エンジンの呼び出し
`_observe_engine_call` と backend の `OciEnterpriseAiClient`。会話の履歴による質問の書き換え・
質問の理解・回答の生成・検証・送り直しの 1 回ずつ）で、利用量は provider が返した値
（`record_genai_usage` の `gen_ai.usage.*`）。

- 工程の span（`rag.engine.stage` など）は数えない（子の呼び出しと二重に数えない）。
  `gen_ai.chat` の中に `gen_ai.chat` があれば、内側（実際の呼び出し）だけを数える。
- embedding / rerank（`gen_ai.embeddings` / `gen_ai.rerank`）は LLM の token に入れない。
- 合計は入力 + 出力。cached は入力の内数、reasoning は出力の内数で、合計に足さない。
- 利用量の無い呼び出し（失敗・取消・provider が返さない）・終わっていない呼び出し・記録の上限
  で切った trace は「一部計測」（`measurement="partial"`）。LLM を呼ばなかった実行は実測の 0
  （`complete`）。
- 記録の無い実行（この機能より前の記録・trace の始まる前に失敗した実行）は利用量を持たない
  （画面は「未計測」）。

比較の列（チャット）は列ごとに trace と実行が分かれているので、列の間で混ざらない。MCP の呼び
出し元の業務 Agent の利用量も別の trace で、ここには入らない。
"""

from __future__ import annotations

import threading
from typing import Literal

from pr_backend_core.observability.tracing import SpanUpdate
from pydantic import BaseModel, Field

LLM_CALL_SPAN = "gen_ai.chat"
_TOKEN_ATTRIBUTES = {
    "input": "gen_ai.usage.input_tokens",
    "output": "gen_ai.usage.output_tokens",
    "cached": "gen_ai.usage.cache_read.input_tokens",
    "reasoning": "gen_ai.usage.reasoning.output_tokens",
}
_FINISHED = {"done", "failed", "cancelled", "skipped"}

type UsageMeasurement = Literal["complete", "partial"]


class RunModelUsage(BaseModel):
    """1 回の実行のモデルの利用量（実行履歴の `stages_json.usage`）。"""

    # 呼び出したモデルの ID（最初に呼んだ順。回答のモデルを先頭にする）。
    models: list[str] = Field(default_factory=list)
    # LLM の呼び出しの回数（失敗・取消の呼び出しも 1 回）。
    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    # 入力 + 出力（cached・reasoning は内数で足さない）。
    total_tokens: int = Field(default=0, ge=0)
    # 入力のうちキャッシュから読んだ token（provider が返した呼び出しだけの和。どれも返さなければ
    # None）。
    cached_input_tokens: int | None = Field(default=None, ge=0)
    # 出力のうち推論の token（同上）。
    reasoning_output_tokens: int | None = Field(default=None, ge=0)
    # complete: すべての呼び出しの入力・出力を計測した。partial: 一部の呼び出しの利用量が無い。
    measurement: UsageMeasurement = "complete"
    # 利用量（入力と出力）を計測できた呼び出しの数。
    measured_requests: int = Field(default=0, ge=0)


class _Call:
    __slots__ = ("model", "parent", "status", "tokens")

    def __init__(self) -> None:
        self.model = ""
        self.parent: str | None = None
        self.status = "running"
        self.tokens: dict[str, int] = {}


def _token(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class ModelUsageCollector:
    """trace の span の知らせから、LLM の呼び出しの回数と利用量を集める。

    回答の thread からも呼ばれる。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._parents: dict[str, str | None] = {}
        self._calls: dict[str, _Call] = {}

    def observe(self, update: SpanUpdate) -> None:
        with self._lock:
            if update.root:
                self._started = True
            self._parents[update.span_id] = update.parent_span_id
            if update.operation_name != LLM_CALL_SPAN:
                return
            call = self._calls.setdefault(update.span_id, _Call())
            call.parent = update.parent_span_id
            call.status = update.status
            attributes = update.attributes
            model = attributes.get("gen_ai.response.model") or attributes.get(
                "gen_ai.request.model"
            )
            if isinstance(model, str) and model:
                call.model = model
            for key, name in _TOKEN_ATTRIBUTES.items():
                value = _token(attributes.get(name))
                if value is not None:
                    call.tokens[key] = value

    def summary(
        self, *, primary_model: str | None = None, truncated: bool = False
    ) -> RunModelUsage | None:
        """集めた利用量。trace が始まっていなければ None（未計測）。

        ``primary_model`` は回答のモデル（呼んでいれば `models` の先頭にする）。``truncated`` は
        記録の上限で span を切った trace（呼び出しを数えきれていないので一部計測）。
        """
        with self._lock:
            if not self._started and not self._calls:
                return None
            calls = {span_id: call for span_id, call in self._calls.items()}
            parents = dict(self._parents)
        # `gen_ai.chat` の中の `gen_ai.chat` は内側だけを数える（外側は子の呼び出しの和と重なる）。
        outer: set[str] = set()
        for span_id in calls:
            seen: set[str] = {span_id}
            parent = parents.get(span_id)
            while parent is not None and parent not in seen:
                seen.add(parent)
                if parent in calls:
                    outer.add(parent)
                parent = parents.get(parent)
        leaves = [call for span_id, call in calls.items() if span_id not in outer]
        models: list[str] = []
        for call in leaves:
            if call.model and call.model not in models:
                models.append(call.model)
        if primary_model and primary_model in models:
            models.remove(primary_model)
            models.insert(0, primary_model)
        measured = [
            call
            for call in leaves
            if call.status in _FINISHED and "input" in call.tokens and "output" in call.tokens
        ]
        input_tokens = sum(call.tokens.get("input", 0) for call in leaves)
        output_tokens = sum(call.tokens.get("output", 0) for call in leaves)

        def subtotal(key: str) -> int | None:
            values = [call.tokens[key] for call in leaves if key in call.tokens]
            return sum(values) if values else None

        return RunModelUsage(
            models=models,
            requests=len(leaves),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cached_input_tokens=subtotal("cached"),
            reasoning_output_tokens=subtotal("reasoning"),
            measurement="partial" if truncated or len(measured) < len(leaves) else "complete",
            measured_requests=len(measured),
        )


def parse_run_usage(value: object) -> RunModelUsage | None:
    """保存した利用量を読む。無い・形の違う記録（この機能より前の実行）は None（未計測）。"""
    if not isinstance(value, dict):
        return None
    try:
        return RunModelUsage.model_validate(value)
    except ValueError:
        return None


def run_usage_totals(
    total_tokens: object, measurement: object
) -> tuple[int | None, UsageMeasurement | None]:
    """一覧の行の利用量（合計の token と計測の状態。#3466）。

    一覧は `stages_json.usage` の全体を読まず、SQL で取り出した 2 つの値だけを使う。計測の状態が
    無い・形の違う記録（この機能より前の実行・trace の始まる前に失敗した実行）は未計測（どちらも
    None。0 にしない）。計測した記録の合計が無ければ 0（`RunModelUsage` の既定と同じ。LLM を
    呼ばなかった実行は実測の 0）。
    """
    if measurement != "complete" and measurement != "partial":
        return None, None
    if isinstance(total_tokens, bool) or not isinstance(total_tokens, int | float):
        return 0, measurement
    if total_tokens < 0 or not float(total_tokens).is_integer():
        return 0, measurement
    return int(total_tokens), measurement
