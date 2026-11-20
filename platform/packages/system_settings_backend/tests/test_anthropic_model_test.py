"""クォータナリ接続（Anthropic Claude の Messages API）のモデルの接続テスト（#5546）。

Messages API の呼び出し（`pr_anthropic_messages.acreate_anthropic_message`）を決定論のスタブに
置き換え、実の API は呼ばない（SDK の応答・例外の分類は pr_anthropic_messages のテストが確かめる）。
"""

from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import Any

import pytest
from pr_anthropic_messages import (
    AnthropicMessagesError,
    AnthropicMessagesRequest,
    AnthropicMessagesResult,
    AnthropicUsage,
)
from test_model import FakeSettings, make_client, make_store
from test_model_connections import QUATERNARY_ENDPOINT, four_connection_payload

from pr_system_settings import anthropic_model_test
from pr_system_settings.model import (
    ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT,
    ENTERPRISE_AI_API_KEY_ENV,
    ENTERPRISE_AI_QUATERNARY_API_KEY_ENV,
    ENTERPRISE_AI_SECONDARY_API_KEY_ENV,
    ENTERPRISE_AI_TERTIARY_API_KEY_ENV,
    ModelSettingsTestRequest,
)
from pr_system_settings.model_test_input import (
    MODEL_TEST_IMAGE_MIME_TYPE,
    MODEL_TEST_TEXT_PROMPT,
    MODEL_TEST_VISION_PROMPT,
)


@pytest.fixture(autouse=True)
def _no_process_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        ENTERPRISE_AI_API_KEY_ENV,
        ENTERPRISE_AI_SECONDARY_API_KEY_ENV,
        ENTERPRISE_AI_TERTIARY_API_KEY_ENV,
        ENTERPRISE_AI_QUATERNARY_API_KEY_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


class _FakeMessages:
    """`acreate_anthropic_message` の代わり。受け取った引数を記録し、決めた結果か例外を返す。"""

    def __init__(self, outcome: AnthropicMessagesResult | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, request: AnthropicMessagesRequest, **kwargs: Any
    ) -> AnthropicMessagesResult:
        self.calls.append({"request": request, **kwargs})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _result(text: str = "OK") -> AnthropicMessagesResult:
    return AnthropicMessagesResult(
        text=text,
        model="claude-d",
        stop_reason="end_turn",
        usage=AnthropicUsage(input_tokens=12, output_tokens=3, cache_read_input_tokens=4),
        request_id="req_test",
    )


def _install(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> _FakeMessages:
    fake = _FakeMessages(outcome)
    monkeypatch.setattr(anthropic_model_test, "acreate_anthropic_message", fake)
    return fake


def _not_called_run_test() -> Any:
    async def run_test(_candidate: Any, _request: ModelSettingsTestRequest) -> dict[str, Any]:
        raise AssertionError("クォータナリ接続のモデルで製品の run_model_test を呼んだ")

    return run_test


def _post_test(
    tmp_path: Path,
    payload: dict[str, Any],
    *,
    target_type: str = "enterprise_text",
    model_id: str = "claude-d",
    run_model_test: Any = None,
) -> dict[str, Any]:
    settings = FakeSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store, run_model_test=run_model_test or _not_called_run_test())
    response = client.post(
        "/api/settings/model/test",
        json={"settings": payload, "target_type": target_type, "model_id": model_id},
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def _payload_with_model(**model: Any) -> dict[str, Any]:
    payload = four_connection_payload()
    payload["enterprise_ai"]["models"][-1].update(model)
    return payload


def test_text_test_calls_messages_api_with_unsaved_model_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """テキストのテストは Messages API へ短い要求を送り、製品の run_model_test を呼ばない。"""
    fake = _install(monkeypatch, _result("応答です"))

    data = _post_test(
        tmp_path,
        _payload_with_model(reasoning_effort="high", max_output_tokens=4096),
    )

    assert data["status"] == "success"
    assert (
        data["message"] == "Anthropic Claude の回答生成モデル「claude-d」から応答を取得しました。"
    )
    assert data["details"] == {
        "surface": "llm",
        "api": "anthropic_messages",
        "response_chars": 4,
        "stop_reason": "end_turn",
        "input_tokens": 16,
        "output_tokens": 3,
    }
    [call] = fake.calls
    request: AnthropicMessagesRequest = call["request"]
    assert request.model_id == "claude-d"
    assert request.user_text == MODEL_TEST_TEXT_PROMPT
    assert request.images == ()
    # 推論の量・出力の上限は、保存前に入力したモデルの値（受け付けない値を保存の前に確かめる）。
    assert request.reasoning_effort == "high"
    assert request.max_output_tokens == 4096
    assert call["api_key"] == "sk-quaternary"
    # Endpoint URL が空の接続は、既定の URL（`effective_endpoint`）で呼ぶ。
    assert call["endpoint_url"] == ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT
    assert call["timeout_seconds"] == 600.0


def test_vision_test_sends_the_shared_test_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画像対応モデルのテストは、共通のテスト画像を添えて送る。Endpoint URL は入力の値で呼ぶ。"""
    fake = _install(monkeypatch, _result("赤"))
    payload = _payload_with_model(vision_enabled=True)
    payload["enterprise_ai"]["connections"][3]["endpoint"] = QUATERNARY_ENDPOINT

    data = _post_test(tmp_path, payload, target_type="enterprise_vision")

    assert data["status"] == "success"
    assert (
        data["message"] == "Anthropic Claude の画像対応モデル「claude-d」から応答を取得しました。"
    )
    assert data["details"]["surface"] == "vision"
    [call] = fake.calls
    request: AnthropicMessagesRequest = call["request"]
    assert request.user_text == MODEL_TEST_VISION_PROMPT
    assert [image.media_type for image in request.images] == [MODEL_TEST_IMAGE_MIME_TYPE]
    assert call["endpoint_url"] == QUATERNARY_ENDPOINT


def test_other_connections_still_use_product_run_model_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI 互換の接続のモデルは、従来どおり製品の run_model_test で確かめる。"""
    fake = _install(monkeypatch, _result())
    seen: list[str] = []

    async def run_test(_candidate: Any, request: ModelSettingsTestRequest) -> dict[str, Any]:
        seen.append(request.model_id)
        return {"surface": "llm"}

    data = _post_test(
        tmp_path, four_connection_payload(), model_id="llm-a", run_model_test=run_test
    )

    assert data["status"] == "success"
    assert data["message"].startswith("Enterprise AI の回答生成モデル")
    assert seen == ["llm-a"]
    assert fake.calls == []


@pytest.mark.parametrize(
    ("kind", "status_code", "expected_tip"),
    [
        ("authentication", 401, "API key が接続先に受け付けられませんでした"),
        ("authorization", 403, "呼び出す権限がありません"),
        ("model_not_found", 404, "モデル ID が見つからないか"),
        ("request", 400, "推論の量"),
        ("request", 404, "Messages API の path が見つかりません"),
        ("rate_limit", 429, "rate limit"),
        ("unavailable", 529, "一時的に応答できません"),
        ("timeout", None, "タイムアウト秒数"),
        ("response_format", None, "Messages API を指しているか"),
        ("refusal", None, "応答を拒否しました"),
        ("incomplete", None, "出力の上限を広げる"),
    ],
    ids=[
        "authentication",
        "authorization",
        "model_not_found",
        "request_400",
        "request_404_path",
        "rate_limit",
        "unavailable",
        "timeout",
        "response_format",
        "refusal",
        "incomplete",
    ],
)
def test_failures_map_to_quaternary_troubleshooting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: Any,
    status_code: int | None,
    expected_tip: str,
) -> None:
    """失敗の種類ごとに、クォータナリ接続向けの案内を出す（OCI の IAM 前提の文を出さない）。"""
    error = AnthropicMessagesError(
        "モデル「claude-d」の呼び出しで、Anthropic Messages API: 失敗（sk-quaternary）。",
        kind=kind,
        model_id="claude-d",
        status_code=status_code,
    )
    _install(monkeypatch, error)

    data = _post_test(tmp_path, four_connection_payload())

    assert data["status"] == "failed"
    assert data["message"] == "Anthropic Claude のモデル「claude-d」のテストに失敗しました。"
    assert data["error_type"] == "AnthropicMessagesError"
    assert data["details"] == {"failure_kind": kind, "http_status": status_code}
    assert len(data["troubleshooting"]) == 1
    assert expected_tip in data["troubleshooting"][0]
    serialized = json.dumps(data, ensure_ascii=False)
    for oci_only in ("IAM", "Project OCID", "OCI", "compartment"):
        assert oci_only not in serialized
    # 例外の文に紛れた API key は伏せる。
    assert "sk-quaternary" not in serialized
    assert "<secret>" in data["raw_error"]


def test_unreached_endpoint_shows_only_the_network_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接続先へ届かない失敗は、API key・モデル ID を疑わせない接続の案内だけにする（#4084）。"""
    try:
        try:
            raise OSError(errno.ECONNREFUSED, "Connect call failed ('192.0.2.1', 443)")
        except OSError as os_error:
            raise ConnectionError("Connection error.") from os_error
    except ConnectionError as cause:
        error = AnthropicMessagesError(
            "モデル「claude-d」の呼び出しで、Anthropic Messages API: 接続できませんでした。",
            kind="connection",
        )
        error.__cause__ = cause
    _install(monkeypatch, error)

    data = _post_test(tmp_path, four_connection_payload())

    assert data["status"] == "failed"
    assert data["details"] == {
        "failure_kind": "connection",
        "http_status": None,
        "network_failure_cause": "refused",
    }
    [tip] = data["troubleshooting"]
    assert "API key やモデル ID の誤りではありません" in tip
    assert "192.0.2.1" not in json.dumps(data, ensure_ascii=False)


def test_connection_failure_without_known_cause_names_the_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原因の分からない接続の失敗は、呼んだ Endpoint URL（既定の URL）を案内に出す。"""
    _install(
        monkeypatch,
        AnthropicMessagesError("接続できませんでした。", kind="connection"),
    )

    data = _post_test(tmp_path, four_connection_payload())

    [tip] = data["troubleshooting"]
    assert ANTHROPIC_MESSAGES_DEFAULT_ENDPOINT in tip
    assert data["details"] == {"failure_kind": "connection", "http_status": None}


def test_unexpected_exception_is_reported_with_generic_quaternary_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分類できない例外も、クォータナリ接続の文で失敗にする（例外を外へ出さない）。"""
    _install(monkeypatch, RuntimeError("unexpected"))

    data = _post_test(tmp_path, four_connection_payload())

    assert data["status"] == "failed"
    assert data["error_type"] == "RuntimeError"
    assert data["details"] == {}
    assert data["troubleshooting"] == [
        "接続の応答（raw error）を確かめ、クォータナリ接続の Endpoint URL と API key を"
        "確かめてください。"
    ]
