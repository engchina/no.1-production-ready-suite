"""登録モデルごとの出力の上限（#1719）が、組み込み Runtime に渡ること。

未完了の応答（`status=incomplete`）は、理由・上限・使った token の分かる失敗にする。
OCI へは接続しない（Responses API の client は差し替える）。
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from pr_system_settings.model import EnterpriseAiConfiguredModel

from app.features.agent import builtin_runtime
from app.features.agent.builtin_runtime import BuiltinRuntimeError, ModelTarget
from app.settings import Settings

ENDPOINT = "https://primary.example/openai/v1"


def test_model_settings_send_model_output_limit() -> None:
    target = ModelTarget(
        model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", max_output_tokens=16000
    )

    assert builtin_runtime.model_settings_for(target).max_tokens == 16000
    # 推論の量を送るときも同じ。
    with_effort = ModelTarget(
        model_id="m", endpoint=ENDPOINT, project_ocid="", api_key="k", reasoning_effort="high"
    )
    assert builtin_runtime.model_settings_for(with_effort).max_tokens == 32768


def test_resolve_model_target_reads_output_limit_of_agent_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        oci_enterprise_ai_endpoint=ENDPOINT,
        oci_enterprise_ai_api_key="sk-test",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="text-a", max_output_tokens=16000),
            EnterpriseAiConfiguredModel(model_id="text-small", max_input_tokens=8192),
            EnterpriseAiConfiguredModel(model_id="text-b"),
        ],
        oci_enterprise_ai_default_text_model="text-a",
    )
    monkeypatch.setattr(builtin_runtime, "get_settings", lambda: settings)

    assert builtin_runtime.resolve_model_target("").max_output_tokens == 16000
    # 入力の上限の小さいモデルは入力の上限まで。値を書かないモデルは既定の 32768。
    assert builtin_runtime.resolve_model_target("text-small").max_output_tokens == 8192
    assert builtin_runtime.resolve_model_target("text-b").max_output_tokens == 32768


class _Response:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.status = data["status"]

    def model_dump(self) -> dict[str, Any]:
        return dict(self._data)


class _Inner:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.requests.append(kwargs)
        return self.response


def _incomplete() -> _Response:
    return _Response(
        {
            "status": "incomplete",
            "model": "openai.gpt-oss-120b",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 1200, "output_tokens_details": {"reasoning_tokens": 1198}},
        }
    )


def test_incomplete_response_fails_with_reason_limit_and_used_tokens() -> None:
    inner = _Inner(_incomplete())
    responses = builtin_runtime._OciResponses(inner)

    async def call() -> None:
        await responses.create(model="openai.gpt-oss-120b", input=[], max_output_tokens=1200)

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)

    assert raised.value.code == "runtime.model_output_incomplete"
    message = raised.value.message
    assert "openai.gpt-oss-120b" in message
    assert "理由=max_output_tokens" in message
    assert "出力の上限=1200 tokens" in message
    assert "使った出力=1200 tokens（うち推論 1198）" in message
    # 送り直さない（送った上限がモデルの出力の上限）。
    assert len(inner.requests) == 1


def test_completed_response_is_returned() -> None:
    completed = _Response({"status": "completed"})
    responses = builtin_runtime._OciResponses(_Inner(completed))

    async def call() -> Any:
        return await responses.create(model="m", input=[], max_output_tokens=32768)

    assert anyio.run(call) is completed


class _Sequence(_Inner):
    """順に応答を返す（推論の量を下げた送り直しの確認。#2023）。"""

    def __init__(self, *responses: _Response) -> None:
        super().__init__(responses[0])
        self.responses = list(responses)

    async def create(self, **kwargs: Any) -> _Response:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def test_incomplete_is_resent_once_with_lower_reasoning_effort() -> None:
    """出力の上限で未完了なら、推論の量を 1 段下げて（medium → low）1 回だけ送り直す。"""
    completed = _Response({"status": "completed"})
    inner = _Sequence(_incomplete(), completed)
    responses = builtin_runtime._OciResponses(inner)

    async def call() -> Any:
        return await responses.create(
            model="openai.gpt-oss-120b",
            input=[],
            max_output_tokens=1200,
            reasoning=builtin_runtime.Reasoning(effort="medium"),
        )

    assert anyio.run(call) is completed
    assert [request["reasoning"] for request in inner.requests[1:]] == [{"effort": "low"}]
    assert inner.requests[1]["max_output_tokens"] == 1200


def test_incomplete_after_lowering_fails_with_the_retry_and_advice() -> None:
    inner = _Sequence(_incomplete(), _incomplete())
    responses = builtin_runtime._OciResponses(inner)

    async def call() -> None:
        await responses.create(
            model="openai.gpt-oss-120b",
            input=[],
            max_output_tokens=1200,
            reasoning={"effort": "high"},
        )

    with pytest.raises(BuiltinRuntimeError) as raised:
        anyio.run(call)

    assert len(inner.requests) == 2
    assert inner.requests[1]["reasoning"] == {"effort": "medium"}
    message = raised.value.message
    assert "推論の量を下げて送り直しても" in message
    # 最後に送った medium はまだ下げられるので、直し方に入れる。
    assert "「推論の量」を下げる" in message


@pytest.mark.parametrize(
    ("reasoning", "reason"),
    [
        ({"effort": "low"}, "max_output_tokens"),
        (None, "max_output_tokens"),
        ({"effort": "high"}, "content_filter"),
    ],
    ids=["low-is-the-floor", "not-sent", "content-filter"],
)
def test_incomplete_is_not_resent_when_effort_cannot_drop(
    reasoning: dict[str, str] | None, reason: str
) -> None:
    response = _incomplete()
    response._data["incomplete_details"] = {"reason": reason}
    inner = _Inner(response)
    responses = builtin_runtime._OciResponses(inner)
    kwargs: dict[str, Any] = {"model": "m", "input": [], "max_output_tokens": 1200}
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    async def call() -> None:
        await responses.create(**kwargs)

    with pytest.raises(BuiltinRuntimeError):
        anyio.run(call)
    assert len(inner.requests) == 1
