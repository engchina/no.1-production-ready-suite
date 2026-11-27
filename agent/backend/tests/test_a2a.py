"""外部の Agent を A2A で呼ぶ接続（#5932）の決定論テスト。

- A2A の server は `httpx.MockTransport` の合成のスタブ（Agent card の GET と `message/send` の
  POST）。モデルは SDK の `ScriptedModel`、保存先は memory。外部への通信はしない。
- OCI 署名の鍵はテストの中で生成する（OCI には接続しない）。
- 名前・内容は合成で、業務の内容・実環境の値は含まない。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from agents.testing import ScriptedModel, assistant_message, function_call
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest import MonkeyPatch
from security_support import client

from app.features.agent import builtin_runtime, call_abort, external_writes
from app.features.agent.a2a import (
    A2A_TOOL_NAME,
    OCI_SIGNATURE_NOT_FOUND_HINT,
    A2aConnectionClient,
    agent_card_url,
)
from app.features.agent.builtin_runtime import ModelTarget
from app.features.agent.config import (
    AIDP_CONNECTION_ID,
    AIDP_CONNECTION_LABEL,
    McpConnectionConfig,
    _declared_state,
    runtime_config_store,
)
from app.features.agent.runtime import (
    AgentProfile,
    RunCreateRequest,
    RunStatus,
    runtime_repository,
)
from app.features.agent.skills import (
    AIDP_AGENT_SKILL_ID,
    AgentSkillDefinition,
    SkillMcpRequirement,
    builtin_skills,
    skill_registry,
)
from app.features.agent.tools import (
    ExternalToolError,
    ToolInvocationContext,
    ToolPolicy,
    ToolPolicyDecision,
    list_mcp_connection_tools,
    mcp_tool_definition,
    mcp_tool_handler,
)
from app.settings import get_settings

USER_UUID = "11111111-2222-3333-4444-555555555932"
CONTEXT = ToolInvocationContext(user_uuid=USER_UUID)
ENDPOINT = "https://agent-a.example.test/a2a"
# 合成の AIDP の構成（実在しないリージョン・OCID。形だけ合わせる）。
AIDP_REGION = "xx-synthetic-1"
AIDP_OCID = "ocid1.aidataplatform.oc1.." + "a" * 24
AIDP_HOST = f"gateway.aidp.{AIDP_REGION}.oci.oraclecloud.com"
AIDP_ENDPOINT = f"https://{AIDP_HOST}/agentendpoint/agent-a/a2a"
# 合成の API キー（secret scan が鍵の定数と見なさないよう、組み立てて作る）。
SYNTHETIC_API_KEY = "-".join(["synthetic", "value", "5932"])
CARD: dict[str, Any] = {
    "name": "合成の Agent A",
    "description": "合成の表を調べて答える。",
    "protocolVersion": "0.3.0",
    "version": "1.0.0",
    "url": "https://other.example.test/ignored",
    "skills": [{"id": "s1", "name": "合成の調査", "description": "合成の表を集計する。"}],
}


def _message(text: str) -> dict[str, Any]:
    return {
        "result": {
            "kind": "message",
            "role": "agent",
            "messageId": "m-1",
            "parts": [{"kind": "text", "text": text}],
        }
    }


def _task(state: str, *, text: str = "", artifact: str = "") -> dict[str, Any]:
    status: dict[str, Any] = {"state": state}
    if text:
        status["message"] = {
            "kind": "message",
            "role": "agent",
            "messageId": "m-2",
            "parts": [{"kind": "text", "text": text}],
        }
    task: dict[str, Any] = {"kind": "task", "id": "t-1", "contextId": "c-1", "status": status}
    if artifact:
        task["artifacts"] = [
            {
                "artifactId": "a-1",
                "parts": [{"kind": "text", "text": artifact}, {"kind": "file", "file": {}}],
            }
        ]
    return {"result": task}


class _StubA2a:
    """合成の A2A の server。`message/send` に `outcomes` を順に返す。

    `outcomes` の値: dict は JSON-RPC の応答（`result` / `error`）、int は HTTP status、例外は
    そのまま送出（`ConnectError` は届く前の失敗なので、受け取った数に数えない）。
    """

    def __init__(self) -> None:
        self.card: Any = dict(CARD)
        self.card_status = 200
        self.send_status: int | None = None
        self.outcomes: list[dict[str, Any] | int | Exception] = []
        self.card_requests: list[httpx.Request] = []
        self.send_requests: list[httpx.Request] = []

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.send_requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.card_requests.append(request)
            if self.card_status != 200:
                return httpx.Response(self.card_status, text="合成の失敗")
            return httpx.Response(200, json=self.card)
        outcome = self.outcomes.pop(0) if self.outcomes else _message("合成の応答")
        if isinstance(outcome, httpx.ConnectError):
            raise outcome
        self.send_requests.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome, text="合成の失敗")
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], **outcome})


@pytest.fixture
def stub(monkeypatch: MonkeyPatch) -> _StubA2a:
    server = _StubA2a()
    real_client = httpx.Client

    def factory(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(server.handle), timeout=kwargs["timeout"])

    monkeypatch.setattr("app.features.agent.tools.httpx.Client", factory)
    return server


@contextlib.contextmanager
def _connection(server_id: str, **fields: Any) -> Iterator[McpConnectionConfig]:
    fields.setdefault("base_url", ENDPOINT)
    fields.setdefault("auth_mode", "none")
    config = runtime_config_store.upsert_mcp_server(server_id, protocol="a2a", **fields)
    try:
        yield config
    finally:
        with contextlib.suppress(KeyError, ValueError):
            runtime_config_store.remove_mcp_server(server_id)


def _a2a_config(**fields: Any) -> McpConnectionConfig:
    values: dict[str, Any] = {
        "server_id": "synthetic-a2a",
        "label": "合成の接続",
        "protocol": "a2a",
        "base_url": ENDPOINT,
        "auth_mode": "none",
    }
    return McpConnectionConfig(**{**values, **fields})


def _send(config: McpConnectionConfig, text: str = "合成の依頼", **kwargs: Any) -> dict[str, Any]:
    return A2aConnectionClient(config).send_message(text, **kwargs)


@pytest.fixture
def oci_api_key(tmp_path: Path, monkeypatch: MonkeyPatch) -> str:
    """テストの中で作った API キーの OCI config（「システム設定 › OCI 認証」の代わり）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    fingerprint = ":".join(digest[index : index + 2] for index in range(0, 32, 2))
    key_file = tmp_path / "api_key.pem"
    key_file.write_bytes(pem)
    user = "ocid1.user.oc1.." + "u" * 24
    tenancy = "ocid1.tenancy.oc1.." + "t" * 24
    config_file = tmp_path / "config"
    config_file.write_text(
        f"[DEFAULT]\nuser={user}\nfingerprint={fingerprint}\ntenancy={tenancy}\n"
        f"region={AIDP_REGION}\nkey_file={key_file}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "oci_config_file", str(config_file))
    monkeypatch.setattr(get_settings(), "oci_config_profile", "DEFAULT")
    return f"{tenancy}/{user}/{fingerprint}"


@pytest.fixture
def aidp(monkeypatch: MonkeyPatch) -> McpConnectionConfig:
    """AIDP を配備した構成（`PLATFORM_AIDP_OCID` がある）の接続の一覧にする。"""
    monkeypatch.setattr(get_settings(), "aidp_ocid", AIDP_OCID)
    monkeypatch.setattr(get_settings(), "aidp_region", AIDP_REGION)
    servers, _policy = _declared_state()
    monkeypatch.setattr(runtime_config_store, "_mcp_servers", servers)
    return runtime_config_store.get_mcp(AIDP_CONNECTION_ID)


@pytest.fixture
def skill_for() -> Iterator[Any]:
    created: list[str] = []

    def create(skill_id: str, server_id: str) -> str:
        skill_registry.upsert_custom(
            AgentSkillDefinition(
                id=skill_id,
                name="合成の Skill",
                instructions="send_message で相手の Agent に依頼する。",
                mcp_requirements=[
                    SkillMcpRequirement(server_id=server_id, tool_names=[A2A_TOOL_NAME])
                ],
            )
        )
        created.append(skill_id)
        return skill_id

    yield create
    for skill_id in created:
        with contextlib.suppress(KeyError, ValueError):
            skill_registry.remove(skill_id)


# ---- Agent card -------------------------------------------------------------------------------


def test_agent_card_becomes_one_tool(stub: _StubA2a) -> None:
    with _connection("test5932-card", label="合成の接続"):
        listed = list_mcp_connection_tools("test5932-card", context=CONTEXT)

    [tool] = listed.tools
    assert tool.name == A2A_TOOL_NAME
    assert tool.function_name == "test5932-card__send_message"
    assert tool.title == "合成の Agent A"
    assert tool.read_only is False
    # 何を任せるかは Agent card の名前・説明・できることから作る。
    assert "合成の Agent A" in tool.description
    assert "合成の表を調べて答える。" in tool.description
    assert "合成の調査：合成の表を集計する。" in tool.description
    assert tool.input_schema["required"] == ["message"]
    assert listed.metadata["protocol"] == "a2a"
    assert listed.metadata["agent_card"] == {
        "name": "合成の Agent A",
        "description": "合成の表を調べて答える。",
        "protocol_version": "0.3.0",
        "version": "1.0.0",
    }
    # 既定の場所（well-known）から取る。
    [request] = stub.card_requests
    assert str(request.url) == f"{ENDPOINT}/.well-known/agent-card.json"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, f"{ENDPOINT}/.well-known/agent-card.json"),
        ({"base_url": f"{ENDPOINT}/"}, f"{ENDPOINT}/.well-known/agent-card.json"),
        ({"base_url": AIDP_ENDPOINT}, f"{AIDP_ENDPOINT}/agent-card.json"),
        (
            {"agent_card_url": "https://agent-a.example.test/card.json"},
            "https://agent-a.example.test/card.json",
        ),
        ({"base_url": None}, None),
    ],
    ids=["well-known", "trailing-slash", "aidp-gateway", "explicit", "no-endpoint"],
)
def test_agent_card_url(fields: dict[str, Any], expected: str | None) -> None:
    assert agent_card_url(_a2a_config(**fields)) == expected


@pytest.mark.parametrize(
    ("card", "missing"),
    [
        ({"description": "説明だけ"}, ["name"]),
        ({"name": "  ", "description": "名前が空白"}, ["name"]),
        ({"name": "名前だけ"}, ["description"]),
        (["object", "ではない"], ["name"]),
    ],
    ids=["no-name", "blank-name", "no-description", "not-an-object"],
)
def test_agent_card_requires_name_and_description(
    stub: _StubA2a, card: Any, missing: list[str]
) -> None:
    stub.card = card

    with pytest.raises(ExternalToolError) as raised:
        A2aConnectionClient(_a2a_config()).list_tools()

    if isinstance(card, list):
        # JSON の object でない応答は、形式の失敗。
        assert raised.value.code == "a2a.invalid_json"
        return
    assert raised.value.code == "a2a.invalid_agent_card"
    assert raised.value.details["missing"] == missing


def test_agent_card_skills_with_other_shapes_are_dropped(stub: _StubA2a) -> None:
    stub.card = {**CARD, "skills": ["text", {"name": "合成の調査"}, 3], "protocolVersion": 1}

    listed = A2aConnectionClient(_a2a_config()).list_tools()

    assert "- 合成の調査" in listed.tools[0].description
    assert listed.metadata["agent_card"]["protocol_version"] is None


def test_unset_endpoint_is_not_configured(stub: _StubA2a) -> None:
    with pytest.raises(ExternalToolError) as raised:
        A2aConnectionClient(_a2a_config(base_url=None)).list_tools()

    assert raised.value.code == "a2a.not_configured"
    assert stub.card_requests == []


# ---- message/send -----------------------------------------------------------------------------


def test_message_response_is_the_answer_text(stub: _StubA2a) -> None:
    stub.outcomes = [_message("合成の応答の本文")]

    output = _send(_a2a_config(), "合成の依頼", message_id="op_synthetic", trace_id="call-1")

    assert output == {"status": "completed", "text": "合成の応答の本文"}
    [body] = stub.sent
    assert body["method"] == "message/send"
    assert body["id"] == "call-1"
    assert body["params"] == {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": "op_synthetic",
            "parts": [{"kind": "text", "text": "合成の依頼"}],
        }
    }
    # 送り先は接続の endpoint（Agent card の `url` へは送らない）。
    assert str(stub.send_requests[0].url) == ENDPOINT


def test_each_call_is_independent(stub: _StubA2a) -> None:
    """呼び出しごとに新しいメッセージで、前の task・文脈の ID を送らない。"""
    stub.outcomes = [_task("input-required", text="期間は？"), _message("二回目")]
    config = _a2a_config()

    _send(config, "一回目")
    _send(config, "二回目")

    first, second = (body["params"]["message"] for body in stub.sent)
    assert first["messageId"] != second["messageId"]
    for message in (first, second):
        assert "taskId" not in message and "contextId" not in message


def test_completed_task_returns_the_artifact_text(stub: _StubA2a) -> None:
    stub.outcomes = [_task("completed", text="状態の文", artifact="成果物の本文")]

    output = _send(_a2a_config())

    assert output["status"] == "completed"
    assert output["text"] == "成果物の本文"
    # text 以外の part は受け取らず、種類と数だけを残す。
    assert output["content_omitted"] == [{"type": "file", "count": 1}]


def test_completed_task_without_artifacts_uses_the_status_message(stub: _StubA2a) -> None:
    stub.outcomes = [_task("completed", text="状態の文")]

    assert _send(_a2a_config()) == {"status": "completed", "text": "状態の文"}


def test_input_required_returns_the_question(stub: _StubA2a) -> None:
    """相手の Agent が聞き返したら、その文をツールの結果として返す。"""
    stub.outcomes = [_task("input-required", text="どの期間を調べますか？")]

    output = _send(_a2a_config())

    assert output["status"] == "input_required"
    assert output["text"] == "どの期間を調べますか？"
    assert "引き継がれません" in output["next_step"]


@pytest.mark.parametrize("state", ["working", "submitted"])
def test_unfinished_task_is_reported_as_incomplete(stub: _StubA2a, state: str) -> None:
    """完了しない task は待たず、「完了しなかった」と分かる結果にする。"""
    stub.outcomes = [_task(state)]

    output = _send(_a2a_config())

    assert output["status"] == "incomplete"
    assert output["text"] == ""
    assert "完了しませんでした" in output["next_step"]
    assert len(stub.sent) == 1


@pytest.mark.parametrize("state", ["failed", "rejected", "canceled", "auth-required"])
def test_failed_task_is_a_tool_error(stub: _StubA2a, state: str) -> None:
    stub.outcomes = [_task(state, text="合成の理由")]

    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config())

    assert raised.value.code == "a2a.task_failed"
    assert raised.value.details["task_state"] == state
    assert "合成の理由" in raised.value.message


def test_rpc_error_and_unknown_shapes_are_tool_errors(stub: _StubA2a) -> None:
    stub.outcomes = [
        {"error": {"code": -32602, "message": "invalid params"}},
        {"result": {"kind": "other"}},
        {},
    ]
    config = _a2a_config()

    codes = []
    for _ in range(3):
        with pytest.raises(ExternalToolError) as raised:
            _send(config)
        codes.append(raised.value.code)

    assert codes == ["a2a.rpc_error", "a2a.invalid_response", "a2a.missing_result"]


def test_empty_message_is_not_sent(stub: _StubA2a) -> None:
    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config(), "   ")

    assert raised.value.code == "a2a.empty_message"
    assert stub.sent == []


# ---- 失敗・再送・取消 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [502, 504, 500, httpx.ReadError("合成の切断"), httpx.ReadTimeout("合成の時間切れ")],
    ids=["502", "504", "500", "disconnect", "read-timeout"],
)
def test_failure_after_delivery_is_not_resent(stub: _StubA2a, failure: int | Exception) -> None:
    """届いた後の失敗では再送しない（相手が書き込むことがある）。結果は不明として残す。"""
    stub.outcomes = [failure, _message("再送されたら届く応答")]

    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config())

    assert len(stub.sent) == 1
    assert raised.value.details["outcome"] == "unknown"
    assert raised.value.details["retryable"] is False


def test_timeout_names_the_connection_timeout(stub: _StubA2a) -> None:
    stub.outcomes = [httpx.ReadTimeout("合成の時間切れ")]

    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config(timeout_seconds=7))

    assert raised.value.code == "a2a.timeout"
    assert "7 秒以内" in raised.value.message
    assert "外部の Agent「合成の接続」" in raised.value.message


@pytest.mark.parametrize(
    "failure",
    [503, 429, httpx.ConnectError("合成の接続の失敗")],
    ids=["503", "429", "connect-error"],
)
def test_failure_before_acceptance_is_retried(stub: _StubA2a, failure: int | Exception) -> None:
    """届く前の失敗と、受け付ける前に断られた応答（429 / 503）は再試行する。"""
    stub.outcomes = [failure, _message("二回目の応答")]

    assert _send(_a2a_config())["text"] == "二回目の応答"


@pytest.mark.parametrize(
    ("status_code", "expected", "unexpected"),
    [
        # 401 は署名（OCI 認証の設定）。権限・URL の話にしない。
        (401, ["システム設定 › OCI 認証", "フィンガープリント"], ["use ai-data-platforms"]),
        (403, ["use ai-data-platforms", "USE 権限"], ["endpoint の URL"]),
        # 404 は「無い」と「権限が無い」を区別できないので、どちらも挙げる。
        (
            404,
            ["endpoint の URL", "配備されているか", "use ai-data-platforms", "USE 権限"],
            ["フィンガープリント"],
        ),
    ],
    ids=["401-signature", "403-permission", "404-not-found-or-permission"],
)
def test_oci_signature_failures_say_what_to_check(
    stub: _StubA2a, oci_api_key: str, status_code: int, expected: list[str], unexpected: list[str]
) -> None:
    """OCI 署名の接続の 401 / 403 / 404 は、確かめる所が分かる案内にする（ツールの失敗）。"""
    stub.outcomes = [status_code]
    config = _a2a_config(auth_mode="oci_signature")

    with pytest.raises(ExternalToolError) as raised:
        _send(config)

    error = raised.value
    assert error.code == "a2a.http_error"
    assert error.details["status_code"] == status_code
    assert f"HTTP {status_code}" in error.message
    for text in expected:
        assert text in error.message
    for text in unexpected:
        assert text not in error.message
    # 署名のヘッダー・鍵の ID を結果に出さない。
    dumped = error.message + json.dumps(error.details, ensure_ascii=False)
    assert "Signature" not in dumped and oci_api_key not in dumped


def test_oci_signature_hint_on_the_agent_card(stub: _StubA2a, oci_api_key: str) -> None:
    """接続のテスト（Agent card の取得）の 404 も同じ案内にする。"""
    stub.card_status = 404

    with pytest.raises(ExternalToolError) as raised:
        A2aConnectionClient(_a2a_config(auth_mode="oci_signature")).list_tools()

    assert raised.value.code == "a2a.http_error"
    assert OCI_SIGNATURE_NOT_FOUND_HINT in raised.value.message


def test_other_auth_modes_keep_the_generic_http_message(stub: _StubA2a) -> None:
    stub.outcomes = [404]

    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config(auth_mode="api_key", api_key=SYNTHETIC_API_KEY))

    assert "use ai-data-platforms" not in raised.value.message
    assert "接続の URL が正しいか" in raised.value.message


def test_cancelled_run_does_not_send(stub: _StubA2a) -> None:
    """Run の取消の後は送らない（`a2a.call_aborted`）。"""
    with call_abort.bind_call_abort("run-5932-cancel") as abort:
        abort.abort(call_abort.REASON_RUN_CANCELLED)
        with pytest.raises(ExternalToolError) as raised:
            _send(_a2a_config())

    assert raised.value.code == "a2a.call_aborted"
    assert stub.sent == []


# ---- 認証 -------------------------------------------------------------------------------------


def test_oci_signature_signs_the_card_and_the_message(stub: _StubA2a, oci_api_key: str) -> None:
    config = _a2a_config(auth_mode="oci_signature", base_url=AIDP_ENDPOINT)

    A2aConnectionClient(config).list_tools()
    _send(config)

    [card_request] = stub.card_requests
    [send_request] = stub.send_requests
    assert str(card_request.url) == f"{AIDP_ENDPOINT}/agent-card.json"
    for request in (card_request, send_request):
        authorization = request.headers["authorization"]
        assert authorization.startswith("Signature ")
        assert f'keyId="{oci_api_key}"' in authorization
        assert "date" in request.headers
    assert "(request-target) host" in card_request.headers["authorization"]
    # 本文のある `message/send` は、本文の hash も署名する。
    assert "x-content-sha256" in send_request.headers["authorization"]
    assert send_request.headers["x-content-sha256"]


def test_oci_signature_without_an_api_key_is_not_configured(
    stub: _StubA2a, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "oci_config_file", str(tmp_path / "missing"))

    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config(auth_mode="oci_signature"))

    assert raised.value.code == "a2a.oci_signature_not_configured"
    assert "システム設定 › OCI 認証" in raised.value.message
    assert str(tmp_path) not in raised.value.message
    assert stub.sent == []


def test_oci_signature_is_not_sent_over_plain_http(stub: _StubA2a, oci_api_key: str) -> None:
    config = _a2a_config(auth_mode="oci_signature", base_url="http://agent-a.example.test/a2a")

    with pytest.raises(ExternalToolError) as raised:
        _send(config)

    assert raised.value.code == "a2a.oci_signature_requires_https"
    assert stub.sent == []


def test_api_key_is_sent_as_a_bearer_token(stub: _StubA2a) -> None:
    _send(_a2a_config(auth_mode="api_key", api_key=SYNTHETIC_API_KEY))

    assert stub.send_requests[0].headers["authorization"] == f"Bearer {SYNTHETIC_API_KEY}"


def test_service_token_is_not_sent_to_an_external_agent(stub: _StubA2a) -> None:
    with pytest.raises(ExternalToolError) as raised:
        _send(_a2a_config(auth_mode="service_token"))

    assert raised.value.code == "a2a.auth_mode_unsupported"
    assert stub.sent == []


# ---- ツールの権限 -----------------------------------------------------------------------------


def _definition(config: McpConnectionConfig, stub: _StubA2a) -> Any:
    [tool] = A2aConnectionClient(config).list_tools().tools
    return mcp_tool_definition(config, tool)


def test_untrusted_a2a_connection_asks_for_approval(stub: _StubA2a) -> None:
    """AIDP 以外の A2A の接続は、外部の MCP の接続と同じ既定（信頼しない）。"""
    definition = _definition(_a2a_config(), stub)

    assert definition.name == "synthetic-a2a__send_message"
    assert definition.mcp_tool_name == A2A_TOOL_NAME
    assert definition.side_effects is True
    assert ToolPolicy().explain(definition) == (ToolPolicyDecision.ASK, "default_mode")


def test_trusted_a2a_connection_runs_without_approval_but_is_never_resent(
    stub: _StubA2a,
) -> None:
    """信頼した接続は承認なしに呼ぶが、読み取り専用にはしない（再送しない・結果不明を守る）。"""
    definition = _definition(_a2a_config(trust_tool_annotations=True), stub)

    assert ToolPolicy().explain(definition) == (ToolPolicyDecision.ALLOW, "trusted_connection")
    assert definition.trusted_read_only is False
    assert definition.resend_safe is False
    assert external_writes.guarded(definition) is True
    # 管理者は「ツール権限」で変えられる。
    assert ToolPolicy(ask={definition.name}).decide(definition) == ToolPolicyDecision.ASK
    assert ToolPolicy(deny={definition.name}).decide(definition) == ToolPolicyDecision.DENY


def test_tool_handler_sends_the_message_argument(stub: _StubA2a) -> None:
    config = _a2a_config()
    [tool] = A2aConnectionClient(config).list_tools().tools

    result = mcp_tool_handler(config, tool)(
        {"message": "合成の依頼"}, CONTEXT.model_copy(update={"operation_key": "op_handler"})
    )

    assert result == {"status": "completed", "text": "合成の応答"}
    message = stub.sent[0]["params"]["message"]
    assert message["parts"] == [{"kind": "text", "text": "合成の依頼"}]
    # 論理操作の ID をメッセージの ID にする（承認の後の送り直しでも同じ ID）。
    assert message["messageId"] == "op_handler"


# ---- AIDP の既定（PLATFORM_AIDP_OCID の有無）-------------------------------------------------


def test_without_aidp_nothing_aidp_specific_is_offered() -> None:
    servers, _policy = _declared_state()

    assert AIDP_CONNECTION_ID not in servers
    assert AIDP_AGENT_SKILL_ID not in {skill.id for skill in builtin_skills()}
    assert skill_registry.get(AIDP_AGENT_SKILL_ID) is None
    listed = client.get("/api/settings/mcp-connections")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["presets"] == []


def test_with_aidp_the_connection_preset_and_skill_are_offered(
    aidp: McpConnectionConfig,
) -> None:
    assert aidp.source == "builtin"
    assert aidp.protocol == "a2a"
    assert aidp.effective_auth_mode() == "oci_signature"
    assert aidp.base_url is None
    assert aidp.annotations_trusted() is True

    data = client.get("/api/settings/mcp-connections").json()["data"]
    by_id = {item["server_id"]: item for item in data["connections"]}
    assert by_id[AIDP_CONNECTION_ID]["label"] == AIDP_CONNECTION_LABEL
    assert by_id[AIDP_CONNECTION_ID]["protocol"] == "a2a"
    assert by_id[AIDP_CONNECTION_ID]["auth_mode"] == "oci_signature"
    assert by_id[AIDP_CONNECTION_ID]["removable"] is False
    # endpoint を入れるまでは未準備。
    assert by_id[AIDP_CONNECTION_ID]["configured"] is False
    assert data["presets"] == [
        {
            "id": "aidp_agent",
            "protocol": "a2a",
            "auth_mode": "oci_signature",
            "base_url_prefix": f"https://{AIDP_HOST}/agentendpoint/",
            "base_url_example": f"https://{AIDP_HOST}/agentendpoint/<agentId>/a2a",
            "timeout_seconds": 120.0,
        }
    ]

    skills = {skill.id: skill for skill in builtin_skills()}
    skill = skills[AIDP_AGENT_SKILL_ID]
    assert skill.source == "builtin"
    assert [(item.server_id, item.tool_names) for item in skill.mcp_requirements] == [
        (AIDP_CONNECTION_ID, [A2A_TOOL_NAME])
    ]


def test_aidp_skill_is_not_ready_until_the_endpoint_is_set(
    aidp: McpConnectionConfig, stub: _StubA2a, oci_api_key: str, skill_for: Any
) -> None:
    """endpoint が未設定の間は、スキルのツールを渡さない（理由を返し、Run は始めない）。"""
    skill_id = skill_for("test5932-aidp-skill", AIDP_CONNECTION_ID)

    tools, warnings = builtin_runtime.discover_mcp_tools([skill_id], context=CONTEXT)

    assert tools == []
    [warning] = warnings
    assert f"外部の Agent「{AIDP_CONNECTION_LABEL}」" in warning
    assert "endpoint の URL が設定されていません" in warning
    assert stub.card_requests == []

    saved = client.patch(
        f"/api/settings/mcp-connections/{AIDP_CONNECTION_ID}", json={"base_url": AIDP_ENDPOINT}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["configured"] is True

    tools, warnings = builtin_runtime.discover_mcp_tools([skill_id], context=CONTEXT)

    assert warnings == []
    [(definition, _handler)] = tools
    assert definition.name == "aidp__send_message"
    # 組み込みの接続は既定で承認なし。送信後の失敗で再送する対象にはしない。
    assert ToolPolicy().explain(definition) == (ToolPolicyDecision.ALLOW, "trusted_connection")
    assert definition.resend_safe is False


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"base_url": "https://agent-a.example.test/a2a"}, "AI Data Platform の Agent の endpoint"),
        ({"base_url": f"http://{AIDP_HOST}/agentendpoint/agent-a/a2a"}, "endpoint"),
        (
            {"base_url": "https://gateway.aidp.yy-other-1.oci.oraclecloud.com/agentendpoint/a/a2a"},
            "endpoint",
        ),
        ({"protocol": "mcp"}, "プロトコル（A2A）と認証（OCI 署名）"),
        ({"auth_mode": "none"}, "プロトコル（A2A）と認証（OCI 署名）"),
        ({"label": "別の名前"}, "名前は変えられません"),
        ({"trust_tool_annotations": False}, "ツール権限"),
        ({"agent_card_url": "https://agent-a.example.test/card.json"}, "Agent card"),
    ],
    ids=[
        "other-host",
        "http",
        "other-region",
        "protocol",
        "auth",
        "label",
        "trust",
        "agent-card-url",
    ],
)
def test_aidp_connection_accepts_only_the_gateway_endpoint(
    aidp: McpConnectionConfig, patch: dict[str, Any], message: str
) -> None:
    response = client.patch(f"/api/settings/mcp-connections/{AIDP_CONNECTION_ID}", json=patch)

    assert response.status_code == 400, response.text
    assert message in response.json()["error_messages"][0]
    assert runtime_config_store.get_mcp(AIDP_CONNECTION_ID).base_url is None


def test_saved_aidp_endpoint_is_restored_only_when_aidp_is_deployed(
    aidp: McpConnectionConfig, monkeypatch: MonkeyPatch
) -> None:
    stored = aidp.model_copy(update={"base_url": AIDP_ENDPOINT, "auth_mode": "none"})

    runtime_config_store.replace_restored([stored], None)
    restored = runtime_config_store.get_mcp(AIDP_CONNECTION_ID)
    # URL は保存した値、プロトコル・認証は固定のまま。
    assert restored.base_url == AIDP_ENDPOINT
    assert restored.effective_auth_mode() == "oci_signature"
    assert restored.source == "builtin"

    monkeypatch.setattr(get_settings(), "aidp_ocid", "")
    runtime_config_store.replace_restored([stored], None)
    with pytest.raises(KeyError):
        runtime_config_store.get_mcp(AIDP_CONNECTION_ID)


# ---- 接続の API -------------------------------------------------------------------------------


@pytest.fixture
def created_ids() -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    for server_id in ids:
        with contextlib.suppress(KeyError, ValueError):
            runtime_config_store.remove_mcp_server(server_id)


def test_create_a2a_connection_and_read_the_agent_card(
    stub: _StubA2a, created_ids: list[str]
) -> None:
    created_ids.append("api5932")
    created = client.post(
        "/api/settings/mcp-connections",
        json={
            "server_id": "api5932",
            "label": "合成の接続",
            "protocol": "a2a",
            "base_url": ENDPOINT,
            "agent_card_url": "https://agent-a.example.test/card.json",
            "auth_mode": "none",
        },
    )

    assert created.status_code == 200, created.text
    data = created.json()["data"]
    assert data["protocol"] == "a2a"
    assert data["agent_card_url"] == "https://agent-a.example.test/card.json"
    assert data["configured"] is True
    assert data["trust_tool_annotations"] is False

    tools = client.get("/api/settings/mcp-connections/api5932/tools")

    assert tools.status_code == 200, tools.text
    body = tools.json()["data"]
    assert [tool["name"] for tool in body["tools"]] == [A2A_TOOL_NAME]
    # 信頼していない接続の既定は承認。
    assert body["tools"][0]["default_decision"] == "ask"
    assert body["metadata"]["agent_card"]["protocol_version"] == "0.3.0"
    assert str(stub.card_requests[0].url) == "https://agent-a.example.test/card.json"


def test_connection_test_reports_the_iam_hint(
    stub: _StubA2a, oci_api_key: str, created_ids: list[str]
) -> None:
    """接続のテスト（ツールの取得の API）でも、OCI 署名の 404 は要る権限の案内になる。"""
    created_ids.append("hint5932")
    runtime_config_store.upsert_mcp_server(
        "hint5932", protocol="a2a", base_url=AIDP_ENDPOINT, auth_mode="oci_signature"
    )
    stub.card_status = 404

    response = client.get("/api/settings/mcp-connections/hint5932/tools")

    assert response.status_code == 502, response.text
    assert "use ai-data-platforms" in response.text
    assert "Signature" not in response.text


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"protocol": "mcp", "auth_mode": "oci_signature"}, "OCI 署名の認証は"),
        ({"protocol": "a2a", "auth_mode": "service_token"}, "サービストークンの認証を使えません"),
    ],
    ids=["mcp-oci-signature", "a2a-service-token"],
)
def test_unsupported_protocol_and_auth_combinations_are_rejected(
    payload: dict[str, str], message: str, created_ids: list[str]
) -> None:
    created_ids.append("combo5932")

    response = client.post(
        "/api/settings/mcp-connections",
        json={"server_id": "combo5932", "base_url": ENDPOINT, **payload},
    )

    assert response.status_code == 400, response.text
    assert message in response.json()["error_messages"][0]
    with pytest.raises(KeyError):
        runtime_config_store.get_mcp("combo5932")


def test_patch_rejects_a_combination_that_the_saved_connection_cannot_use(
    created_ids: list[str],
) -> None:
    created_ids.append("combo5932-patch")
    runtime_config_store.upsert_mcp_server(
        "combo5932-patch", protocol="a2a", base_url=ENDPOINT, auth_mode="oci_signature"
    )

    response = client.patch(
        "/api/settings/mcp-connections/combo5932-patch", json={"protocol": "mcp"}
    )

    assert response.status_code == 400, response.text
    assert runtime_config_store.get_mcp("combo5932-patch").protocol == "a2a"


def test_aidp_connection_id_is_reserved() -> None:
    response = client.post(
        "/api/settings/mcp-connections", json={"server_id": AIDP_CONNECTION_ID, "protocol": "a2a"}
    )

    assert response.status_code == 422, response.text
    assert "組み込みの接続" in response.json()["error_messages"][0]


# ---- Skill から 1 つのツールとして呼ぶ（Run）--------------------------------------------------


def test_skill_calls_the_external_agent_in_a_run(
    monkeypatch: MonkeyPatch, stub: _StubA2a, skill_for: Any
) -> None:
    server_id = "test5932-run"
    agent_id = "test5932-run-agent"
    function_name = f"{server_id}__send_message"
    stub.outcomes = [_task("input-required", text="どの期間ですか？"), _message("合成の結果")]
    model = ScriptedModel(
        [
            [function_call(function_name, json.dumps({"message": "合成の依頼"}), call_id="c-1")],
            [
                function_call(
                    function_name, json.dumps({"message": "合成の依頼（期間 A）"}), call_id="c-2"
                )
            ],
            [assistant_message("合成の結果でした。")],
        ]
    )
    monkeypatch.setattr(
        builtin_runtime,
        "resolve_model_target",
        lambda model_id="": ModelTarget(
            model_id=model_id or "test-model",
            endpoint="https://oci.example",
            project_ocid="",
            api_key="k",
        ),
    )
    monkeypatch.setattr(builtin_runtime, "model_factory", lambda _target: model)
    with _connection(server_id, label="合成の接続", trust_tool_annotations=True):
        skill_id = skill_for("test5932-run-skill", server_id)
        runtime_repository.create_agent(
            AgentProfile(id=agent_id, name="合成の業務 Agent", skill_ids=[skill_id])
        )
        try:
            run = runtime_repository.create_builtin_run(
                RunCreateRequest(goal="合成の質問", agent_id=agent_id),
                created_by_user_uuid=USER_UUID,
            )
            anyio.run(builtin_runtime.execute_run, run.id)
            finished = runtime_repository.get_run(run.id)
        finally:
            with contextlib.suppress(KeyError, ValueError):
                runtime_repository.delete_agent(agent_id)

    assert finished.status == RunStatus.COMPLETED, finished.events[-1].message
    exposed = {tool.name: tool for tool in model.calls[0].tools}
    assert function_name in exposed
    # モデルに渡すツールの説明は Agent card から作る。
    assert "合成の Agent A" in str(getattr(exposed[function_name], "description", ""))
    # 指示の素の名前は、モデルに渡す名前に書き直す。
    assert f"{function_name} で相手の Agent に依頼する。" in str(model.calls[0].system_instructions)
    texts = [body["params"]["message"]["parts"][0]["text"] for body in stub.sent]
    assert texts == ["合成の依頼", "合成の依頼（期間 A）"]
    # 聞き返しはツールの結果としてモデルに返り、次の呼び出しは独立のメッセージ。
    steps = [step for step in finished.steps if step.tool_call is not None]
    first = steps[0].tool_result
    assert first is not None and first.success is True
    assert first.output is not None and first.output["status"] == "input_required"
    assert first.output["text"] == "どの期間ですか？"
    # 副作用のあり得るツールとして、論理操作の ID を付けて送る。
    assert stub.sent[0]["params"]["message"]["messageId"].startswith("op_")
    assert steps[0].tool_call is not None and steps[0].tool_call.operation_key
