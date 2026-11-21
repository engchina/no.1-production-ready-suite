"""provider が明示した usage だけを正規化する。cache / reasoning は合計の内数。"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pr_backend_core.generation_input import reported_input_tokens

if TYPE_CHECKING:
    from .tracing import ObservedSpan


def _field(usage: object, key: str) -> object:
    return usage.get(key) if isinstance(usage, Mapping) else getattr(usage, key, None)


def _count(usage: object, *keys: str) -> int | None:
    for key in keys:
        value = _field(usage, key)
        if type(value) is int and value >= 0:
            return value
    return None


def reported_token_counts(usage: object) -> dict[str, int]:
    """Responses / Chat Completions の実測値。未報告の項目を除き、0 は残す。"""
    input_details = _field(usage, "input_tokens_details") or _field(usage, "prompt_tokens_details")
    output_details = _field(usage, "output_tokens_details") or _field(
        usage, "completion_tokens_details"
    )
    totals = {}
    if (input_tokens := reported_input_tokens(usage)) is not None:
        totals["input_tokens"] = input_tokens
    if (output_tokens := _count(usage, "output_tokens", "completion_tokens")) is not None:
        totals["output_tokens"] = output_tokens
    cached = _count(input_details, "cached_tokens")
    if cached is not None and (input_tokens is None or cached <= input_tokens):
        totals["cached_tokens"] = cached
    reasoning = _count(output_details, "reasoning_tokens")
    if reasoning is not None and (output_tokens is None or reasoning <= output_tokens):
        totals["reasoning_tokens"] = reasoning
    return totals


def genai_usage_attributes(usage: object) -> dict[str, int]:
    names = {
        "input_tokens": "gen_ai.usage.input_tokens",
        "output_tokens": "gen_ai.usage.output_tokens",
        "cached_tokens": "gen_ai.usage.cache_read.input_tokens",
        "reasoning_tokens": "gen_ai.usage.reasoning.output_tokens",
    }
    return {names[key]: count for key, count in reported_token_counts(usage).items()}


def record_genai_usage(span: "ObservedSpan", usage: object) -> None:
    span.set_attributes(genai_usage_attributes(usage))
