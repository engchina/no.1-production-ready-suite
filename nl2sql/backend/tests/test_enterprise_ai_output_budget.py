"""Structured Outputs の出力の上限と、未完了の送り直し・組への分割（#1719）。

推論のあるモデルは推論の token も出力の上限に数える。2026-10-13 の実測（コメント管理の SQL 生成と
同じ要求、5 表・32 列）で、openai.gpt-oss-120b（推論 medium）は上限 1200 では output 1200 のうち
reasoning 1198 で `status=incomplete`・`incomplete_details.reason=max_output_tokens` になった。
ここでは同じ振る舞いを HTTP のスタブ（推論の token + 本文の token が上限を超えたら未完了）で
再現する。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pr_system_settings.model import ModelSettingsStore, estimate_input_tokens

from app.features.nl2sql import enterprise_ai_client as module
from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import MetadataSqlGenerateRequest
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.features.nl2sql.structured_outputs import SqlOutput, response_format
from app.settings import EnterpriseAiConfiguredModel, Settings

_API_KEY = "synthetic-secret-api-key-1719"
_MODEL = "xai.grok-4.3"
_REASONING_TOKENS = 1198
# 推論の量を low に下げた送り直し（#2023）で使う推論の token（スタブの値）。
_LOW_REASONING_TOKENS = 300
_ENDPOINT = "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1"
_PROJECT = "ocid1.generativeaiproject.oc1.us-chicago-1.synthetic"


def _settings(**model: Any) -> Settings:
    return Settings(
        oci_enterprise_ai_endpoint=_ENDPOINT,
        oci_enterprise_ai_api_key=_API_KEY,
        oci_enterprise_ai_project_ocid=_PROJECT,
        oci_enterprise_ai_default_text_model=_MODEL,
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id=_MODEL, reasoning_effort="medium", **model)
        ],
    )


def _targets_in(payload: dict[str, Any]) -> list[str]:
    text = str(payload["input"][0]["content"])
    match = re.search(r"^targets: (.*)$", text, flags=re.M)
    assert match is not None
    return [item.split(":", 1)[1] for item in match.group(1).split(", ")]


class _ReasoningModel:
    """推論のあるモデルのスタブ: 推論の token + 本文の token が上限を超えたら未完了を返す。"""

    def __init__(
        self,
        *,
        tokens_per_target: int = 300,
        sql_for: Callable[[list[str]], str] | None = None,
        fail_targets: frozenset[str] = frozenset(),
    ) -> None:
        self.tokens_per_target = tokens_per_target
        self.sql_for = sql_for or (
            lambda targets: "\n".join(
                f"COMMENT ON TABLE {name} IS 'AI のコメント';" for name in targets
            )
        )
        self.fail_targets = fail_targets
        self.payloads: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.payloads.append(payload)
        budget = int(payload["max_output_tokens"])
        targets = _targets_in(payload)
        reasoning = (
            _LOW_REASONING_TOKENS
            if payload.get("reasoning") == {"effort": "low"}
            else _REASONING_TOKENS
        )
        needed = reasoning + self.tokens_per_target * len(targets)
        if budget < needed or self.fail_targets.intersection(targets):
            return httpx.Response(
                200,
                json={
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [
                        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "…"}]}
                    ],
                    "usage": {
                        "input_tokens": 934,
                        "output_tokens": budget,
                        "output_tokens_details": {"reasoning_tokens": min(budget, reasoning)},
                    },
                },
            )
        text = json.dumps({"sql": self.sql_for(targets), "explanation": ""}, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "…"}]},
                    {"type": "message", "content": [{"type": "output_text", "text": text}]},
                ],
                "usage": {
                    "input_tokens": 934,
                    "output_tokens": needed,
                    "output_tokens_details": {"reasoning_tokens": reasoning},
                },
            },
        )


_REAL_HTTPX_CLIENT = httpx.Client


def _mock_http(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _REAL_HTTPX_CLIENT(transport=transport, **kwargs)
    )


def _service(settings: Settings) -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = module.OciEnterpriseAiDirectClient(settings)
    return service


def _request(service: Nl2SqlService, count: int, columns: int = 10) -> MetadataSqlGenerateRequest:
    names = [f"T{index:02d}" for index in range(1, count + 1)]
    blocks = [
        "\n".join(
            [f"OBJECT: {name}", "TYPE: table", "COMMENT: -", "COLUMNS:"]
            + [f"- C{column}: NUMBER NULLABLE=Y COMMENT=-" for column in range(1, columns + 1)]
        )
        for name in names
    ]
    return MetadataSqlGenerateRequest(
        targets=[{"object_name": name, "object_type": "table"} for name in names],
        structure_text="\n\n".join(blocks),
        primary_key_text="\n\n".join(f"OBJECT: {name}\n{name}_PK P(C1)" for name in names),
    )


def test_old_common_limit_is_ignored_and_model_capacity_is_respected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧い共通上限を読んでも、登録容量を守ってオンラインの予算を送る。"""
    (tmp_path / "model-settings.json").write_text(
        json.dumps(
            {
                "version": 5,
                "enterprise_ai": {
                    "connections": [
                        {
                            "connection_id": "primary",
                            "endpoint": _ENDPOINT,
                            "project_ocid": _PROJECT,
                        }
                    ],
                    "models": [{"model_id": _MODEL, "reasoning_effort": "medium"}],
                    "default_text_model_id": _MODEL,
                    "default_vision_model_id": "",
                    "llm_max_output_tokens": 1200,
                    "vlm_max_output_tokens": 65536,
                },
            }
        ),
        encoding="utf-8",
    )
    settings = _settings()
    ModelSettingsStore(
        resolve_path=lambda _settings: tmp_path / "model-settings.json",
        env_file=lambda _settings: tmp_path / ".env",
    ).load(settings)
    assert settings.oci_enterprise_ai_models[0].max_output_tokens == 32_768
    model = _ReasoningModel()
    _mock_http(monkeypatch, model)
    service = _service(settings)

    result = service.generate_comment_sql(_request(service, 5))

    assert result.source == "oci_enterprise_ai"
    assert result.fallback is False
    assert [payload["max_output_tokens"] for payload in model.payloads] == [32_768]
    assert model.payloads[0]["reasoning"] == {"effort": "medium"}
    assert result.sql.count("COMMENT ON TABLE") == 5


def test_online_uses_single_budget_without_changing_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """オンライン生成はモデル容量とは別の単一予算で呼ぶ。"""
    model = _ReasoningModel(tokens_per_target=8_000)
    _mock_http(monkeypatch, model)
    service = _service(_settings(max_input_tokens=1_000_000, max_output_tokens=131_072))

    result = service.generate_comment_sql(_request(service, 5))

    assert result.source == "oci_enterprise_ai"
    assert result.fallback is False
    assert [payload["max_output_tokens"] for payload in model.payloads] == [65_536]


def test_many_tables_are_split_into_batches_and_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    """上限の小さいモデルでも、表を組に分けると 1 回の出力が上限に収まる。"""
    model = _ReasoningModel(tokens_per_target=280)
    _mock_http(monkeypatch, model)
    # 12 表を 1 回で呼ぶと 1198 + 280 × 12 = 4558 > 4096 で未完了、10 表 + 2 表なら収まる。
    service = _service(_settings(max_output_tokens=4_096))

    result = service.generate_comment_sql(_request(service, 12))

    assert result.source == "oci_enterprise_ai"
    assert result.fallback is False
    assert sorted(len(_targets_in(payload)) for payload in model.payloads) == [2, 10]
    assert all(payload["max_output_tokens"] == 4_096 for payload in model.payloads)
    assert result.sql.count("COMMENT ON TABLE") == 12
    # 組ごとの入力には、ほかの組の表の塊を入れない。
    for payload in model.payloads:
        content = str(payload["input"][0]["content"])
        targets = _targets_in(payload)
        assert content.count("OBJECT: ") == 2 * len(targets)


def test_annotation_and_domain_are_split_too(monkeypatch: pytest.MonkeyPatch) -> None:
    annotation_model = _ReasoningModel(
        tokens_per_target=280,
        sql_for=lambda targets: "\n".join(
            f"ALTER TABLE {name} ANNOTATIONS (ADD OR REPLACE \"DESCRIPTION\" 'AI の説明');"
            for name in targets
        ),
    )
    _mock_http(monkeypatch, annotation_model)
    service = _service(_settings(max_output_tokens=4_096))
    annotations = service.generate_annotation_sql(_request(service, 12))
    assert annotations.source == "oci_enterprise_ai"
    assert annotations.fallback is False
    assert annotations.sql.count("ANNOTATIONS") == 12

    domain_model = _ReasoningModel(
        tokens_per_target=280,
        sql_for=lambda targets: f"CREATE DOMAIN IF NOT EXISTS D_{len(targets)} AS NUMBER;",
    )
    _mock_http(monkeypatch, domain_model)
    domains = service.generate_domain_sql(_request(service, 12))
    assert domains.source == "oci_enterprise_ai"
    assert domains.fallback is False
    assert [len(_targets_in(payload)) for payload in domain_model.payloads] == [10, 2]
    # ドメインは順に呼び、前の組で作るドメインを次の組に渡す（同じ意味の列で再利用させる）。
    second = str(domain_model.payloads[1]["input"][0]["content"])
    assert "この生成の前の組で作るドメイン" in second
    assert "D_10" in second


def test_failure_shows_reason_limit_and_usage_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """次の段階を送れない容量なら終了し、理由・上限・usageを失敗の詳細に残す。"""
    model = _ReasoningModel(tokens_per_target=200)
    _mock_http(monkeypatch, model)
    service = _service(_settings(max_output_tokens=1_200))

    result = service.generate_comment_sql(_request(service, 5))

    # 同じ上限や推論量の変更での追加要求は送らない。
    assert [payload["max_output_tokens"] for payload in model.payloads] == [1_200]
    assert [payload["reasoning"] for payload in model.payloads] == [{"effort": "medium"}]
    assert result.source == "deterministic"
    assert result.fallback is True
    assert result.fallback_objects == []
    assert "出力の上限" in result.fallback_reason
    assert "理由=max_output_tokens" in result.fallback_detail
    assert "出力の上限=1200 tokens" in result.fallback_detail
    assert "使った出力=1200 tokens（うち推論 1198）" in result.fallback_detail
    # 規則ベースの SQL の理由は warnings に重ねない。secret は出さない。
    assert not any("fallback" in warning for warning in result.warnings)
    assert _API_KEY not in result.model_dump_json()


def test_partial_failure_marks_only_the_failed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _ReasoningModel(tokens_per_target=280, fail_targets=frozenset())
    service = _service(_settings(max_output_tokens=4_096))
    failing = service._db_admin_object_identity("T11").qualified_name
    model.fail_targets = frozenset({failing})
    _mock_http(monkeypatch, model)

    result = service.generate_comment_sql(_request(service, 12))

    assert result.source == "oci_enterprise_ai"
    assert result.fallback is True
    assert result.fallback_objects == [
        service._db_admin_object_identity(name).qualified_name for name in ("T11", "T12")
    ]
    assert result.sql.count("'AI のコメント'") == 10
    assert "T11" in result.sql and "T12" in result.sql
    assert result.fallback_detail.startswith(failing)


def test_client_incomplete_stops_after_one_request_with_reason_limit_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _ReasoningModel(tokens_per_target=200_000)
    _mock_http(monkeypatch, model)
    client = module.OciEnterpriseAiDirectClient(
        _settings(max_input_tokens=1_000_000, max_output_tokens=131_072)
    )

    with pytest.raises(EnterpriseAiDirectError) as raised:
        client.generate(
            prompt="COMMENT",
            context="targets: table:APP.T1",
            system_prompt="SQL",
            max_output_tokens=4_000,
            response_format=response_format(SqlOutput),
        )

    assert raised.value.code == "incomplete"
    assert raised.value.incomplete is not None
    message = str(raised.value)
    assert raised.value.attempts == 1
    assert "出力の上限=65536 tokens" in message
    # オンラインの再試行では推論の量を変更しない。
    assert "「推論の量」を下げる" not in message
    assert _API_KEY not in message
    assert [payload["max_output_tokens"] for payload in model.payloads] == [
        65_536,
    ]
    assert all(payload["reasoning"] == {"effort": "medium"} for payload in model.payloads)


def test_capacity_shortage_does_not_retry_the_same_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _ReasoningModel(tokens_per_target=200)
    _mock_http(monkeypatch, model)
    client = module.OciEnterpriseAiDirectClient(_settings(max_output_tokens=1_200))
    with pytest.raises(EnterpriseAiDirectError) as raised:
        client.generate(
            prompt="COMMENT",
            context="targets: table:APP.T1",
            system_prompt="SQL",
            response_format=response_format(SqlOutput),
        )
    assert raised.value.code == "incomplete"
    assert raised.value.attempts == 1
    assert [payload["reasoning"] for payload in model.payloads] == [{"effort": "medium"}]


def test_budget_is_capped_by_context_room(monkeypatch: pytest.MonkeyPatch) -> None:
    """出力の上限が入力の上限と同じモデルでも、prompt の分を引いた残りで送る。

    prompt が入らずに失敗するのを防ぐ。
    """
    model = _ReasoningModel(tokens_per_target=1)
    _mock_http(monkeypatch, model)
    client = module.OciEnterpriseAiDirectClient(
        _settings(max_input_tokens=8_192, max_output_tokens=8_192)
    )
    context = "targets: table:APP.T1\n" + "あ" * 3_000

    client.generate(
        prompt="COMMENT",
        context=context,
        system_prompt="SQL",
        response_format=response_format(SqlOutput),
    )

    sent = model.payloads[0]["max_output_tokens"]
    actual_input = {
        key: value for key, value in model.payloads[0].items() if key != "max_output_tokens"
    }
    input_tokens = estimate_input_tokens(json.dumps(actual_input, ensure_ascii=False))
    assert sent > 4000
    assert sent + input_tokens <= 8192


def test_unstructured_online_response_rejects_partial_output_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(max_input_tokens=1_000_000, max_output_tokens=65_536)
    settings.oci_enterprise_ai_llm_payload_template = json.dumps(
        {
            "model": "${model}",
            "input": "${prompt}",
            "max_output_tokens": 4096,
            "reasoning": {"effort": "medium"},
        }
    )
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) < 3:
            settings.oci_enterprise_ai_llm_payload_template = (
                '{"input":"changed","max_output_tokens":999999}'
            )
            settings.oci_enterprise_ai_models[0].reasoning_effort = "low"
            return httpx.Response(
                200,
                json={
                    "status": "incomplete",
                    "output_text": "partial",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            )
        return httpx.Response(200, json={"status": "completed", "output_text": "completed"})

    _mock_http(monkeypatch, handler)

    def reject_partial_parse(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("未完了の応答を SQL として解析してはいけません。")

    monkeypatch.setattr(module, "_parse_response", reject_partial_parse)
    client = module.OciEnterpriseAiDirectClient(settings)
    with pytest.raises(EnterpriseAiDirectError) as raised:
        client.generate(prompt="synthetic", context="", system_prompt="synthetic")
    assert raised.value.code == "incomplete"
    assert [p["max_output_tokens"] for p in payloads] == [65536]
    assert all(
        p["input"] == "synthetic" and p["reasoning"] == {"effort": "medium"} for p in payloads
    )
    assert all(p["model"] == _MODEL for p in payloads)


def test_online_deadline_is_checked_before_every_transport_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    sent: list[httpx.Request] = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        assert request.extensions["timeout"]["read"] == 10.0
        clock[0] = 11.0
        return httpx.Response(503, json={"error": {"message": "busy"}})

    _mock_http(monkeypatch, handler)
    client = module.OciEnterpriseAiDirectClient(_settings())
    with pytest.raises(EnterpriseAiDirectError) as raised:
        client.generate(
            prompt="synthetic",
            context="",
            system_prompt="synthetic",
            timeout_seconds=10,
            max_retries=2,
        )
    assert raised.value.code == "deadline_exceeded"
    assert len(sent) == 1


def test_online_context_exhaustion_does_not_send_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []
    _mock_http(monkeypatch, lambda request: sent.append(request))
    client = module.OciEnterpriseAiDirectClient(
        _settings(max_input_tokens=4096, max_output_tokens=1024)
    )
    with pytest.raises(EnterpriseAiDirectError) as raised:
        client.generate(prompt="あ" * 4096, context="", system_prompt="synthetic")
    assert raised.value.code == "context_budget_exceeded"
    assert not sent


def test_output_incomplete_uses_a_shared_error_code_and_friendly_message() -> None:
    from pr_system_settings.enterprise_ai_errors import EnterpriseAiIncomplete

    from app.features.nl2sql.service import _job_failure_error_code, _job_failure_presentation

    error = EnterpriseAiDirectError(
        "technical",
        code="incomplete",
        model_id=_MODEL,
        incomplete=EnterpriseAiIncomplete(
            status="incomplete", reason="max_output_tokens", max_output_tokens=32768
        ),
        user_message="回答の上限に達しました。質問を分けてください。",
    )
    assert _job_failure_error_code(error, fallback="fallback") == "ENTERPRISE_AI_OUTPUT_INCOMPLETE"
    assert _job_failure_presentation(error)[0] == error.user_message


@pytest.mark.parametrize("code", ["deadline_exceeded", "context_budget_exceeded"])
def test_budget_exhaustion_keeps_its_friendly_reason(code: str) -> None:
    from app.features.nl2sql.service import _job_failure_error_code, _job_failure_presentation

    error = EnterpriseAiDirectError("対象を絞ってやり直してください。", code=code)
    assert _job_failure_error_code(error, fallback="fallback") == "ENTERPRISE_AI_" + code.upper()
    assert _job_failure_presentation(error)[0] == str(error)


def test_online_budget_uses_configured_request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """モデル容量を増やしても、設定した要求予算は増えない。"""
    model = _ReasoningModel()
    _mock_http(monkeypatch, model)
    settings = _settings(max_input_tokens=1_000_000, max_output_tokens=131_072)
    settings.online_max_output_tokens = 24_576
    client = module.OciEnterpriseAiDirectClient(settings)
    client.generate(
        prompt="COMMENT",
        context="targets: table:APP.T1",
        system_prompt="SQL",
        response_format=response_format(SqlOutput),
    )
    assert [payload["max_output_tokens"] for payload in model.payloads] == [24_576]
