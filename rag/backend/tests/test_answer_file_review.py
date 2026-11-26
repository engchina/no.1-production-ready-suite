"""回答の入口の範囲・新候補の確認・保存の境界。"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Request
from pr_backend_core.file_review import ReviewAction

from app.api.routes import search
from app.mcp.tools import ChatInput, FileSelection, SearchInput, _chat_request, _search_request
from app.rag import file_review
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
        },
    }
    db = AsyncMock()
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
        assert req.filters["file_name"] == "資料"
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
