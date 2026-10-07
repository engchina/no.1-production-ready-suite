"""回答の最終の検証（#1246）。根拠は今の権限と版で読み直し、主張の監査（モデル）はスタブ。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pytest import MonkeyPatch

from app.config import get_settings
from app.mcp import tools as mcp_tools
from app.rag.answer_validation import AnswerValidation, EvidenceRef, is_valid, validate_answer
from app.schemas.search import RetrievedChunk
from tests.test_mcp_api import _call, _token, auth  # noqa: F401 - fixture を使う


def test_validity_rules() -> None:
    assert is_valid("completed", {"supported": 2, "data_confirmation": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 2, "unsupported": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1, "contradicted": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1, "unassessed": 1}, unreadable=0)
    assert not is_valid("completed", {"supported": 1}, unreadable=1)
    assert not is_valid("completed", {"data_confirmation": 1}, unreadable=0)
    assert not is_valid("input_too_large", {}, unreadable=0)


class _Oracle:
    def __init__(self) -> None:
        self.chunks = {
            ("d1", "c1"): RetrievedChunk(
                document_id="d1",
                chunk_id="c1",
                text="有効期限は最長 30 日です。",
                score=0.5,
                file_name="manual.pdf",
                metadata={"page_start": 1, "page_end": 1},
            )
        }
        self.stale = {("d1", "c-old")}

    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> Any:
        return self.chunks.get((document_id, chunk_id))

    async def accessible_chunk_exists(self, document_id: str, chunk_id: str) -> bool:
        return (document_id, chunk_id) in self.stale


async def test_validate_answer_rereads_evidence_and_reports_unreadable(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_claims(
        question: str, answer: str, items: list[dict[str, Any]], settings: Any
    ) -> dict[str, Any]:
        captured["items"] = items
        return {
            "status": "completed",
            "counts": {"supported": 1},
            "claim_checks": [
                {
                    "answer_quote": answer,
                    "status": "supported",
                    "source_id": "c1",
                    "reason": "根拠に記載",
                }
            ],
            "evidence_truncated": False,
        }

    import rag_engine.evaluation.answer_validation as engine_validation

    monkeypatch.setattr(engine_validation, "validate_answer_claims", fake_claims)
    refs = [
        EvidenceRef("d1", "c1"),
        EvidenceRef("d1", "c-old"),
        EvidenceRef("d9", "x"),
        EvidenceRef("d1", "c1"),
    ]
    result = await validate_answer(
        "q", "有効期限は 30 日です。", refs, get_settings(), oracle=cast(Any, _Oracle())
    )
    # サーバーが読み直した本文だけを監査に渡す（重複は 1 回）。
    assert [item["text"] for item in captured["items"]] == ["有効期限は最長 30 日です。"]
    assert result.stale_evidence == [EvidenceRef("d1", "c-old")]
    assert result.missing_evidence == [EvidenceRef("d9", "x")]
    assert result.valid is False  # 読めない根拠がある

    none = await validate_answer(
        "q", "a", [EvidenceRef("d9", "x")], get_settings(), oracle=cast(Any, _Oracle())
    )
    assert (none.status, none.valid) == ("no_evidence", False)


@pytest.mark.usefixtures("auth")
def test_mcp_validate_answer(auth: Any, monkeypatch: MonkeyPatch) -> None:  # noqa: F811
    async def fake_validate(
        question: str, answer: str, refs: list[EvidenceRef], settings: Any
    ) -> AnswerValidation:
        assert [ref.chunk_id for ref in refs] == ["c1"]
        return AnswerValidation(
            status="completed",
            valid=False,
            counts={"supported": 1, "unsupported": 1},
            claim_checks=[
                {
                    "answer_quote": "30 日です。",
                    "status": "supported",
                    "source_id": "c1",
                    "reason": "記載",
                },
                {
                    "answer_quote": "延長は 90 日。",
                    "status": "unsupported",
                    "source_id": "",
                    "reason": "記載なし",
                },
            ],
        )

    monkeypatch.setattr(mcp_tools, "validate_answer", fake_validate)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_validate_answer",
        {
            "query": "q",
            "answer": "30 日です。延長は 90 日。",
            "evidence": [{"document_id": "d1", "chunk_id": "c1"}],
        },
        _token(user.user_uuid),
    )["structuredContent"]
    assert body["valid"] is False
    assert [claim["status"] for claim in body["claims"]] == ["supported", "unsupported"]
    assert body["claims"][0]["chunk_id"] == "c1" and body["claims"][1]["chunk_id"] is None
    chatter = auth.user_with_permissions("chatter", ["menu.chat"])
    denied = _call(
        "rag_validate_answer",
        {"query": "q", "answer": "a", "evidence": [{"document_id": "d1", "chunk_id": "c1"}]},
        _token(chatter.user_uuid),
    )
    assert denied["isError"] is True
