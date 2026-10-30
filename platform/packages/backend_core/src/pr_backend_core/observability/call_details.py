"""製品の記録用の呼び出し snapshot。HTTP header・binary・画像の実体は保存しない。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from ..chat_progress import CallContent, CallDetails

_SECRET = re.compile(
    r"password|passwd|secret|token$|api[_-]?key|authorization|cookie|credential", re.I
)
_MEDIA = {"image_url", "image_bytes", "audio", "file_data", "b64_json"}


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 30:
        return "[depth omitted]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    elif callable(getattr(value, "model_dump", None)):
        value = value.model_dump()
    if isinstance(value, Mapping):
        return {
            str(k): "***"
            if _SECRET.search(str(k)) and str(k) not in {"max_output_tokens", "max_tokens"}
            else "[media omitted]"
            if str(k) in _MEDIA
            else _safe(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth + 1) for v in value[:1000]] + (
            ["[remaining items omitted]"] if len(value) > 1000 else []
        )
    if isinstance(value, str):
        return "[media omitted]" if value.startswith("data:") else value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return "[unsupported value]"


def call_content(value: Any) -> CallContent:
    """送った値をコピーし、secret の除去を長さの制限より先に行う。"""
    try:
        safe = _safe(value)
        text = safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
        return CallContent.model_validate({"status": "recorded", "text": text})
    except Exception:
        return CallContent()


def call_input(request: Any, schema: Any = None) -> CallDetails:
    if schema is None and isinstance(request, Mapping):
        schema = {k: request[k] for k in ("tools", "text", "response_format") if k in request}
    return CallDetails(
        input=call_content(request), **({"schema": call_content(schema)} if schema else {})
    )


def withhold_call_details(value: Any, *, allow_restricted: bool, all_calls: bool = False) -> Any:
    """詳細を保存したまま、今の閲覧権限を満たさない snapshot の本文だけを隠す。"""
    if isinstance(value, dict):
        if (
            "visibility" in value
            and all(key in value for key in ("input", "output", "schema"))
            and (all_calls or (value["visibility"] == "system_admin" and not allow_restricted))
        ):
            return {
                **value,
                **{
                    key: {"status": "withheld", "text": ""} for key in ("input", "output", "schema")
                },
            }
        return {
            key: withhold_call_details(item, allow_restricted=allow_restricted, all_calls=all_calls)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            withhold_call_details(item, allow_restricted=allow_restricted, all_calls=all_calls)
            for item in value
        ]
    return value
