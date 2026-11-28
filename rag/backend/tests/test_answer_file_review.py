"""回答の入口の範囲・新候補の確認・保存の境界。"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Request
from pr_backend_core.file_review import ReviewAction, StaleReview
from pr_system_settings.guardrails import GuardrailPolicy

from app.api.routes import chat as chat_route
from app.api.routes import search
from app.clients import oracle as oracle_client
from app.clients.oracle import StoredMessage
from app.config import Settings
from app.mcp.tools import ChatInput, FileSelection, SearchInput, _chat_request, _search_request
from app.rag import file_review
from app.rag.run_history import RunRecorder, search_run_conditions
from app.schemas.search import RetrievedChunk, SearchRequest


def hit(doc: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=doc,
        file_name=doc + ".txt",
        chunk_id=doc + ":1",
        text="合成の根拠",
        score=1.0,
        metadata={"page_number": 1},
    )


@pytest.mark.parametrize("surface", ["chat", "search"])
@pytest.mark.anyio
async def test_owner_confirms_candidates_before_creating_answer(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {
        "user_id_hash": "owner",
        "review_state": None,
        "review_request": {
            "flow": "expanded" if surface == "chat" else "basic",
            "request": {"query": "元の質問", "filters": {"file_name": "資料"}},
            "conversation_id": "conversation-a" if surface == "chat" else None,
        },
    }
    db = AsyncMock()
    db.get_chat_answer_message_id.return_value = "message-a"
    db.get_file_review.return_value = row
    db.get_answer_record.return_value = {
        "trace_id": "first",
        "question": "元の質問",
        "answer": "初回の回答",
        "answer_engine": "grounded",
        "surface": surface,
        "created_at": datetime.now(UTC),
        "citations_json": [hit(d).model_dump() for d in "AB"],
    }
    db.file_review_ratings.return_value = {"first": "not_helpful"}
    db.accessible_document_ids.side_effect = lambda ids: set(ids)
    monkeypatch.setattr(file_review, "OracleClient", lambda: db)
    monkeypatch.setattr(file_review, "caller_owns", lambda owner: owner == "owner")
    request = Request({"type": "http"})
    request.state.principal = Mock()
    request.state.principal.has_permission.side_effect = lambda code: code == "menu." + surface
    calls = []

    async def search_again(req: SearchRequest, **kwargs: object) -> Mock:
        calls.append(req)
        assert kwargs["flow"] == ("expanded" if surface == "chat" else "basic")
        assert req.filters["file_name"] == "資料"
        # 再回答も候補の検索も、元の回答と同じ種類の実行として実行履歴に残す（#5913）。
        recorder = kwargs["run_recorder"]
        assert isinstance(recorder, RunRecorder)
        assert recorder.surface == surface
        if surface == "chat":
            assert (recorder.conversation_id, recorder.message_id) == (
                "conversation-a",
                "message-a",
            )
            assert recorder.conditions is None
        else:
            assert (recorder.conversation_id, recorder.message_id) == (None, None)
            assert recorder.conditions == search_run_conditions(req)
        if req.generate_answer:
            assert json.loads(req.filters["include_document_ids"]) == ["C"]
            staged = kwargs["staged_answer"]
            assert isinstance(staged, dict)
            staged.update(trace_id="second")
        return Mock(
            trace_id="second" if req.generate_answer else "candidates",
            citations=[hit("C")],
            answer="再回答" if req.generate_answer else "",
            notice=None,
            storage_failures=[],
        )

    monkeypatch.setattr(search, "_run_search_with_timeout", search_again)
    first = await file_review.review_answer(request, "first")
    assert first.versions[0].rating == "not_helpful"
    discover = ReviewAction(revision=0, task="question", action="discover", selected=["A"])
    found = await file_review.review_answer(request, "first", discover)
    assert len(found.versions) == 1 and found.kept["question"] == ["A"]
    assert json.loads(calls[0].filters["exclude_document_ids"]) == ["A", "B"]
    assert db.save_file_review.await_args.args[-1] == {}
    row["review_state"] = found.model_dump(mode="json")
    done = await file_review.review_answer(
        request, "first", ReviewAction(revision=1, task="question", action="answer", selected=["C"])
    )
    assert done.versions[-1].id == "second" and done.versions[-1].rating is None
    assert set(done.excluded) == {"A", "B"}
    assert db.save_file_review.await_args.args[-1] == {"trace_id": "second", "surface": surface}
    monkeypatch.setattr(file_review, "caller_owns", lambda _: False)
    with pytest.raises(HTTPException) as error:
        await file_review.review_answer(request, "first", discover)
    assert error.value.status_code == 403


@pytest.mark.anyio
async def test_unsaved_review_answer_is_marked_on_its_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再回答を作った後に版を確定できなければ、その実行に保存できなかったことを残す（#5913）。"""
    db = AsyncMock()
    db.get_file_review.return_value = {
        "user_id_hash": "owner",
        "review_state": None,
        "review_request": {"flow": "basic", "request": {"query": "元の質問"}},
    }
    db.get_answer_record.return_value = {
        "trace_id": "first",
        "question": "元の質問",
        "answer": "初回の回答",
        "answer_engine": "grounded",
        "surface": "search",
        "created_at": datetime.now(UTC),
        "citations_json": [hit(d).model_dump() for d in "AB"],
    }
    db.accessible_document_ids.side_effect = lambda ids: set(ids)
    db.save_file_review.side_effect = StaleReview("別の操作が先に保存されました。")
    saved: list[dict[str, object]] = []

    class Store:
        def __init__(self, _oracle: object) -> None:
            pass

        async def save_run(self, record: dict[str, object]) -> None:
            saved.append(dict(record))

        async def purge_runs(self, retention_days: int) -> int:
            return 0

    monkeypatch.setattr(file_review, "OracleClient", lambda: db)
    monkeypatch.setattr(file_review, "RunHistoryStore", Store)
    monkeypatch.setattr(file_review, "caller_owns", lambda owner: owner == "owner")
    request = Request({"type": "http"})
    request.state.principal = Mock()
    request.state.principal.has_permission.return_value = True

    async def answer_again(req: SearchRequest, **kwargs: object) -> Mock:
        staged = kwargs["staged_answer"]
        recorder = kwargs["run_recorder"]
        assert isinstance(staged, dict) and isinstance(recorder, RunRecorder)
        staged.update(trace_id="second")
        # pipeline が実行の終わりに残す記録（成功）。
        await recorder.record(
            run_id="second",
            question=req.query,
            search_answer_profile_id=None,
            answer_generated=True,
            status="success",
            elapsed_ms=1.0,
        )
        return Mock(
            trace_id="second",
            citations=[hit("A")],
            answer="再回答",
            notice=None,
            storage_failures=[],
        )

    monkeypatch.setattr(search, "_run_search_with_timeout", answer_again)
    with pytest.raises(HTTPException) as error:
        await file_review.review_answer(
            request,
            "first",
            ReviewAction(revision=0, task="question", action="answer", selected=["A"]),
        )
    assert error.value.status_code == 409
    assert [record["run_id"] for record in saved] == ["second", "second"]
    first, last = saved[0]["stages"], saved[-1]["stages"]
    assert isinstance(first, dict) and isinstance(last, dict)
    assert "storage_failures" not in first
    assert saved[-1]["status"] == "success"
    assert last["storage_failures"] == [
        {"step": "answer_record_save", "error_type": "StaleReview", "error_code": None}
    ]


def _limited_review(
    monkeypatch: pytest.MonkeyPatch, documents: str, limit: int
) -> tuple[Request, AsyncMock, list[SearchRequest]]:
    """上限 ``limit`` 件の設定で、``documents`` の資料を根拠に持つ回答を用意する。"""
    db = AsyncMock()
    db.get_file_review.return_value = {
        "user_id_hash": "owner",
        "review_state": None,
        "review_request": {"flow": "basic", "request": {"query": "元の質問"}},
    }
    db.get_answer_record.return_value = {
        "trace_id": "first",
        "question": "元の質問",
        "answer": "初回の回答",
        "answer_engine": "grounded",
        "surface": "search",
        "created_at": datetime.now(UTC),
        "citations_json": [hit(d).model_dump() for d in documents],
    }
    db.file_review_ratings.return_value = {}
    db.accessible_document_ids.side_effect = lambda ids: set(ids)
    monkeypatch.setattr(file_review, "OracleClient", lambda: db)
    monkeypatch.setattr(file_review, "caller_owns", lambda owner: owner == "owner")
    monkeypatch.setattr(
        file_review, "get_settings", lambda: Settings(rag_file_review_max_files=limit)
    )
    request = Request({"type": "http"})
    request.state.principal = Mock()
    request.state.principal.has_permission.return_value = True
    calls: list[SearchRequest] = []

    async def search_again(req: SearchRequest, **kwargs: object) -> Mock:
        calls.append(req)
        if req.generate_answer:
            staged = kwargs["staged_answer"]
            assert isinstance(staged, dict)
            staged.update(trace_id="second")
            included = json.loads(req.filters["include_document_ids"])
            return Mock(
                trace_id="second",
                citations=[hit(d) for d in included],
                answer="再回答",
                notice=None,
                storage_failures=[],
            )
        return Mock(
            trace_id="candidates", citations=[hit("Z")], answer="", notice=None, storage_failures=[]
        )

    monkeypatch.setattr(search, "_run_search_with_timeout", search_again)
    return request, db, calls


@pytest.mark.parametrize(
    ("action", "method"),
    [("answer", "only"), ("discover", "mix"), ("answer", None), ("discover", None)],
    ids=["only", "mix", "candidates-answer", "candidates-more"],
)
@pytest.mark.anyio
async def test_selection_over_the_limit_is_rejected_with_422(
    action: str, method: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """選んだ資料だけで回答・選んだ資料に追加して探すは、上限を超える選択を 422 にする（#5981）。"""
    request, db, calls = _limited_review(monkeypatch, "ABCD", limit=3)
    first = await file_review.review_answer(request, "first")
    assert first.selection_limit == 3
    command = ReviewAction.model_validate(
        {
            "revision": 0,
            "task": "question",
            "action": action,
            "selected": list("ABCD"),
            "method": method,
        }
    )
    with pytest.raises(HTTPException) as error:
        await file_review.review_answer(request, "first", command)
    assert error.value.status_code == 422
    assert error.value.detail == "一度に選べる資料は 3 件までです。"
    # 検索・生成も保存もしない。
    assert calls == []
    db.save_file_review.assert_not_awaited()
    # 上限ちょうどの選択は通り、応答は今の設定の上限を返す。
    accepted = await file_review.review_answer(
        request, "first", command.model_copy(update={"selected": list("ABC")})
    )
    assert accepted.selection_limit == 3
    assert accepted.kept["question"] == ["A", "B", "C"]


@pytest.mark.anyio
async def test_replace_is_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """「別の資料を探す」は選んだ資料の数を確かめない（#5981）。"""
    request, db, calls = _limited_review(monkeypatch, "ABCD", limit=3)
    found = await file_review.review_answer(
        request,
        "first",
        ReviewAction(
            revision=0, task="question", action="discover", selected=list("ABCD"), method="replace"
        ),
    )
    assert found.selection_limit == 3
    assert found.kept["question"] == ["A", "B", "C", "D"]
    assert len(calls) == 1 and not calls[0].generate_answer
    db.save_file_review.assert_awaited_once()


def test_file_review_limit_setting_has_a_default_and_a_range() -> None:
    """上限は RAG の設定 RAG_FILE_REVIEW_MAX_FILES（既定 5、1〜50。#5981）。"""
    from pr_backend_core.config import settings_env_names
    from pydantic import ValidationError

    assert settings_env_names(Settings)["rag_file_review_max_files"] == "RAG_FILE_REVIEW_MAX_FILES"
    assert Settings().rag_file_review_max_files == 5
    for value in (0, 51):
        with pytest.raises(ValidationError):
            Settings(rag_file_review_max_files=value)


@pytest.mark.anyio
async def test_mcp_internal_tool_returns_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent の同じ面は、MCP の内部のツールで RAG の設定の上限を読む（#5981）。"""
    from app.mcp import tools as mcp_tools

    monkeypatch.setattr(mcp_tools, "get_settings", lambda: Settings(rag_file_review_max_files=7))
    output = await mcp_tools.file_review_limits(mcp_tools.FileReviewLimitsInput())
    assert output.selection_limit == 7
    tool = mcp_tools.build_rag_mcp_server(None).tools["rag_file_review_limits"]  # type: ignore[arg-type]
    assert tool.listed is False and tool.read_only


@pytest.mark.anyio
async def test_chat_history_uses_latest_reviewed_answer() -> None:
    """資料を選び直して回答し直した回答は、続きの質問の履歴に最新の版を使う（#5914）。"""
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def turn(index: int, reviewed_answer: str | None) -> list[StoredMessage]:
        return [
            StoredMessage(
                id=f"u{index}",
                conversation_id="c",
                role="USER",
                content=f"質問{index}",
                created_at=now,
            ),
            StoredMessage(
                id=f"a{index}",
                conversation_id="c",
                role="ASSISTANT",
                content=f"元の回答{index}",
                reply_to_message_id=f"u{index}",
                created_at=now,
                reviewed_answer=reviewed_answer,
            ),
        ]

    turns = await chat_route._build_safe_history(
        [*turn(1, "選び直した回答1"), *turn(2, None)],
        GuardrailPolicy(Settings(guardrails_input_enabled=False, guardrails_output_enabled=False)),
    )
    assert [(item.role, item.content) for item in turns] == [
        ("USER", "質問1"),
        ("ASSISTANT", "選び直した回答1"),
        ("USER", "質問2"),
        ("ASSISTANT", "元の回答2"),
    ]


@pytest.mark.anyio
async def test_list_messages_statement_keeps_order_by_after_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実行される文（改行を消した後）で ORDER BY と bind が行コメントに入らない（#5923）。"""
    statements: list[str] = []

    async def fetch_all(statement: str, binds: object = None) -> list[dict[str, object]]:
        statements.append(oracle_client._normalize_sql(statement))
        return []

    client = oracle_client.OracleClient()
    monkeypatch.setattr(client, "_fetch_all", fetch_all)
    assert await client.list_messages("conversation-a", limit=5) == []
    (statement,) = statements
    assert "--" not in statement
    assert statement.index(":conversation_id") < statement.index("ORDER BY m.created_at ASC")
    assert statement.endswith("FETCH NEXT :limit ROWS ONLY")
    assert "$.versions[last].answer" in statement


def test_both_mcp_tools_share_retrieval_constraints() -> None:
    selection = FileSelection(include=["doc-a"], exclude=["doc-b"], candidates_only=True)
    for request in (
        _chat_request(ChatInput(query="質問", file_selection=selection)),
        _search_request(SearchInput(query="質問", file_selection=selection)),
    ):
        assert not request.generate_answer
        assert json.loads(request.filters["include_document_ids"]) == ["doc-a"]
        assert json.loads(request.filters["exclude_document_ids"]) == ["doc-b"]
    filters = file_review.scoped_filters({"include_document_ids": '["doc-c"]'}, ["doc-a"], [])
    assert json.loads(filters["include_document_ids"]) == []
