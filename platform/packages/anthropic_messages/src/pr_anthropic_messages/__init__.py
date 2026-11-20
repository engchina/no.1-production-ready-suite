"""Anthropic Claude の Messages API を公式の Python SDK で呼ぶ共通の部品。

使い方・要求の組み方・失敗の写し方は :mod:`pr_anthropic_messages.messages`（1 回の生成）・
:mod:`pr_anthropic_messages.conversation`（会話の履歴とツールの往復）の docstring と README。
失敗の確かめる所の案内（接続テストと 3 製品の失敗の文）は
:mod:`pr_anthropic_messages.failure_actions`。
"""

from pr_anthropic_messages.conversation import (
    THINKING_BINDING_BETA,
    AnthropicConversationRequest,
    AnthropicTool,
    AnthropicToolChoice,
    AnthropicToolUse,
    AnthropicTurnResult,
    acreate_anthropic_turn,
    anthropic_messages,
    anthropic_text_block,
    anthropic_tool_result_block,
    anthropic_tool_use_block,
)
from pr_anthropic_messages.failure_actions import (
    anthropic_failure_actions,
    anthropic_failure_hint,
    find_anthropic_error,
)
from pr_anthropic_messages.messages import (
    ANTHROPIC_EFFORT_BY_REASONING_EFFORT,
    ANTHROPIC_IMAGE_MEDIA_TYPES,
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicEffort,
    AnthropicImage,
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicUsage,
    acreate_anthropic_message,
    anthropic_base_url,
    anthropic_effort,
    anthropic_response_schema,
    classify_anthropic_error,
    create_anthropic_message,
)

__all__ = [
    "ANTHROPIC_EFFORT_BY_REASONING_EFFORT",
    "ANTHROPIC_IMAGE_MEDIA_TYPES",
    "DEFAULT_TIMEOUT_SECONDS",
    "THINKING_BINDING_BETA",
    "AnthropicConversationRequest",
    "AnthropicEffort",
    "AnthropicImage",
    "AnthropicMessagesError",
    "AnthropicMessagesRequest",
    "AnthropicMessagesResult",
    "AnthropicTool",
    "AnthropicToolChoice",
    "AnthropicToolUse",
    "AnthropicTurnResult",
    "AnthropicUsage",
    "acreate_anthropic_message",
    "acreate_anthropic_turn",
    "anthropic_base_url",
    "anthropic_effort",
    "anthropic_failure_actions",
    "anthropic_failure_hint",
    "anthropic_messages",
    "anthropic_response_schema",
    "anthropic_text_block",
    "anthropic_tool_result_block",
    "anthropic_tool_use_block",
    "classify_anthropic_error",
    "create_anthropic_message",
    "find_anthropic_error",
]
