"""モデルに渡す入力を、モデルの入力の上限に収める（コンテキストの管理。#2383）。

組み込み Runtime は Run の中のツールの結果をすべて次のモデルの呼び出しに積む（SDK の既定）。
根拠の多い検索を何回か呼ぶと、入力がモデルの入力の上限を超えて呼び出しが 400 で失敗する。
主要な実装の型（OpenAI Agents SDK の `call_model_input_filter` と `ToolOutputTrimmer`、Anthropic の
context editing が古いツールの結果から消す、LangChain の `trim_messages`）にそろえ、呼び出しの直前に
入力を見積もり、予算を超えるときだけ古いツールの結果から順に縮める。

- **予算**: 登録モデルの入力の上限（`max_input_tokens`。prompt と応答の合計）から、出力の上限
  （`max_output_tokens`。入力の上限の半分までを引く。小さいモデルで予算が 0 にならないように）と
  安全の余白（入力の上限 × `AGENT_CONTEXT_SAFETY_MARGIN_RATIO`）を引いた token の数。指示と
  ツールの定義は縮めず、残りに会話・ツールの結果を収める。
- **見積もり**: platform の `estimate_input_tokens`（3 製品で共通の 1 つ）に
  `AGENT_CONTEXT_TOKEN_ESTIMATE_SCALE` を掛ける。プロバイダが入力の超過を返したら、返った
  prompt の token 数との比で Run の残りの見積もりを補正する。
- **縮め方**: 古いツールの結果から順に、段階を 1 つずつ上げる（すべての古い結果を段階 1 にしても
  収まらなければ段階 2 …）。収まった時点で止める。最新のツールの結果（最後のツールの呼び出しの
  まとまり）は、古い結果をすべて縮めても収まらないときだけ段階 3 まで縮める。利用者の質問・会話の
  履歴・指示・モデルの発話は縮めない。
  1. 一覧の項目の長い文字列を先頭だけにする（`truncated` を持つ項目は true にする）
  2. 加えて、一覧の先頭の数件の後ろの項目を ID だけにする
  3. 一覧の項目をすべて ID だけにし、一覧の外の長い文字列も先頭だけにする
  4. ID だけを残す（古い結果だけ）
  どの段階でも ID の項目（`id`・`*_id`。根拠の `document_id`・`chunk_id`・`evidence_id` など）は
  消さない（引用の照合・回答の検証に使う）。縮めた結果には `context_compaction`（元の文字数と
  次の手。読み直すツールがあればその名前）を足す。同じツールの呼び直しを案内するのは、信頼できる
  読み取り専用のツールだけ（副作用があり得るツールは、呼び直すと同じ操作をもう一度実行する。#2632）。
- Run の step に残すツールの結果は全文のまま（回答の検証・引用の照合は step を使う）。縮めるのは
  モデルに渡す入力だけで、呼び出しごとに元の入力から作り直す。

ここは見積もり・予算・縮め方だけを持つ（Run への記録と SDK・プロバイダへの組み込みは
`builtin_runtime`）。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pr_system_settings.model import estimate_input_tokens

# 縮めた結果に足す項目の名前。
COMPACTION_KEY = "context_compaction"
# 段階ごとの（一覧の項目の文字列の長さの上限, 一覧で全体を残す項目の数〔None はすべて〕）。
_LEVELS: dict[int, tuple[int, int | None]] = {1: (400, None), 2: (200, 5), 3: (100, 0)}
OLDER_MAX_LEVEL = 4
LATEST_MAX_LEVEL = 3
# JSON ではない（文字列の）結果の、段階ごとの残す長さ。
_TEXT_LIMITS = {1: 4000, 2: 1500, 3: 500, 4: 200}
# 一覧の外の文字列を段階 3 で残す長さ。
_TOP_LEVEL_TEXT_CHARS = 200
# 1 件の入力の項目ごとの見積もりの上乗せ（役割・区切りの token）。
_ITEM_OVERHEAD_TOKENS = 8
# プロバイダが入力の超過を返したが prompt の token 数が読めないときに、見積もりに掛ける倍率。
FALLBACK_PROVIDER_SCALE = 1.5
_ID_KEY = re.compile(r"(?:^|_)id$")
_TOOL_OUTPUT_TYPES = {"function_call_output"}


@dataclass(frozen=True)
class ContextLimits:
    """Run のモデルの入力・出力の上限（tokens。システム設定 > モデル の登録モデルの値）。"""

    model_id: str
    max_input_tokens: int
    max_output_tokens: int


@dataclass
class ContextBudget:
    """入力の予算と見積もりの係数（係数はプロバイダの超過で Run の中で補正する）。"""

    limits: ContextLimits
    estimate_scale: float = 1.0
    margin_ratio: float = 0.05

    @property
    def output_reserve_tokens(self) -> int:
        return max(0, min(self.limits.max_output_tokens, self.limits.max_input_tokens // 2))

    @property
    def margin_tokens(self) -> int:
        return max(0, math.ceil(self.limits.max_input_tokens * self.margin_ratio))

    def input_tokens(self, max_input_tokens: int | None = None) -> int:
        """会話・ツールの結果と指示・ツールの定義に使える token の数。"""
        limit = self.limits.max_input_tokens
        if max_input_tokens is not None and max_input_tokens > 0:
            limit = min(limit, max_input_tokens)
        return max(0, limit - self.output_reserve_tokens - self.margin_tokens)

    def estimate(self, text: str) -> int:
        return math.ceil(estimate_input_tokens(text) * self.estimate_scale)


@dataclass(frozen=True)
class CompactedOutput:
    """縮めたツールの結果 1 件（Run の記録に残す）。"""

    call_id: str
    tool_name: str
    level: int
    original_chars: int
    compacted_chars: int
    latest: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "level": self.level,
            "original_chars": self.original_chars,
            "compacted_chars": self.compacted_chars,
            "latest": self.latest,
        }


@dataclass
class CompactionResult:
    """縮めた入力と、予算・見積もりの前後。"""

    items: list[Any]
    budget_tokens: int
    fixed_tokens: int
    tokens_before: int
    tokens_after: int
    outputs: list[CompactedOutput] = field(default_factory=list)

    @property
    def fits(self) -> bool:
        return self.tokens_after <= self.budget_tokens

    @property
    def fixed_exceeds(self) -> bool:
        """指示とツールの定義だけで予算を超えるか（ツールの結果を縮めても収まらない）。"""
        return self.fixed_tokens > self.budget_tokens


def _as_dict(item: Any) -> Any:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        dumped = item.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else item
    return item


def _item_text(item: Any) -> str:
    data = _as_dict(item)
    if isinstance(data, dict):
        output = data.get("output")
        if data.get("type") in _TOOL_OUTPUT_TYPES and isinstance(output, str):
            return output
        content = data.get("content")
        if isinstance(content, str) and set(data) <= {"role", "content", "type"}:
            return content
        return json.dumps(data, ensure_ascii=False, default=str)
    return str(item)


def tools_text(tools: Sequence[Any]) -> str:
    """ツールの定義（名前・説明・引数の schema）を見積もる文字列にする。"""
    parts: list[str] = []
    for tool in tools:
        data = _as_dict(tool)
        if isinstance(data, dict):
            parts.append(json.dumps(data, ensure_ascii=False, default=str))
            continue
        parts.append(
            json.dumps(
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "params_json_schema", None),
                },
                ensure_ascii=False,
                default=str,
            )
        )
    return "\n".join(parts)


def is_id_key(key: object) -> bool:
    """ID の項目か（`id`・`*_id`）。縮めても消さない。"""
    return isinstance(key, str) and bool(_ID_KEY.search(key))


def _id_ref(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if is_id_key(key) and isinstance(value, str | int) and not isinstance(value, bool)
    }


@dataclass
class _Stats:
    clipped: int = 0
    id_only: int = 0
    dropped: int = 0


def _clip(text: str, chars: int, stats: _Stats) -> str:
    if len(text) <= chars:
        return text
    stats.clipped += 1
    return text[:chars] + "…"


def _shrink(value: Any, *, level: int, stats: _Stats, in_list: bool = False) -> Any:
    chars, keep = _LEVELS[level]
    if isinstance(value, str):
        if in_list:
            return _clip(value, chars, stats)
        return _clip(value, _TOP_LEVEL_TEXT_CHARS, stats) if level >= 3 else value
    if isinstance(value, list):
        shrunk: list[Any] = []
        for index, item in enumerate(value):
            if keep is not None and index >= keep:
                ref = _id_ref(item) if isinstance(item, dict) else {}
                if ref:
                    shrunk.append(ref)
                    stats.id_only += 1
                else:
                    stats.dropped += 1
                continue
            shrunk.append(_shrink(item, level=level, stats=stats, in_list=True))
        return shrunk
    if isinstance(value, dict):
        clipped = stats.clipped
        result = {
            key: item
            if is_id_key(key)
            else _shrink(item, level=level, stats=stats, in_list=in_list)
            for key, item in value.items()
            if key != COMPACTION_KEY
        }
        if isinstance(result.get("truncated"), bool) and stats.clipped > clipped:
            result["truncated"] = True
        return result
    return value


def _ids_only(value: Any, stats: _Stats) -> Any:
    if isinstance(value, dict):
        kept: dict[str, Any] = _id_ref(value)
        for key, item in value.items():
            if is_id_key(key) or key == COMPACTION_KEY:
                continue
            if isinstance(item, dict | list):
                nested = _ids_only(item, stats)
                if nested:
                    kept[key] = nested
        return kept
    if isinstance(value, list):
        refs: list[Any] = []
        for item in value:
            ref = _ids_only(item, stats) if isinstance(item, dict) else None
            if ref:
                refs.append(ref)
                stats.id_only += 1
            else:
                stats.dropped += 1
        return refs
    return None


def _note(
    original_chars: int, stats: _Stats, read_tool: str | None, rerunnable: bool
) -> dict[str, Any]:
    done: list[str] = []
    if stats.clipped:
        done.append(f"長い文字列 {stats.clipped} 件を先頭だけ")
    if stats.id_only:
        done.append(f"{stats.id_only} 件を ID だけ")
    if stats.dropped:
        done.append(f"ID の無い {stats.dropped} 件を省略")
    summary = "、".join(done) or "一部を省略"
    text = (
        f"この結果は長いため縮めました（元は {original_chars} 文字。{summary}）。"
        "ID の項目は元のまま残しているので、引用にそのまま使えます。"
    )
    if read_tool:
        text += (
            f"本文が要る根拠は、document_id と chunk_id を {read_tool} に渡して読み直してください。"
        )
    elif rerunnable:
        text += "省いた内容が要るときは、対象を絞って同じツールを呼び直してください。"
    else:
        # 副作用があり得るツール（書き込み・注釈を信頼しない接続）の結果は、呼び直すと同じ操作を
        # もう一度実行する（#2632）。省いた内容を得る手段として呼び直させない。
        text += (
            "省いた内容は読み直せません。このツールは呼び直さず、残っている内容で続けてください。"
        )
    return {"original_chars": original_chars, "next_step": text}


def compact_output(
    output: str, level: int, *, read_tool: str | None = None, rerunnable: bool = False
) -> str:
    """ツールの結果（JSON の文字列か文字列）を段階 `level` で縮める（縮まなければ元のまま）。

    `read_tool` は本文を読み直すツール、`rerunnable` は同じツールを呼び直しても副作用が無いか
    （信頼できる読み取り専用。既定は False で、呼び直しを案内しない）。
    """
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        previous = data.get(COMPACTION_KEY)
        original_chars = (
            int(previous["original_chars"])
            if isinstance(previous, dict) and isinstance(previous.get("original_chars"), int)
            else len(output)
        )
        stats = _Stats()
        shrunk = (
            _ids_only(data, stats)
            if level >= OLDER_MAX_LEVEL
            else _shrink(data, level=level, stats=stats)
        )
        compacted = json.dumps(
            {**shrunk, COMPACTION_KEY: _note(original_chars, stats, read_tool, rerunnable)},
            ensure_ascii=False,
            default=str,
        )
        return compacted if len(compacted) < len(output) else output
    limit = _TEXT_LIMITS[min(level, OLDER_MAX_LEVEL)]
    if len(output) <= limit:
        return output
    return (
        f"{output[:limit]}\n…（この結果は長いため、{len(output)} 文字のうち先頭の {limit} 文字"
        "だけを渡しています。）"
    )


def _call_names(items: Sequence[Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in items:
        data = _as_dict(item)
        if isinstance(data, dict) and data.get("type") == "function_call":
            call_id, name = data.get("call_id"), data.get("name")
            if isinstance(call_id, str) and isinstance(name, str):
                names[call_id] = name
    return names


def _latest_outputs(items: Sequence[Any], outputs: Sequence[int]) -> set[int]:
    """最新のツールの呼び出しのまとまりの結果（最後のツールの結果と、その前に続くツールの結果）。

    SDK は 1 回の応答のツールの呼び出しを並べた後に結果を並べる（`呼び出し, 呼び出し, 結果,
    結果`）ので、続く結果のまとまりが同じ応答の呼び出しの結果になる（前の応答の結果とは、
    次の応答の呼び出しで区切られる）。
    """
    if not outputs:
        return set()
    index = outputs[-1]
    while index > 0:
        data = _as_dict(items[index - 1])
        if not isinstance(data, dict) or data.get("type") not in _TOOL_OUTPUT_TYPES:
            break
        index -= 1
    return {position for position in outputs if position >= index}


def _item_tokens(item: Any, budget: ContextBudget) -> int:
    return budget.estimate(_item_text(item)) + _ITEM_OVERHEAD_TOKENS


def estimate_tokens(items: Sequence[Any], *, budget: ContextBudget, fixed_text: str) -> int:
    """入力（指示・ツールの定義と、入力の項目）の token の見積もり。"""
    return budget.estimate(fixed_text) + sum(_item_tokens(_as_dict(item), budget) for item in items)


def compact_items(
    items: Sequence[Any],
    *,
    budget: ContextBudget,
    fixed_text: str,
    read_tool_for: Callable[[str], str | None] | None = None,
    rerunnable: Callable[[str], bool] | None = None,
    max_input_tokens: int | None = None,
) -> CompactionResult:
    """入力の項目を予算に収める（元の項目は変えない。収まらなければ縮められるだけ縮めて返す）。

    `read_tool_for` はツールの名前 → 本文を読み直すツール、`rerunnable` はツールの名前 → 同じ
    ツールを呼び直しても副作用が無いか（無ければどのツールも呼び直しを案内しない）。
    """
    budget_tokens = budget.input_tokens(max_input_tokens)
    fixed_tokens = budget.estimate(fixed_text)
    sized = [_as_dict(item) for item in items]
    tokens = [_item_tokens(item, budget) for item in sized]
    before = fixed_tokens + sum(tokens)
    result = CompactionResult(
        items=list(items),
        budget_tokens=budget_tokens,
        fixed_tokens=fixed_tokens,
        tokens_before=before,
        tokens_after=before,
    )
    if before <= budget_tokens:
        return result
    outputs = [
        index
        for index, item in enumerate(sized)
        if isinstance(item, dict)
        and item.get("type") in _TOOL_OUTPUT_TYPES
        and isinstance(item.get("output"), str)
    ]
    latest = _latest_outputs(sized, outputs)
    names = _call_names(sized)
    compacted: list[Any] = list(items)
    levels: dict[int, int] = {}
    total = before

    def apply(index: int, level: int) -> None:
        nonlocal total
        item = sized[index]
        output = str(item["output"])
        call_id = str(item.get("call_id") or "")
        name = names.get(call_id, "")
        read_tool = read_tool_for(name) if read_tool_for is not None and name else None
        safe_to_rerun = bool(name) and rerunnable is not None and rerunnable(name)
        shrunk = compact_output(output, level, read_tool=read_tool, rerunnable=safe_to_rerun)
        if shrunk == output:
            return
        compacted[index] = {**item, "output": shrunk}
        size = _item_tokens(shrunk, budget)
        total += size - tokens[index]
        tokens[index] = size
        levels[index] = level

    for group, max_level in (
        ([index for index in outputs if index not in latest], OLDER_MAX_LEVEL),
        ([index for index in outputs if index in latest], LATEST_MAX_LEVEL),
    ):
        for level in range(1, max_level + 1):
            for index in group:
                if total <= budget_tokens:
                    break
                apply(index, level)
        if total <= budget_tokens:
            break

    result.items = compacted
    result.tokens_after = total
    result.outputs = [
        CompactedOutput(
            call_id=str(sized[index].get("call_id") or ""),
            tool_name=names.get(str(sized[index].get("call_id") or ""), ""),
            level=level,
            original_chars=len(str(sized[index]["output"])),
            compacted_chars=len(str(compacted[index]["output"])),
            latest=index in latest,
        )
        for index, level in sorted(levels.items())
    ]
    return result


# プロバイダが返す入力の超過の文（vLLM・OpenAI 互換 API）。
_OVERFLOW_CODES = {"context_length_exceeded", "string_above_max_length", "input_too_long"}
_OVERFLOW_TEXT = re.compile(
    r"max_model_len|context length|context window|maximum context|prompt is too long"
    r"|prompt length|too many (?:input )?tokens|input (?:is )?too long"
    r"|reduce (?:the )?(?:prompt|input)",
    re.IGNORECASE,
)
_PROMPT_AND_LIMIT = (
    # vLLM: "The engine prompt length 160066 exceeds the max_model_len 131072."
    re.compile(r"prompt length (\d+) exceeds the max_model_len (\d+)", re.IGNORECASE),
    # OpenAI 互換: "maximum context length is 131072 tokens. However, ... resulted in 160066 tokens"
    re.compile(
        r"maximum context length is (?P<limit>\d+) tokens.*?(?P<prompt>\d+) tokens",
        re.IGNORECASE | re.DOTALL,
    ),
)


@dataclass(frozen=True)
class ProviderOverflow:
    """プロバイダが返した入力の超過（prompt の token 数と上限。読めなければ None）。"""

    prompt_tokens: int | None = None
    limit_tokens: int | None = None


def provider_overflow(code: str | None, message: str | None) -> ProviderOverflow | None:
    """プロバイダのエラーが入力の超過なら、その token 数（超過でなければ None）。"""
    text = message or ""
    if (code or "") not in _OVERFLOW_CODES and not _OVERFLOW_TEXT.search(text):
        return None
    vllm, openai_style = _PROMPT_AND_LIMIT
    if match := vllm.search(text):
        return ProviderOverflow(prompt_tokens=int(match.group(1)), limit_tokens=int(match.group(2)))
    if match := openai_style.search(text):
        return ProviderOverflow(
            prompt_tokens=int(match.group("prompt")), limit_tokens=int(match.group("limit"))
        )
    return ProviderOverflow()


def overflow_message(result: CompactionResult, budget: ContextBudget) -> str:
    """縮めても収まらないときの、利用者に分かる失敗の理由と直し方。"""
    limits = budget.limits
    numbers = (
        f"見積もり 約 {result.tokens_after:,} tokens、使える量 {result.budget_tokens:,} tokens"
        f"（入力の上限 {limits.max_input_tokens:,} から出力の分 {budget.output_reserve_tokens:,} と"
        f"余白 {budget.margin_tokens:,} を引いた量）"
    )
    if result.fixed_exceeds:
        return (
            f"業務 Agent の指示とツールの定義だけで、モデル「{limits.model_id}」の入力の上限に"
            f"収まりません（{numbers}）。業務 Agent のスキル・指示を減らすか、"
            "システム設定 > モデル で入力の上限の大きいモデルを業務 Agent に設定してください。"
        )
    return (
        f"モデルに渡す入力（会話とツールの結果）が、モデル「{limits.model_id}」の入力の上限に"
        f"収まりません（ツールの結果を縮めた後も{numbers}）。質問の対象（文書・期間・条件）を"
        "絞って質問し直すか、システム設定 > モデル で入力の上限の大きいモデルを業務 Agent に"
        "設定してください。"
    )


def provider_overflow_message(budget: ContextBudget, overflow: ProviderOverflow) -> str:
    """ツールの結果を縮めて送り直しても、プロバイダが入力の超過を返したときの失敗の理由。"""
    counted = (
        f"（プロバイダが数えた入力 {overflow.prompt_tokens:,} tokens、"
        f"上限 {overflow.limit_tokens:,} tokens）"
        if overflow.prompt_tokens is not None and overflow.limit_tokens is not None
        else ""
    )
    return (
        f"モデルに渡す入力が、モデル「{budget.limits.model_id}」の入力の上限を超えました{counted}。"
        "ツールの結果を縮めて送り直しましたが収まりませんでした。質問の対象（文書・期間・条件）を"
        "絞って質問し直すか、システム設定 > モデル で入力の上限を実際のモデルに合わせる・"
        "入力の上限の大きいモデルを業務 Agent に設定してください。"
    )


__all__ = [
    "COMPACTION_KEY",
    "FALLBACK_PROVIDER_SCALE",
    "CompactedOutput",
    "CompactionResult",
    "ContextBudget",
    "ContextLimits",
    "ProviderOverflow",
    "compact_items",
    "compact_output",
    "estimate_tokens",
    "is_id_key",
    "overflow_message",
    "provider_overflow",
    "provider_overflow_message",
    "tools_text",
]
