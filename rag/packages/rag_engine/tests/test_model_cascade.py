"""回答のモデルのカスケード（#4581）を、モデルを差し替えた合成の入出力で確かめる。

1 つ目のモデル（provider ``enterprise-ai``）と 2 つ目のモデル（``enterprise-ai-escalation``）に別の応答を返させ、
機械で判定できる確認の不合格のときだけ、検索の結果をそのまま使って 2 つ目のモデルで生成の段をやり直すことを確かめる。
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_grounded_answer import LIST_TEXT, FakeModel, context, draft, ids, item, marks, record

from rag_engine.adapters.oci import InvalidStructuredOutput
from rag_engine.config import ENTERPRISE_AI_LLM_PROVIDER, ESCALATION_LLM_PROVIDER, get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded, model_cascade
from rag_engine.generation.answering import synthesize_with_model_cascade
from rag_engine.generation.citation_range_repair import CitationRangeRepair
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.generation.execution_record import _structured_steps
from rag_engine.models.llm import GroundedDraft

QUESTION = "年次集計の明細一覧表を出力する手順を教えてください"
STEP = "一覧表を選択し、処理を選択します。"
INVENTED = "一覧表を選択すると自動で集計が完了します。"


def _settings(*, escalation: bool = True, mode: str = grounded.AUDIT_MODE_NONE, **overrides):
    environ = {"OCI_ENTERPRISE_AI_DEFAULT_MODEL": "synthetic-light"}
    if escalation:
        environ.update({"RAG_ENGINE_ESCALATION_MODEL": "synthetic-strong", "RAG_ENGINE_ESCALATION_MAX_INPUT_TOKENS": "200000"})
    return replace(get_settings(environ=environ, dotenv_path=None), answer_audit_mode=mode, **overrides)


class ByProvider:
    """provider ごとに別の FakeModel へ振り分け、呼ばれた (provider, schema) を順に記録する。"""

    def __init__(self, **models):
        self.models = models
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system, prompt, settings, schema, **options):
        provider = options.get("provider_id") or settings.default_answer_llm
        self.calls.append((provider, schema.__name__))
        if issubclass(schema, CitationRangeRepair):
            return schema.model_validate({"ranges": []})
        return self.models[provider](system, prompt, settings, schema, **options)

    def generations(self, provider: str) -> int:
        draft_names = {generation_draft_wire_model().__name__, GroundedDraft.__name__}
        return sum(1 for called, name in self.calls if called == provider and (name in draft_names or name.startswith("GroundedDraft")))


def _run(model, ctx, settings):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_with_model_cascade(QUESTION, ctx, settings, image_prompt_mode="text_only").response


def _ctx():
    return context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))


def _good(ctx):
    eid = ids(ctx)[LIST_TEXT]
    return FakeModel([draft(item(eid, STEP, STEP))], [{}])


def _invented(ctx):
    eid = ids(ctx)[LIST_TEXT]
    return FakeModel([draft(item(eid, INVENTED, INVENTED))], [{}])


def test_citation_mismatch_regenerates_with_the_second_model_and_keeps_retrieval():
    ctx = _ctx()
    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: _invented(ctx), ESCALATION_LLM_PROVIDER: _good(ctx)})

    result = _run(model, ctx, _settings())

    assert result.generation_trace["model_cascade"] == {
        "primary_model": "synthetic-light",
        "escalation_model": "synthetic-strong",
        "escalated": True,
        "reasons": ["citation_mismatch"],
        "stage": "answer.generate",
    }
    # 1 つ目のモデルは生成を 1 回だけ（修正の回を 1 つ目のモデルで続けない）。2 つ目のモデルの回答を公開する。
    assert model.generations(ENTERPRISE_AI_LLM_PROVIDER) == 1
    assert model.generations(ESCALATION_LLM_PROVIDER) == 1
    assert STEP in result.answer_text and INVENTED not in result.answer_text
    assert STEP not in marks(result)
    # 昇格の後の呼び出しはすべて 2 つ目のモデル（戻さない）。
    first_escalated = next(index for index, (provider, _name) in enumerate(model.calls) if provider == ESCALATION_LLM_PROVIDER)
    assert {provider for provider, _name in model.calls[first_escalated:]} == {ESCALATION_LLM_PROVIDER}
    # 2 つ目のモデルの回答には信号が残らない。
    assert result.generation_trace["escalation_signals"] == []


def test_structured_output_invalid_after_retry_escalates():
    ctx = _ctx()

    def broken(system, prompt, settings, schema, **options):
        raise InvalidStructuredOutput("synthetic")

    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: broken, ESCALATION_LLM_PROVIDER: _good(ctx)})
    steps: list[dict] = []
    token = _structured_steps.set(steps)
    try:
        result = _run(model, ctx, _settings())
    finally:
        _structured_steps.reset(token)

    # 1 つ目のモデルの工程は「資料の記載だけを示す」ではなく、回答し直すことを示す。
    first = json.dumps(next(step for step in steps if str(step.get("name", "")).startswith("回答文の生成と根拠確認")), ensure_ascii=False)
    assert "確認を通らなかったときのモデルで回答し直します" in first
    assert "資料の記載だけを示します" not in first
    assert [step.get("name") for step in steps].count("高性能モデルで回答し直す") == 1

    assert result.generation_trace["model_cascade"]["escalated"] is True
    assert result.generation_trace["model_cascade"]["reasons"] == ["structured_output_invalid"]
    assert "generation_failure" not in result.generation_trace
    assert STEP in result.answer_text


def test_passing_answer_does_not_escalate_and_records_the_cascade():
    ctx = _ctx()
    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: _good(ctx), ESCALATION_LLM_PROVIDER: _invented(ctx)})

    result = _run(model, ctx, _settings())

    assert result.generation_trace["model_cascade"] == {
        "primary_model": "synthetic-light",
        "escalation_model": "synthetic-strong",
        "escalated": False,
        "reasons": [],
    }
    assert model.generations(ESCALATION_LLM_PROVIDER) == 0
    assert {provider for provider, _name in model.calls} == {ENTERPRISE_AI_LLM_PROVIDER}


def test_gap_only_answer_is_not_a_signal():
    """資料に記載が無い（根拠が足りない）回答は、モデルを変えても直らないので昇格しない。"""
    ctx = _ctx()
    gap = FakeModel([draft({"kind": "gap", "request_id": "Q1", "text": "資料に記載がありません。"}, summary="")], [{}])
    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: gap, ESCALATION_LLM_PROVIDER: _good(ctx)})

    result = _run(model, ctx, _settings())

    assert result.generation_trace["model_cascade"]["escalated"] is False
    assert model.generations(ESCALATION_LLM_PROVIDER) == 0


def test_transport_failure_is_not_escalated():
    """接続・429・5xx の失敗は品質の昇格に混ぜない（既存の失敗の扱いのまま送出する）。"""
    ctx = _ctx()

    def unavailable(system, prompt, settings, schema, **options):
        raise ConnectionError("synthetic transport failure")

    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: unavailable, ESCALATION_LLM_PROVIDER: _good(ctx)})

    with pytest.raises(ConnectionError):
        _run(model, ctx, _settings())
    assert model.generations(ESCALATION_LLM_PROVIDER) == 0


def test_second_model_failure_keeps_existing_failure_handling_and_escalates_once():
    ctx = _ctx()
    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: _invented(ctx), ESCALATION_LLM_PROVIDER: _invented(ctx)})

    result = _run(model, ctx, _settings())

    assert result.generation_trace["model_cascade"]["escalated"] is True
    # 2 つ目のモデルも通らなければ、既存の扱い（印を付けて残す）で返し、3 回目の昇格はしない。
    assert marks(result)[INVENTED] == ["citation_range_out_of_bounds"]
    assert result.generation_trace["escalation_signals"] == ["citation_mismatch"]
    assert model.generations(ENTERPRISE_AI_LLM_PROVIDER) == 1
    first_escalated = next(index for index, (provider, _name) in enumerate(model.calls) if provider == ESCALATION_LLM_PROVIDER)
    assert {provider for provider, _name in model.calls[first_escalated:]} == {ESCALATION_LLM_PROVIDER}


def test_without_escalation_model_the_answer_is_unchanged():
    ctx = _ctx()
    model = ByProvider(**{ENTERPRISE_AI_LLM_PROVIDER: _invented(ctx)})

    result = _run(model, ctx, _settings(escalation=False))

    assert "model_cascade" not in result.generation_trace
    assert result.generation_trace["escalation_signals"] == ["citation_mismatch"]
    assert {provider for provider, _name in model.calls} == {ENTERPRISE_AI_LLM_PROVIDER}


def test_escalated_settings_use_the_second_model_limits():
    settings = _settings()
    escalated = model_cascade.escalated_settings(settings)
    assert escalated.default_answer_llm == ESCALATION_LLM_PROVIDER
    assert escalated.answer_max_input_tokens == 200000
    assert escalated.escalation_llm == ""
    assert model_cascade.answer_cascade(escalated, None).enabled is False
    # 同じモデルを 2 つ目に選んでも使わない。
    same = _settings(escalation=False)
    assert model_cascade.answer_cascade(replace(same, escalation_llm=ENTERPRISE_AI_LLM_PROVIDER), None).enabled is False


def _round(**values):
    defaults = {"checked": [], "dropped": [], "applications": [], "summary_reason": "", "timing": {}}
    return SimpleNamespace(**{**defaults, **values})


def _entry(**values):
    defaults = {"also": [], "citation_resolution": {}, "unmatched": False, "removed": False, "withdrawn": False, "table_conflict": ""}
    return SimpleNamespace(**{**defaults, **values})


@pytest.mark.parametrize(
    ("current", "mode", "expected"),
    [
        (_round(applications=[SimpleNamespace(arithmetic_issues=["1+1=3"])]), "none", ("arithmetic_mismatch",)),
        (_round(checked=[_entry(unmatched=True)]), "none", ("citation_mismatch",)),
        (
            _round(dropped=[{"citation_resolution": {"reason_code": "citation_range_invalid"}}]),
            "none",
            ("citation_mismatch",),
        ),
        (_round(summary_reason="要求の根拠計画にない説明を除外したため、要約の結論を保留"), "none", ("conclusion_withheld",)),
        # 「資料と矛盾」は監査が有効なときだけ。表の対応の取り違えは決定的な検査なので数えない。
        (_round(checked=[_entry(removed=True, withdrawn=True)]), "detailed", ("audit_contradicted",)),
        (_round(checked=[_entry(removed=True, withdrawn=True)]), "none", ()),
        (_round(checked=[_entry(removed=True, table_conflict="A は B")]), "detailed", ()),
        # 未確認・警告の印だけの段落は信号にしない。
        (_round(checked=[_entry(marks=[{"code": "unsupported"}])]), "detailed", ()),
    ],
    ids=[
        "arithmetic",
        "unmatched",
        "range",
        "scope-withheld",
        "contradicted",
        "contradicted-without-audit",
        "table-conflict",
        "marks-only",
    ],
)
def test_round_signals(current, mode, expected):
    assert model_cascade.round_signals(current, audit_mode=mode) == expected
