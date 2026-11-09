"""要求ごとの根拠の覆域と、根拠の無い要求だけの再検索（#1279）。外部 I/O と LLM は使わない。"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence
from rag_engine.retrieval.request_coverage import (
    TargetedRetrieval,
    coverage_targets,
    select_new_parents,
)

_VOCABULARY = re.compile("請求書|請求|再発行|受注|登録|方法|業務|種別|追加|担当|インボイス|承認|取消|締め|語[0-9]")


def split(text: str) -> list[str]:
    """決定的な分かち書きのスタブ（語彙の一致だけ）。"""
    return _VOCABULARY.findall(text)


def record(uid: str, text: str, level: str = "parent") -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="manual.pdf",
        source_run_id="source",
        chunk_id=uid,
        chunk_uid=f"run:{uid}",
        chunk_level=level,
    )


def parent(uid: str, text: str, child_text: str = "") -> ContextParentEvidence:
    children = (ContextChildEvidence(record(f"{uid}c", child_text, "child"), "retrieved_anchor", "top"),) if child_text else ()
    return ContextParentEvidence(record=record(uid, text), role="synthesis_parent", reason="top", children=children)


def context(*parents: ContextParentEvidence) -> AnswerContext:
    return AnswerContext(records=[p.record for p in parents], text="x", evidence_tree=tuple(parents))


QUESTION = "受注の登録方法は？請求書の再発行方法は？"


def test_request_registration_does_not_assess_evidence() -> None:
    targets = coverage_targets(QUESTION, split)
    assert [t.id for t in targets] == ["Q1"]
    assert targets[0].text == QUESTION


def test_background_sentence_is_not_assessed() -> None:
    targets = coverage_targets("先月から担当が変わった。受注の登録方法を教えてください。", split)

    assert [t.kind for t in targets] == ["request"]


def test_parallel_targets_are_assessed_separately() -> None:
    # 「AとBを追加」は対象ごとに判定する（親の文は一方だけで足りてしまう）。
    targets = coverage_targets("業務と種別を追加したい。", split)

    assert [t.text for t in targets] == ["業務と種別を追加したい。"]


def test_select_new_parents_round_robin_skips_existing_and_caps() -> None:
    first = TargetedRetrieval("Q1", ("a",), (parent("old", "x"), parent("a1", "x"), parent("a2", "x"), parent("a3", "x")))
    second = TargetedRetrieval("Q2", ("b",), (parent("b1", "x"), parent("a1", "x")))

    selected = select_new_parents([first, second], {"run:old"}, max_chunks=3)

    assert [(rid, p.record.chunk_uid) for rid, p in selected] == [("Q1", "run:a1"), ("Q2", "run:b1"), ("Q1", "run:a2")]


def _settings(**changes: Any) -> Any:
    changes.setdefault("request_coverage_retrieval_enabled", True)
    return replace(get_settings(environ={}, dotenv_path=None), **changes)


def _patch_search(monkeypatch: pytest.MonkeyPatch, results: dict[str, AnswerContext]) -> list[tuple[str, tuple]]:
    calls: list[tuple[str, tuple]] = []

    def fake_build(question: str, run_id: Any, engines: Any, settings: Any, **kwargs: Any) -> AnswerContext:
        # 文書の選択と画面目録（LLM）は使わない。
        assert settings.document_selection_enabled is False and settings.screen_linking_enabled is False
        calls.append((question, tuple(kwargs["retrieval_queries"])))
        return results.get(question, context())

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", fake_build)
    monkeypatch.setattr(a, "_coverage_tokenizer", lambda settings: split)
    return calls


def _cover(
    ctx: AnswerContext, settings: Any, question: str = QUESTION, searched: tuple[str, ...] = (), gaps: bool = True
) -> tuple[AnswerContext, dict[str, Any]]:
    return a._cover_missing_requests(
        question,
        ctx,
        settings,
        run_id="run",
        preferred_engines=["docling"],
        top_k=8,
        neighbor_child_count=0,
        rerank_enabled=False,
        retrieval_scope="knowledge_base",
        classification_filter=None,
        runtime_knowledge=None,
        searched_queries=searched,
        unanswered_requests=[{"request_id": "Q1", "status": "partial"}] if gaps else None,
    )


# #2665 の合成の事例: 番号つきの 3 要求の元の質問と、呼び出し元が検索用に言い換えた検索文（番号を除いた文・
# 検索語の連結）。
NUMBERED_ORIGINAL = "1. 受注の登録方法は？\n2. 請求書の再発行方法は？\n3. 承認の担当は？"
SHORTENED_QUERIES = ("受注を登録する。請求書を再発行する。承認の担当を知りたい。", "受注 登録 請求書 再発行 承認 担当")


def _answer_with_request_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, query: str, request_question: str | None
) -> tuple[Any, list[str], list[str]]:
    """検索は「承認」を含む文だけに根拠を返し（前半の 2 要求は根拠なし）、生成は渡された質問を記録する。"""
    from rag_engine.generation.answer_models import AnswerResponse, GroundedAnswer

    searched: list[str] = []
    generated: list[str] = []

    def fake_build(question: str, run_id: Any, engines: Any, settings: Any, **kwargs: Any) -> AnswerContext:
        searched.append(question)
        return context(parent("apr", "承認の担当は課長です。")) if "承認" in question else context()

    def fake_synthesize(question: str, ctx: AnswerContext, settings: Any, **options: Any) -> GroundedAnswer:
        generated.append(question)
        return GroundedAnswer(AnswerResponse(answer_text="承認の担当は課長です。"), ctx, (), options["image_prompt_mode"])

    monkeypatch.setattr(a, "check_adb_hybrid_search_ready", lambda **kwargs: None)
    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", fake_build)
    monkeypatch.setattr(a, "_coverage_tokenizer", lambda settings: split)
    monkeypatch.setattr(a, "synthesize_grounded_answer", fake_synthesize)
    result = a.answer_question_result(
        query,
        "",
        ["docling"],
        replace(_settings(), output_dir=tmp_path),
        query_strategy=a.SIMPLE_RETRIEVAL_LABEL,
        answer_flow=a.STANDARD_ANSWER_FLOW,
        retrieval_scope=a.RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
        request_question=request_question,
    )
    return result, searched, generated
