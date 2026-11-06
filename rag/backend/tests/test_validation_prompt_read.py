"""監査指示の読取失敗と、照合結果の再利用失効を区別する。"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import rag_engine.evaluation.answer_validation as engine_validation
from rag_engine.resources.runtime import (
    bind_prompt_overrides,
    resolve_prompt,
    saved_prompt_overrides,
)

from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag import answer_validation as validation
from app.schemas.search import RetrievedChunk

KEY = "answer_validation.system"


class Oracle:
    def __init__(self, reader: AsyncMock) -> None:
        self.answer_prompt_overrides = reader

    async def retrievable_chunk(self, document_id: str, chunk_id: str) -> RetrievedChunk:
        return RetrievedChunk(
            document_id=document_id, chunk_id=chunk_id, text="値は42です。", score=1
        )

    async def accessible_chunk_exists(self, document_id: str, chunk_id: str) -> bool:
        return True


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def claims(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(resolve_prompt(KEY, "builtin"))
        return {"status": "completed", "counts": {"supported": 1}, "claim_checks": []}

    async def same_thread(call: Any, **kwargs: Any) -> Any:
        return call()

    monkeypatch.setattr(engine_validation, "validate_answer_claims", claims)
    monkeypatch.setattr(validation, "run_answer_in_thread", same_thread)
    return seen


async def run(reader: AsyncMock) -> validation.AnswerValidation:
    return await validation.validate_answer(
        "値は？",
        "値は42です。",
        [validation.EvidenceRef("doc-a", "chunk-a")],
        Settings(),
        oracle=cast(OracleClient, Oracle(reader)),
    )


async def test_read_failure_stops_before_audit(audit: list[str]) -> None:
    reader = AsyncMock(side_effect=RuntimeError("synthetic configuration read failure"))
    with pytest.raises(RuntimeError, match="configuration read failure"):
        await run(reader)
    reader.assert_awaited_once()
    assert audit == []
    assert saved_prompt_overrides() is None


@pytest.mark.parametrize("overrides", [{}, {KEY: "保存した監査指示"}])
@pytest.mark.parametrize("reuse_failure", [False, True])
async def test_reuse_failure_preserves_loaded_instructions(
    overrides: dict[str, str],
    reuse_failure: bool,
    audit: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if reuse_failure:

        def invalid_context(*args: Any, **kwargs: Any) -> str:
            raise ValueError("synthetic reuse context failure")

        monkeypatch.setattr(validation, "validation_reuse_context", invalid_context)
    reader = AsyncMock(return_value=overrides)
    result = await run(reader)
    assert result.status == "completed"
    assert audit == [overrides.get(KEY, "builtin")]
    reader.assert_awaited_once()
    assert saved_prompt_overrides() is None


@pytest.mark.parametrize("overrides", [{}, {KEY: "要求に固定した監査指示"}])
async def test_bound_snapshot_does_not_read_again(
    overrides: dict[str, str], audit: list[str]
) -> None:
    reader = AsyncMock(side_effect=RuntimeError("must not read"))
    with bind_prompt_overrides(overrides):
        result = await run(reader)
    reader.assert_not_awaited()
    assert result.status == "completed"
    assert audit == [overrides.get(KEY, "builtin")]
    assert saved_prompt_overrides() is None
