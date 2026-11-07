"""表の行の判定を、送った表の投影（grounded）と推導の段・推導の審査（applications / application_review）へ通す（#4349）。

LLM は合成の題の録画（xai.grok-4.7・low）と決定論の stub だけ。実モデル・ネットワークは使わない。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from application_verdict_stub import audit_reply, is_application_schema

from rag_engine.config import get_settings
from rag_engine.generation import applications, grounded
from rag_engine.generation import row_conditions as rc
from rag_engine.generation.application_review import application_documents, review_applications
from rag_engine.generation.applications import TABLE_UNDETERMINED_REASON, CheckedApplication
from rag_engine.models.llm import GroundedApplication

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "row_conditions_recorded.json").read_text(encoding="utf-8"))


def recorded_outputs(round_name: str, case_id: str) -> list[str]:
    rows = [row for row in FIXTURE["recorded"] if (row["round"], row["case_id"]) == (round_name, case_id)]
    return [row["output_text"] for call in ("type", "judge") for row in rows if row["call"] == call]


def settings() -> Any:
    return get_settings(
        environ={
            "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "answer-model",
            "OCI_ENTERPRISE_AI_ROW_CONDITION_MODEL": "xai.grok-4.7",
            "OCI_ENTERPRISE_AI_ROW_CONDITION_REASONING_EFFORT": "low",
            "RAG_ENGINE_ROW_CONDITION_REASONING_EFFORT": "low",
        },
        dotenv_path=None,
    )


def table_span(case_id: str) -> dict[str, Any]:
    if FIXTURE["cases"][case_id]["evidence_text"] is None:
        span = dict(FIXTURE["catalog_snapshots"][0]["span"])
    else:
        source = FIXTURE["cases"][case_id]["source"]
        span = {
            "source_id": source,
            "evidence_id": "E-" + source,
            "source": source + ".txt",
            "text": FIXTURE["cases"][case_id]["evidence_text"],
            "section_path": ["合成資料", "値と規則"],
            "document_scope": [source, "v1"],
            "label": "E1",
            "page": 1,
        }
    return span


@pytest.fixture
def sent_spans() -> Any:
    """生成が記録する送達の版（present_in_prompt=True）を、テストの span に付ける。"""
    token = grounded._CITATION_VIEWS.set({})

    def mark(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        views = dict(grounded._CITATION_VIEWS.get() or {})
        for span in spans:
            views[id(span)] = (span, replace(grounded._citation_view(span, explicit_units=True), present_in_prompt=True))
        grounded._CITATION_VIEWS.set(views)
        return spans

    yield mark
    grounded._CITATION_VIEWS.reset(token)


class Replay:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def __call__(self, system: str, user: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        self.calls += 1
        return schema.model_validate_json(self.outputs.pop(0))


def row_result(case_id: str, round_name: str, spans: list[dict[str, Any]]) -> rc.RowConditionsResult:
    tables, skipped = grounded.sent_table_views(spans)
    return rc.evaluate_tables(
        FIXTURE["cases"][case_id]["question"],
        tables,
        skipped,
        parse_text=Replay(recorded_outputs(round_name, case_id)),
        settings=settings(),
    )


def entry_for(span: dict[str, Any]) -> CheckedApplication:
    quote = span["text"].splitlines()[1]
    item = SimpleNamespace(
        item=SimpleNamespace(kind="rule", applies="matched", text=quote, quote=quote, condition=""),
        span=span,
        quote_only=False,
        removed=False,
        state="",
    )
    return CheckedApplication(
        GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="合成の当てはめ。", result="合成の結論。"), [item]
    )


# ---------------------------------------------------------------- grounded: 送った表だけを投影する


def test_sent_table_views_projects_only_the_sent_snapshot(sent_spans: Any) -> None:
    sent = sent_spans([table_span("reg-period-reversed")])
    tables, skipped = grounded.sent_table_views(sent)
    assert len(tables) == 1 and not skipped
    table = tables[0]
    text = sent[0]["text"]
    for alias in table["aliases"].values():
        assert text[alias["start"] : alias["end"]] == alias["raw"]
    assert table["header"] == "not_recorded"
    # 送達の記録が無い span（今回の入力に無い）は投影しない。
    assert grounded.sent_table_views([table_span("reg-period-reversed")]) == ([], [])


def test_recorded_rows_through_grounded_projection(sent_spans: Any) -> None:
    result = row_result("reg-period-reversed", "reg", sent_spans([table_span("reg-period-reversed")]))
    assert result.status == "completed" and result.undetermined() == [] and result.calls == 2
    rows = {row["row_id"]: row["applicability"] for row in result.tables[0].application["rows"]}  # type: ignore[index]
    assert rows == {"R1": "no_condition", "R2": "applies", "R3": "does_not_apply"}


# ---------------------------------------------------------------- application_review: 決められない表は公開しない


def test_undetermined_table_withholds_derivations_without_audit_call(sent_spans: Any) -> None:
    spans = sent_spans([table_span("rows-unknown"), dict(FIXTURE["catalog_snapshots"][1]["span"])])
    result = row_result("rows-unknown", "reg", spans)
    assert result.undetermined() == [spans[0]["evidence_id"]]
    entry = entry_for(spans[0])
    trace = review_applications(
        FIXTURE["cases"]["rows-unknown"]["question"],
        [entry],
        evidence_spans=spans,
        existing_audit=None,
        audit_requested=False,
        parse_text=lambda *a, **kw: pytest.fail("決められない表の推導は監査に送らない"),
        settings=settings(),
        provider_id=None,
        row_conditions=result,
    )
    assert trace["status"] == "withheld_table_not_determined" and trace["calls"] == 0
    assert not entry.published and entry.reasons == [TABLE_UNDETERMINED_REASON]
    assert entry.assessment == {"status": "unknown", "reason": TABLE_UNDETERMINED_REASON}


def test_determined_table_judgments_reach_the_audit_documents(sent_spans: Any) -> None:
    spans = sent_spans([table_span("reg-period-reversed")])
    result = row_result("reg-period-reversed", "reg", spans)
    entry = entry_for(spans[0])
    seen: list[dict[str, Any]] = []

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        assert is_application_schema(schema)
        if "documents" in prompt:
            seen.append(json.loads(prompt))
        return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt)

    trace = review_applications(
        FIXTURE["cases"]["reg-period-reversed"]["question"],
        [entry],
        evidence_spans=spans,
        existing_audit=None,
        audit_requested=False,
        parse_text=parse,
        settings=settings(),
        provider_id=None,
        row_conditions=result,
    )
    assert trace["status"] == "completed" and entry.published
    judgments = seen[0]["documents"][0]["evidence"][0]["table_row_judgments"]
    assert judgments["table_result"] == "determined"
    assert [row["applicability"] for row in judgments["rows"]] == ["no_condition", "applies", "does_not_apply"]
    assert judgments["rows"][1]["condition"] in spans[0]["text"]
    # 行の判定が無ければ documents は変わらない（既存の推導の審査の入力のまま）。
    assert "table_row_judgments" not in json.dumps(application_documents(spans), ensure_ascii=False)


# ---------------------------------------------------------------- applications: 当てはめの段へ事実として渡す


def checked_item(span: dict[str, Any]) -> Any:
    quote = span["text"].splitlines()[2]
    return SimpleNamespace(
        item=SimpleNamespace(kind="rule", applies="matched", text=quote, quote=quote, condition=""),
        span=span,
        quote_only=False,
        removed=False,
        state="",
    )


def test_application_step_does_not_draft_when_a_table_is_not_determined(sent_spans: Any) -> None:
    spans = sent_spans([table_span("rows-unknown")])
    result = row_result("rows-unknown", "reg", spans)
    published, summary = applications.run_application_step(
        FIXTURE["cases"]["rows-unknown"]["question"],
        [checked_item(spans[0])],
        [{"id": "Q1", "kind": "request", "text": "合計"}],
        evidence_spans=spans,
        parse_text=lambda *a, **kw: pytest.fail("決められない表があれば推導を作らない"),
        settings=settings(),
        audit_settings=settings(),
        provider_id=None,
        render_prompt=lambda inputs: json.dumps(inputs, ensure_ascii=False),
        row_conditions=result,
    )
    assert published == [] and summary == ""


def test_application_step_receives_row_judgments_as_facts(sent_spans: Any) -> None:
    spans = sent_spans([table_span("reg-period-reversed")])
    result = row_result("reg-period-reversed", "reg", spans)
    prompts: list[dict[str, Any]] = []

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        data = json.loads(prompt)
        prompts.append(data)
        if is_application_schema(schema):
            return audit_reply(schema, [{"index": 0, "valid": True}], summary_supported=True, prompt=prompt)
        return SimpleNamespace(
            applications=[GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="合成の当てはめ。", result="合成の結論。")],
            summary="合成の結論。",
        )

    published, summary = applications.run_application_step(
        FIXTURE["cases"]["reg-period-reversed"]["question"],
        [checked_item(spans[0])],
        [{"id": "Q1", "kind": "request", "text": "割合"}],
        evidence_spans=spans,
        parse_text=parse,
        settings=settings(),
        audit_settings=settings(),
        provider_id=None,
        render_prompt=lambda inputs: json.dumps(inputs, ensure_ascii=False),
        row_conditions=result,
    )
    draft_input, audit_input = prompts
    assert draft_input["table_rows"] == result.facts()
    assert "table_rows" not in audit_input
    assert audit_input["documents"][0]["evidence"][0]["table_row_judgments"] == result.facts()[0]
    assert [entry.published for entry in published] == [True] and summary == "合成の結論。"


def test_step_prompt_names_the_fact_field() -> None:
    assert "table_rows" in applications.APPLICATION_SYSTEM_PROMPT


# ---------------------------------------------------------------- answering: 段の記録とモデルの設定


def test_answering_step_records_unavailable_model_without_calls(sent_spans: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.generation import answering

    spans = sent_spans([table_span("reg-period-reversed")])
    monkeypatch.setattr(answering, "parse_text_response", lambda *a, **kw: pytest.fail("モデルが使えなければ呼ばない"))
    unavailable = replace(settings(), row_condition_unavailable="モデル「xai.grok-4.7」は登録されていません。")
    result = answering._row_condition_step(
        FIXTURE["cases"]["reg-period-reversed"]["question"], spans, [checked_item(spans[0])], settings=unavailable
    )
    assert result is not None and result.status == "model_unavailable" and result.undetermined() == []


def test_answering_step_uses_the_step_model(sent_spans: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.generation import answering

    spans = sent_spans([table_span("reg-period-reversed")])
    replay = Replay(recorded_outputs("reg", "reg-period-reversed"))
    providers: list[str] = []

    def parse(system: str, user: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        providers.append(call_settings.llm_providers[options["provider_id"]].model)
        return replay(system, user, call_settings, schema)

    monkeypatch.setattr(answering, "parse_text_response", parse)
    result = answering._row_condition_step(
        FIXTURE["cases"]["reg-period-reversed"]["question"], spans, [checked_item(spans[0])], settings=settings()
    )
    assert result is not None and result.status == "completed" and providers == ["xai.grok-4.7", "xai.grok-4.7"]
    # 表の無い根拠だけなら段を開かない。
    assert answering._row_condition_step("質問。", spans, [], settings=settings()) is None
