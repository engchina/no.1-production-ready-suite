"""承認済み FAQ の否定の質問と、一致度が下限以上のとき回答をそのまま返す段（#4788）。"""

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

from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.config import Settings, get_settings
from app.main import app
from app.rag.answer_engine import AnswerEngine, AnswerOutcome, AnswerScope
from app.rag.guardrails import GuardrailResult
from app.rag.pipeline import ChatTurn, PreparedHistoryRewrite, RagPipeline
from app.rag.search_answer_profile_knowledge import (
    FAQ_DIRECT_ANSWER_ENABLED_KEY,
    add_approved_faq,
    match_direct_approved_faq,
    save_approved_faq_settings,
)
from app.schemas.search import SearchRequest
from tests.support import AsgiTestClient
from tests.test_answer_engine import SavingOracle
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
PROFILE = "bv-1"
BASE = f"/api/search-answer-profiles/{PROFILE}/approved-faq"
QUESTION = "機器 A の電源を入れる手順は？"
ANSWER = "合成の回答: 前面のボタンを 3 秒押します。"
NEGATIVE = "機器 A の電源を切る手順は？"


def _embedding(text: str) -> list[float]:
    # 合成の埋め込み: 「切る」の文、その言い換え（「止め」）、それ以外の文を別の向きに置く。
    directions = {"切る": [0.2, 1.0], "止め": [0.3, 1.0]}
    head = next((vector for word, vector in directions.items() if word in text), [1.0, 0.2])
    return head + [0.0] * 1534


class StubGenAi:
    """意味照合の embedding を決定論にする（外部の OCI を呼ばない）。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.calls: list[str] = []

    async def embed(self, texts: list[str], *, input_type: str = "") -> list[list[float]]:
        self.calls.append(input_type)
        return [_embedding(text) for text in texts]

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        return []


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(knowledge_route, "OciGenAiClient", StubGenAi)
    # 既定は文字列の照合だけ（意味の照合は、使うテストが有効にする）。
    monkeypatch.setattr(get_settings(), "rag_approved_faq_semantic_enabled", False)
    return fake


# --- 否定の質問の保存と照合（API）---


def test_negative_questions_are_saved_listed_and_replaced(fake_oracle: FakeKnowledgeOracle) -> None:
    added = client.post(
        BASE,
        json={"question": QUESTION, "answer": ANSWER, "negative_questions": [NEGATIVE, "  "]},
    )
    assert added.status_code == 200
    (record,) = added.json()["data"]["records"]
    assert record["negative_questions"] == [NEGATIVE]

    replaced = client.put(
        f"{BASE}/{record['id']}/negative-questions",
        json={"negative_questions": ["機器 A を止めたい", NEGATIVE, NEGATIVE]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["data"]["records"][0]["negative_questions"] == [
        "機器 A を止めたい",
        NEGATIVE,
    ]
    # 質問・回答・承認は変えない。
    assert replaced.json()["data"]["records"][0]["answer"] == ANSWER
    assert replaced.json()["data"]["records"][0]["approved_at"] == record["approved_at"]

    cleared = client.put(
        f"{BASE}/{record['id']}/negative-questions", json={"negative_questions": []}
    )
    assert cleared.json()["data"]["records"][0]["negative_questions"] == []


def test_negative_question_equal_to_faq_question_is_rejected(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    message = "当てはめない質問に、この Q&A の質問と同じ文は登録できません。"
    rejected = client.post(
        BASE, json={"question": QUESTION, "answer": ANSWER, "negative_questions": [QUESTION]}
    )
    assert rejected.status_code == 422
    assert rejected.json()["error_messages"] == [message]
    assert client.get(BASE).json()["data"]["records"] == []

    (record,) = client.post(BASE, json={"question": QUESTION, "answer": ANSWER}).json()["data"][
        "records"
    ]
    updated = client.put(
        f"{BASE}/{record['id']}/negative-questions",
        json={"negative_questions": ["機器Ａの電源を入れる手順は"]},
    )
    assert updated.status_code == 422
    assert updated.json()["error_messages"] == [message]
    missing = client.put(f"{BASE}/no-such-faq/negative-questions", json={"negative_questions": []})
    assert missing.status_code == 404


@pytest.mark.parametrize("purpose", ["chat", "search"])
def test_suggestions_drop_the_faq_whose_negative_question_matches(
    fake_oracle: FakeKnowledgeOracle, purpose: str
) -> None:
    client.post(
        BASE, json={"question": QUESTION, "answer": ANSWER, "negative_questions": [NEGATIVE]}
    )

    hit = client.post(f"{BASE}/suggest", json={"query": QUESTION, "purpose": purpose})
    assert [item["question"] for item in hit.json()["data"]["suggestions"]] == [QUESTION]
    # 否定の質問と同じ質問には、その FAQ を候補に出さない（字面は FAQ の質問にも近い）。
    negative = client.post(f"{BASE}/suggest", json={"query": NEGATIVE, "purpose": purpose})
    assert negative.json()["data"]["suggestions"] == []


def test_semantic_paraphrase_of_negative_question_is_dropped(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_approved_faq_semantic_enabled", True)
    monkeypatch.setattr(settings, "rag_approved_faq_suggestion_min_score", 0.1)
    client.post(
        BASE, json={"question": QUESTION, "answer": ANSWER, "negative_questions": [NEGATIVE]}
    )

    kept = client.post(f"{BASE}/suggest", json={"query": "機器 A を起動したい"})
    assert [item["question"] for item in kept.json()["data"]["suggestions"]] == [QUESTION]
    dropped = client.post(f"{BASE}/suggest", json={"query": "機器 A を止めたいときの操作"})
    assert dropped.json()["data"]["suggestions"] == []

    # 否定の質問の下限を上げると、当たったとみなさない（設定で調整できる）。
    monkeypatch.setattr(settings, "rag_approved_faq_negative_min_score", 1.0)
    relaxed = client.post(f"{BASE}/suggest", json={"query": "機器 A を止めたいときの操作"})
    assert [item["question"] for item in relaxed.json()["data"]["suggestions"]] == [QUESTION]


# --- そのまま返す設定（API）---


def test_direct_answer_settings_default_off_and_change_only_sent_fields(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    data = client.get(BASE).json()["data"]
    assert data["direct_answer_enabled"] is False
    # 下限は全体設定の値を返す（画面の説明に出す）。
    assert data["direct_answer_min_score"] == 0.92

    on = client.put(f"{BASE}/settings", json={"direct_answer_enabled": True})
    assert on.status_code == 200
    assert on.json()["data"]["direct_answer_enabled"] is True
    assert on.json()["data"]["enabled"] is True
    # FAQ の追加・類似問の提示の切り替えでも、設定は残る。
    client.post(BASE, json={"question": QUESTION, "answer": ANSWER})
    off = client.put(f"{BASE}/settings", json={"enabled": False}).json()["data"]
    assert (off["enabled"], off["direct_answer_enabled"]) == (False, True)
    assert fake_oracle.payloads[(PROFILE, "approved_faq")][FAQ_DIRECT_ANSWER_ENABLED_KEY] is True


def test_chat_suggest_skips_the_choice_only_when_direct_answer_applies(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post(BASE, json={"question": QUESTION, "answer": ANSWER})
    chat = {"query": QUESTION, "purpose": "chat"}

    # 既定（オフ）は今までどおり候補を出し、利用者が選ぶ。
    shown = client.post(f"{BASE}/suggest", json=chat).json()["data"]
    assert shown["direct_answer"] is False
    assert [item["direct"] for item in shown["suggestions"]] == [True]

    client.put(f"{BASE}/settings", json={"direct_answer_enabled": True})
    direct = client.post(f"{BASE}/suggest", json=chat).json()["data"]
    assert direct == {"suggestions": [], "direct_answer": True}
    # RAG 検索も同じ下限で直接回答に進む。
    search = client.post(f"{BASE}/suggest", json={"query": QUESTION}).json()["data"]
    assert search == {"suggestions": [], "direct_answer": True}

    # 下限未満の候補は、オンでも今までどおり候補として出す。
    monkeypatch.setattr(get_settings(), "rag_approved_faq_direct_answer_min_score", 1.0)
    near = client.post(
        f"{BASE}/suggest", json={"query": "機器 A の電源を入れる手順は何？", "purpose": "chat"}
    ).json()["data"]
    assert near["direct_answer"] is False
    assert [item["direct"] for item in near["suggestions"]] == [False]


# --- 補完の後の段（pipeline）---


class FaqOracle(SavingOracle):
    """回答の記録の保存と、検索・回答プロファイルの FAQ の保存を持つ fake。"""

    def __init__(self) -> None:
        super().__init__()
        self.knowledge = FakeKnowledgeOracle()
        self.get_search_answer_profile = self.knowledge.get_search_answer_profile
        self.approved_faq_source_document_states = (
            self.knowledge.approved_faq_source_document_states
        )
        self.get_search_answer_profile_knowledge = (
            self.knowledge.get_search_answer_profile_knowledge
        )
        self.save_search_answer_profile_knowledge = (
            self.knowledge.save_search_answer_profile_knowledge
        )


class FakeLlm:
    """補完の構造化出力を返す決定論のスタブ（ほかの呼び出しは記録だけ）。"""

    def __init__(self, rewritten: str | None = None, intent: str = "search") -> None:
        self.rewritten = rewritten
        self.intent = intent
        self.calls: list[str] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        name = kwargs["response_schema_name"]
        self.calls.append(name)
        if name == "history_rewrite":
            return json.dumps(
                {
                    "question": self.rewritten or prompt,
                    "confirmed_fact_ids": [],
                    "intent": self.intent,
                },
                ensure_ascii=False,
            )
        return json.dumps({"reply": "合成の応答です。"}, ensure_ascii=False)


@pytest.fixture
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


async def _oracle(*, enabled: bool = True) -> FaqOracle:
    oracle = FaqOracle()
    store = cast(Any, oracle)
    await add_approved_faq(
        store, PROFILE, question=QUESTION, answer=ANSWER, negative_questions=[NEGATIVE]
    )
    await save_approved_faq_settings(store, PROFILE, {FAQ_DIRECT_ANSWER_ENABLED_KEY: enabled})
    return oracle


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    oracle: FaqOracle,
    query: str,
    *,
    llm: FakeLlm | None = None,
    genai: StubGenAi | None = None,
    settings: Settings | None = None,
    direct_faq_answer: bool = True,
    scope: AnswerScope | None = None,
    approved_faq: tuple[str, str] | None = None,
    prepared: PreparedHistoryRewrite | None = None,
    history: list[ChatTurn] | None = None,
) -> tuple[Any, TraceSummary, list[str], FakeLlm]:
    engine_queries: list[str] = []

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        engine_queries.append(request.query)
        return AnswerOutcome(
            answer="合成の根拠つき回答", citations=[], context_text="", diagnostics={}
        )

    monkeypatch.setattr(AnswerEngine, "run", answer)
    llm = llm or FakeLlm()
    pipeline = RagPipeline(
        settings=settings
        or Settings(
            _env_file=None,
            rag_history_rewrite_enabled=True,
            rag_approved_faq_semantic_enabled=False,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, genai or StubGenAi()),
        llm=cast(Any, llm),
        history_question_prepared=prepared is not None,
        prepared_history_rewrite=prepared,
        direct_faq_answer=direct_faq_answer,
        scope=scope,
        approved_faq=approved_faq,
    )
    request = SearchRequest(query=query, search_answer_profile_id=PROFILE)
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
            history=history if history is not None else [],
        )
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    return response, saved, engine_queries, llm


def _stage(summary: TraceSummary, name: str = "faq_direct_match") -> Any:
    return next((s for s in summary.spans if s.attributes.get("pr.stage") == name), None)


def _fields(step: Any) -> dict[str, str]:
    return {f.label: f.value for f in step.display.details.fields}


pipeline_test = pytest.mark.usefixtures("system_access", "tracing")


@pipeline_test
async def test_direct_match_returns_the_faq_answer_without_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = await _oracle()
    response, summary, engine_queries, llm = await _run(monkeypatch, oracle, QUESTION)

    # モデルの呼び出しは補完の 1 回だけ。検索と回答の生成の流れは通らない。
    assert llm.calls == ["history_rewrite"]
    assert engine_queries == []
    assert response.answer == f"{ANSWER}\n\n（出典: 登録済みQ&A「{QUESTION}」）"
    assert response.citations == []
    answer = response.diagnostics.answer
    assert answer["answer_source"] == "approved_faq"
    assert answer["outcome"] == "answered"
    assert answer["approved_faq_question"] == QUESTION
    assert answer["approved_faq_answer"] == ANSWER
    assert answer["approved_faq_direct"]["score"] == 1.0
    assert answer["approved_faq_direct"]["min_score"] == 0.92
    assert answer["models"] == {"embedding": "", "rerank": ""}
    step = _stage(summary)
    assert step.status == "done"
    assert step.display.kind == "faq_direct_match"
    fields = _fields(step)
    assert fields["結果"].startswith("登録済みQ&Aの回答をそのまま返しました")
    assert fields["一致した Q&A"] == QUESTION
    assert fields["一致度"] == "1.00（下限 0.92）"
    # 回答の記録は、ほかの回答と同じく残す。
    assert len(oracle.saved) == 1


@pipeline_test
async def test_negative_question_goes_to_the_normal_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = await _oracle()
    response, summary, engine_queries, _llm = await _run(monkeypatch, oracle, NEGATIVE)

    assert response.answer == "合成の根拠つき回答"
    assert engine_queries == [NEGATIVE]
    assert "approved_faq_direct" not in response.diagnostics.answer
    step = _stage(summary)
    fields = _fields(step)
    assert fields["結果"] == "下限以上で一致する登録済みQ&Aが無いため、検索して回答します"
    assert fields["最も近い Q&A"] == "候補なし（下限 0.92）"
    assert fields["当てはめない質問に当たって外した Q&A"] == "1 件"
    assert list(step.display.details.lines) == [f"{QUESTION}（当てはめない質問: {NEGATIVE}）"]


@pipeline_test
async def test_match_below_min_score_goes_to_the_normal_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = await _oracle()
    query = "機器 A の電源のランプの色は？"
    response, summary, engine_queries, _llm = await _run(monkeypatch, oracle, query)

    assert response.answer == "合成の根拠つき回答"
    assert engine_queries == [query]
    fields = _fields(_stage(summary))
    assert fields["結果"] == "下限以上で一致する登録済みQ&Aが無いため、検索して回答します"
    assert fields["最も近い Q&A の一致度"].endswith("（下限 0.92）")


@pipeline_test
@pytest.mark.parametrize(
    "case", ["profile-off", "not-requested", "scope", "chosen-faq", "suggestions-off"]
)
async def test_no_direct_match_stage_when_it_does_not_apply(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    oracle = await _oracle(enabled=case != "profile-off")
    if case == "suggestions-off":
        await save_approved_faq_settings(cast(Any, oracle), PROFILE, {"enabled": False})
    response, summary, engine_queries, _llm = await _run(
        monkeypatch,
        oracle,
        QUESTION,
        direct_faq_answer=case != "not-requested",
        scope=AnswerScope(context="合成の確認") if case == "scope" else None,
        approved_faq=(QUESTION, ANSWER) if case == "chosen-faq" else None,
    )

    assert response.answer == "合成の根拠つき回答"
    assert engine_queries == [QUESTION]
    assert _stage(summary) is None


@pipeline_test
async def test_rewritten_question_is_matched_after_the_history_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = await _oracle()
    history = [
        ChatTurn(role="USER", content="機器 A について教えてください。"),
        ChatTurn(role="ASSISTANT", content="合成の回答 A"),
    ]
    response, summary, engine_queries, llm = await _run(
        monkeypatch,
        oracle,
        "電源を入れる手順は？",
        llm=FakeLlm(rewritten=QUESTION),
        history=history,
    )

    assert llm.calls == ["history_rewrite"]
    assert engine_queries == []
    assert response.answer.startswith(ANSWER)
    stages = [s.attributes.get("pr.stage") for s in summary.spans]
    assert stages.index("history_rewrite") < stages.index("faq_direct_match")
    # 送信の前に補完を済ませた質問も、同じ段で照合する（補完は呼び直さない）。
    prepared_llm = FakeLlm()
    prepared_response, _summary, prepared_queries, _ = await _run(
        monkeypatch,
        await _oracle(),
        QUESTION,
        llm=prepared_llm,
        prepared=PreparedHistoryRewrite(elapsed_seconds=0.2, intent="search"),
        history=history,
    )
    assert prepared_llm.calls == []
    assert prepared_queries == []
    assert prepared_response.answer.startswith(ANSWER)


@pipeline_test
async def test_small_talk_is_answered_before_the_faq_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = await _oracle()
    response, summary, engine_queries, llm = await _run(
        monkeypatch, oracle, QUESTION, llm=FakeLlm(intent="greeting")
    )

    assert llm.calls == ["history_rewrite", "small_talk_reply"]
    assert engine_queries == []
    assert response.answer == "合成の応答です。"
    assert _stage(summary) is None


@pipeline_test
async def test_min_score_setting_and_semantic_match_decide_the_direct_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        rag_history_rewrite_enabled=True,
        rag_approved_faq_semantic_enabled=True,
        rag_approved_faq_direct_answer_min_score=0.95,
    )
    paraphrase = "機器 A を起動したい"
    genai = StubGenAi()
    # 意味の一致度は 1.0（合成の埋め込み）。全体設定の下限 0.95 以上なのでそのまま返す。
    response, summary, engine_queries, _llm = await _run(
        monkeypatch, await _oracle(), paraphrase, genai=genai, settings=settings
    )
    assert engine_queries == []
    assert response.answer.startswith(ANSWER)
    assert response.diagnostics.answer["approved_faq_direct"]["match_method"] == "semantic"
    assert response.diagnostics.answer["models"]["embedding"] == settings.oci_genai_embedding_model
    assert _fields(_stage(summary))["一致度"] == "1.00（下限 0.95）"
    # FAQ の質問の index を 1 回作り、質問を 1 回 embedding する。
    assert genai.calls == ["SEARCH_DOCUMENT", "SEARCH_QUERY"]

    # 否定の質問の言い換えは、意味の照合で外す。
    negative, negative_summary, negative_queries, _ = await _run(
        monkeypatch,
        await _oracle(),
        "機器 A を止めたいときの操作",
        genai=StubGenAi(),
        settings=settings,
    )
    assert negative.answer == "合成の根拠つき回答"
    assert negative_queries == ["機器 A を止めたいときの操作"]
    assert _fields(_stage(negative_summary))["当てはめない質問に当たって外した Q&A"] == "1 件"


@pipeline_test
async def test_failed_match_is_recorded_and_the_normal_answer_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import search_answer_profile_knowledge as knowledge

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic failure")

    oracle = await _oracle()
    monkeypatch.setattr(knowledge, "rank_approved_faq", broken)
    response, summary, engine_queries, _llm = await _run(monkeypatch, oracle, QUESTION)

    # 照合できなくても回答は止めない。段は失敗として残す。
    assert response.answer == "合成の根拠つき回答"
    assert engine_queries == [QUESTION]
    assert _stage(summary).status == "failed"


@pytest.mark.usefixtures("system_access")
async def test_match_failure_degrades_to_the_normal_flow() -> None:
    class BrokenStore(FakeKnowledgeOracle):
        async def get_search_answer_profile_knowledge(self, *args: Any) -> dict[str, object] | None:
            raise RuntimeError("synthetic failure")

    settings = Settings(_env_file=None, rag_approved_faq_semantic_enabled=False)
    assert (
        await match_direct_approved_faq(
            cast(Any, BrokenStore()), PROFILE, QUESTION, settings, StubGenAi
        )
        is None
    )
