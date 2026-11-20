"""クォータナリ接続の失敗の案内を、一括の呼び出しの実行器が platform の文で出す（#5587）。

品質評価の判定・下書きの提案（1 回の生成）は上限つきの並行の実行器を通る。Messages API の失敗
（`AnthropicMessagesError`）は、種類の分類を変えずに platform のクォータナリ接続向けの案内を付け、
OCI の IAM・Project OCID・リージョンの文を出さない。実の API は呼ばない。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pr_anthropic_messages import AnthropicMessagesError, anthropic_failure_actions
from pr_backend_core.bounded_calls import BoundedCallAborted, ErrorKind, chain_classifiers

from app.features.agent.ai_calls import agent_error_kind, make_ai_caller

# OpenAI 互換の接続（OCI Enterprise AI）の案内にだけある語。
OCI_ONLY_WORDS = ("IAM", "Project OCID", "リージョン", "OCI", "generative-ai-family")

FAILURES = [
    ("authentication", 401),
    ("authorization", 403),
    ("model_not_found", 404),
    ("rate_limit", 429),
    ("unavailable", 500),
    ("timeout", None),
    ("connection", None),
]
FAILURE_IDS = ["401", "403", "404", "429", "5xx", "timeout", "connection"]


@pytest.mark.parametrize(("kind", "status_code"), FAILURES, ids=FAILURE_IDS)
def test_classifier_adds_the_platform_text_without_changing_the_kind(
    kind: Any, status_code: int | None
) -> None:
    error = AnthropicMessagesError("失敗", kind=kind, status_code=status_code)
    classified = make_ai_caller("evaluation_judge", max_concurrency=1)._classify(error)  # noqa: SLF001
    expected = anthropic_failure_actions(kind, status_code=status_code)[0]
    assert classified.hint == expected
    assert classified.kind is chain_classifiers(agent_error_kind)(error).kind
    for word in OCI_ONLY_WORDS:
        assert word not in classified.hint


def test_permission_failure_stops_with_the_platform_text() -> None:
    error = AnthropicMessagesError("失敗", kind="authorization", status_code=403)

    async def judge(_attempt: Any) -> str:
        raise error

    caller = make_ai_caller("evaluation_judge", max_concurrency=1)
    with pytest.raises(BoundedCallAborted) as caught:
        asyncio.run(caller.call(judge, label="case"))

    assert caught.value.kind is ErrorKind.PERMISSION
    message = str(caught.value)
    assert anthropic_failure_actions("authorization", status_code=403)[0] in message
    for word in OCI_ONLY_WORDS:
        assert word not in message
