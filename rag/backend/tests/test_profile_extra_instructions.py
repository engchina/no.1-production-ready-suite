"""検索・回答プロファイルの追加の指示（質問に足す文・回答の指示に足す文。#5848）。

- 質問に足す文は、用語・同義語の照合の後に質問の末尾へ足し、その後の検索（文書検索・rerank・Wiki・
  検索拡張）と回答の生成に使う。見せる・保存する質問は利用者の質問のまま。入力の安全チェック・
  履歴による補完・雑談の判定・業務ガイドの照合は利用者の質問で行う。
- 回答の指示に足す文は、回答の生成の system の役割の文の後・固定の部分の前にだけ足す。

資料・質問・足す文はすべて合成。LLM・検索・DB は決定論の代役。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast

import pytest
from pr_system_settings import guardrails
from pr_system_settings.guardrails import GuardrailInspection
from pydantic import ValidationError
from rag_engine.generation import grounded
from rag_engine.generation.answer_models import QUESTION_METADATA_SEPARATORS, with_question_suffix

from app.api.routes import search as search_route
from app.config import Settings, get_settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.answer_provenance import answer_prompt_version
from app.rag.pipeline import BASIC_FLOW, EXPANDED_FLOW, AnswerFlow, ChatTurn, RagPipeline
from app.rag.search_answer_profile_config import (
    ANSWER_INSTRUCTION_MAX_CHARS,
    QUESTION_SUFFIX_MAX_CHARS,
    SearchAnswerProfileConfig,
    dump_search_answer_profile_config,
    parse_search_answer_profile_config,
    resolve_search_answer_profile_settings,
)
from app.rag.support_guide_query import build_guide_query
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import (
    FakeGenAi,
    RewriteLlm,
    SavingQueryOracle,
    _fake_llm,
    _nfkc,
)
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle
from tests.test_small_talk_skip import FakeLlm
from tests.test_support_guide_runtime import GUIDES

QUESTION = "受注の登録方法は？"
SUFFIX = "根拠の章の番号を書いてください。"
INSTRUCTION = "箇条書きで答えてください。"
SUFFIX_STEP = "質問に足す文の追加"


# --- 設定（保存の検証と Settings への解決）---


def test_both_fields_default_to_empty_and_old_json_still_loads() -> None:
    config = parse_search_answer_profile_config({"knowledge_base_ids": ["kb-1"]})
    assert (config.question_suffix, config.answer_instruction) == ("", "")
    settings = get_settings()
    merged, applied = resolve_search_answer_profile_settings(settings, SearchAnswerProfileConfig())
    assert merged is settings and applied is False
    assert (merged.rag_question_suffix, merged.rag_answer_instruction) == ("", "")


def test_values_are_trimmed_kept_multiline_and_resolved_to_request_settings() -> None:
    config = SearchAnswerProfileConfig.model_validate(
        {
            "question_suffix": f"  {SUFFIX}\r\n",
            "answer_instruction": f"\n{INSTRUCTION}\r\n合成の 2 行目。  ",
        }
    )
    assert config.question_suffix == SUFFIX
    assert config.answer_instruction == f"{INSTRUCTION}\n合成の 2 行目。"
    dumped = dump_search_answer_profile_config(config)
    assert parse_search_answer_profile_config(dumped) == config
    merged, applied = resolve_search_answer_profile_settings(get_settings(), config)
    assert applied is True
    assert merged.rag_question_suffix == SUFFIX
    assert merged.rag_answer_instruction == f"{INSTRUCTION}\n合成の 2 行目。"
    # 全体の設定は変えない。
    assert get_settings().rag_question_suffix == ""


@pytest.mark.parametrize(
    ("field", "limit", "label"),
    [
        ("question_suffix", QUESTION_SUFFIX_MAX_CHARS, "質問に足す文は 500 文字以内"),
        ("answer_instruction", ANSWER_INSTRUCTION_MAX_CHARS, "回答の指示に足す文は 2,000 文字以内"),
    ],
    ids=["question_suffix", "answer_instruction"],
)
def test_length_limit_counts_the_trimmed_value(field: str, limit: int, label: str) -> None:
    at_limit = SearchAnswerProfileConfig.model_validate({field: " " + "あ" * limit + "\n"})
    assert len(getattr(at_limit, field)) == limit
    with pytest.raises(ValidationError, match=label):
        SearchAnswerProfileConfig.model_validate({field: "あ" * (limit + 1)})


@pytest.mark.parametrize("separator", QUESTION_METADATA_SEPARATORS)
def test_question_suffix_rejects_the_question_separators(separator: str) -> None:
    """元の質問の切り出しの区切り（engine の定義）を含む文は、欄のエラーにする。"""
    with pytest.raises(ValidationError, match="質問に足す文に") as error:
        SearchAnswerProfileConfig.model_validate({"question_suffix": f"合成の文\n{separator}"})
    assert error.value.errors()[0]["loc"] == ("question_suffix",)
    # 回答の指示に足す文は質問として切り出さないので、止めない。
    SearchAnswerProfileConfig.model_validate({"answer_instruction": f"合成の文\n{separator}"})


def test_prompt_version_changes_only_when_an_instruction_is_added() -> None:
    base = answer_prompt_version({})
    assert answer_prompt_version({}, answer_instruction="") == base
    added = answer_prompt_version({}, answer_instruction=INSTRUCTION)
    assert added != base
    assert answer_prompt_version({}, answer_instruction=INSTRUCTION + "合成") != added


# --- 回答の工程（pipeline から回答エンジンまで）---


class RecordingGenAi(FakeGenAi):
    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.rerank_queries: list[str] = []

    async def embed(
        self, texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
    ) -> list[list[float]]:
        self.embedded.extend(texts)
        return await super().embed(texts, input_type=input_type)

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        self.rerank_queries.append(query)
        return await super().rerank(query, documents, top_n)


class TwoHitOracle(SavingQueryOracle):
    """主の検索が 2 件を返す（rerank を通す）。"""

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        await super().hybrid_search(query, embedding, top_k, mode, filters)
        return list(self.chunks)


class Run:
    """1 回の実行で、各段に届いた質問の文を集める。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import rag_engine.adapters.oci as engine_oci

        self.oracle = TwoHitOracle()
        self.genai = RecordingGenAi()
        self.generations: list[tuple[str, str]] = []
        self.backend_queries: dict[str, list[str]] = {"wiki": [], "entity": []}

        def llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
            self.generations.append((system, prompt))
            return _fake_llm(system, prompt, settings, schema, **options)

        async def wiki(_self: Any, request: SearchRequest, state: Any) -> list[Any]:
            self.backend_queries["wiki"].append(request.query)
            return []

        entity_expansion = AnswerEngine._entity_expansion

        async def entity(_self: Any, request: SearchRequest, state: Any, anchors: Any) -> Any:
            self.backend_queries["entity"].append(request.query)
            return await entity_expansion(_self, request, state, anchors)

        monkeypatch.setattr(engine_oci, "parse_text_response", llm)
        monkeypatch.setattr(AnswerEngine, "_wiki_sources", wiki)
        monkeypatch.setattr(AnswerEngine, "_entity_expansion", entity)

    async def run(
        self,
        request: SearchRequest,
        *,
        flow: AnswerFlow = BASIC_FLOW,
        history: list[ChatTurn] | None = None,
        llm: Any = None,
        **settings: Any,
    ) -> Any:
        pipeline = RagPipeline(
            settings=Settings(
                _env_file=None,
                rag_wiki_search_enabled=True,
                # rerank は compartment があるときだけ実行する。
                oci_compartment_id="ocid1.compartment.oc1..example",
                **settings,
            ),
            oracle=cast(Any, self.oracle),
            genai=cast(Any, self.genai),
            llm=cast(Any, llm),
            flow=flow,
        )
        return await pipeline.run(request, trace_id="trace-1", history=history)

    def question_step(self) -> str:
        """最後の生成の入力の「問い合わせ」の Step の本文。"""
        prompt = self.generations[-1][1]
        return prompt.split("## Step 1: 問い合わせ\n", 1)[1].split("\n\n## Step 2:", 1)[0]


def _answer(response: Any) -> dict[str, Any]:
    assert response.diagnostics.answer is not None
    return cast(dict[str, Any], response.diagnostics.answer)


def _step_names(response: Any) -> list[str]:
    return [str(step["name"]) for step in _answer(response)["execution_steps"]]


@pytest.mark.parametrize("flow", [BASIC_FLOW, EXPANDED_FLOW])
async def test_suffix_reaches_every_search_stage_and_generation_but_not_the_saved_question(
    monkeypatch: pytest.MonkeyPatch, flow: AnswerFlow
) -> None:
    run = Run(monkeypatch)
    response = await run.run(
        SearchRequest(query=QUESTION, search_answer_profile_id="profile-a"),
        flow=flow,
        rag_question_suffix=SUFFIX,
        rag_answer_instruction=INSTRUCTION,
    )

    searched = _nfkc(QUESTION) + " " + SUFFIX
    # (b) 文書検索（ベクトルの埋め込み・全文）と rerank と、回答の生成の質問に届く。
    assert run.oracle.queries == [searched]
    assert run.genai.embedded == [searched]
    assert run.genai.rerank_queries == [with_question_suffix(QUESTION, SUFFIX)]
    assert run.question_step() == _nfkc(QUESTION) + "\n" + SUFFIX
    # backend の側の検索（Wiki・検索拡張）は RAG チャットの経路だけ。同じ文が届く。
    expanded = [with_question_suffix(QUESTION, SUFFIX)] if flow == EXPANDED_FLOW else []
    assert run.backend_queries == {"wiki": expanded, "entity": expanded}
    # (a) 返す・保存する質問は利用者の質問のまま。
    answer = _answer(response)
    assert answer["original_question"] == QUESTION
    assert answer["rewritten_question"] == ""
    (saved,) = run.oracle.saved
    assert saved["question"] == QUESTION
    assert saved["rewritten_question"] == ""
    assert saved["evaluation_input"]["question"] == QUESTION
    # 診断にそのときの値と、検索に使った質問を残す。
    assert answer["question_suffix"] == SUFFIX
    assert answer["effective_question"] == with_question_suffix(QUESTION, SUFFIX)
    assert answer["answer_instruction"] == INSTRUCTION
    assert saved["diagnostics"]["question_suffix"] == SUFFIX
    # 処理の経過: 用語・同義語の照合の後・文書検索の前に 1 回。
    names = _step_names(response)
    assert names.count(SUFFIX_STEP) == 1
    assert (
        names.index("用語・同義語による検索文の追加")
        < names.index(SUFFIX_STEP)
        < names.index("文書検索")
    )
    # 回答の指示に足す文は生成の system にだけ入る（役割の文の後・固定の部分の前）。
    system, prompt = run.generations[-1]
    assert system == grounded.generation_system_prompt(answer_instruction=INSTRUCTION)
    assert INSTRUCTION not in prompt
    assert all(INSTRUCTION not in text for text in [*run.oracle.queries, *run.genai.rerank_queries])
    assert answer["provenance"]["prompt_version"] == answer_prompt_version(
        {}, answer_instruction=INSTRUCTION
    )


async def test_empty_fields_keep_the_flow_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)
    response = await run.run(SearchRequest(query=QUESTION), flow=EXPANDED_FLOW)

    assert run.oracle.queries == [_nfkc(QUESTION)]
    assert run.backend_queries == {"wiki": [QUESTION], "entity": [QUESTION]}
    assert run.generations[-1][0] == grounded.generation_system_prompt()
    answer = _answer(response)
    assert not {"question_suffix", "effective_question", "answer_instruction"} & set(answer)
    assert SUFFIX_STEP not in _step_names(response)
    assert answer["provenance"]["prompt_version"] == answer_prompt_version({})


async def test_history_rewrite_reads_the_user_question_and_the_suffix_is_added_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会話: 履歴による補完は利用者の質問で行い、足す文は補完の後にターンごとに 1 回だけ足す。"""
    rewritten = "受注入力画面での受注の登録方法は？"
    llm = RewriteLlm(json.dumps({"question": rewritten}))
    run = Run(monkeypatch)
    turns = [
        ChatTurn(role="USER", content="受注入力画面の使い方は？"),
        ChatTurn(role="ASSISTANT", content="受注番号を入力して登録します。"),
    ]
    user_question = "それの登録方法は？"
    response = await run.run(
        SearchRequest(query=user_question, search_answer_profile_id="profile-a"),
        flow=EXPANDED_FLOW,
        history=turns,
        llm=llm,
        rag_question_suffix=SUFFIX,
    )

    # 補完の入力（質問と履歴）に足す文は無い。
    assert llm.calls == [(user_question, llm.calls[0][1])]
    assert SUFFIX not in llm.calls[0][1]
    # 検索は補完した質問 + 足す文、回答の生成は利用者の質問 + 足す文。
    assert run.oracle.queries == [_nfkc(rewritten) + " " + SUFFIX]
    assert run.backend_queries["wiki"] == [with_question_suffix(rewritten, SUFFIX)]
    assert run.question_step() == _nfkc(user_question) + "\n" + SUFFIX
    # 保存する質問は利用者の質問、補完の質問は足す文を含まない。
    answer = _answer(response)
    (saved,) = run.oracle.saved
    assert (saved["question"], saved["rewritten_question"]) == (user_question, rewritten)
    assert (answer["original_question"], answer["rewritten_question"]) == (user_question, rewritten)
    assert answer["effective_question"] == with_question_suffix(rewritten, SUFFIX)

    # 次のターン: 履歴は保存した質問（利用者の質問）から作るので、足す文は積もらない。
    next_turns = [
        *turns,
        ChatTurn(role="USER", content=str(saved["question"])),
        ChatTurn(role="ASSISTANT", content=response.answer),
    ]
    follow_up = Run(monkeypatch)
    next_llm = RewriteLlm(json.dumps({"question": "合成の次の質問は？"}))
    await follow_up.run(
        SearchRequest(query="次は？", search_answer_profile_id="profile-a"),
        flow=EXPANDED_FLOW,
        history=next_turns,
        llm=next_llm,
        rag_question_suffix=SUFFIX,
    )
    assert SUFFIX not in next_llm.calls[0][1]
    assert follow_up.oracle.queries[0].count(SUFFIX) == 1
    assert follow_up.question_step().count(SUFFIX) == 1


async def test_history_without_a_rewrite_adds_the_suffix_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """補完が質問を変えなかったターン（書き直しなし）も同じ。"""
    run = Run(monkeypatch)
    response = await run.run(
        SearchRequest(query=QUESTION, search_answer_profile_id="profile-a"),
        flow=EXPANDED_FLOW,
        history=[],
        llm=RewriteLlm(json.dumps({"question": QUESTION})),
        rag_question_suffix=SUFFIX,
    )
    assert run.oracle.queries == [_nfkc(QUESTION) + " " + SUFFIX]
    assert run.question_step() == _nfkc(QUESTION) + "\n" + SUFFIX
    assert _answer(response)["rewritten_question"] == ""
    assert run.oracle.saved[0]["question"] == QUESTION


async def test_caller_rewritten_search_text_and_original_question_both_get_the_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP: 呼び出し元が検索の文を言い換え、元の質問を別に渡す（``original_question``）。"""
    run = Run(monkeypatch)
    search_text, original = "受注 登録 方法", "受注を登録する方法を教えてください。"
    response = await run.run(
        SearchRequest(
            query=search_text, original_question=original, search_answer_profile_id="profile-a"
        ),
        flow=EXPANDED_FLOW,
        rag_question_suffix=SUFFIX,
    )
    assert run.oracle.queries == [search_text + " " + SUFFIX]
    assert run.backend_queries["entity"] == [with_question_suffix(search_text, SUFFIX)]
    assert run.question_step() == original + "\n" + SUFFIX
    assert run.oracle.saved[0]["question"] == original
    assert _answer(response)["original_question"] == original


@pytest.mark.parametrize("retrieval_only", [False, True], ids=["evidence", "retrieval_only"])
async def test_search_without_an_answer_uses_the_suffix_and_never_the_instruction(
    monkeypatch: pytest.MonkeyPatch, retrieval_only: bool
) -> None:
    run = Run(monkeypatch)
    response = await run.run(
        SearchRequest(
            query=QUESTION,
            generate_answer=False,
            retrieval_only=retrieval_only,
            search_answer_profile_id="profile-a",
        ),
        rag_question_suffix=SUFFIX,
        rag_answer_instruction=INSTRUCTION,
    )
    assert run.oracle.queries == [_nfkc(QUESTION) + " " + SUFFIX]
    assert run.genai.rerank_queries == [with_question_suffix(QUESTION, SUFFIX)]
    assert run.generations == [] and run.oracle.saved == []
    assert response.citations
    if not retrieval_only:
        answer = _answer(response)
        assert answer["question_suffix"] == SUFFIX
        assert "answer_instruction" not in answer


async def test_chat_attachment_evidence_is_selected_with_the_suffixed_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会話の添付の根拠の選択も検索の側。文書の検索と同じ「質問 + 足す文」で選ぶ。"""
    from types import SimpleNamespace

    from app.rag import chat_attachments

    questions: list[str] = []

    async def evidence(question: str, conversation_id: str, **kwargs: Any) -> list[Any]:
        questions.append(question)
        return []

    monkeypatch.setattr(chat_attachments, "attachment_evidence", evidence)
    monkeypatch.setattr(
        chat_attachments,
        "chat_attachment_service",
        lambda: SimpleNamespace(reading_status=lambda: "done"),
    )
    run = Run(monkeypatch)
    pipeline = RagPipeline(
        settings=Settings(_env_file=None, rag_question_suffix=SUFFIX),
        oracle=cast(Any, run.oracle),
        genai=cast(Any, run.genai),
        attachment_conversation_id="conv-1",
    )
    await pipeline.run(SearchRequest(query=QUESTION), trace_id="trace-1")
    assert questions == [with_question_suffix(QUESTION, SUFFIX)]
    assert run.oracle.saved[0]["question"] == QUESTION


async def test_input_guardrail_inspects_the_user_question_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected: list[str] = []

    class Recording:
        def inspect_text(self, text: str) -> GuardrailInspection:
            inspected.append(text)
            return GuardrailInspection()

    monkeypatch.setattr(guardrails, "OciGuardrailsClient", lambda settings: Recording())
    run = Run(monkeypatch)
    await run.run(
        SearchRequest(query=QUESTION, search_answer_profile_id="profile-a"),
        rag_question_suffix=SUFFIX,
        rag_answer_instruction=INSTRUCTION,
        guardrails_input_enabled=True,
        guardrails_output_enabled=True,
    )
    assert QUESTION in inspected
    assert all(SUFFIX not in text and INSTRUCTION not in text for text in inspected)
    assert run.oracle.queries == [_nfkc(QUESTION) + " " + SUFFIX]


def _engine_must_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        raise AssertionError("検索しない回答で回答エンジンを呼んだ")

    monkeypatch.setattr(AnswerEngine, "run", fail)


def _assert_no_extra_instruction(response: Any, saved: Mapping[str, Any]) -> None:
    answer = _answer(response)
    assert not {"question_suffix", "effective_question", "answer_instruction"} & set(answer)
    assert SUFFIX not in json.dumps(saved, ensure_ascii=False, default=str)
    assert INSTRUCTION not in json.dumps(saved, ensure_ascii=False, default=str)


@pytest.mark.parametrize("intent", ["greeting", "small_talk"])
async def test_small_talk_is_judged_on_the_user_question_and_gets_no_extra_instruction(
    monkeypatch: pytest.MonkeyPatch, intent: str
) -> None:
    """雑談の判定（履歴による補完の発話の種類）と応答は利用者の発話だけで行い、検索しない。"""
    _engine_must_not_run(monkeypatch)
    llm = FakeLlm(intent=intent)
    oracle = SavingQueryOracle()
    pipeline = RagPipeline(
        settings=Settings(
            _env_file=None,
            rag_history_rewrite_enabled=True,
            rag_question_suffix=SUFFIX,
            rag_answer_instruction=INSTRUCTION,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
        llm=cast(Any, llm),
        flow=EXPANDED_FLOW,
    )
    response = await pipeline.run(
        SearchRequest(query="合成の挨拶", search_answer_profile_id="profile-a"),
        trace_id="trace-1",
        history=[],
    )
    assert llm.calls == ["history_rewrite", "small_talk_reply"]
    sent = [*llm.rewrite_contexts, *llm.reply_contexts]
    assert all(SUFFIX not in text and INSTRUCTION not in text for text in sent)
    assert response.answer == "合成の応答です。"
    assert oracle.queries == []
    (saved,) = oracle.saved
    assert saved["question"] == "合成の挨拶"
    _assert_no_extra_instruction(response, saved)


@pytest.mark.parametrize("case", ["approved_faq", "guide_clarification"])
async def test_answers_without_retrieval_get_no_extra_instruction(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """選んだ登録済みQ&Aの回答・業務ガイドの確認の質問は、検索も回答の生成もしない。"""
    _engine_must_not_run(monkeypatch)
    by_case: dict[str, dict[str, Any]] = {
        "approved_faq": {
            "rag_approved_faq_selected": {
                "id": "faq-1",
                "question": "合成の質問",
                "answer": "合成の登録済みの回答",
            }
        },
        "guide_clarification": {
            "rag_support_guide_enabled": True,
            "rag_support_guide": {
                "guide_id": "g-1",
                "revision": 1,
                "title": "合成のガイド",
                "decision": "clarify",
                "short_answer": "合成の確認の質問ですか。",
            },
        },
    }
    oracle = SavingQueryOracle()
    pipeline = RagPipeline(
        settings=Settings(
            _env_file=None,
            rag_question_suffix=SUFFIX,
            rag_answer_instruction=INSTRUCTION,
            **by_case[case],
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
        flow=EXPANDED_FLOW,
    )
    response = await pipeline.run(
        SearchRequest(query="合成の質問", search_answer_profile_id="profile-a"), trace_id="trace-1"
    )
    assert response.answer in {"合成の登録済みの回答", "合成の確認の質問ですか。"}
    (saved,) = oracle.saved
    assert saved["question"] == "合成の質問"
    _assert_no_extra_instruction(response, saved)


# --- 入口（検索・チャット・MCP・品質評価が共有する解決）---


class _ProfileOracle(FakeKnowledgeOracle):
    """追加の指示を持つ検索・回答プロファイルを返す。"""

    async def get_search_answer_profile(self, search_answer_profile_id: str) -> Any:
        view = await super().get_search_answer_profile(search_answer_profile_id)
        if view is None:
            return None
        config = view.config.model_copy(
            update={"question_suffix": SUFFIX, "answer_instruction": INSTRUCTION}
        )
        return view.model_copy(update={"config": config})


class _GuideStore:
    def __init__(self, _oracle: object) -> None:
        pass

    async def published_contents(self, _profile: str) -> list[Any]:
        return GUIDES


@pytest.mark.usefixtures("system_access")
@pytest.mark.parametrize("flow", [BASIC_FLOW, EXPANDED_FLOW])
async def test_entry_resolves_the_fields_and_matches_guides_on_the_user_question(
    monkeypatch: pytest.MonkeyPatch, flow: AnswerFlow
) -> None:
    """入口は足す文を Settings に解決するだけで、質問は変えない。業務ガイドの照合は利用者の質問。"""
    guide_texts: list[str] = []

    async def spy_build(*args: Any, **kwargs: Any) -> Any:
        guide_texts.append(args[4])
        return await build_guide_query(*args, **kwargs)

    fake = _ProfileOracle(support_guide_enabled=True)
    monkeypatch.setattr(search_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(search_route, "SupportGuideStore", _GuideStore)
    monkeypatch.setattr(search_route, "build_guide_query", spy_build)

    request = SearchRequest(query=QUESTION, search_answer_profile_id="bv-1")
    resolved, settings, _, applied = await search_route._resolve_query_context(
        request, get_settings(), flow=flow
    )
    assert applied == "bv-1"
    assert (resolved.query, resolved.original_question) == (QUESTION, None)
    assert settings.rag_question_suffix == SUFFIX
    assert settings.rag_answer_instruction == INSTRUCTION
    assert guide_texts == ([QUESTION] if flow == EXPANDED_FLOW else [])


@pytest.mark.usefixtures("system_access")
@pytest.mark.parametrize("mode", ["search", "chat"])
async def test_evaluation_route_resolves_the_fields_for_its_pipeline(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """品質評価の job は検索・回答と同じ解決を通り、その Settings で pipeline を作る。"""
    from app.rag.evaluation import EVALUATION_ANSWER_ROUTES

    monkeypatch.setattr(search_route, "OracleClient", lambda: _ProfileOracle())
    route = EVALUATION_ANSWER_ROUTES[cast(Any, mode)]
    request = SearchRequest(query=QUESTION, search_answer_profile_id="bv-1")
    resolved, settings = await route.resolve(request, get_settings(), ())
    assert resolved.query == QUESTION
    pipeline = route.pipeline(settings)
    assert pipeline._settings.rag_question_suffix == SUFFIX
    assert pipeline._settings.rag_answer_instruction == INSTRUCTION


def test_instruction_step_is_listed_between_the_role_and_the_fixed_part() -> None:
    """回答プロンプトの画面の一覧でも、役割の文の後・固定の部分の前に並ぶ。"""
    from app.rag.answer_prompts import PROMPT_STAGES

    (generation,) = [stage for stage in PROMPT_STAGES if stage.id == "generation"]
    parts = [part for part, _ref in generation.parts]
    assert parts[parts.index("role") : parts.index("fixed") + 1] == [
        "role",
        "answerInstruction",
        "fixed",
    ]
    assert re.search(r"^## Step \d+: 追加の指示に従う$", grounded.ANSWER_INSTRUCTION_PROMPT, re.M)
