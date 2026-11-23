"""送信の前の別の要求で済ませた工程（登録済みQ&Aの候補・確認の候補）を、送信の回答の記録に残す
（#5776）。"""

from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    observed_span,
    operation_trace,
    shutdown_tracing,
)
from pr_system_settings.guardrails import GuardrailResult

from app.config import Settings
from app.rag import approved_faq_answer
from app.rag.chat_question_token import (
    APPROVED_FAQ_CANDIDATES_STAGE,
    CLARIFICATION_CANDIDATES_STAGE,
    _key,
    add_prepared_stage,
    issue_question_token,
    read_history_intent,
    read_prepared_stages,
    read_question_token,
)
from app.rag.model_call_record import collect_model_calls
from app.rag.pipeline import _record_prepared_stage
from app.rag.signed_url import verify_signed_token

SECRET = "synthetic-signing-key-" * 3
BINDING = {"owner": "owner-a", "profile": "profile-a"}


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with call_content_policy(lambda: True):
            yield
    finally:
        shutdown_tracing()


def _embedding_call() -> None:
    with observed_span(
        "gen_ai.embeddings", kind="client", attributes={"pr.gen_ai.purpose": "query.embed"}
    ) as span:
        span.set_call_input({"input": "合成の質問"}, restricted=True)


def test_collects_embeddings_and_rerank_calls() -> None:
    with collect_model_calls() as calls:
        _embedding_call()
        with observed_span("gen_ai.rerank", kind="client"):
            pass
    assert [call.operation_name for call in calls] == ["gen_ai.embeddings", "gen_ai.rerank"]


def test_added_stage_keeps_the_question_intent_and_expiry() -> None:
    token = issue_question_token(SECRET, BINDING, "対象 A は？", history_intent="greeting")
    with collect_model_calls() as calls:
        _embedding_call()

    added = add_prepared_stage(SECRET, token, APPROVED_FAQ_CANDIDATES_STAGE, (10, 20), calls)
    added = add_prepared_stage(SECRET, added, CLARIFICATION_CANDIDATES_STAGE, (30, 40), ())
    added = add_prepared_stage(SECRET, added, "unknown_stage", (50, 60), ())

    assert read_question_token(SECRET, added, BINDING) == "対象 A は？"
    assert read_history_intent(SECRET, added) == "greeting"
    before = verify_signed_token(_key(SECRET), token, version="chat-question-v1")
    after = verify_signed_token(_key(SECRET), added, version="chat-question-v1")
    assert after["exp"] <= before["exp"]
    # 呼び出しは暗号化して入れる（入力の詳細を平文で出さない）。
    assert "合成の質問" not in added and "query.embed" not in added
    faq, clarification = read_prepared_stages(SECRET, added)
    assert (faq.stage, faq.window) == (APPROVED_FAQ_CANDIDATES_STAGE, (10, 20))
    assert [call.attributes["pr.gen_ai.purpose"] for call in faq.calls] == ["query.embed"]
    assert (clarification.stage, clarification.window, clarification.calls) == (
        CLARIFICATION_CANDIDATES_STAGE,
        (30, 40),
        (),
    )


def test_calls_that_do_not_fit_leave_only_the_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag import chat_question_token

    token = issue_question_token(SECRET, BINDING, "対象 A は？")
    with collect_model_calls() as calls:
        _embedding_call()
    monkeypatch.setattr(chat_question_token, "_TOKEN_MAX_CHARS", len(token) + 10)
    [stage] = read_prepared_stages(
        SECRET, add_prepared_stage(SECRET, token, CLARIFICATION_CANDIDATES_STAGE, (1, 2), calls)
    )
    assert stage.window == (1, 2) and stage.calls == ()


async def test_faq_candidates_put_the_matching_calls_into_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def loaded(*args: Any) -> tuple[dict[str, Any], list[Any]]:
        return {}, [object()]

    async def checked(text: str, settings: Any) -> GuardrailResult:
        return GuardrailResult(allowed=True, sanitized_text=text, findings=[])

    async def prepare(question: str, result: GuardrailResult) -> tuple[str, str | None]:
        return question, issue_question_token(SECRET, BINDING, question)

    async def suggest(*args: Any, **kwargs: Any) -> list[Any]:
        _embedding_call()
        return []

    monkeypatch.setattr(approved_faq_answer, "load_approved_faq", loaded)
    monkeypatch.setattr(approved_faq_answer, "approved_faq_enabled", lambda payload: True)
    monkeypatch.setattr(approved_faq_answer, "check_question_text", checked)
    monkeypatch.setattr(approved_faq_answer, "suggest_approved_faq", suggest)
    settings = Settings(_env_file=None, app_service_token_secret=SECRET)

    _, token = await approved_faq_answer.approved_faq_candidates(
        cast(Any, None), "profile-a", "対象 A は？", settings, prepare=prepare
    )

    assert token is not None
    [stage] = read_prepared_stages(SECRET, token)
    assert stage.stage == APPROVED_FAQ_CANDIDATES_STAGE
    assert stage.window[0] <= stage.calls[0].started_at.timestamp() * 1e9
    assert [call.operation_name for call in stage.calls] == ["gen_ai.embeddings"]


def test_prepared_stage_is_recorded_at_its_real_time_with_its_calls() -> None:
    token = issue_question_token(SECRET, BINDING, "対象 A は？")
    with collect_model_calls() as calls:
        _embedding_call()
    window = (
        int(calls[0].started_at.timestamp() * 1e9) - 1_000_000,
        int(calls[0].finished_at.timestamp() * 1e9) + 1_000_000,  # type: ignore[union-attr]
    )
    [prepared] = read_prepared_stages(
        SECRET, add_prepared_stage(SECRET, token, CLARIFICATION_CANDIDATES_STAGE, window, calls)
    )

    with operation_trace("rag.answer", operation_id="synthetic") as summary:
        _record_prepared_stage(prepared)

    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    stage = next(
        item for item in saved.spans if item.operation_name == "rag.clarification_candidates"
    )
    child = next(item for item in saved.spans if item.operation_name == "gen_ai.embeddings")
    assert stage.attributes["pr.stage"] == CLARIFICATION_CANDIDATES_STAGE
    assert stage.display is not None and stage.display.kind == CLARIFICATION_CANDIDATES_STAGE
    # datetime はマイクロ秒まで（float の誤差も見込む）。
    assert abs(stage.started_at.timestamp() * 1e9 - window[0]) < 2_000
    assert child.parent_span_id == stage.span_id
    assert child.started_at == calls[0].started_at
