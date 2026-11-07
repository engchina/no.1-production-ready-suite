"""推導の独立監査の判定と表示の理由の分離（#4471）。外部呼出し 0。

録画応答（fixtures/application_reason_recorded.json）は実モデルの検証の記録から写した合成の題で、
判定の段の入力・応答と理由の段の入力・応答を持つ。理由の段の入力がコードから同じ bytes で作られること、
門と判定の扱い、出荷する指示と schema に題・gold の文字列が無いことを確かめる。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import OpenAI
from test_bound_audit_transport import response_bytes

from rag_engine.adapters import oci
from rag_engine.config import get_settings
from rag_engine.generation import application_reason
from rag_engine.generation.application_reason import (
    APPLICATION_REASON_SYSTEM_PROMPT,
    HIDDEN_REASON,
    VERDICT_LABELS,
    AuditReason,
    display_gate,
    explain_verdicts,
    reason_input_texts,
    reason_inputs,
)
from rag_engine.generation.application_review import review_applications
from rag_engine.generation.applications import APPLICATION_AUDIT_SYSTEM_PROMPT, CheckedApplication
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded
from rag_engine.models.llm import (
    ApplicationAudit,
    ApplicationVerdictAudit,
    GroundedApplication,
    GroundedApplicationReview,
)

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "application_reason_recorded.json").read_text(encoding="utf-8"))
SLOTS = RECORDED["recorded"]


def settings() -> Any:
    return get_settings(environ={}, dotenv_path=None)


def case_inputs(case: str) -> dict[str, Any]:
    return json.loads(RECORDED["cases"][case]["audit_user"])


def case_strings() -> list[str]:
    """題・gold を識別する文字列（質問・原文の各文・結論・推導・引用・資料名・見出し・ID・gold の理由・対象名）。"""
    found: set[str] = set()
    for case in RECORDED["case_inputs"]:
        found.update([case["question"], case["selected_quote"], case["application_reasoning"], case["application_result"]])
        for document in case["documents"]:
            found.update([document["source"], document["section"], document["document_id"], document["chunk_set_id"]])
            found.update(line for line in re.split(r"[\n。]", document["text"]) if len(line) >= 4)
        # 「端末M」「接続A」「版B」の形の対象名。
        for text in [case["question"], *(document["text"] for document in case["documents"])]:
            found.update(re.findall(r"[一-龥ァ-ヶ]+[A-Z0-9]", text))
    for reason in RECORDED["gold_reasons"].values():
        found.update(part for part in re.split(r"[。、]", reason) if len(part) >= 4)
    return sorted(value for value in found if value.strip())


def shipped_texts() -> list[str]:
    """モデルに渡す指示・schema・ラベルと、利用者に示す定型の文。"""
    return [
        APPLICATION_REASON_SYSTEM_PROMPT,
        APPLICATION_AUDIT_SYSTEM_PROMPT,
        json.dumps(AuditReason.model_json_schema(), ensure_ascii=False),
        json.dumps(ApplicationVerdictAudit.model_json_schema(), ensure_ascii=False),
        *VERDICT_LABELS.values(),
        HIDDEN_REASON,
    ]


def test_no_case_or_gold_strings_in_shipped_prompts_or_schema() -> None:
    strings = case_strings()
    assert len(strings) > 50
    for text in shipped_texts():
        hits = [value for value in strings if value in text]
        assert not hits, hits


def test_reason_system_is_the_verified_text() -> None:
    # 実モデルで検証した文面。変えるときは同じ手順で再検証し、録画を更新する。
    assert RECORDED["reason_system"] == APPLICATION_REASON_SYSTEM_PROMPT


def test_verdict_schema_replaces_only_reason_in_place() -> None:
    original = ApplicationAudit.model_json_schema()
    verdict = ApplicationVerdictAudit.model_json_schema()
    old = original["$defs"]["GroundedApplicationReview"]
    new = verdict["$defs"]["ApplicationVerdict"]
    assert list(old["properties"]) == ["index", "valid", "reason"]
    assert list(new["properties"]) == ["index", "valid", "basis_evidence_ids"]
    assert new["required"] == ["index", "valid", "basis_evidence_ids"]
    assert new["properties"]["index"] == old["properties"]["index"]
    assert new["properties"]["valid"] == old["properties"]["valid"]
    assert new["description"] == old["description"]
    assert new["properties"]["basis_evidence_ids"]["items"] == {"type": "string"}
    assert "reason" not in json.dumps(verdict)
    unchanged = ("additionalProperties", "description", "required")
    assert {key: verdict[key] for key in unchanged} == {key: original[key] for key in unchanged}
    assert verdict["properties"]["summary_supported"] == original["properties"]["summary_supported"]
    assert {**verdict["properties"]["application_reviews"], "items": None} == {
        **original["properties"]["application_reviews"],
        "items": None,
    }
    # 生成の段・全体の監査が使う型は変えない。
    assert "reason" in GroundedApplicationReview.model_fields


def test_reason_schema_has_one_reason_field() -> None:
    schema = AuditReason.model_json_schema()
    assert list(schema["properties"]) == ["reason"] and schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("slot", SLOTS, ids=[row["slot"] for row in SLOTS])
def test_recorded_reason_input_is_rebuilt_byte_for_byte_and_shown(slot: dict[str, Any]) -> None:
    inputs = case_inputs(slot["case"])
    audit = ApplicationVerdictAudit.model_validate(slot["stage_a"])
    sent: list[tuple[str, str, Any, str]] = []

    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        sent.append((system, prompt, schema, options.get("provider_id")))
        return AuditReason.model_validate(slot["stage_b"])

    reasons, trace = explain_verdicts(inputs, audit, parse_text=parse, settings=settings(), provider_id=None)
    assert len(sent) == trace["calls"] == 1
    system, prompt, schema, _ = sent[0]
    assert system == APPLICATION_REASON_SYSTEM_PROMPT and schema is AuditReason
    assert prompt == slot["stage_b_user"]
    assert audit.application_reviews[0].valid is RECORDED["cases"][slot["case"]]["gold_valid"]
    assert reasons == {0: slot["stage_b"]["reason"]}
    assert trace["results"][0]["status"] == "shown"


def test_reason_inputs_are_japanese_only_and_cut_by_code() -> None:
    inputs = case_inputs("same-name-other-target")
    payload = reason_inputs(inputs, 0, "invalid", ["evidence-1", "evidence-2"])
    prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    texts = {row["evidence_id"]: row["text"] for document in inputs["documents"] for row in document["evidence"]}
    # 引用の束縛の原文が先、判定に使ったほかの原文が後。本文は入力の逐字。
    assert [row["本文"] for row in payload["原文"]] == [texts["evidence-2"], texts["evidence-1"]]
    assert [row["引用元"] for row in payload["原文"]] == ["はい", "いいえ"]
    assert "evidence-" not in prompt and not re.search(r"U[0-9a-f]{24}", prompt)
    assert all(not re.search(r"[A-Za-z]", key) for key in payload)
    assert not re.search(r"[A-Za-z]", payload["判定"])
    assert inputs["applications"][0]["reasoning"] not in prompt and "reasoning" not in prompt


def test_unknown_basis_id_is_dropped_without_changing_verdict() -> None:
    inputs = case_inputs("source-positive")
    audit = ApplicationVerdictAudit.model_validate(
        {
            "application_reviews": [{"index": 0, "valid": True, "basis_evidence_ids": ["evidence-1", "evidence-9"]}],
            "summary_supported": True,
        }
    )
    prompts: list[str] = []

    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        prompts.append(prompt)
        return AuditReason(reason="選択した資料の条件が質問と同じため、結論を適用できます。")

    reasons, trace = explain_verdicts(inputs, audit, parse_text=parse, settings=settings(), provider_id=None)
    assert trace["unknown_basis_ids"] == {"0": ["evidence-9"]}
    assert json.loads(prompts[0])["判定"] == VERDICT_LABELS["valid"]
    assert len(json.loads(prompts[0])["原文"]) == 1
    assert reasons[0].startswith("選択した資料")


@pytest.mark.parametrize("rows", [[], [{"index": 0, "valid": True, "basis_evidence_ids": []}] * 2], ids=["missing", "duplicate"])
def test_missing_or_duplicate_verdict_sends_no_reason_call(rows: list[dict[str, Any]]) -> None:
    audit = ApplicationVerdictAudit.model_validate({"application_reviews": rows, "summary_supported": True})
    reasons, trace = explain_verdicts(
        case_inputs("source-positive"),
        audit,
        parse_text=lambda *a, **kw: pytest.fail("判定が確定しない推導の理由は作らない"),
        settings=settings(),
        provider_id=None,
    )
    assert reasons == {} and trace["calls"] == 0


@pytest.mark.parametrize(
    ("reason", "shown"),
    [
        ("選択した資料は接続の対象が異なるため、適用できません。", True),
        ("The selected document applies to another target.", False),
        ("", False),
        ("選択した evidence-2 は対象が異なります。", False),
        ("選択したE2は対象が異なります。", False),
        ("資料 manual.pdf の手順です。", True),
        ("資料 Synthetic Operation Guide の手順です。", True),
        ("選択した資料は the other target only なので適用できません。", False),
        ("選択した資料 doc-a:cs-a:chunk-a は対象が異なります。", False),
    ],
    ids=["japanese", "english-only", "empty", "evidence-id", "label-id", "source-name", "source-latin-run", "latin-run", "source-id"],
)
def test_display_gate_is_format_only(reason: str, shown: bool) -> None:
    inputs = ["質問", "manual.pdf\n見出し\nSynthetic Operation Guide の本文"]
    gate = display_gate(reason, inputs, ["doc-a:cs-a:chunk-a"])
    assert (gate["status"] == "shown") is shown


SOURCE = "合成の操作は点検中だけに使う手順です。\n開始を選びます。"
QUESTION = "点検中に開始する操作を教えてください。"


def candidate() -> CheckedApplication:
    item = SimpleNamespace(kind="rule", applies="matched", text="開始を選びます。", quote="開始を選びます。")
    span = {"evidence_id": "evidence-1", "source_id": "s1", "text": SOURCE, "source": "synthetic.txt", "section_path": ["点検"]}
    entry = SimpleNamespace(item=item, span=span, quote_only=False, removed=False, state="")
    return CheckedApplication(
        GroundedApplication(item_indexes=[0], reasoning="点検中の条件に当てはめる。", result="点検中は開始を選びます。"), [entry]
    )


def review(entry: CheckedApplication, parse: Any, config: Any = None) -> dict[str, Any]:
    return review_applications(
        QUESTION,
        [entry],
        evidence_spans=[entry.entries[0].span],
        existing_audit=None,
        audit_requested=False,
        parse_text=parse,
        settings=config or settings(),
        provider_id=None,
    )


def staged(valid: bool, reason: Any) -> Any:
    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        if schema is ApplicationVerdictAudit:
            return schema.model_validate(
                {"application_reviews": [{"index": 0, "valid": valid, "basis_evidence_ids": ["evidence-1"]}], "summary_supported": True}
            )
        assert schema is AuditReason
        if isinstance(reason, BaseException):
            raise reason
        return AuditReason(reason=reason)

    return parse


@pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
def test_hidden_reason_never_changes_the_verdict(valid: bool) -> None:
    entry = candidate()
    trace = review(entry, staged(valid, "The cited step applies only during inspection."))
    assert trace["calls"] == 2 and trace["status"] == "completed"
    assert entry.assessment == {"status": "valid" if valid else "invalid", "reason": HIDDEN_REASON}
    assert trace["reason_step"]["results"][0]["why"] == "no_kana"
    assert entry.published is valid
    if not valid:
        assert entry.reasons == [f"監査: {HIDDEN_REASON}"]


def test_shown_reason_is_used_for_the_rejection() -> None:
    entry = candidate()
    reason = "原文は点検中だけに使う手順で、結論はその条件の外の操作を述べています。"
    review(entry, staged(False, reason))
    assert entry.assessment == {"status": "invalid", "reason": reason}
    assert entry.reasons == [f"監査: {reason}"]


def test_reason_step_failure_keeps_the_verdict() -> None:
    entry = candidate()
    trace = review(entry, staged(True, TimeoutError("synthetic")))
    assert trace["calls"] == 2 and trace["status"] == "completed"
    assert entry.published and entry.assessment == {"status": "valid", "reason": HIDDEN_REASON}
    assert trace["reason_step"]["results"][0]["error_type"] == "TimeoutError"


def test_reason_step_authorization_failure_is_not_converted() -> None:
    with pytest.raises(PermissionError):
        review(candidate(), staged(True, PermissionError("synthetic")))


def test_reason_step_over_budget_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def over(*args: Any, **kwargs: Any) -> None:
        raise GenerationInputBudgetExceeded({"fits": False})

    monkeypatch.setattr(application_reason, "ensure_model_input", over)
    entry = candidate()
    calls: list[Any] = []

    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        calls.append(schema)
        return staged(False, "")(system, prompt, config, schema)

    trace = review(entry, parse)
    assert calls == [ApplicationVerdictAudit]
    assert trace["calls"] == 1 and entry.assessment == {"status": "invalid", "reason": HIDDEN_REASON}
    assert trace["reason_step"]["results"][0]["why"] == "input_budget_exceeded"


def test_unknown_verdict_sends_no_reason_call() -> None:
    entry = candidate()
    calls: list[Any] = []

    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        calls.append(schema)
        return schema.model_validate({"application_reviews": [], "summary_supported": True})

    trace = review(entry, parse)
    assert calls == [ApplicationVerdictAudit] and trace["calls"] == 1
    assert entry.assessment["status"] == "unknown" and not entry.published


def test_saved_reason_instruction_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.resources.runtime import bind_prompt_overrides

    custom = "## Step 1: 合成の保存済み指示\n理由を書く。"
    systems: list[str] = []

    def parse(system: str, prompt: str, config: Any, schema: Any, **options: Any) -> Any:
        systems.append(system)
        return staged(True, "点検中の手順なので適用できます。")(system, prompt, config, schema)

    with bind_prompt_overrides({"application.reasonSystem": custom}):
        review(candidate(), parse)
    assert systems[-1] == custom


def mock_client(monkeypatch: pytest.MonkeyPatch, raw: str, sent: list[dict[str, Any]]) -> OpenAI:
    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        data, headers = response_bytes(raw, False)
        return httpx.Response(200, content=data, headers=headers)

    client = OpenAI(
        api_key="mock-only",
        base_url="https://mock.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_args: client)
    monkeypatch.setattr(oci, "_stall_detection_enabled", lambda _settings: False)
    return client


def sdk_settings() -> Any:
    return get_settings(
        environ={
            "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "synthetic",
            "OCI_ENTERPRISE_AI_ENDPOINT": "https://mock.invalid/v1",
            "OCI_ENTERPRISE_AI_API_KEY": "mock-only",
        },
        dotenv_path=None,
        llm_stall_resends=0,
    )


def test_sdk_wire_of_both_stages_matches_the_recorded_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """実 SDK の送信: 判定の段は既存の監査の system と入力のまま、理由の段は録画と同じ system・user。"""
    slot = next(row for row in SLOTS if row["case"] == "same-name-other-target")
    inputs = case_inputs(slot["case"])
    config = sdk_settings()
    sent: list[dict[str, Any]] = []
    client = mock_client(monkeypatch, json.dumps(slot["stage_a"], ensure_ascii=False), sent)
    try:
        audit = oci.parse_text_response(
            APPLICATION_AUDIT_SYSTEM_PROMPT, RECORDED["cases"][slot["case"]]["audit_user"], config, ApplicationVerdictAudit
        )
    finally:
        client.close()
    assert [message["content"] for message in sent[0]["input"]] == [
        APPLICATION_AUDIT_SYSTEM_PROMPT,
        RECORDED["cases"][slot["case"]]["audit_user"],
    ]
    wire = sent[0]["text"]["format"]
    assert wire["strict"] is True and wire["name"] == "ApplicationVerdictAudit"
    assert "reason" not in json.dumps(wire["schema"])
    client = mock_client(monkeypatch, json.dumps(slot["stage_b"], ensure_ascii=False), sent)
    try:
        reasons, trace = explain_verdicts(inputs, audit, parse_text=oci.parse_text_response, settings=config, provider_id=None)
    finally:
        client.close()
    assert [message["content"] for message in sent[1]["input"]] == [RECORDED["reason_system"], slot["stage_b_user"]]
    assert sent[1]["text"]["format"]["strict"] is True and sent[1]["text"]["format"]["name"] == "AuditReason"
    assert list(sent[1]["text"]["format"]["schema"]["properties"]) == ["reason"]
    assert reasons == {0: slot["stage_b"]["reason"]} and trace["calls"] == 1
    assert reason_input_texts(json.loads(slot["stage_b_user"]))[0] == inputs["question"]


def test_budget_settings_still_reject_oversized_reason_input() -> None:
    inputs = case_inputs("source-positive")
    audit = ApplicationVerdictAudit.model_validate(
        {"application_reviews": [{"index": 0, "valid": True, "basis_evidence_ids": ["evidence-1"]}], "summary_supported": True}
    )
    config = replace(settings(), answer_max_input_tokens=1)
    reasons, trace = explain_verdicts(
        inputs, audit, parse_text=lambda *a, **kw: pytest.fail("予算超過では送信しない"), settings=config, provider_id=None
    )
    assert trace["calls"] == 0 and reasons == {0: HIDDEN_REASON}
    assert trace["results"][0]["why"] == "input_budget_exceeded"
