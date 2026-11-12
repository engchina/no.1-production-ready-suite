"""チャットの分類予測だけでは検索を省略しない。"""

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
from app.rag.chat_question_token import issue_question_token, read_history_intent
from app.rag.guardrails import GuardrailResult
from app.rag.history_question import EMPTY_CONVERSATION_HISTORY, HistoryRewrite
from app.rag.pipeline import ChatTurn, PreparedHistoryRewrite, RagPipeline
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi, SavingOracle

SECRET = "synthetic-signing-key-" * 3
HISTORY = [
    ChatTurn(role="USER", content="合成の質問 A"),
    ChatTurn(role="ASSISTANT", content="合成の回答 A"),
]


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
        self.rewrite_contexts: list[str] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        name = kwargs["response_schema_name"]
        self.calls.append(name)
        if name == "history_rewrite":
            self.rewrite_contexts.append(context)
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
    history: list[ChatTurn] | None = HISTORY,
) -> tuple[Any, TraceSummary, list[str]]:
    engine_queries: list[str] = []

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        engine_queries.append(request.query)
        return AnswerOutcome(
            answer="合成の根拠つき回答", citations=[], context_text="", diagnostics={}
        )

    monkeypatch.setattr(AnswerEngine, "run", answer)
    pipeline = RagPipeline(
        settings=settings or Settings(_env_file=None, rag_history_rewrite_enabled=True),
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
            history=history,
        )
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    return response, saved, engine_queries


def _stage(summary: TraceSummary, name: str) -> Any:
    return next((s for s in summary.spans if s.attributes.get("pr.stage") == name), None)


@pytest.mark.parametrize("intent", ["greeting", "small_talk", "search"])
async def test_classification_never_prevents_retrieval(
    monkeypatch: pytest.MonkeyPatch, intent: str
) -> None:
    llm = FakeLlm(intent=intent)
    response, summary, engine_queries = await _run(monkeypatch, llm)
    assert llm.calls == ["history_rewrite"]
    assert engine_queries == ["合成の挨拶"]
    assert response.answer == "合成の根拠つき回答"
    assert "retrieval_skipped" not in response.diagnostics.answer
    assert _stage(summary, "small_talk_reply") is None


async def test_prepared_classification_keeps_retrieval_without_rewriting_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm()
    response, summary, engine_queries = await _run(
        monkeypatch, llm, prepared=PreparedHistoryRewrite(elapsed_seconds=0.5, intent="greeting")
    )
    assert llm.calls == []
    assert engine_queries == ["合成の挨拶"]
    assert response.answer == "合成の根拠つき回答"


def test_rewrite_without_intent_means_search() -> None:
    parsed = HistoryRewrite.model_validate_json('{"question": "合成の質問"}')
    assert parsed.intent == "search"


def test_token_carries_only_known_small_talk_intent() -> None:
    binding = {"owner": "owner-a"}
    token = issue_question_token(SECRET, binding, "合成の挨拶", history_intent="greeting")
    assert read_history_intent(SECRET, token) == "greeting"
    assert read_history_intent(SECRET, issue_question_token(SECRET, binding, "合成の質問")) == (
        "search"
    )


async def test_first_turn_goes_through_the_same_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会話の 1 回目（空の履歴）も、補完の分類によらず通常の検索へ進む。"""
    llm = FakeLlm(intent="greeting")
    response, summary, engine_queries = await _run(monkeypatch, llm, history=[])
    assert llm.calls == ["history_rewrite"]
    # 履歴が無いときは、空の履歴の形で渡す（指示が履歴を前提にしても読める入力にする）。
    assert llm.rewrite_contexts == [EMPTY_CONVERSATION_HISTORY]
    assert engine_queries == ["合成の挨拶"]
    assert response.citations == []
    rewrite = _stage(summary, "history_rewrite")
    fields = {f.label: f.value for f in rewrite.display.details.fields}
    assert fields["参照した会話"] == "なし（会話の最初の発話）"
    assert _stage(summary, "small_talk_reply") is None


async def test_first_turn_question_is_searched_with_the_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeLlm(intent="search")
    response, summary, engine_queries = await _run(monkeypatch, llm, history=[])
    assert llm.calls == ["history_rewrite"]
    assert engine_queries == ["合成の挨拶"]
    assert response.answer == "合成の根拠つき回答"
    assert response.diagnostics.answer["rewritten_question"] == ""


@pytest.mark.parametrize("case", ["no-history-argument", "rewrite-disabled"])
async def test_paths_without_rewrite_do_not_classify(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """RAG 検索など履歴を渡さない経路と、書き換えが無効のときは、呼び出しを足さない。"""
    llm = FakeLlm(intent="greeting")
    settings = Settings(
        _env_file=None,
        rag_history_rewrite_enabled=case != "rewrite-disabled",
    )
    response, summary, engine_queries = await _run(
        monkeypatch,
        llm,
        settings=settings,
        history=None if case == "no-history-argument" else [],
    )
    assert llm.calls == []
    assert _stage(summary, "history_rewrite") is None
    assert engine_queries == ["合成の挨拶"]
    assert response.answer == "合成の根拠つき回答"
