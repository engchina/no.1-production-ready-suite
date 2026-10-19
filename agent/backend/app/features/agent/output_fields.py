"""業務 Agent の出力の項目と、その値を記録するツール（#2549）。

業務 Agent に出力の項目（key・表示名・型・必須）を定義すると、その Run にだけ
`record_output` を渡す。引数の JSON schema は業務 Agent の項目から作り、値の検証はここ
（コード）で行う。誤り（必須の欠け・型・日付の形）は項目ごとにモデルへ返して直させ、正しく
記録できた行を成果物（`structured_output`）に残す。成果物の形は画面の表
（`toTabularData` の `{columns, rows}`）と同じ。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.features.agent.tools import (
    ExternalToolError,
    ToolDefinition,
    ToolHandler,
    ToolInvocationContext,
)

RECORD_OUTPUT_TOOL = "record_output"
OUTPUT_KIND = "structured_output"
OUTPUT_NAME = "出力の項目"
# 1 つの業務 Agent に定義できる項目の数、1 回の記録の行の数、値の文字数の上限。
MAX_OUTPUT_FIELDS = 30
MAX_OUTPUT_ROWS = 200
MAX_VALUE_CHARS = 2000
OUTPUT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

OutputFieldType = Literal["text", "number", "date", "boolean"]

_JSON_TYPES: dict[str, str] = {
    "text": "string",
    "number": "number",
    "date": "string",
    "boolean": "boolean",
}
_TYPE_LABELS: dict[str, str] = {
    "text": "文字",
    "number": "数値",
    "date": "日付（YYYY-MM-DD）",
    "boolean": "真偽",
}


class AgentOutputField(BaseModel):
    """出力の項目の 1 つ。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=40)
    type: OutputFieldType = "text"
    required: bool = False
    description: str = Field(default="", max_length=200)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        key = value.strip()
        if not OUTPUT_KEY_PATTERN.fullmatch(key):
            raise ValueError(
                "項目の key は英小文字で始め、英小文字・数字・_ の 40 文字以内にしてください。"
            )
        return key

    @field_validator("label", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def validate_output_fields(fields: Sequence[AgentOutputField]) -> list[AgentOutputField]:
    """項目の一覧の検証（数の上限・key の重複）。違反は ValueError（画面に出す文）。"""
    if len(fields) > MAX_OUTPUT_FIELDS:
        raise ValueError(f"出力の項目は {MAX_OUTPUT_FIELDS} 項目までです。")
    keys = [field.key for field in fields]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise ValueError(f"出力の項目の key が重なっています: {'、'.join(duplicated)}")
    return list(fields)


def output_input_schema(fields: Sequence[AgentOutputField]) -> dict[str, Any]:
    """`record_output` の引数の JSON schema（業務 Agent の項目から作る）。"""
    properties: dict[str, Any] = {}
    for field in fields:
        description = f"{field.label}（{_TYPE_LABELS[field.type]}）"
        if field.description:
            description += f"。{field.description}"
        if not field.required:
            description += "。分からなければ null"
        properties[field.key] = {
            "type": _JSON_TYPES[field.type]
            if field.required
            else [_JSON_TYPES[field.type], "null"],
            "description": description,
        }
    return {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "description": f"記録する行（1〜{MAX_OUTPUT_ROWS} 行。明細が複数なら 1 件 1 行）",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": [field.key for field in fields if field.required],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }


def _value_error(field: AgentOutputField, value: Any) -> str | None:
    """値の型の誤り（無ければ None）。"""
    if field.type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "数値で書いてください。"
    elif field.type == "boolean":
        if not isinstance(value, bool):
            return "true か false で書いてください。"
    elif not isinstance(value, str):
        return "文字列で書いてください。"
    elif len(value) > MAX_VALUE_CHARS:
        return f"{MAX_VALUE_CHARS} 文字以内にしてください。"
    elif field.type == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return "日付は YYYY-MM-DD の形で書いてください。"
    return None


def validate_rows(
    fields: Sequence[AgentOutputField], arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    """記録する行を検証して返す。誤りは項目ごとに `ExternalToolError` でモデルへ返す。"""
    rows = arguments.get("rows")
    errors: list[dict[str, Any]] = []
    if not isinstance(rows, list) or not rows:
        errors.append({"loc": ["rows"], "message": "記録する行を 1 行以上渡してください。"})
    elif len(rows) > MAX_OUTPUT_ROWS:
        errors.append({"loc": ["rows"], "message": f"行は {MAX_OUTPUT_ROWS} 行までです。"})
    known = {field.key for field in fields}
    cleaned: list[dict[str, Any]] = []
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            errors.append(
                {"loc": ["rows", index], "message": "行は項目の key と値の object です。"}
            )
            continue
        for key in sorted(set(row) - known):
            errors.append({"loc": ["rows", index, key], "message": "出力の項目に無い key です。"})
        values: dict[str, Any] = {}
        for field in fields:
            value = row.get(field.key)
            if value is None or (isinstance(value, str) and not value.strip()):
                if field.required:
                    errors.append(
                        {"loc": ["rows", index, field.key], "message": "必須の項目です。"}
                    )
                values[field.key] = None
                continue
            problem = _value_error(field, value)
            if problem is not None:
                errors.append({"loc": ["rows", index, field.key], "message": problem})
            values[field.key] = value.strip() if isinstance(value, str) else value
        cleaned.append(values)
    if errors:
        raise ExternalToolError(
            "output.invalid",
            "出力の項目の値に誤りがあります。argument_errors の項目を直して、"
            "すべての行をもう一度記録してください。",
            {"details": {"errors": errors}},
        )
    return cleaned


def output_content(
    fields: Sequence[AgentOutputField], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """成果物の内容（画面の表の形）。"""
    return {
        "columns": [
            {"name": field.key, "label": field.label, "type": field.type} for field in fields
        ],
        "rows": rows,
    }


def record_output_tool(
    run_id: str, fields: Sequence[AgentOutputField]
) -> tuple[ToolDefinition, ToolHandler]:
    """Run に渡す `record_output`（その業務 Agent の項目の schema と、検証して残す handler）。"""
    columns = list(fields)

    def handler(arguments: dict[str, Any], _: ToolInvocationContext) -> dict[str, Any]:
        from app.features.agent.runtime import runtime_repository

        rows = validate_rows(columns, arguments)
        runtime_repository.save_builtin_artifact(
            run_id, kind=OUTPUT_KIND, name=OUTPUT_NAME, content=output_content(columns, rows)
        )
        # 表の形で返すと、画面がツールの結果の表としても出すので、件数だけを返す。
        return {"recorded": len(rows)}

    return (
        ToolDefinition(
            name=RECORD_OUTPUT_TOOL,
            title="出力の項目の記録",
            description=(
                "この業務 Agent の出力の項目の値を記録する。回答を書く前に、確かめた値で呼ぶ。"
                "呼び直すと前の記録を置き換えるので、すべての行をまとめて渡す。"
            ),
            input_schema=output_input_schema(columns),
            output_schema={"type": "object"},
            side_effects=False,
            audit_tags=["output"],
        ),
        handler,
    )


OUTPUT_RETRY_MESSAGE = (
    f"この業務 Agent には出力の項目があります。{RECORD_OUTPUT_TOOL} で項目の値を記録してから、"
    "回答をもう一度書いてください。確かめられなかった値は、必須でなければ null にしてください。"
)


def output_retried(metadata: object) -> bool:
    """この Run で、出力の項目の記録を差し戻したか（1 回だけ）。"""
    from app.features.agent.tool_grounding import METADATA_KEY

    record = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    return isinstance(record, dict) and bool(record.get("output_retry"))


def output_recorded(artifacts: Sequence[Any]) -> bool:
    """Run が出力の項目を記録したか。"""
    return any(getattr(item, "kind", None) == OUTPUT_KIND for item in artifacts)
