"""推導の監査の判定の段（#4565）と理由の段（#4564）の段のモデル。外部呼出し 0。

段のモデルの設定（RAG_ENGINE_APPLICATION_AUDIT_* / RAG_ENGINE_APPLICATION_REASON_*）があれば、その段の 1 回の
呼び出しと入力の大きさの検査だけをその provider で行い、ほかの呼び出しは回答のモデルのままにする。
録画の応答（fixtures/application_stage_models_grok_recorded.json）は段のモデル xai.grok-4.7 の実モデルの記録で、
題・原文は既存の録画（application_reason_recorded.json の case_inputs）と同じ合成のもの。製品のコードが
その応答を得た入力と同じ入力（system と user の sha256）を組み立てることを確かめる。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import OpenAI

from rag_engine.adapters import oci
from rag_engine.config import APPLICATION_AUDIT_LLM_PROVIDER, APPLICATION_REASON_LLM_PROVIDER, get_settings
from rag_engine.generation import grounded
from rag_engine.generation.application_reason import HIDDEN_REASON
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import CheckedApplication
from rag_engine.generation.citation_unit_ids import unit_generation_model
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedApplication

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "application_stage_models_grok_recorded.json").read_text(encoding="utf-8"))
CASES = {
    case["case_id"]: case
    for case in json.loads((FIXTURES / "application_reason_recorded.json").read_text(encoding="utf-8"))["case_inputs"]
}
STEP_MODEL = RECORDED["model"]
ANSWER_MODEL = "synthetic-answer-model"
ANSWER_EFFORT = "medium"
AUDIT = "ApplicationVerdictAudit"
REASON = "AuditReason"
SYNTHETIC_REASON = {"reason": "引用した資料の条件に照らした合成の理由です。"}

STAGES = {
    "audit": ("RAG_ENGINE_APPLICATION_AUDIT", APPLICATION_AUDIT_LLM_PROVIDER, "application_audit_llm"),
    "reason": ("RAG_ENGINE_APPLICATION_REASON", APPLICATION_REASON_LLM_PROVIDER, "application_reason_llm"),
}


def stage_env(*stages: str, connected: bool = True) -> dict[str, str]:
    """回答のモデルと段のモデルの接続（合成の接続先）。``connected=False`` は登録していないモデル（model だけ）。"""
    env = {
        "OCI_ENTERPRISE_AI_ENDPOINT": "https://answer.invalid/openai/v1",
        "OCI_ENTERPRISE_AI_API_KEY": "synthetic-answer",
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": ANSWER_MODEL,
        "OCI_ENTERPRISE_AI_REASONING_EFFORT": ANSWER_EFFORT,
    }
    for stage in stages:
        prefix = STAGES[stage][0]
        env[f"{prefix}_MODEL"] = STEP_MODEL
        if connected:
            env.update(
                {
                    f"{prefix}_ENDPOINT": f"https://{stage}.invalid/openai/v1",
                    f"{prefix}_API_KEY": f"synthetic-{stage}",
                    f"{prefix}_REASONING_EFFORT": RECORDED["reasoning_effort"],
                    f"{prefix}_MAX_OUTPUT_TOKENS": str(RECORDED["max_output_tokens"]),
                }
            )
    return env


def input_sha256(body: dict[str, Any]) -> str:
    """送った system と user の 2 つの文の sha256（録画の ``input_sha256`` と同じ作り方）。"""
    contents = [message["content"] for message in body["input"]]
    return hashlib.sha256(json.dumps(contents, ensure_ascii=False).encode("utf-8")).hexdigest()


def responses_body(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    """合成の Responses API の応答（録画の無い段の応答に使う）。"""
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_synthetic",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(payload, ensure_ascii=False), "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


def application(case_id: str) -> tuple[str, list[CheckedApplication], list[dict[str, Any]]]:
    """合成の題から、製品の経路（生成の引用の検証 → 推導の束縛）で確認対象の推導を 1 件作る。"""
    case = CASES[case_id]
    spans: list[dict[str, Any]] = []
    for number, document in enumerate(case["documents"], 1):
        spans.append(
            {
                "evidence_id": f"evidence-{number}",
                "label": f"E{number}",
                "source_id": f"{document['document_id']}:{document['chunk_set_id']}:{document['chunk_uid']}",
                "chunk_uid": document["chunk_uid"],
                "source": document["source"],
                "document_scope": [document["source"], document["chunk_set_id"], document["document_id"]],
                "page": 1,
                "text": document["text"],
                "start": 0,
                "end": len(document["text"]),
                "function": (document["source"], "", "synthetic-sha", "docling", document["chunk_set_id"], document["document_id"], ""),
                "section_path": [document["section"]],
                "source_order": [1, number, 0],
            }
        )
    grounded.format_functions(spans)
    catalog = grounded.generation_unit_catalog(spans)
    chosen = RECORDED["selected_document_index"][case_id]
    snapshot = catalog.snapshots[chosen]
    matches = [
        position
        for position, (start, end) in enumerate(snapshot.units)
        if spans[chosen]["text"][start:end].strip() == case["selected_quote"]
    ]
    assert len(matches) == 1
    raw = unit_generation_model(generation_draft_wire_model(), catalog).model_validate(
        {
            "items": [
                {
                    "request_id": "Q1",
                    "kind": "rule",
                    "text": case["selected_quote"],
                    "citations": [{"unit_ids": [snapshot.unit_ids[matches[0]]]}],
                }
            ],
            "request_coverage": [
                {
                    "request_id": "Q1",
                    "evidence_scope": [{"evidence_id": f"E{chosen + 1}", "target": "", "relation": "direct", "link": None}],
                }
            ],
            "summary": "",
            "confidence": "low",
        }
    )
    entries, dropped = grounded.verify(case["question"], canonical_generation_draft(raw, unit_catalog=catalog), spans)
    assert len(entries) == 1 and not dropped
    checked = CheckedApplication(
        GroundedApplication(item_indexes=[0], reasoning=case["application_reasoning"], result=case["application_result"]),
        entries,
    )
    return case["question"], [checked], spans


class Wire:
    """MockTransport の送信の記録。schema の名前ごとに返す応答を決める。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, replies: dict[str, dict[str, Any]]) -> None:
        from pr_backend_core.observability.model_purpose import model_purpose_attributes

        self.sent: list[tuple[str, str, str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            name = body["text"]["format"]["name"]
            purpose = model_purpose_attributes().get("pr.gen_ai.purpose", "")
            self.sent.append((name, body["model"], body.get("reasoning", {}).get("effort", ""), purpose, input_sha256(body)))
            return httpx.Response(200, json=replies[name])

        self.client = OpenAI(
            api_key="synthetic",
            base_url="https://synthetic.invalid/openai/v1",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        monkeypatch.setattr(oci, "_openai_client_for_provider", lambda provider, settings: self.client)
        monkeypatch.setattr(oci, "_stall_detection_enabled", lambda _settings: False)

    def calls(self) -> list[tuple[str, str, str, str]]:
        return [row[:4] for row in self.sent]


def review(case_id: str, *stages: str, connected: bool = True) -> dict[str, Any]:
    question, checked, spans = application(case_id)
    settings = get_settings(environ=stage_env(*stages, connected=connected), dotenv_path=None, llm_stall_resends=0)
    return review_applications(
        question,
        checked,
        evidence_spans=spans,
        existing_audit=None,
        audit_requested=False,
        parse_text=oci.parse_text_response,
        settings=settings,
        provider_id=None,
    )


# ---------- 設定の env から段の provider を作る ----------


@pytest.mark.parametrize("stage", sorted(STAGES))
def test_stage_model_env_builds_the_stage_provider(stage: str) -> None:
    _prefix, provider_id, field = STAGES[stage]
    settings = get_settings(environ=stage_env(stage), dotenv_path=None)
    provider = settings.llm_providers[provider_id]
    assert getattr(settings, field) == provider_id
    assert (provider.model, provider.base_url, provider.reasoning_effort, provider.max_output_tokens) == (
        STEP_MODEL,
        f"https://{stage}.invalid/openai/v1",
        "low",
        32768,
    )
    # 回答のモデルの provider は変えない。もう一方の段は設定が無ければ作らない。
    assert settings.llm_providers[settings.default_answer_llm].model == ANSWER_MODEL
    other = next(name for name in STAGES if name != stage)
    assert STAGES[other][1] not in settings.llm_providers and getattr(settings, STAGES[other][2]) == ""


# ---------- 判定の段（#4565） ----------


@pytest.mark.parametrize("slot", RECORDED["stage_a"], ids=[slot["slot"] for slot in RECORDED["stage_a"]])
def test_audit_stage_sends_the_recorded_input_to_its_model(slot: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    wire = Wire(monkeypatch, {AUDIT: slot["response"], REASON: responses_body(ANSWER_MODEL, SYNTHETIC_REASON)})

    diagnostic = review(slot["case"], "audit")

    # 判定の段だけが段のモデル・その推論の量で送られ、理由の段は回答のモデルのまま。
    assert wire.calls() == [
        (AUDIT, STEP_MODEL, "low", "answer.audit"),
        (REASON, ANSWER_MODEL, ANSWER_EFFORT, "answer.audit_reason"),
    ]
    assert wire.sent[0][4] == slot["input_sha256"]
    assert diagnostic["status"] == "completed" and diagnostic["calls"] == 2
    assert diagnostic["results"][0]["status"] == slot["product_status"] == ("valid" if slot["gold_valid"] else "invalid")
    assert "basis_not_cited" not in diagnostic and "unknown_basis_ids" not in diagnostic


def test_audit_stage_model_without_a_connection_is_incomplete_and_does_not_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    # backend は登録していないモデルの接続を渡さない（model だけ）。回答のモデルへ切り替えて送らない。
    wire = Wire(monkeypatch, {})

    diagnostic = review("source-positive", "audit", connected=False)

    assert wire.sent == []
    assert (diagnostic["status"], diagnostic["error_type"], diagnostic["calls"]) == ("incomplete", "RuntimeError", 1)
    assert diagnostic["results"][0]["status"] == "unknown"


def test_audit_stage_input_budget_uses_the_stage_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """入力の大きさの検査は段のモデルの出力の上限で測る（回答のモデルの上限では測らない）。"""
    from rag_engine.generation import application_review

    measured: list[str | None] = []
    monkeypatch.setattr(
        application_review, "ensure_model_input", lambda *args, provider_id=None, **kwargs: measured.append(provider_id)
    )
    Wire(monkeypatch, {AUDIT: RECORDED["stage_a"][0]["response"], REASON: responses_body(ANSWER_MODEL, SYNTHETIC_REASON)})

    review("source-positive", "audit")

    assert measured == [APPLICATION_AUDIT_LLM_PROVIDER]


# ---------- 理由の段（#4564） ----------


@pytest.mark.parametrize("slot", RECORDED["stage_b"], ids=[slot["slot"] for slot in RECORDED["stage_b"]])
def test_reason_stage_sends_the_recorded_input_to_its_model(slot: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    # 判定の段は回答のモデルのまま（固定した判定を返す）、理由の段だけを段のモデルで送る構成。
    wire = Wire(monkeypatch, {AUDIT: responses_body(ANSWER_MODEL, slot["stage_a"]), REASON: slot["response"]})

    diagnostic = review(slot["case"], "reason")

    assert wire.calls() == [
        (AUDIT, ANSWER_MODEL, ANSWER_EFFORT, "answer.audit"),
        (REASON, STEP_MODEL, "low", "answer.audit_reason"),
    ]
    assert wire.sent[1][4] == slot["input_sha256"]
    assert diagnostic["reason_step"]["results"][0]["status"] == slot["gate"] == "shown"
    assert diagnostic["results"][0]["reason"] == slot["reason"]
    # 入力の欄の名前を理由に出さない（#4564 の漏れ）。
    assert "引用元" not in slot["reason"]


def test_reason_stage_model_without_a_connection_keeps_the_fixed_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    # 登録していないモデルは例外にせず定型の文にする。判定は変えず、回答のモデルへ切り替えて送らない。
    slot = RECORDED["stage_b"][0]
    wire = Wire(monkeypatch, {AUDIT: responses_body(ANSWER_MODEL, slot["stage_a"])})

    diagnostic = review(slot["case"], "reason", connected=False)

    assert wire.calls() == [(AUDIT, ANSWER_MODEL, ANSWER_EFFORT, "answer.audit")]
    result = diagnostic["reason_step"]["results"][0]
    assert (result["status"], result["why"], result["error_type"]) == ("hidden", "reason_step_failed", "RuntimeError")
    assert diagnostic["results"][0]["status"] == "invalid"
    assert diagnostic["results"][0]["reason"] == HIDDEN_REASON


def test_reason_stage_input_budget_uses_the_stage_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.generation import application_reason

    measured: list[str | None] = []
    monkeypatch.setattr(
        application_reason, "ensure_model_input", lambda *args, provider_id=None, **kwargs: measured.append(provider_id)
    )
    slot = RECORDED["stage_b"][0]
    Wire(monkeypatch, {AUDIT: responses_body(ANSWER_MODEL, slot["stage_a"]), REASON: slot["response"]})

    review(slot["case"], "reason")

    assert measured == [APPLICATION_REASON_LLM_PROVIDER]


def test_both_stage_models_and_none() -> None:
    """両方の段を同じ登録モデルにしても provider は段ごと。設定が無ければ段の provider を作らない。"""
    both = get_settings(environ=stage_env("audit", "reason"), dotenv_path=None)
    assert (both.application_audit_llm, both.application_reason_llm) == (APPLICATION_AUDIT_LLM_PROVIDER, APPLICATION_REASON_LLM_PROVIDER)
    plain = get_settings(environ=stage_env(), dotenv_path=None)
    assert (plain.application_audit_llm, plain.application_reason_llm) == ("", "")
    assert not {APPLICATION_AUDIT_LLM_PROVIDER, APPLICATION_REASON_LLM_PROVIDER} & set(plain.llm_providers)
