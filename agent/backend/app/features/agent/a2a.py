"""外部の Agent を A2A（Agent2Agent）で呼ぶ client（#5932）。

持つのは client だけ（自分を A2A の server として公開しない。外向けの公開は MCP）。プロトコルの
実装は汎用で、Oracle AI Data Platform（AIDP）に固有なのは認証方式（OCI 署名）と既定値（組み込みの
接続 `aidp` は `config.py`、Agent card の場所と権限の案内はこの module）だけ。

- 接続（`McpConnectionConfig.protocol == "a2a"`）は 1 つの外部の Agent。Agent card を読み、
  その Agent にテキストのメッセージを送る **1 つのツール**（`send_message`）にする。Skill は MCP の
  ツールと同じ形（`mcp_requirements`）で宣言し、呼び出しは `tool_registry.invoke`（ポリシー・
  監査）を通る。sub-agent / handoff の概念は持たない。
- 呼び出しは JSON-RPC 2.0 の `message/send`（非ストリーミング）だけで、呼び出しごとに独立（会話の
  文脈・task を引き継がない）。相手の Agent が聞き返した（`input-required`）ときは、その文を結果
  として返す。
- 送り先は接続の endpoint の URL だけ（Agent card の `url` へは送らない。相手が示す URL に従って
  送り先を変えない）。
- 相手が書き込むことがあるので、`message/send` は送信後の失敗（502 / 504・切断・読み取りの
  timeout）では再送しない（`McpRetryKind.UNSAFE`。結果不明の扱いは `external_writes`）。Run の
  取消の後は送らず、送った後は結果を待つ。
- 認証は接続の方式（なし / API キー / OAuth client credentials / OCI 署名）。OCI 署名は
  「システム設定 › OCI 認証」の API キーで署名し、接続ごとの secret を持たない。3 製品の間の
  サービストークンは使えない。secret・署名のヘッダーは log・結果に出さない。

ponytail: 完了しない task（`working` / `submitted`）は `tasks/get` で待たない。結果に
「完了しなかった」（`status: "incomplete"`）を返す。長い処理の Agent を使うようになったら、接続の
timeout の中で `tasks/get` を繰り返す。
ponytail: ストリーミング（`message/stream`）・多ターン（`contextId` / `taskId` の引き継ぎ）・
text 以外の part（file / data。種類と数だけを `content_omitted` に残す）・push 通知は扱わない。
ponytail: Agent card は呼ぶたびに取る（cache しない。MCP の `tools/list` と同じ）。
ponytail: 送る形は A2A v0.2 / v0.3 の JSON-RPC（`message/send`・`parts[].kind`）。v1.0 の形
（method の名前・part の形が違う）は未対応で、相手の版は Agent card の `protocolVersion`
（接続のテストに出す）で分かる。
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pr_system_settings.oci_signing import (
    OciRequestSigningNotConfiguredError,
    oci_request_signing_auth,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.features.agent.config import McpConnectionConfig
from app.features.agent.mcp_url import mask_url_credentials
from app.features.agent.tools import (
    ExternalMcpToolInfo,
    ExternalMcpToolsData,
    ExternalToolError,
    JsonObject,
    JsonRpcResponse,
    McpRetryKind,
    ToolHandler,
    ToolInvocationContext,
    _mcp_oauth_bearer_token,
    _post_mcp_message,
    mcp_function_name,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

# 1 つの外部の Agent を表すツールの名前（Skill の宣言は `<接続>:send_message`）。
A2A_TOOL_NAME = "send_message"
# 結果の `status`（`A2A_TOOL_OUTPUT_SCHEMA` の enum と同じ）。
STATUS_COMPLETED = "completed"
STATUS_INPUT_REQUIRED = "input_required"
STATUS_INCOMPLETE = "incomplete"

A2A_TOOL_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "minLength": 1,
            "description": (
                "相手の Agent に送る依頼の文。呼び出しは 1 回ごとに独立で、前の呼び出しの内容は"
                "相手に引き継がれないので、この 1 通だけで依頼が分かるように、対象・条件・前提を"
                "すべて書く。"
            ),
        }
    },
    "required": ["message"],
    "additionalProperties": False,
}
A2A_TOOL_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [STATUS_COMPLETED, STATUS_INPUT_REQUIRED, STATUS_INCOMPLETE],
            "description": (
                "completed は応答の本文が text にある。input_required は相手の Agent が"
                "続けるための情報を求めていて、その問いが text にある。incomplete は待てる時間の"
                "中で相手の処理が完了せず、結果を受け取れていない。"
            ),
        },
        "text": {"type": "string", "description": "相手の Agent の応答の本文（無ければ空）。"},
        "content_omitted": {
            "type": "array",
            "description": "応答にあった text 以外の内容（種類と数。中身は受け取らない）。",
            "items": {
                "type": "object",
                "properties": {"type": {"type": "string"}, "count": {"type": "integer"}},
            },
        },
        "next_step": {"type": "string", "description": "次にすること（あるときだけ）。"},
    },
    "required": ["status", "text"],
}

# 状態で変わる案内はツールの結果に置く（docs/agents/prompts.md）。
_INPUT_REQUIRED_NEXT_STEP = (
    "## Step 1: 相手の問いに答える\n"
    "相手の Agent が、続けるための情報を求めています（問いは text）。この実行で分かっている内容で"
    "答えられるなら、元の依頼とその答えを 1 通にまとめて送り直してください（前の呼び出しの内容は"
    "相手に引き継がれません）。答えられなければ、問いを利用者にそのまま伝えて聞いてください。"
)
_INCOMPLETE_NEXT_STEP = (
    "## Step 1: 完了しなかったことを伝える\n"
    "相手の Agent の処理は、待てる時間の中で完了しませんでした。結果は受け取れていません。"
    "同じ依頼を繰り返さず、完了しなかったことを利用者に伝えてください。"
)

# Agent card から取る文の上限（モデルに渡すツールの説明に入れるため）。
_CARD_NAME_MAX_CHARS = 120
_CARD_DESCRIPTION_MAX_CHARS = 600
_CARD_SKILL_MAX = 8
_CARD_SKILL_MAX_CHARS = 160
_WHITESPACE = re.compile(r"\s+")
# A2A の task の状態（v0.2 / v0.3）。
_TASK_STATE_COMPLETED = "completed"
_TASK_STATE_INPUT_REQUIRED = "input-required"
_TASK_STATES_IN_FLIGHT = frozenset({"submitted", "working"})
_AIDP_GATEWAY_HOST = re.compile(r"^gateway\.aidp\.[a-z0-9-]+\.oci\.oraclecloud\.com$")
# OCI 署名の接続の 401 / 403 / 404 の案内（実環境の AIDP の gateway で確かめた応答に合わせる）。
# - 401: 署名が無い・受け付けられない（「システム設定 › OCI 認証」の API キーの設定）。
# - 404: 署名が通っても、Agent が無いときと権限が無いときに同じ 404（`NotAuthorizedOrNotFound`）を
#   返すので、区別できない。URL の誤り・Agent が未配備・権限の不足をすべて挙げる。
_OCI_PERMISSIONS = (
    "署名に使う OCI ユーザーの、IAM の use ai-data-platforms の権限と、"
    "AI Data Platform の中のその Agent の USE 権限"
)
OCI_SIGNATURE_UNAUTHORIZED_HINT = (
    "OCI 署名が受け付けられませんでした。「システム設定 › OCI 認証」の API キー"
    "（ユーザー OCID・テナンシ OCID・フィンガープリント・秘密鍵）が、OCI に登録した API キーと"
    "合っているかを確認してください。"
)
OCI_SIGNATURE_FORBIDDEN_HINT = f"権限がありません。{_OCI_PERMISSIONS}を確認してください。"
OCI_SIGNATURE_NOT_FOUND_HINT = (
    "Agent が見つからないか、権限がありません（この 2 つは同じ応答で、区別できません）。"
    "endpoint の URL が正しいか、Agent が配備されているかと、"
    f"{_OCI_PERMISSIONS}を確認してください。"
)
_OCI_SIGNATURE_HINTS: dict[int, str] = {
    401: OCI_SIGNATURE_UNAUTHORIZED_HINT,
    403: OCI_SIGNATURE_FORBIDDEN_HINT,
    404: OCI_SIGNATURE_NOT_FOUND_HINT,
}


def is_aidp_gateway_host(host: str | None) -> bool:
    """AIDP の Agent の gateway の host か（`gateway.aidp.<region>.oci.oraclecloud.com`）。"""
    return bool(host) and _AIDP_GATEWAY_HOST.fullmatch(str(host).lower()) is not None


def agent_card_url(config: McpConnectionConfig) -> str | None:
    """Agent card の URL。接続に指定が無ければ endpoint から決める。

    既定は A2A の well-known の場所（`<endpoint>/.well-known/agent-card.json`）。AIDP の gateway は
    `<endpoint>/agent-card.json` に置く（AIDP の既定値）。
    """
    if config.agent_card_url:
        return config.agent_card_url
    if not config.base_url:
        return None
    endpoint = config.base_url.rstrip("/")
    if is_aidp_gateway_host(urlsplit(endpoint).hostname):
        return f"{endpoint}/agent-card.json"
    return f"{endpoint}/.well-known/agent-card.json"


def _clip(value: object, limit: int) -> str:
    text = _WHITESPACE.sub(" ", value).strip() if isinstance(value, str) else ""
    return text if len(text) <= limit else f"{text[:limit]}…"


class AgentCardSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    description: str = ""


class AgentCard(BaseModel):
    """A2A の Agent card のうち、使う項目（名前・説明・版・できること）。

    必須は `name`（空でない）と `description`（文字列）。ほかの項目は無くても読める。
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(min_length=1)
    description: str
    protocol_version: str | None = Field(default=None, alias="protocolVersion")
    version: str | None = None
    skills: list[AgentCardSkill] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("protocol_version", "version", mode="before")
    @classmethod
    def _text_or_none(cls, value: object) -> object:
        return value if isinstance(value, str) else None

    @field_validator("skills", mode="before")
    @classmethod
    def _object_skills(cls, value: object) -> object:
        # 形の違う項目は捨てる（名前・説明が読めれば接続は使える）。
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def tool_description(self) -> str:
        """モデルに渡すツールの説明（何をするかは Agent card の説明に任せる）。"""
        name = _clip(self.name, _CARD_NAME_MAX_CHARS)
        lines = [f"外部の Agent「{name}」にテキストの依頼を 1 通送り、その応答の本文を返す。"]
        description = _clip(self.description, _CARD_DESCRIPTION_MAX_CHARS)
        if description:
            lines.append(f"この Agent の説明: {description}")
        skills: list[str] = []
        for skill in self.skills[:_CARD_SKILL_MAX]:
            parts = (
                _clip(skill.name, _CARD_NAME_MAX_CHARS),
                _clip(skill.description, _CARD_SKILL_MAX_CHARS),
            )
            if line := "：".join(part for part in parts if part):
                skills.append(line)
        if skills:
            lines.append("この Agent ができること:")
            lines.extend(f"- {item}" for item in skills)
        return "\n".join(lines)


def _text_and_omitted(parts: object, omitted: dict[str, int]) -> str:
    """parts の text をつなぐ。text 以外の part は種類ごとに数える。"""
    texts: list[str] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        kind = part.get("kind")
        if kind in (None, "text") and isinstance(text, str):
            if text.strip():
                texts.append(text)
            continue
        name = str(kind or "unknown")
        omitted[name] = omitted.get(name, 0) + 1
    return "\n".join(texts)


def _message_text(message: object, omitted: dict[str, int]) -> str:
    return _text_and_omitted(message.get("parts"), omitted) if isinstance(message, dict) else ""


def _last_agent_message_text(history: object, omitted: dict[str, int]) -> str:
    for message in reversed(history if isinstance(history, list) else []):
        if isinstance(message, dict) and message.get("role") == "agent":
            return _message_text(message, omitted)
    return ""


def _output(status: str, text: str, omitted: dict[str, int]) -> JsonObject:
    output: JsonObject = {"status": status, "text": text}
    if omitted:
        output["content_omitted"] = [
            {"type": name, "count": count} for name, count in sorted(omitted.items())
        ]
    if status == STATUS_INPUT_REQUIRED:
        output["next_step"] = _INPUT_REQUIRED_NEXT_STEP
    elif status == STATUS_INCOMPLETE:
        output["next_step"] = _INCOMPLETE_NEXT_STEP
    return output


def parse_send_result(result: JsonObject, *, label: str, details: JsonObject) -> JsonObject:
    """`message/send` の `result`（`message` か `task`）を、ツールの結果にする。

    - `message`: 応答の本文（text の part）。
    - `task`: 状態が `completed` なら成果物（artifacts）の text、無ければ状態の文・履歴の最後の
      Agent の文。`input-required` は相手の問い（状態の文）。`submitted` / `working` は完了して
      いない（待たない）。それ以外（`failed` / `rejected` / `canceled` / `auth-required` など）は
      失敗にする。
    """
    omitted: dict[str, int] = {}
    kind = result.get("kind")
    if kind == "message" or (kind is None and "parts" in result):
        return _output(STATUS_COMPLETED, _message_text(result, omitted), omitted)
    status = result.get("status")
    if kind not in (None, "task") or not isinstance(status, dict):
        raise ExternalToolError(
            "a2a.invalid_response",
            f"{label}の応答が A2A の message / task の形ではありません"
            "（接続の URL が A2A の endpoint かを確認してください）。",
            {**details, "kind": kind if isinstance(kind, str) else None},
        )
    state = status.get("state")
    state = state if isinstance(state, str) else ""
    status_text = _message_text(status.get("message"), omitted)
    if state == _TASK_STATE_COMPLETED:
        artifacts = result.get("artifacts")
        text = "\n".join(
            text
            for artifact in (artifacts if isinstance(artifacts, list) else [])
            if isinstance(artifact, dict)
            and (text := _text_and_omitted(artifact.get("parts"), omitted))
        )
        text = text or status_text or _last_agent_message_text(result.get("history"), omitted)
        return _output(STATUS_COMPLETED, text, omitted)
    if state == _TASK_STATE_INPUT_REQUIRED:
        text = status_text or _last_agent_message_text(result.get("history"), omitted)
        return _output(STATUS_INPUT_REQUIRED, text, omitted)
    if state in _TASK_STATES_IN_FLIGHT:
        return _output(STATUS_INCOMPLETE, status_text, omitted)
    reason = f" 相手の Agent の文: {status_text}" if status_text else ""
    raise ExternalToolError(
        "a2a.task_failed",
        f"{label}が依頼を完了しませんでした（状態: {state or '不明'}）。{reason}".rstrip(),
        {**details, "task_state": state or None},
    )


class A2aConnectionClient:
    """A2A の接続の client（1 回の呼び出しの間だけ使う）。"""

    def __init__(self, config: McpConnectionConfig) -> None:
        self._config = config

    @property
    def server_id(self) -> str:
        return self._config.server_id

    @property
    def _label(self) -> str:
        return self._config.display_name()

    @property
    def _details(self) -> JsonObject:
        return {"server_id": self.server_id}

    def agent_card(self) -> AgentCard:
        """Agent card を取り、必須の項目を確かめる（認証は `message/send` と同じ）。"""
        url = self._card_url()
        data, _headers = self._send(
            url, {}, kind=McpRetryKind.PROTOCOL, http_method="GET", what="Agent card"
        )
        try:
            card = AgentCard.model_validate(data or {})
        except ValidationError as exc:
            raise ExternalToolError(
                "a2a.invalid_agent_card",
                f"{self._label}の Agent card に必須の項目（name・description）がありません"
                "（Agent card の URL を確認してください）。",
                {
                    **self._details,
                    "agent_card_url": mask_url_credentials(url),
                    "missing": sorted({str(error["loc"][0]) for error in exc.errors()}),
                },
            ) from exc
        logger.info(
            "a2a_agent_card_loaded",
            extra={"server_id": self.server_id, "protocol_version": card.protocol_version},
        )
        return card

    def list_tools(self) -> ExternalMcpToolsData:
        """この Agent にメッセージを送る 1 つのツール（説明は Agent card から）。"""
        card = self.agent_card()
        tool = ExternalMcpToolInfo(
            name=A2A_TOOL_NAME,
            title=_clip(card.name, _CARD_NAME_MAX_CHARS),
            description=card.tool_description(),
            input_schema=A2A_TOOL_INPUT_SCHEMA,
            output_schema=A2A_TOOL_OUTPUT_SCHEMA,
            server_id=self.server_id,
            function_name=mcp_function_name(self.server_id, A2A_TOOL_NAME),
        )
        return ExternalMcpToolsData(
            tools=[tool],
            metadata={
                "server_id": self.server_id,
                "protocol": "a2a",
                "agent_card": {
                    "name": _clip(card.name, _CARD_NAME_MAX_CHARS),
                    "description": _clip(card.description, _CARD_DESCRIPTION_MAX_CHARS),
                    "protocol_version": card.protocol_version,
                    "version": card.version,
                },
            },
        )

    def send_message(
        self, text: str, *, message_id: str | None = None, trace_id: str | None = None
    ) -> JsonObject:
        """テキストのメッセージを 1 通送り、応答をツールの結果にする（`parse_send_result`）。

        `message_id` は論理操作の ID（`external_writes.operation_key`）。承認の後の送り直しでも
        同じ値なので、ID で重複を判定できる相手は二重に処理しない。無ければ新しい ID。
        """
        if not text.strip():
            raise ExternalToolError(
                "a2a.empty_message",
                "送るメッセージが空です。依頼の文を message に入れて呼び直してください。",
                self._details,
            )
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": trace_id or f"a2a_{uuid4().hex}",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": message_id or uuid4().hex,
                    "parts": [{"kind": "text", "text": text}],
                }
            },
        }
        # 相手が書き込むことがあるので、届いた後の失敗では再送しない（UNSAFE）。
        data, _headers = self._send(
            self._endpoint(), payload, kind=McpRetryKind.UNSAFE, what="message/send"
        )
        details = {**self._details, "method": "message/send"}
        try:
            response = JsonRpcResponse.model_validate(data or {})
        except ValidationError as exc:
            raise ExternalToolError(
                "a2a.invalid_response",
                f"{self._label}の応答が JSON-RPC の形式ではありません"
                "（接続の URL が A2A の endpoint かを確認してください）。",
                details,
            ) from exc
        if response.error is not None:
            raise ExternalToolError(
                "a2a.rpc_error",
                f"{self._label}が JSON-RPC のエラーを返しました。",
                {**details, "error": response.error},
            )
        if response.result is None:
            raise ExternalToolError(
                "a2a.missing_result", f"{self._label}が空の応答を返しました。", details
            )
        return parse_send_result(response.result, label=self._label, details=details)

    def _endpoint(self) -> str:
        if not self._config.base_url:
            raise ExternalToolError(
                "a2a.not_configured",
                f"{self._label}の endpoint の URL が設定されていません。",
                self._details,
            )
        return self._config.base_url.rstrip("/")

    def _card_url(self) -> str:
        self._endpoint()
        return str(agent_card_url(self._config))

    def _send(
        self,
        url: str,
        payload: JsonObject,
        *,
        kind: McpRetryKind,
        what: str,
        http_method: str = "POST",
    ) -> tuple[JsonObject | None, Any]:
        config = self._config
        headers, auth = self._authentication(url)
        try:
            return _post_mcp_message(
                service_code="a2a",
                service_label=self._label,
                target_name=config.label or config.server_id,
                url=url,
                payload=payload,
                headers={**headers, "Accept": "application/json"},
                timeout_seconds=config.timeout_seconds,
                max_retries=get_settings().agent_external_mcp_max_retries,
                kind=kind,
                auth=auth,
                http_method=http_method,
                span_name="a2a.request",
                # A2A の params に `_meta` は無い（traceparent は HTTP の header だけで渡す）。
                trace_meta=False,
            )
        except ExternalToolError as exc:
            raise self._with_permission_hint(exc, what) from exc

    def _with_permission_hint(self, error: ExternalToolError, what: str) -> ExternalToolError:
        """OCI 署名の接続の 401 / 403 / 404 を、確かめる所が分かる案内にする。

        401 は署名（OCI 認証の設定）、404 は URL の誤り・Agent の未配備・権限の不足のどれか
        （AIDP の gateway は区別しない）。
        """
        error.details.setdefault("request", what)
        error.details.setdefault("server_id", self.server_id)
        status_code = error.details.get("status_code")
        hint = _OCI_SIGNATURE_HINTS.get(status_code) if isinstance(status_code, int) else None
        if self._config.effective_auth_mode() != "oci_signature" or hint is None:
            return error
        return ExternalToolError(
            error.code,
            f"{self._label}が HTTP {status_code} を返しました。{hint}",
            {**error.details, "auth_mode": "oci_signature"},
        )

    def _authentication(self, url: str) -> tuple[dict[str, str], httpx.Auth | None]:
        """認証の header か、リクエストごとの署名（OCI 署名）。資格情報が無ければ送らない。"""
        config = self._config
        mode = config.effective_auth_mode()
        if mode == "oci_signature":
            if urlsplit(url).scheme != "https":
                # 署名（鍵の ID を含む）を平文の通信に載せない。
                raise ExternalToolError(
                    "a2a.oci_signature_requires_https",
                    f"{self._label}の OCI 署名は https の URL でだけ使えます"
                    "（接続の URL を確認してください）。",
                    self._details,
                )
            settings = get_settings()
            try:
                return {}, oci_request_signing_auth(
                    settings.oci_config_file, settings.oci_config_profile
                )
            except OciRequestSigningNotConfiguredError as exc:
                raise ExternalToolError(
                    "a2a.oci_signature_not_configured",
                    f"{self._label}の OCI 署名に使う API キーを読み込めません。{exc}",
                    {**self._details, "reason": exc.reason},
                ) from exc
        if mode == "oauth_client_credentials":
            token = _mcp_oauth_bearer_token(
                token_url=config.oauth_token_url,
                client_id=config.oauth_client_id,
                client_secret=config.oauth_client_secret,
                scope=config.oauth_scope,
                timeout_seconds=config.timeout_seconds,
            )
            if token is None:
                raise ExternalToolError(
                    "a2a.oauth_not_configured",
                    f"{self._label}の OAuth の資格情報が足りません。",
                    self._details,
                )
            return {"Authorization": f"Bearer {token}"}, None
        if mode == "api_key":
            if not config.api_key:
                raise ExternalToolError(
                    "a2a.api_key_not_configured",
                    f"{self._label}の API キーが設定されていません。",
                    self._details,
                )
            return {"Authorization": f"Bearer {config.api_key}"}, None
        if mode == "service_token":
            # 3 製品の間の token（Run の利用者の身元）を外部の Agent へ渡さない。
            raise ExternalToolError(
                "a2a.auth_mode_unsupported",
                f"{self._label}ではサービストークンの認証を使えません"
                "（認証方式を選び直してください）。",
                self._details,
            )
        return {}, None


def a2a_tool_handler(config: McpConnectionConfig) -> ToolHandler:
    """外部の Agent にメッセージを送る handler（`tools.mcp_tool_handler` が A2A の接続に使う）。"""

    def handle(arguments: JsonObject, context: ToolInvocationContext) -> JsonObject:
        message = arguments.get("message")
        return A2aConnectionClient(config).send_message(
            message if isinstance(message, str) else "",
            message_id=context.operation_key,
            trace_id=context.trace_id,
        )

    return handle
