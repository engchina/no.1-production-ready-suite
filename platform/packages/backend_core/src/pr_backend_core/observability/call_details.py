"""製品の記録用の呼び出し snapshot。HTTP header・binary・画像の実体は保存しない。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from ..chat_progress import CALL_CONTENT_MAX, CallContent, CallDetails, redact_chat_progress_text
from .call_body_capture import current_body_capture
from .trace_bodies import BodyVisibility, prepare_body

_SECRET = re.compile(
    r"password|passwd|secret|token$|api[_-]?key|authorization|cookie|credential", re.I
)
_MEDIA = {"image_url", "image_bytes", "audio", "file_data", "b64_json"}


def _safe(value: Any, depth: int = 0, *, complete: bool = False) -> Any:
    if not complete and depth > 30:
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
            if str(k) in _MEDIA or (str(k) == "data" and value.get("type") in {"image", "audio"})
            else _safe(v, depth + 1, complete=complete)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _safe(v, depth + 1, complete=complete) for v in (value if complete else value[:1000])
        ] + (["[remaining items omitted]"] if not complete and len(value) > 1000 else [])
    if isinstance(value, str):
        if value.startswith("data:"):
            return "[media omitted]"
        # SDK はツールの結果を JSON の文字列としても渡す。
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(
                    _safe(json.loads(value), depth + 1, complete=complete), ensure_ascii=False
                )
            except (ValueError, TypeError):
                pass
        return value
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return "[unsupported value]"


def _shorten(value: Any, string_limit: int, array_limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= string_limit else value[:string_limit] + "…"
    if isinstance(value, dict):
        return {key: _shorten(item, string_limit, array_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten(item, string_limit, array_limit) for item in value[:array_limit]] + (
            ["[remaining items omitted]"] if len(value) > array_limit else []
        )
    return value


def _full_call_content(value: Any, *, visibility: BodyVisibility) -> CallContent:
    capture = current_body_capture()
    if capture is None:
        raise RuntimeError("trace body capture is not configured")
    try:
        safe = _safe(value, complete=True)
        text = redact_chat_progress_text(
            safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
        )
        if len(text) <= CALL_CONTENT_MAX:
            return CallContent(status="recorded", text=text)
        body = prepare_body(text, part_characters=capture.part_characters, visibility=visibility)
    except Exception:
        return CallContent(status="incomplete", failure_code="capture_failed")
    try:
        submission = capture.submit(body)
        if submission.reference != body.reference:
            raise ValueError("trace body submission returned another reference")
    except Exception:
        return CallContent(
            status="incomplete",
            text=text[:CALL_CONTENT_MAX],
            body_reference=body.reference,
            preview_only=True,
            failure_code="storage_unavailable",
        )
    return CallContent(
        status="recorded" if submission.accepted else "incomplete",
        text=text[:CALL_CONTENT_MAX],
        body_reference=body.reference,
        preview_only=len(text) > CALL_CONTENT_MAX,
        failure_code=submission.failure_code,
    )


def call_content(value: Any, *, visibility: BodyVisibility = "owner") -> CallContent:
    """送った値をコピーし、secret の除去を長さの制限より先に行う。"""
    if current_body_capture() is not None:
        return _full_call_content(value, visibility=visibility)
    try:
        safe = _safe(value)
        if isinstance(safe, str):
            if safe.lstrip().startswith(("{", "[")):
                try:
                    safe = json.loads(safe)
                except ValueError:
                    return CallContent.model_validate({"status": "recorded", "text": safe})
            else:
                return CallContent.model_validate({"status": "recorded", "text": safe})
        text = json.dumps(safe, ensure_ascii=False, indent=2)
        cut = "[remaining items omitted]" in text or bool(
            isinstance(safe, dict) and safe.get("capture_truncated")
        )
        if len(text) > CALL_CONTENT_MAX:
            cut = True
            # JSON の途中で切らず、値を縮めてから serialize する。
            string_limit, array_limit = CALL_CONTENT_MAX // 2, 1000
            while len(text) > CALL_CONTENT_MAX and string_limit >= 16:
                safe = _shorten(safe, string_limit, array_limit)
                text = json.dumps(safe, ensure_ascii=False, indent=2)
                string_limit //= 2
                array_limit = max(1, array_limit // 2)
            if len(text) > CALL_CONTENT_MAX:
                text = json.dumps(
                    {"preview": text[: CALL_CONTENT_MAX // 2], "omitted": True}, ensure_ascii=False
                )
        return CallContent(status="truncated" if cut else "recorded", text=text)
    except Exception:
        return CallContent()


def call_input(
    request: Any, schema: Any = None, *, visibility: BodyVisibility = "owner"
) -> CallDetails:
    if schema is None and isinstance(request, Mapping):
        schema = {k: request[k] for k in ("tools", "text", "response_format") if k in request}
    return CallDetails(
        visibility=visibility,
        input=call_content(request, visibility=visibility),
        **({"schema": call_content(schema, visibility=visibility)} if schema else {}),
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
