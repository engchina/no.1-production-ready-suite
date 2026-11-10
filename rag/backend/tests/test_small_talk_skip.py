"""検索の要らない発話（挨拶・雑談）は、補完と同じ呼び出しで分けて検索を飛ばす（#4787）。"""

import json
from time import perf_counter
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome, AnswerScope
from app.rag.chat_question_token import (
    issue_question_token,
    read_history_intent,
    with_rule_selection_answers,
)
from app.rag.guardrails import GuardrailResult
from app.rag.history_question import HistoryRewrite
from app.rag.pipeline import ChatTurn, PreparedHistoryRewrite, RagPipeline
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi, SavingOracle

SECRET = "synthetic-signing-key-" * 3


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


class FakeLlm:
    """補完と応答の構造化出力を、schema の名前で返し分ける決定論のスタブ。"""

    def __init__(self, intent: str = "greeting", reply: str | Exception = "合成の応答です。"):
        self.intent = intent
        self.reply = reply
        self.calls: list[str] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        name = kwargs["response_schema_name"]
        self.calls.append(name)
        if name == "history_rewrite":
            return json.dumps({"question": prompt, "confirmed_fact_ids": [], "intent": self.intent})
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps({"reply": self.reply}, ensure_ascii=False)


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    llm: FakeLlm,
    *,
    settings: Settings | None = None,
    prepared: PreparedHistoryRewrite | None = None,
    scope: AnswerScope | None = None,
    approved_faq: tuple[str, str] | None = None,
) -> tuple[Any, TraceSummary, list[str]]:
    engine_queries: list[str] = []

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        engine_queries.append(request.query)
        return AnswerOutcome(
            answer="合成の根拠つき回答", citations=[], context_text="", diagnostics={}
        )

    async def fields(self: Any, request: Any, **kwargs: Any) -> list[Any]:
        engine_queries.append("field_filter")
        return []

    monkeypatch.setattr(AnswerEngine, "run", answer)
    monkeypatch.setattr(RagPipeline, "_read_field_conditions", fields)
    pipeline = RagPipeline(
        settings=settings
        or Settings(
            _env_file=None, rag_history_rewrite_enabled=True, rag_auto_field_filter_enabled=True
        ),
        oracle=cast(Any, SavingOracle()),
        genai=cast(Any, FakeGenAi()),
        llm=cast(Any, llm),
        history_question_prepared=prepared is not None,
        prepared_history_rewrite=prepared,
        scope=scope,
        approved_faq=approved_faq,
    )
    request = SearchRequest(query="合成の挨拶", search_answer_profile_id="profile-a")
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        response = await pipeline._run_engine(
            request,
            trace_id="synthetic",
            started_at=perf_counter(),
            query_guardrail=GuardrailResult(
                allowed=True, sanitized_text=request.query, findings=[]
            ),
            token_callback=None,
            history=[
                ChatTurn(role="USER", content="合成の質問 A"),
                ChatTurn(role="ASSISTANT", content="合成の回答 A"),
            ],
        )
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    return response, saved, engine_queries


def _stage(summary: TraceSummary, name: str) -> Any:
    return next((s for s in summary.spans if s.attributes.get("pr.stage") == name), None)


@pytest.mark.parametrize("intent", ["greeting", "small_talk"])
async def test_small_talk_skips_retrieval_and_replies_without_citations(
    monkeypatch: pytest.MonkeyPatch, intent: str
) -> None:
    llm = FakeLlm(intent=intent)
    response, summary, engine_queries = await _run(monkeypatch, llm)
    # 補完 1 回と応答 1 回だけ。検索・条件の読み取り・回答の生成の流れは通らない。
    assert llm.calls == ["history_rewrite", "small_talk_reply"]
    assert engine_queries == []
    assert response.answer == "合成の応答です。"
    assert response.citations == []
    assert response.diagnostics.answer["retrieval_skipped"] == {"intent": intent}
    rewrite = _stage(summary, "history_rewrite")
    fields = {f.label: f.value for f in rewrite.display.details.fields}
    assert fields["発話の種類"].endswith("（検索の要らない発話）")
    step = _stage(summary, "small_talk_reply")
    assert step.status == "done"
    assert step.display.kind == "small_talk_reply"
    details = {f.label: f.value for f in step.display.details.fields}
    assert details["理由"].endswith("のため検索しませんでした")
    assert details["結果"] == "根拠なしの短い応答（引用なし）"


@pytest.mark.parametrize(
    "case", ["search-intent", "disabled", "reply-failed", "empty-reply", "scope", "approved-faq"]
)
async def test_retrieval_runs_when_not_safely_skippable(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    llm = FakeLlm(
        intent="search" if case == "search-intent" else "greeting",
        reply=RuntimeError("synthetic failure")
        if case == "reply-failed"
        else ""
        if case == "empty-reply"
        else "合成の応答です。",
    )
    settings = Settings(
        _env_file=None,
        rag_history_rewrite_enabled=True,
        rag_auto_field_filter_enabled=True,
        rag_small_talk_skip_enabled=case != "disabled",
    )
    response, summary, engine_queries = await _run(
        monkeypatch,
        llm,
        settings=settings,
        scope=AnswerScope(context="合成の確認") if case == "scope" else None,
        approved_faq=("合成の FAQ", "合成の FAQ の回答") if case == "approved-faq" else None,
    )
    assert response.answer == "合成の根拠つき回答"
    assert "retrieval_skipped" not in response.diagnostics.answer
    step = _stage(summary, "small_talk_reply")
    if case in {"reply-failed", "empty-reply"}:
        # 応答を作れなければ、いつもの検索と回答に戻す（条件の読み取りも行う）。
        assert step.status == "failed"
        assert engine_queries == ["field_filter", "合成の挨拶"]
    else:
        assert step is None
        assert "small_talk_reply" not in llm.calls


async def test_prepared_small_talk_skips_without_calling_rewrite_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm()
    response, summary, engine_queries = await _run(
        monkeypatch,
        llm,
        prepared=PreparedHistoryRewrite(elapsed_seconds=0.5, intent="greeting"),
    )
    assert llm.calls == ["small_talk_reply"]
    assert engine_queries == []
    assert response.citations == []
    assert _stage(summary, "history_rewrite").status == "done"


def test_rewrite_without_intent_means_search() -> None:
    parsed = HistoryRewrite.model_validate_json('{"question": "合成の質問"}')
    assert parsed.intent == "search"


def test_token_carries_only_known_small_talk_intent() -> None:
    binding = {"owner": "owner-a"}
    token = issue_question_token(SECRET, binding, "合成の挨拶", history_intent="greeting")
    assert read_history_intent(SECRET, token) == "greeting"
    # 確認の提案が選択を足して署名し直しても保つ。
    resigned = with_rule_selection_answers(SECRET, token, [], elapsed_seconds=0.1)
    assert read_history_intent(SECRET, resigned) == "greeting"
    assert read_history_intent(SECRET, issue_question_token(SECRET, binding, "合成の質問")) == (
        "search"
    )
