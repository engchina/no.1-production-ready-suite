"""RAG MCP 改名の保存値移行。instructions・ユーザー本文の文字列は置換しない。"""

from __future__ import annotations

import json
from typing import Any

_OLD_LIST = "rag_list_business_views"
_NEW_LIST = "rag_list_search_answer_profiles"
_FIELDS = {
    "business_view_id": "search_answer_profile_id",
    "business_view_ids": "search_answer_profile_ids",
}


def migrate_tool_name(name: str) -> str:
    prefix, separator, base = name.rpartition("__")
    if base == _OLD_LIST:
        return prefix + separator + _NEW_LIST
    return name


def migrate_rag_call(value: object) -> object:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return value
    name = value["name"]
    if name.rsplit("__", 1)[-1] not in {_OLD_LIST, _NEW_LIST, "rag_search"}:
        return value
    result = dict(value)
    result["name"] = migrate_tool_name(name)
    arguments = value.get("arguments")
    encoded = isinstance(arguments, str)
    if encoded:
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return result
    if isinstance(arguments, dict):
        arguments = dict(arguments)
        for old, new in _FIELDS.items():
            if old not in arguments:
                continue
            if new in arguments and arguments[new] != arguments[old]:
                raise ValueError("保存された RAG 対象の新旧指定が一致しません。")
            arguments[new] = arguments.pop(old)
        result["arguments"] = json.dumps(arguments, ensure_ascii=False) if encoded else arguments
    return result


def migrate_sdk_checkpoint(text: str) -> str:
    """SDK の function call だけを移行する。未解析の文字列は改変しない。"""
    try:
        source = json.loads(text)
    except ValueError:
        return text

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            result = {key: visit(item) for key, item in value.items()}
            if value.get("type") in {"function_call", "tool_call"}:
                return migrate_rag_call(result)
            return result
        return value

    result = visit(source)
    return text if result == source else json.dumps(result, ensure_ascii=False)
