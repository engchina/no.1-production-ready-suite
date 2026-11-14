"""履歴の補完を確認・送信で共有し、古い文脈を再利用しない。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from pr_system_settings.guardrails import GuardrailPolicy

from app.api.routes import chat
from app.clients.oracle import StoredConversation, StoredMessage
from app.config import Settings
from app.rag.chat_question_token import context_digest, issue_question_token, read_question_token
from app.rag.pipeline import RagPipeline
from app.schemas.search_answer_profile import SearchAnswerProfileDetail


class ContextOracle:
    def __init__(self) -> None:
        self.conversation = StoredConversation(
            id="conversation-a",
            search_answer_profile_id="profile-a",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            user_id_hash="owner-a",
        )
        self.messages = [
            StoredMessage(
                id="user-a",
                conversation_id="conversation-a",
                role="USER",
                content="対象 A について。",
                created_at=datetime.now(UTC),
                status="COMPLETE",
            )
        ]
        self.view = SearchAnswerProfileDetail(
            id="profile-a",
            name="合成プロファイル A",
            status="ACTIVE",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def get_conversation(self, _id: str) -> StoredConversation | None:
        return self.conversation if _id == self.conversation.id else None

    async def list_messages(self, _id: str) -> list[StoredMessage]:
        return self.messages

    async def get_search_answer_profile(self, _id: str) -> SearchAnswerProfileDetail:
        return self.view


@pytest.fixture
def context_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[ContextOracle, Settings, list[str]]:
    oracle = ContextOracle()
    settings = Settings(app_service_token_secret="synthetic-signing-key-" * 3)
    calls: list[str] = []

    async def empty(*args: Any) -> dict[str, Any]:
        return {}

    async def rewrite(self: Any, query: str, history: Any) -> str:
        calls.append(query)
        assert history[0].role == "USER"
        return "対象 A の手順は？ 条件 B との違いは？"

    monkeypatch.setattr(chat, "load_runtime_knowledge_payload", empty)
    monkeypatch.setattr(chat, "_published_guides", empty)
    monkeypatch.setattr(chat, "_ensure_caller_owns", lambda _conversation: None)
    monkeypatch.setattr(RagPipeline, "_safe_rewritten_query", rewrite)
    return oracle, settings, calls


async def test_clarification_context_is_reused_without_second_rewrite(context_setup: Any) -> None:
    oracle, settings, calls = context_setup
    query = "その手順は？ 条件 B との違いは？"
    question, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", query, settings
    )
    assert token
    reused, _ = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", query, settings, token=token
    )
    assert reused == question == "対象 A の手順は？ 条件 B との違いは？"
    assert calls == [query]


@pytest.mark.parametrize("change", ["history", "query", "settings", "profile", "tamper"])
async def test_context_rejects_stale_or_tampered_preparation(
    context_setup: Any, change: str
) -> None:
    oracle, settings, _calls = context_setup
    query = "その手順は？"
    _, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", query, settings
    )
    assert token is not None
    if change == "history":
        oracle.messages[0].content = "対象 B について。"
    elif change == "query":
        query = "別の質問は？"
    elif change == "settings":
        settings = settings.model_copy(update={"rag_history_rewrite_enabled": False})
    elif change == "profile":
        oracle.view.name = "合成プロファイル B"
    else:
        token += "invalid"
    with pytest.raises(HTTPException) as error:
        await chat.prepare_chat_question(
            oracle, "conversation-a", "profile-a", query, settings, token=token
        )
    assert error.value.status_code == 409


async def test_context_checks_conversation_scope_and_owner(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    oracle, settings, calls = context_setup
    with pytest.raises(HTTPException) as missing:
        await chat.prepare_chat_question(oracle, "missing", "profile-a", "その手順は？", settings)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as profile:
        await chat.prepare_chat_question(
            oracle, "conversation-a", "profile-b", "その手順は？", settings
        )
    assert profile.value.status_code == 404

    def forbidden(_conversation: Any) -> None:
        raise HTTPException(status_code=403)

    monkeypatch.setattr(chat, "_ensure_caller_owns", forbidden)
    with pytest.raises(HTTPException) as owner:
        await chat.prepare_chat_question(
            oracle, "conversation-a", "profile-a", "その手順は？", settings
        )
    assert owner.value.status_code == 403
    assert not calls


async def test_disabled_rewrite_does_not_call_model(context_setup: Any) -> None:
    oracle, settings, calls = context_setup
    settings.rag_history_rewrite_enabled = False
    question, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", "その手順は？", settings
    )
    assert question == "その手順は？" and token
    assert not calls


def test_token_binds_subject_and_expires() -> None:
    from app.rag.chat_question_token import _key
    from app.rag.signed_url import SignedTokenError, issue_signed_token

    secret = "synthetic-signing-key-" * 3
    binding = {"owner": "owner-a", "revision": context_digest(["user-a"])}
    token = issue_question_token(secret, binding, "対象 A は？")
    with pytest.raises(ValueError):
        read_question_token(secret, token, {**binding, "owner": "owner-b"})
    expired, _ = issue_signed_token(
        _key(secret),
        version="chat-question-v1",
        claims={"binding": binding, "question": "対象 A は？"},
        now=1,
    )
    with pytest.raises(SignedTokenError):
        read_question_token(secret, expired, binding)


async def test_prepared_query_reaches_search_while_original_is_saved(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.chat import ChatMessageRequest

    oracle, settings, calls = context_setup
    query = "その手順は？ 条件 B との違いは？"
    question, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", query, settings
    )
    captured = []

    async def resolve(request: Any, effective: Any, **kwargs: Any) -> Any:
        captured.append(request)
        return request, effective, None, None

    async def append(message: StoredMessage) -> StoredMessage:
        oracle.messages.append(message)
        return message

    monkeypatch.setattr(chat, "_resolve_query_context", resolve)
    monkeypatch.setattr(oracle, "append_message", append, raising=False)
    turn = await chat._prepare_chat_turn(
        oracle,
        "conversation-a",
        "profile-a",
        ChatMessageRequest(content=query, question_context_token=token),
        settings,
    )
    assert captured[0].query == question
    assert turn.request.original_question == query
    assert turn.user_message.content == query
    assert turn.history_question_prepared
    assert calls == [query]


@pytest.mark.parametrize("case", ["rewritten", "disabled", "answered"])
async def test_prepared_rewrite_reaches_send_side_for_progress_step(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """準備で補った結果と所要時間を送信の側へ渡し、処理の経過に段を残す（#4794）。"""
    from app.rag.chat_question_token import read_history_rewrite_elapsed
    from app.schemas.chat import ChatMessageRequest
    from app.schemas.search_answer_profile_knowledge import GuideConditionAnswer

    oracle, settings, calls = context_setup
    if case == "disabled":
        settings.rag_history_rewrite_enabled = False
    if case == "answered":
        # 業務ガイドを使うプロファイル（確認への答えを受ける）。設定は準備の前に決める。
        oracle.view.config.query.support_guide_enabled = True
    query = "その手順は？"
    _question, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", query, settings
    )
    assert token is not None
    secret = settings.app_service_token_secret
    elapsed = read_history_rewrite_elapsed(secret, token)
    assert (elapsed is None) is (case == "disabled")

    async def resolve(request: Any, effective: Any, **kwargs: Any) -> Any:
        return request, effective, None, None

    async def append(message: StoredMessage) -> StoredMessage:
        return message

    async def no_payload(*args: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(chat, "_resolve_query_context", resolve)
    monkeypatch.setattr(oracle, "append_message", append, raising=False)
    turn_request = ChatMessageRequest(content=query, question_context_token=token)
    if case == "answered":
        # 準備の後に業務ガイドの確認へ新しく答えたら、補った質問を使わない（段も作らない）。
        monkeypatch.setattr(
            chat, "resolve_guide_clarification", lambda *args: ({"c1": "値 A"}, "合成")
        )
        monkeypatch.setattr(chat, "guide_clarification_record", lambda *args: None)
        turn_request = turn_request.model_copy(
            update={
                "clarification": GuideConditionAnswer(
                    guide_condition_id="guide:g1:c1", option_ids=["o1"]
                )
            }
        )
    turn = await chat._prepare_chat_turn(
        oracle, "conversation-a", "profile-a", turn_request, settings
    )
    assert turn.history_question_prepared
    if case == "answered":
        assert turn.prepared_history_rewrite is None
        assert turn.request.query == query
        return
    if case == "disabled":
        assert turn.prepared_history_rewrite is None
        assert not calls
        return
    assert turn.prepared_history_rewrite is not None
    assert turn.prepared_history_rewrite.elapsed_seconds == elapsed
    assert turn.request.query == "対象 A の手順は？ 条件 B との違いは？"
    assert calls == [query]


@pytest.mark.parametrize("outcome", ["answered", "skipped"])
async def test_only_explicit_confirmations_enter_safe_user_history(outcome: str) -> None:
    message = StoredMessage(
        id="user-a",
        conversation_id="conversation-a",
        role="USER",
        content="その手順は？",
        status="COMPLETE",
        created_at=datetime.now(UTC),
        clarification={
            "outcome": outcome,
            "entries": [{"prompt": "対象は？", "selected": ["対象 A"]}],
        },
    )
    turns = await chat._build_safe_history([message], GuardrailPolicy(Settings()))
    assert turns[0].role == "USER"
    assert ("対象 A" in turns[0].content) is (outcome == "answered")


async def test_clarification_api_matches_resolved_question(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routes import search_answer_profile_knowledge as route
    from app.schemas.search_answer_profile_knowledge import ClarificationSuggestRequest

    oracle, settings, calls = context_setup
    settings.rag_support_guide_enabled = False
    oracle.view.config.query.support_guide_enabled = False
    monkeypatch.setattr(route, "OracleClient", lambda: oracle)
    monkeypatch.setattr(route, "get_settings", lambda: settings)

    async def empty(*args: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(route, "load_runtime_knowledge_payload", empty)
    result = await route.post_clarification_suggest(
        "profile-a",
        ClarificationSuggestRequest(
            query="その手順は？ 条件 B との違いは？",
            conversation_id="conversation-a",
        ),
    )
    # 業務ガイドを使わないプロファイルは確認を出さず、補った質問の token だけを返す。
    assert result.data is not None and result.data.suggestion is None
    assert result.data.question_context_token
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["direct", "declined", "guides-off"])
async def test_clarification_is_skipped_when_faq_answer_is_returned_directly(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """補完した質問が承認済み FAQ に下限以上で一致するなら、確認を出さない（#4788）。"""
    from rag_engine.knowledge.approved_faq import ApprovedFaqRecord, ApprovedFaqSuggestion

    from app.api.routes import search_answer_profile_knowledge as route
    from app.rag.search_answer_profile_knowledge import DirectFaqMatch
    from app.schemas.search_answer_profile_knowledge import ClarificationSuggestRequest

    oracle, settings, _calls = context_setup
    guides_enabled = case != "guides-off"
    settings.rag_support_guide_enabled = guides_enabled
    oracle.view.config.query.support_guide_enabled = guides_enabled
    matched: list[str] = []
    guide_queries: list[str] = []
    monkeypatch.setattr(route, "OracleClient", lambda: oracle)
    monkeypatch.setattr(route, "get_settings", lambda: settings)

    async def empty(*args: Any) -> dict[str, Any]:
        return {}

    async def no_guides(*args: Any) -> list[Any]:
        return []

    async def direct(_store: Any, _profile: str, query: str, *args: Any) -> DirectFaqMatch:
        matched.append(query)
        record = ApprovedFaqRecord(id="faq-a", question=query, approved_answer="合成の回答")
        return DirectFaqMatch(
            min_score=0.92,
            best=ApprovedFaqSuggestion(record=record, matched_question=query, score=1.0),
        )

    async def guide_query(*args: Any) -> str:
        guide_queries.append(args[4])
        return str(args[4])

    async def no_context(*args: Any) -> None:
        return None

    async def unchanged(selected: Any, *args: Any) -> Any:
        return selected

    monkeypatch.setattr(route, "load_runtime_knowledge_payload", empty)
    monkeypatch.setattr(route, "_published_guides", no_guides)
    monkeypatch.setattr(route, "match_direct_approved_faq", direct)
    monkeypatch.setattr(route, "build_guide_query", guide_query)
    monkeypatch.setattr(route, "support_guide_context", no_context)
    monkeypatch.setattr(route, "select_guide", lambda *args, **kwargs: None)
    monkeypatch.setattr(route, "resolve_text_conditions", unchanged)
    result = await route.post_clarification_suggest(
        "profile-a",
        ClarificationSuggestRequest(
            query="その手順は？ 条件 B との違いは？",
            conversation_id="conversation-a",
            approved_faq_declined=case == "declined",
        ),
    )
    resolved = "対象 A の手順は？ 条件 B との違いは？"
    # 照合するのは補完した質問。利用者が類似問を断った質問は照合せず、いつもの確認に進む。
    # 確認を出さないプロファイル（業務ガイドを使わない）では、照合もしない。
    assert matched == ([resolved] if case == "direct" else [])
    assert guide_queries == ([resolved] if case == "declined" else [])
    assert result.data is not None and result.data.suggestion is None
    assert result.data.question_context_token


async def test_blocked_confirmation_also_excludes_assistant_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pr_system_settings.guardrails import GuardrailResult

    policy = GuardrailPolicy(Settings())
    monkeypatch.setattr(
        policy,
        "validate_query",
        lambda text: GuardrailResult(
            allowed="合成の拒否対象" not in text, sanitized_text=text, findings=[]
        ),
    )
    user = StoredMessage(
        id="user-a",
        conversation_id="conversation-a",
        role="USER",
        content="その手順は？",
        status="COMPLETE",
        created_at=datetime.now(UTC),
        clarification={
            "outcome": "answered",
            "entries": [{"prompt": "対象は？", "selected": ["合成の拒否対象"]}],
        },
    )
    assistant = StoredMessage(
        id="assistant-a",
        conversation_id="conversation-a",
        role="ASSISTANT",
        content="合成の回答。",
        reply_to_message_id="user-a",
        status="COMPLETE",
        created_at=datetime.now(UTC),
    )
    assert await chat._build_safe_history([user, assistant], policy) == []


async def test_chat_history_understanding_uses_profile_language_scope(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rag_parser_core.language import current_pack

    oracle, settings, _ = context_setup
    oracle.view = oracle.view.model_copy(
        update={
            "config": oracle.view.config.model_copy(
                update={
                    "language_pack": {
                        "question_cues": {"subquestion_reference_followers": ["理屈"]}
                    }
                }
            )
        }
    )
    global_hash = current_pack().hash
    observed: list[tuple[str, ...]] = []

    async def rewrite(self: Any, query: str, history: Any) -> str:
        observed.append(current_pack().words("question_cues", "subquestion_reference_followers"))
        return query

    monkeypatch.setattr(RagPipeline, "_safe_rewritten_query", rewrite)
    await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", "対象 A の理屈を教えてください。", settings
    )
    assert observed == [("理屈",)]
    assert current_pack().hash == global_hash


async def test_prepared_question_rejects_changed_global_language_pack(context_setup: Any) -> None:
    from rag_parser_core.language import default_pack, resolve, set_default_pack

    oracle, settings, _ = context_setup
    _, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", "その手順は？", settings
    )
    assert token
    old = default_pack()
    try:
        set_default_pack(
            resolve(old, {"question_cues": {"subquestion_reference_followers": ["合成"]}})
        )
        with pytest.raises(HTTPException) as rejected:
            await chat.prepare_chat_question(
                oracle, "conversation-a", "profile-a", "その手順は？", settings, token=token
            )
        assert rejected.value.status_code == 409
    finally:
        set_default_pack(old)


async def test_small_talk_suggestion_skips_clarification_and_keeps_intent(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """補完が挨拶と分けたら確認を出さず、条件の読み取りのモデルも呼ばない（#4787）。"""
    from app.api.routes import search_answer_profile_knowledge as route
    from app.rag.chat_question_token import read_history_intent
    from app.schemas.search_answer_profile_knowledge import ClarificationSuggestRequest

    oracle, settings, _calls = context_setup

    async def greeting(self: Any, query: str, history: Any) -> str:
        self._history_intent = "greeting"
        return ""

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("挨拶では条件の読み取りを呼ばない")

    monkeypatch.setattr(RagPipeline, "_safe_rewritten_query", greeting)
    monkeypatch.setattr(route, "OracleClient", lambda: oracle)
    monkeypatch.setattr(route, "get_settings", lambda: settings)
    monkeypatch.setattr(route, "resolve_text_conditions", forbidden)
    result = await route.post_clarification_suggest(
        "profile-a",
        ClarificationSuggestRequest(query="合成の挨拶", conversation_id="conversation-a"),
    )
    assert result.data is not None
    assert result.data.suggestion is None
    token = result.data.question_context_token
    assert token and read_history_intent(settings.app_service_token_secret, token) == "greeting"


async def test_first_turn_preparation_runs_the_single_call_and_keeps_intent(
    context_setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会話の 1 回目（保存済みの発話なし）の準備でも、同じ 1 回の呼び出しを通す（#4787）。"""
    from app.rag.chat_question_token import read_history_intent, read_history_rewrite_elapsed

    oracle, settings, _calls = context_setup
    oracle.messages = []
    seen: list[tuple[str, int]] = []

    async def greeting(self: Any, query: str, history: Any) -> str:
        seen.append((query, len(history)))
        self._history_intent = "greeting"
        return ""

    monkeypatch.setattr(RagPipeline, "_safe_rewritten_query", greeting)
    question, token = await chat.prepare_chat_question(
        oracle, "conversation-a", "profile-a", "合成の挨拶", settings, force_token=True
    )
    assert seen == [("合成の挨拶", 0)]
    assert question == "合成の挨拶" and token
    secret = settings.app_service_token_secret
    assert read_history_intent(secret, token) == "greeting"
    assert read_history_rewrite_elapsed(secret, token) is not None
