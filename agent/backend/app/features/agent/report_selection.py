"""保存済みの回答案だけを、モデルの最終選択として提示する。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

from agents import AgentOutputSchemaBase, Model, ModelSettings
from agents.exceptions import ModelBehaviorError
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
from agents.tool import Tool
from openai.types.responses.response_prompt_param import ResponsePromptParam

from .report_contract import FinalSelection
from .report_preview import ReportPreviewError


class _SelectionSchema(AgentOutputSchemaBase):
    """この要求を作った時点の候補を、native schemaへ固定する。"""

    def __init__(self, base: AgentOutputSchemaBase, identifiers: tuple[str, ...]) -> None:
        self._base = base
        self._identifiers = identifiers
        schema = deepcopy(base.json_schema())
        properties = schema.get("properties")
        selection = properties.get("preview_id") if isinstance(properties, dict) else None
        if not isinstance(selection, dict):
            raise ReportPreviewError("report.schema_invalid", "回答案の選択形式が不正です。")
        selection["enum"] = list(identifiers)
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self._base.name()

    def json_schema(self) -> dict[str, Any]:
        return deepcopy(self._schema)

    def is_strict_json_schema(self) -> bool:
        return self._base.is_strict_json_schema()

    def validate_json(self, json_str: str) -> Any:
        selected = self._base.validate_json(json_str)
        if FinalSelection.model_validate(selected).preview_id not in self._identifiers:
            raise ModelBehaviorError("この要求で選択できる回答案ではありません。")
        return selected


class ReportSelectionModel(Model):
    """モデル・調査順は変えず、最終選択が使える状態だけをwireへ反映する。"""

    def __init__(self, inner: Model, available: Callable[[], tuple[str, ...]]) -> None:
        self._inner = inner
        self._available = available

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_inner"), name)

    async def _selection(
        self,
        settings: ModelSettings,
        schema: AgentOutputSchemaBase | None,
        tools: list[Tool],
        handoffs: list[Handoff[Any, Any]],
    ) -> tuple[ModelSettings, AgentOutputSchemaBase | None]:
        identifiers = await asyncio.to_thread(self._available)
        if not identifiers:
            if not tools and not handoffs:
                raise ReportPreviewError(
                    "report.no_actions", "選択できる回答案と利用できるツールがありません。"
                )
            return replace(settings, tool_choice="required"), None
        if schema is None:
            raise ReportPreviewError("report.schema_missing", "回答案の選択形式がありません。")
        return settings, _SelectionSchema(schema, identifiers)

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        settings, schema = await self._selection(model_settings, output_schema, tools, handoffs)
        return await self._inner.get_response(
            system_instructions,
            input,
            settings,
            tools,
            schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff[Any, Any]],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        settings, schema = await self._selection(model_settings, output_schema, tools, handoffs)
        async for event in self._inner.stream_response(
            system_instructions,
            input,
            settings,
            tools,
            schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        ):
            yield event

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        return self._inner.get_retry_advice(request)

    async def _cleanup_on_run_end(self, owner: object) -> None:
        await self._inner._cleanup_on_run_end(owner)

    async def close(self) -> None:
        await self._inner.close()
