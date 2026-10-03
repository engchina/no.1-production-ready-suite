"""導入済み Skill の参照文書を必要な部分だけ読む。取得済みの内容以外は参照しない。"""

from __future__ import annotations

from typing import Any

from app.features.agent.plugins import plugin_resource_registry
from app.features.agent.skills import skill_registry
from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext

RESOURCE_TOOL_NAME = "skill_reference_read"
REFERENCE_CHUNK_CHARACTERS = 8000


def skill_reference_ids(skill_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for skill_id in skill_ids:
        skill = skill_registry.get(skill_id)
        if skill is None or not skill.enabled:
            continue
        for resource_id in skill.resource_ids:
            resource = plugin_resource_registry.get(resource_id)
            if (
                resource
                and resource.metadata.get("external_skill_reference")
                and resource_id not in ids
            ):
                ids.append(resource_id)
    return ids


def reference_tool(resource_ids: list[str]) -> tuple[ToolDefinition, ToolHandler]:
    allowed = frozenset(resource_ids)

    def read(arguments: dict[str, Any], _: ToolInvocationContext) -> dict[str, Any]:
        resource_id = arguments.get("resource_id")
        offset = arguments.get("offset", 0)
        if not isinstance(resource_id, str) or resource_id not in allowed:
            raise ValueError("この実行の Skill に割り当てられていない参照文書です。")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset は 0 以上の整数を指定してください。")
        resource = plugin_resource_registry.get(resource_id)
        if resource is None or not isinstance(resource.content, str):
            raise ValueError("参照文書が見つかりません。")
        content = resource.content[offset : offset + REFERENCE_CHUNK_CHARACTERS]
        end = offset + len(content)
        return {
            "resource_id": resource_id,
            "path": resource.name,
            "content": content,
            "next_offset": end if end < len(resource.content) else None,
        }

    return ToolDefinition(
        name=RESOURCE_TOOL_NAME,
        description="割り当て済み Skill の参照文書を 8000 文字ずつ読む。文書を実行しない。",
        input_schema={
            "type": "object",
            "properties": {
                "resource_id": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["resource_id", "offset"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        side_effects=False,
        audit_tags=["skill", "reference"],
    ), read
