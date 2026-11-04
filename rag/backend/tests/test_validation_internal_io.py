"""最終照合の内部3工程と、別経路の実IO。DB/LLMは決定論stubだけを使う。"""

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import operation_trace
from rag_engine.config import get_settings as engine_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.search_stages import (
    CONTEXT_SIBLINGS_STAGE,
    ORIGINAL_QUESTION_SEARCH_STAGE,
)

from app.config import Settings
from app.rag import answer_engine as engine_module
from app.rag import answer_validation as module
from app.rag.answer_engine import AnswerEngine, _SearchState, _stored_child, _with_context_scope
from app.rag.pipeline import _observe_engine_call
from app.schemas.search import RetrievedChunk
from tests.test_answer_engine import FakeGenAi, FakeOracle
from tests.test_context_page_scope import chunk, scope
from tests.test_validation_stage_io import call_of, tracing  # noqa: F401 - fixture


class Oracle:
    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> RetrievedChunk | None:
        if chunk_id == "missing":
            return None
        return RetrievedChunk(document_id=document_id, chunk_id=chunk_id, text="合成根拠", score=1)

    async def accessible_chunk_exists(self, document_id: str, chunk_id: str) -> bool:
        return False

    async def answer_prompt_overrides(self) -> dict[str, str]:
        return {}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("timeout", [True, False])
async def test_validation_internal_stages_record_arguments_and_returned_results(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, timeout: bool
) -> None:
    guide = AsyncMock(return_value=([], ["guide"], 2, None))
    audit_result = {"status": "completed", "counts": {"supported": 1}, "claim_checks": []}
    audit = AsyncMock(
        return_value=audit_result,
        side_effect=TimeoutError("private-error-body") if timeout else None,
    )
    monkeypatch.setattr(module, "_guide_findings", guide)
    monkeypatch.setattr(module, "run_answer_in_thread", audit)
    refs = [module.EvidenceRef("doc-a", "chunk-a"), module.EvidenceRef("doc-a", "missing")]
    guide_ref = module.GuideCheckRef("profile-a", "guide-a", 2)
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.validation", operation_id="synthetic") as summary,
    ):
        result = await module.validate_answer(
            "合成の質問",
            "合成回答",
            refs,
            Settings(),
            oracle=cast(Any, Oracle()),
            guide=guide_ref,
        )
    guide.assert_awaited_once()
    audit.assert_awaited_once()
    for name in [module.GUIDE_LOAD_STAGE, module.EVIDENCE_LOAD_STAGE, module.CLAIM_AUDIT_STAGE]:
        call = call_of(summary, name)
        assert call.capture_enabled is enabled
        assert "private-error-body" not in call.model_dump_json()
        if not enabled:
            assert call.input.text == call.output.text == ""
    if enabled:
        guide_call = call_of(summary, module.GUIDE_LOAD_STAGE)
        assert json.loads(guide_call.input.text)["参照した業務ガイド"]["guide_id"] == "guide-a"
        assert json.loads(guide_call.output.text)["公開版"] == 2
        evidence = call_of(summary, module.EVIDENCE_LOAD_STAGE)
        output = json.loads(evidence.output.text)
        assert [item["id"] for item in output["読んだ根拠"]] == ["chunk-a"]
        assert output["現在読めない根拠"] == [{"document_id": "doc-a", "chunk_id": "missing"}]
        audit_call = call_of(summary, module.CLAIM_AUDIT_STAGE)
        assert json.loads(audit_call.input.text)["読み直した根拠"] == output["読んだ根拠"]
        audit_output = json.loads(audit_call.output.text)
        assert audit_output["時間切れ"] is timeout
        if timeout:
            assert audit_output["監査の返却結果"] is None
        else:
            assert audit_output["監査の返却結果"]["status"] == result.status
    assert result.status == ("timed_out" if timeout else "completed")


async def test_partial_evidence_read_is_retained_when_next_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = Oracle()
    original = oracle.retrievable_chunk

    async def read(document_id: str, chunk_id: str) -> RetrievedChunk | None:
        if chunk_id == "broken":
            raise RuntimeError("private-error-body")
        return await original(document_id, chunk_id)

    monkeypatch.setattr(oracle, "retrievable_chunk", read)
    refs = [module.EvidenceRef("doc-a", "chunk-a"), module.EvidenceRef("doc-a", "broken")]
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.validation", operation_id="synthetic") as summary,
        pytest.raises(RuntimeError),
    ):
        await module.validate_answer(
            "合成の質問", "合成回答", refs, Settings(), oracle=cast(Any, oracle)
        )
    call = call_of(summary, module.EVIDENCE_LOAD_STAGE)
    output = json.loads(call.output.text)
    assert output["返却なし"] is True
    assert output["例外の型"] == "RuntimeError"
    assert [item["id"] for item in output["得られた部分結果"]["読んだ根拠"]] == ["chunk-a"]
    assert "private-error-body" not in call.model_dump_json()


def test_disabled_content_never_evaluates_internal_stage_snapshots() -> None:
    def forbidden() -> Any:
        raise AssertionError("OFFではsnapshotを構築しない")

    with (
        call_content_policy(lambda: False),
        operation_trace("rag.validation", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
        module._validation_stage("合成工程", forbidden, forbidden),
    ):
        pass
    call = call_of(summary, "合成工程")
    assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
def test_original_question_search_has_its_own_stage_io(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, failed: bool
) -> None:
    row = _stored_child(chunk("chunk-2", 2), rrf_score=1)
    seen: list[dict[str, Any]] = []

    def search(**kwargs: Any) -> Any:
        seen.append(kwargs)
        if failed:
            raise RuntimeError("private-error-body")
        return SimpleNamespace(child_chunks=[row])

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("別の呼出しは増やさない")

    monkeypatch.setattr(answering, "search_adb_hybrid_chunks", search)
    dependencies = AnswerDependencies(
        search=unused,
        check_ready=unused,
        rerank=unused,
        parse_text=unused,
        parse_images=unused,
        article_question=lambda: "元の合成質問",
    )
    settings = replace(
        engine_settings(environ={}, dotenv_path=None), article_catalog_original_question_search=True
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
        bind_dependencies(dependencies),
    ):
        result = answering._original_question_children(
            "合成検索文",
            ["合成検索文"],
            {"settings": settings, "retrieval_queries": ["合成検索文"]},
            settings,
        )
    assert len(seen) == 1
    assert len(result) == (0 if failed else 1)
    call = call_of(summary, ORIGINAL_QUESTION_SEARCH_STAGE)
    assert "private-error-body" not in call.model_dump_json()
    if enabled:
        assert json.loads(call.input.text)["検索文"] == seen[0]["retrieval_queries"]
        output = json.loads(call.output.text)
        if failed:
            assert output == {"返却なし": True, "例外の型": "RuntimeError"}
        else:
            assert output["読取候補"][0]["ID"] == "chunk-2"
    else:
        assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failed", [True, False])
async def test_context_fallback_records_actual_scope_and_returned_neighbors(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, failed: bool
) -> None:
    oracle = FakeOracle()
    values = [chunk("chunk-1", 1), chunk("chunk-2", 2)]
    called: list[list[str]] = []

    async def siblings(anchors: list[RetrievedChunk], **kwargs: Any) -> list[RetrievedChunk]:
        called.append([item.chunk_id for item in anchors])
        if failed:
            raise RuntimeError("private-error-body")
        return values

    monkeypatch.setattr(oracle, "context_group_siblings", siblings)
    if not enabled:
        monkeypatch.setattr(
            engine_module,
            "_trace_chunks",
            lambda *args: (_ for _ in ()).throw(AssertionError("OFF")),
        )
    state = _SearchState(chunks={item.chunk_id: item for item in values})
    engine = AnswerEngine(
        Settings(rag_neighbor_child_count=1), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi())
    )
    anchor = _stored_chunk_answer_record(
        _with_context_scope(_stored_child(values[1], rrf_score=1), scope())
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        if failed:
            with pytest.raises(RuntimeError):
                await engine._context_chunks(state, anchors=[anchor])
        else:
            rows = await engine._context_chunks(state, anchors=[anchor])
            assert [row.chunk_uid for row in rows] == ["chunk-2"]
    assert called == [["chunk-2"]]
    call = call_of(summary, CONTEXT_SIBLINGS_STAGE)
    assert "private-error-body" not in call.model_dump_json()
    if enabled:
        assert json.loads(call.input.text)["読取関数へ渡した起点 ID"] == called[0]
        output = json.loads(call.output.text)
        if failed:
            assert output["返却なし"] is True
        else:
            assert output["範囲外で除外した ID"] == ["chunk-1"]
            assert [row["chunk_id"] for row in output["範囲内の返却 Chunk"]] == ["chunk-2"]
    else:
        assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("stage", [module.GUIDE_LOAD_STAGE, module.CLAIM_AUDIT_STAGE])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_internal_failure_or_cancel_does_not_invent_a_return_value(
    monkeypatch: pytest.MonkeyPatch, stage: str, cancelled: bool
) -> None:
    import asyncio

    error = asyncio.CancelledError if cancelled else RuntimeError
    guide = AsyncMock(return_value=([], ["guide"], 2, None))
    audit = AsyncMock(return_value={"status": "completed"})
    (guide if stage == module.GUIDE_LOAD_STAGE else audit).side_effect = error("private-error-body")
    monkeypatch.setattr(module, "_guide_findings", guide)
    monkeypatch.setattr(module, "run_answer_in_thread", audit)
    with (
        call_content_policy(lambda: True),
        operation_trace("rag.validation", operation_id="synthetic") as summary,
        pytest.raises(error),
    ):
        await module.validate_answer(
            "合成の質問",
            "合成回答",
            [module.EvidenceRef("doc-a", "chunk-a")],
            Settings(),
            oracle=cast(Any, Oracle()),
            guide=module.GuideCheckRef("profile-a", "guide-a", 2),
        )
    call = call_of(summary, stage)
    output = json.loads(call.output.text)
    assert output["返却なし"] is True
    assert output["例外の型"] == error.__name__
    assert "private-error-body" not in call.model_dump_json()
    if stage == module.CLAIM_AUDIT_STAGE:
        assert output["得られた部分結果"]["監査の返却結果"] is None
        assert output["得られた部分結果"]["時間切れ"] is False
