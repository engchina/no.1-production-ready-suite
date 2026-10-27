"""最終回答も LLM 監査のスイッチに従い MCP と同じ照合を使う。"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.answer_validation import AnswerValidation, EvidenceRef
from app.schemas.search import RetrievedChunk, SearchRequest


@pytest.mark.parametrize("mode", ["none", "standard", "detailed"])
@pytest.mark.parametrize("valid", [True, False])
async def test_final_validation_follows_audit_mode_and_preserves_failed_verdict(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    valid: bool,
) -> None:
    settings = Settings(rag_answer_audit_mode=cast(Any, mode))
    oracle = object()
    engine = AnswerEngine(settings=settings, oracle=cast(Any, oracle), genai=cast(Any, object()))
    citation = RetrievedChunk(
        document_id="doc-synthetic", chunk_id="chunk-synthetic", text="合成の根拠", score=1
    )
    outcome = AnswerOutcome(
        answer="合成の回答",
        citations=[citation],
        diagnostics={"outcome": "answered", "envelope": {"outcome": "answered"}},
        context_text=citation.text,
    )
    validator = AsyncMock(
        return_value=AnswerValidation(
            status="completed",
            valid=valid,
            counts={"supported": 1 if valid else 0},
            llm_usage={"calls": 1},
        )
    )
    monkeypatch.setattr("app.rag.answer_validation.validate_answer", validator)
    callback = AsyncMock()
    await engine._validate_outcome(
        SearchRequest(query="合成の質問"), outcome, step_callback=callback
    )
    if mode == "none":
        validator.assert_not_awaited()
        callback.assert_not_awaited()
        assert outcome.diagnostics["answer_validation"] == {"status": "not_run", "valid": False}
        return
    validator.assert_awaited_once_with(
        "合成の質問",
        "合成の回答",
        [EvidenceRef(citation.document_id, citation.chunk_id)],
        settings,
        oracle=oracle,
    )
    assert outcome.diagnostics["answer_validation"]["valid"] is valid
    assert outcome.diagnostics["execution_steps"][0]["llm_calls"] == 1
    assert callback.await_args_list[0].args[:2] == ("資料との照合", "started")
    assert callback.await_args_list[-1].args[:2] == ("資料との照合", "success")
    if not valid:
        assert outcome.diagnostics["needs_human_review"] is True
        assert outcome.diagnostics["confidence"] == "low"
        assert outcome.diagnostics["outcome"] == "conditional"
        assert outcome.diagnostics["envelope"]["outcome"] == "conditional"
        assert "資料との照合" in outcome.diagnostics["insufficient_reason"]


async def test_final_validation_failure_remains_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = AnswerEngine(
        settings=Settings(rag_answer_audit_mode="standard"),
        oracle=cast(Any, object()),
        genai=cast(Any, object()),
    )
    outcome = AnswerOutcome(answer="合成の回答", citations=[], diagnostics={}, context_text="")
    monkeypatch.setattr(
        "app.rag.answer_validation.validate_answer", AsyncMock(side_effect=TimeoutError())
    )
    await engine._validate_outcome(SearchRequest(query="合成の質問"), outcome)
    assert outcome.diagnostics["answer_validation"] == {"status": "error", "valid": False}
    assert outcome.diagnostics["needs_human_review"] is True
    assert outcome.answer == "合成の回答"


@pytest.mark.parametrize("valid", [None, True, False])
@pytest.mark.parametrize("version", ["", "same", "different"])
async def test_failed_or_unversioned_final_verdict_is_not_reused_as_supported(
    monkeypatch: pytest.MonkeyPatch,
    valid: bool | None,
    version: str,
) -> None:
    from app.rag.answer_engine import ANSWER_CITATION_LINES_KEY
    from app.rag.answer_validation import audited_answer_lines

    diagnostics: dict[str, Any] = {"envelope": {"items": []}}
    if valid is not None:
        diagnostics["answer_validation"] = {
            "valid": valid,
            "status": "completed",
            "reuse_context": version,
        }

    class Oracle:
        async def get_answer_record(self, trace_id: str) -> dict[str, Any]:
            return {
                "answer": "合成の回答",
                "diagnostics_json": diagnostics,
                "citations_json": [
                    {
                        "chunk_id": "chunk-1",
                        "text": "合成の根拠",
                        "metadata": {ANSWER_CITATION_LINES_KEY: [1]},
                    }
                ],
            }

    monkeypatch.setattr(
        "rag_engine.evaluation.answer_validation.audited_answer_items",
        lambda *args: {"合成の回答": ["chunk-1"]},
    )
    reused = await audited_answer_lines(
        cast(Any, Oracle()), ["trace-1"], {"chunk-1": {"text": "合成の根拠"}}, reuse_context="same"
    )
    assert reused == ({"合成の回答": ["chunk-1"]} if valid is True and version == "same" else {})
