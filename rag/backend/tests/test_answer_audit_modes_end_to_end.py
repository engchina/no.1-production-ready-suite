"""正式回答入口で監査モードの設定・呼出・最終状態をつなげて確認する。"""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from openai import APITimeoutError
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.resources.deadline import DeadlineExceeded

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome, build_engine_settings
from app.schemas.search import SearchRequest
from tests.audit_fixtures import is_audit_schema
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm


@pytest.mark.parametrize("mode", ["none", "standard", "detailed"])
def test_backend_settings_override_engine_default_for_every_valid_mode(
    mode: str, tmp_path: Any
) -> None:
    assert Settings().rag_answer_audit_mode == "none"
    converted = build_engine_settings(
        Settings(rag_answer_audit_mode=cast(Any, mode)), output_dir=tmp_path
    )
    assert converted.answer_audit_mode == mode


async def test_none_can_retrieve_an_explicit_gap_without_enabling_an_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tests.test_answer_engine as existing

    original = existing._fake_llm
    calls: list[str] = []
    audit_schemas: list[str] = []

    def model(system: str, prompt: str, config: Any, schema: type, **options: Any) -> Any:
        calls.append(schema.__name__)
        if is_audit_schema(schema):
            audit_schemas.append(schema.__name__)
        return original(system, prompt, config, schema, **options)

    monkeypatch.setattr(existing, "_fake_llm", model)
    oracle = existing.CoverageOracle()
    result = await existing._answer_with_coverage(
        monkeypatch,
        oracle,
        "受注の登録方法は？請求書の再発行方法は？",
        rag_answer_audit_mode="none",
    )
    assert (
        result.diagnostics["request_coverage"]["after_generation"][0]["re_retrieval"]["applied"]
        is True
    )
    assert audit_schemas == []


@pytest.mark.parametrize(
    "invalid_id,reason", [(True, "citation_id_invalid"), (False, "citation_range_out_of_bounds")]
)
async def test_formal_none_entry_keeps_invalid_id_and_range_checks(
    monkeypatch: pytest.MonkeyPatch, invalid_id: bool, reason: str
) -> None:
    import re

    from rag_engine.models.llm import GroundedDraft

    from tests.test_answer_engine import _doc_chunk

    oracle = FakeOracle()
    oracle.chunks = [_doc_chunk("doc-synthetic", "対象画面を開きます。" + "説明が続きます。" * 120)]
    schemas: list[str] = []

    def model(system: str, prompt: str, config: Any, schema: type, **options: Any) -> Any:
        schemas.append(schema.__name__)
        if issubclass(schema, (GroundedDraft, generation_draft_wire_model())):
            found = re.search(r"\[(E[0-9]+)\]", prompt)
            assert found is not None
            return GroundedDraft.model_validate(
                {
                    "summary": "",
                    "confidence": "low",
                    "items": [
                        {
                            "kind": "operation",
                            "text": "資料にない別の説明",
                            "citations": [
                                {
                                    "evidence_id": "absent" if invalid_id else found.group(1),
                                    "lines": "1" if invalid_id else "999",
                                }
                            ],
                        }
                    ],
                }
            )
        return _fake_llm(system, prompt, config, schema, **options)

    monkeypatch.setattr("rag_engine.adapters.oci.parse_text_response", model)
    result = await AnswerEngine(
        Settings(
            rag_answer_audit_mode="none",
            rag_query_strategy="simple_retrieval",
            rag_section_expansion_enabled=False,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
    ).run(SearchRequest(query="対象の操作手順は？"))
    # 質問の対象の抽出(#4351)は回答ごとに 1 回。監査なしでも生成は 1 回だけ。
    assert schemas.count("QuestionMentionAnchors") == 1
    assert [name for name in schemas if name != "QuestionMentionAnchors"] in (
        ["GroundedDraft"],
        ["GroundedDraftWire"],
    )
    marked = result.diagnostics["envelope"]["passage_marks"]
    assert reason in {proof["code"] for row in marked for proof in row["reasons"]}
