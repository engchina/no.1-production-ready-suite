"""利用者が選んだ承認済み FAQ の回答をそのまま返す入口と回答の段（#5107）。

- 入口（``_resolve_query_context``）は、安全チェック・補完（``question_gate``）の後、業務ガイドの
  照合の前に、選んだ FAQ をプロファイルの FAQ から引き直す。無い・提示がオフの FAQ は 422。
- 回答の段は、検索・回答の生成をせずに FAQ の回答を原文のまま返す（LLM を呼ばない）。
"""

import json
from time import perf_counter
from typing import Any, cast

import pytest
from fastapi import HTTPException
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from pr_system_settings.guardrails import GuardrailResult

from app.api.routes import search as search_route
from app.config import Settings, get_settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.approved_faq_answer import APPROVED_FAQ_ANSWER_STAGE
from app.rag.pipeline import ChatTurn, PreparedHistoryRewrite, RagPipeline
from app.rag.search_answer_profile_knowledge import add_approved_faq, save_approved_faq_settings
from app.schemas.search import SearchRequest
from tests.test_answer_engine import SavingOracle
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

PROFILE = "bv-1"
QUESTION = "合成の機器 A の電源を入れる手順は？"
ANSWER = "合成の回答: 前面のボタンを 3 秒押します。"


async def _faq_id(store: Any, *, enabled: bool = True) -> str:
    mutation = await add_approved_faq(store, PROFILE, question=QUESTION, answer=ANSWER)
    await save_approved_faq_settings(store, PROFILE, enabled=enabled)
    return mutation.records[0].id


class _NoGuides:
    """業務ガイドを読んだら失敗する（選んだ FAQ は業務ガイドの照合の前で止める）。"""

    def __init__(self, _oracle: object) -> None:
        pass

    async def published_contents(self, _profile: str) -> list[Any]:
        raise AssertionError("選んだ FAQ の質問で業務ガイドを読んだ")


@pytest.fixture
def knowledge(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle(support_guide_enabled=True)
    monkeypatch.setattr(search_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(search_route, "SupportGuideStore", _NoGuides)
    return fake


@pytest.mark.usefixtures("system_access")
async def test_entry_resolves_the_selected_faq_after_the_question_gate(
    knowledge: FakeKnowledgeOracle,
) -> None:
    faq_id = await _faq_id(knowledge)
    order: list[str] = []

    async def gate(request: SearchRequest, _settings: Settings) -> SearchRequest:
        order.append("gate")
        # FAQ はまだ引いていない（安全チェック・補完が先）。
        return request.model_copy(update={"query": "補完した質問"})

    request = SearchRequest(query="それの手順は？", search_answer_profile_id=PROFILE)
    selected = request.model_copy(update={"approved_faq_id": faq_id})

    effective, settings, _kb, applied = await search_route._resolve_query_context(
        selected, get_settings(), question_gate=gate
    )

    assert order == ["gate"]
    assert effective.query == "補完した質問"
    assert applied == PROFILE
    assert settings.rag_approved_faq_selected == {
        "id": faq_id,
        "question": QUESTION,
        "answer": ANSWER,
    }
    assert settings.rag_support_guide == {}


@pytest.mark.usefixtures("system_access")
@pytest.mark.parametrize("case", ["unknown", "disabled", "retrieval", "no-answer"])
async def test_entry_rejects_a_faq_that_cannot_be_returned(
    knowledge: FakeKnowledgeOracle, case: str
) -> None:
    faq_id = await _faq_id(knowledge, enabled=case != "disabled")
    request = SearchRequest(
        query=QUESTION,
        search_answer_profile_id=PROFILE,
        approved_faq_id="other" if case == "unknown" else faq_id,
        retrieval_only=case == "retrieval",
        generate_answer=case != "no-answer",
    )

    with pytest.raises(HTTPException) as error:
        await search_route._resolve_query_context(request, get_settings())

    assert error.value.status_code == 422


@pytest.mark.usefixtures("system_access")
async def test_entry_without_a_profile_rejects_the_faq_and_stops_blocked_questions(
    knowledge: FakeKnowledgeOracle,
) -> None:
    async def blocked(_request: SearchRequest, _settings: Settings) -> None:
        return None

    request = SearchRequest(query=QUESTION, approved_faq_id="faq-1")
    with pytest.raises(HTTPException) as error:
        await search_route._resolve_query_context(request, get_settings())
    assert error.value.status_code == 422
    # 止めた質問は FAQ を引かない（安全チェックが先）。
    stopped = await search_route._resolve_query_context(
        request.model_copy(update={"search_answer_profile_id": PROFILE}),
        get_settings(),
        question_gate=blocked,
    )
    assert stopped[1].rag_approved_faq_selected == {}


# --- 回答の段（pipeline）---


class FaqOracle(SavingOracle):
    """回答の記録の保存と、検索・回答プロファイルの FAQ の保存を持つ fake。"""

    def __init__(self) -> None:
        super().__init__()
        self.knowledge = FakeKnowledgeOracle()
        self.get_search_answer_profile = self.knowledge.get_search_answer_profile
        self.get_search_answer_profile_knowledge = (
            self.knowledge.get_search_answer_profile_knowledge
        )
        self.save_search_answer_profile_knowledge = (
            self.knowledge.save_search_answer_profile_knowledge
        )


class FakeLlm:
    """補完の構造化出力を返す決定論のスタブ（ほかの呼び出しは記録だけ）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        name = kwargs["response_schema_name"]
        self.calls.append(name)
        return json.dumps({"question": prompt, "confirmed_fact_ids": [], "intent": "search"})


class StubGenAi:
    async def embed(self, texts: list[str], *, input_type: str = "") -> list[list[float]]:
        raise AssertionError("選んだ FAQ の回答で embedding を呼んだ")

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        raise AssertionError("選んだ FAQ の回答で rerank を呼んだ")


@pytest.fixture
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    selected: dict[str, str],
    *,
    prepared: PreparedHistoryRewrite | None = None,
    history: list[ChatTurn] | None = None,
) -> tuple[Any, TraceSummary, list[str], FakeLlm, FaqOracle]:
    engine_queries: list[str] = []

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        engine_queries.append(request.query)
        return AnswerOutcome(
            answer="合成の根拠つき回答", citations=[], context_text="", diagnostics={}
        )

    monkeypatch.setattr(AnswerEngine, "run", answer)
    llm = FakeLlm()
    oracle = FaqOracle()
    pipeline = RagPipeline(
        settings=Settings(
            _env_file=None,
            rag_history_rewrite_enabled=True,
            rag_approved_faq_selected=selected,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, StubGenAi()),
        llm=cast(Any, llm),
        history_question_prepared=prepared is not None,
        prepared_history_rewrite=prepared,
        attachment_conversation_id="conv-1",
    )
    request = SearchRequest(query=QUESTION, search_answer_profile_id=PROFILE)
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
    return response, saved, engine_queries, llm, oracle


def _stage(summary: TraceSummary, name: str) -> Any:
    return next((s for s in summary.spans if s.attributes.get("pr.stage") == name), None)


pipeline_test = pytest.mark.usefixtures("system_access", "tracing")


@pipeline_test
async def test_selected_faq_answer_is_returned_verbatim_without_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = {"id": "faq-1", "question": QUESTION, "answer": ANSWER}

    response, summary, engine_queries, llm, oracle = await _run(monkeypatch, selected)

    # 検索・会話の添付・回答の生成を通さず、モデルも呼ばない（原文のまま。出典の行も足さない）。
    assert llm.calls == []
    assert engine_queries == []
    assert response.answer == ANSWER
    assert response.citations == []
    answer = response.diagnostics.answer
    assert answer["answer_source"] == "approved_faq"
    assert answer["outcome"] == "answered"
    assert (answer["approved_faq_id"], answer["approved_faq_question"]) == ("faq-1", QUESTION)
    assert answer["approved_faq_answer"] == ANSWER
    assert answer["models"] == {"embedding": "", "rerank": ""}
    step = _stage(summary, APPROVED_FAQ_ANSWER_STAGE)
    assert step.status == "done"
    assert step.display.kind == APPROVED_FAQ_ANSWER_STAGE
    fields = {f.label: f.value for f in step.display.details.fields}
    assert fields["選んだ Q&A"] == QUESTION
    assert _stage(summary, "attachment_evidence") is None
    # 回答の記録は、ほかの回答と同じく残す。
    assert len(oracle.saved) == 1


@pipeline_test
async def test_chat_records_the_prepared_completion_before_the_faq_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """チャットは送信の前に済ませた補完の段を残し、補完を呼び直さずに FAQ の回答を返す。"""
    selected = {"id": "faq-1", "question": QUESTION, "answer": ANSWER}
    prepared = PreparedHistoryRewrite(elapsed_seconds=0.1, confirmed_facts=(), intent="search")

    response, summary, engine_queries, llm, _ = await _run(
        monkeypatch, selected, prepared=prepared, history=[]
    )

    assert llm.calls == []
    assert engine_queries == []
    assert response.answer == ANSWER
    stages = [s.attributes.get("pr.stage") for s in summary.spans]
    assert "history_rewrite" in stages and APPROVED_FAQ_ANSWER_STAGE in stages


@pipeline_test
async def test_without_a_selected_faq_the_normal_answer_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, summary, engine_queries, _, _ = await _run(monkeypatch, {})

    assert engine_queries == [QUESTION]
    assert response.answer.startswith("合成の根拠つき回答")
    assert _stage(summary, APPROVED_FAQ_ANSWER_STAGE) is None
