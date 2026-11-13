"""許可された schema の名前とページ状態を、モデル入力の圧縮でも保つ。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_MAX_SOURCE_BYTES = 128 * 1024
_MAX_SUMMARY_CHARS = 16_000


class _Wire(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Column(_Wire):
    field_ref: str | None = Field(default=None, pattern=r"^S[1-9][0-9]*:F[1-9][0-9]*$")
    name: str = Field(min_length=1, max_length=512)
    data_type: str = Field(min_length=1, max_length=512)
    nullable: bool
    logical_name: str = Field(default="", max_length=512)
    description: str = Field(default="", max_length=256)
    description_truncated: bool = False


class _Object(_Wire):
    owner: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=512)
    object_type: str = Field(min_length=1, max_length=512)
    logical_name: str = Field(default="", max_length=512)
    description: str = Field(default="", max_length=256)
    description_truncated: bool = False
    columns: list[_Column] = Field(max_length=500)
    columns_complete: bool


class _Schema(_Wire):
    schema_ref: str | None = Field(default=None, pattern=r"^S[1-9][0-9]*$")
    descriptions_omitted: Literal[True] | None = None
    schema_version: Literal[4]
    profile_id: str = Field(min_length=1, max_length=128)
    source: Literal["cached"]
    status: Literal["available", "unavailable", "stale"]
    unavailable_reason: (
        Literal[
            "schema_unavailable",
            "scope_unresolved",
            "snapshot_changed",
            "invalid_cursor",
            "metadata_limit",
        ]
        | None
    )
    refreshed_at: str = Field(max_length=128)
    objects: list[_Object] = Field(max_length=500)
    page_complete: bool
    next_cursor: str | None = Field(max_length=1024)
    truncated: bool
    total_objects: int = Field(ge=0)
    total_columns: int = Field(ge=0)


def profile_schema_summary(value: Any) -> dict[str, Any] | None:
    """schema wire だけを処理する。省いた列を存在しない列として見せない。"""
    if not isinstance(value, dict) or not (
        value.get("schema_version") == 4
        and value.get("source") == "cached"
        and "objects" in value
        and "page_complete" in value
    ):
        return None
    try:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > _MAX_SOURCE_BYTES:
            return _unavailable(value)
        parsed = _Schema.model_validate(value)
    except (TypeError, ValueError, ValidationError, RecursionError):
        return _unavailable(value)

    summary = parsed.model_dump(mode="json")
    if summary["descriptions_omitted"] is None:
        summary.pop("descriptions_omitted")
    if summary["schema_ref"] is None:
        summary.pop("schema_ref")
    for obj in summary["objects"]:
        for column in obj["columns"]:
            if column["field_ref"] is None:
                column.pop("field_ref")

    def omit_descriptions() -> None:
        summary["descriptions_omitted"] = True
        summary["truncated"] = True
        summary["page_complete"] = False
        for obj in summary["objects"]:
            for entry in [obj, *obj["columns"]]:
                for key in ("logical_name", "description", "description_truncated"):
                    entry.pop(key, None)

    if parsed.descriptions_omitted:
        omit_descriptions()

    def size() -> int:
        return len(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))

    if size() > _MAX_SUMMARY_CHARS:
        # 空の説明キーも繰り返さず、識別名・型・nullability を優先する。
        for obj in summary["objects"]:
            obj.pop("description", None)
            obj.pop("logical_name", None)
            obj["description_truncated"] = True
            for column in obj["columns"]:
                column.pop("description", None)
                column.pop("logical_name", None)
                column["description_truncated"] = True
        summary["truncated"] = True
        summary["page_complete"] = False
    if summary.get("schema_ref") and size() > _MAX_SUMMARY_CHARS:
        omit_descriptions()
    while size() > _MAX_SUMMARY_CHARS:
        tail_object = next((obj for obj in reversed(summary["objects"]) if obj["columns"]), None)
        if tail_object is not None:
            tail_object["columns"].pop()
            tail_object["columns_complete"] = False
        elif summary["objects"]:
            summary["objects"].pop()
        else:
            return _unavailable(value)
        summary["truncated"] = True
        summary["page_complete"] = False
    return summary


def _unavailable(value: dict[str, Any]) -> dict[str, Any]:
    profile = value.get("profile_id")
    return {
        "schema_version": 4,
        "profile_id": profile if isinstance(profile, str) and 0 < len(profile) <= 128 else "",
        "source": "cached",
        "status": "unavailable",
        "unavailable_reason": "metadata_limit",
        "objects": [],
        "page_complete": False,
        "next_cursor": None,
        "truncated": True,
    }
