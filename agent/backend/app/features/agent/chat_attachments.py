"""チャットの添付（会話の期間だけ使う作業ファイル。#2517）。

保存・解析・見え方・期限は platform の `pr_system_settings.chat_attachments`。Agent が持つのは:

- 会話（thread）の持ち主の判定（添付の API の `conversation_owner`）。
- 画像の読み取り（既定の Vision モデル。OCI Enterprise AI の Responses API）。
- Run に渡すツール: 会話に添付があれば、その Run にだけ 4 つの添付のツール（`chat_attachment_list`
  など）を Control Plane のツールとして渡す（`skill_reference_read` と同じ per-run のツール。
  `tool_registry.invoke` のポリシー・監査を通す）。指示には添付の索引だけを入れる。
"""

from __future__ import annotations

import base64
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from pr_system_settings.chat_attachments import (
    CHAT_ATTACHMENT_TOOL_SPECS,
    IMAGE_READING_PROMPT,
    ChatAttachmentService,
    ChatAttachmentToolError,
    ChatAttachmentToolset,
)
from pr_system_settings.chat_attachments.tools import LIST_TOOL_NAME
from pr_system_settings.model import (
    enterprise_ai_connection_for_model,
    enterprise_ai_output_token_budget,
    enterprise_ai_reasoning,
    enterprise_ai_vision_model_id,
    seed_payload,
)

from app.features.agent.tools import ToolDefinition, ToolHandler, ToolInvocationContext
from app.settings import get_settings

logger = logging.getLogger(__name__)

# モデルが読んだら根拠・データを調べたと数える添付のツール（一覧は数えない。#1835 と同じ考え方）。
ATTACHMENT_READ_TOOLS = frozenset(
    spec.name for spec in CHAT_ATTACHMENT_TOOL_SPECS if spec.name != LIST_TOOL_NAME
)
ATTACHMENT_TOOL_NAMES = frozenset(spec.name for spec in CHAT_ATTACHMENT_TOOL_SPECS)
# 画像の読み取りの応答の待ち時間（秒）。
IMAGE_READ_TIMEOUT_SECONDS = 120.0

_SERVICE: ChatAttachmentService | None = None
_SERVICE_LOCK = threading.Lock()


def _image_reader() -> Callable[[bytes, str], Awaitable[str]] | None:
    """既定の Vision モデルで画像を文にする関数（Vision モデル・接続が無ければ None）。"""
    settings = get_settings()
    model_id = enterprise_ai_vision_model_id(settings)
    if not model_id:
        return None
    connection = enterprise_ai_connection_for_model(settings, model_id)
    if not connection.is_configured():
        return None

    async def read(data: bytes, media_type: str) -> str:
        from openai import AsyncOpenAI

        headers = {"OpenAI-Project": connection.project_ocid} if connection.project_ocid else None
        client = AsyncOpenAI(
            base_url=connection.base_url,
            api_key=connection.api_key,
            default_headers=headers,
            timeout=IMAGE_READ_TIMEOUT_SECONDS,
        )
        image_url = f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"
        extra: dict[str, Any] = {}
        reasoning = enterprise_ai_reasoning(settings, model_id)
        if reasoning is not None:
            extra["reasoning"] = reasoning
        response = await client.responses.create(
            model=model_id,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": IMAGE_READING_PROMPT},
                        {"type": "input_image", "image_url": image_url, "detail": "auto"},
                    ],
                }
            ],
            max_output_tokens=enterprise_ai_output_token_budget(settings, model_id),
            store=False,
            extra_body=seed_payload(settings),
            **extra,
        )
        return str(response.output_text or "")

    return read


def chat_attachment_service() -> ChatAttachmentService:
    """process で 1 つの添付のサービス（保存先は共通認証の store と同じ DB）。"""
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                from app.security.service import get_security_service

                security = get_security_service()
                _SERVICE = ChatAttachmentService(
                    security.chat_attachment_store,
                    product="agent",
                    settings=get_settings,
                    image_reader=_image_reader,
                    # 運用設定 › チャットの添付（製品ごと。既定は無効）。
                    enabled=security.chat_attachment_settings.enabled,
                    settings_failure=security.chat_attachment_settings.failure,
                )
    return _SERVICE


def set_chat_attachment_service(service: ChatAttachmentService | None) -> None:
    """テスト用: service を差し替える（None で既定に戻す）。"""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = service


def thread_attachment_toolset(thread_id: str | None) -> ChatAttachmentToolset | None:
    """会話の添付を読むツール（添付が無い・読めないときは None。Run は止めない）。"""
    if not thread_id:
        return None
    try:
        toolset = chat_attachment_service().toolset(thread_id)
    except Exception as exc:  # noqa: BLE001 - 添付を読めなくても Run は続ける
        logger.warning(
            "agent_chat_attachments_unavailable",
            extra={"thread_id": thread_id, "error_type": type(exc).__name__},
        )
        return None
    return toolset if toolset else None


def attachment_tools(toolset: ChatAttachmentToolset) -> list[tuple[ToolDefinition, ToolHandler]]:
    """添付のツールを Run の function tool の定義と handler にする。"""
    entries: list[tuple[ToolDefinition, ToolHandler]] = []
    for spec in CHAT_ATTACHMENT_TOOL_SPECS:

        def handler(
            arguments: dict[str, Any],
            _: ToolInvocationContext,
            *,
            _name: str = spec.name,
        ) -> dict[str, Any]:
            try:
                return toolset.call(_name, arguments)
            except ChatAttachmentToolError as exc:
                raise ValueError(str(exc)) from exc

        entries.append(
            (
                ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    input_schema=spec.input_schema,
                    output_schema={"type": "object"},
                    side_effects=False,
                    audit_tags=["chat_attachment"],
                ),
                handler,
            )
        )
    return entries


def attachment_instructions(toolset: ChatAttachmentToolset) -> str:
    """指示に足す添付の索引（本文は入れない）。"""
    return "\n".join(
        [
            "# 利用者が添付したファイル",
            "この会話で利用者が添付したファイルです。"
            "内容は chat_attachment_* のツールで必要な部分だけ読んでください。"
            "ファイルの内容について答えるときは、読んだ節・行に基づいて答えてください。",
            *toolset.index_lines(),
        ]
    )
