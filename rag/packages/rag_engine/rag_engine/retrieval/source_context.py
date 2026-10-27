"""根拠ごとの完全な出典。本文・引用照合とは分離して扱う。"""

from __future__ import annotations

import json
from typing import Any


def source_context(record: Any) -> dict[str, Any]:
    metadata = getattr(record, "metadata", {}) or {}
    path = metadata.get("section_path")
    if isinstance(path, str):
        path = [part.strip() for part in path.split(" > ") if part.strip()]
    context: dict[str, Any] = {"document_name": str(getattr(record, "source", "") or "")}
    if isinstance(path, (list, tuple)):
        context["section_path"] = [str(part) for part in path]
    header = metadata.get("context_header")
    if isinstance(header, str) and header.strip():
        context["search_context_header"] = header
    enabled = metadata.get("context_header_enabled")
    if isinstance(enabled, bool):
        context["search_context_header_enabled"] = enabled
    return context


def source_context_text(context: dict[str, Any]) -> str:
    return "出典情報: " + json.dumps(context, ensure_ascii=False)
