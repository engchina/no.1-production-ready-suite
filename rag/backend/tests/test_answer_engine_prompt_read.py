"""API外の通常回答も、保存指示を読めなければ生成へ進まない。"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import rag_engine.generation.answering as answering
import rag_engine.generation.faq_answer as faq
from rag_engine.resources.runtime import (
    bind_prompt_overrides,
    resolve_prompt,
    saved_prompt_overrides,
)

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerEngine
from app.rag.answer_prompts import default_prompt
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle

ROLE_KEY = "generation.role"


def _engine(oracle: Any, *, approved_faq: tuple[str, str] | None = None) -> AnswerEngine:
    return AnswerEngine(
        Settings(),
        oracle=cast(OracleClient, oracle),
        genai=cast(OciGenAiClient, FakeGenAi()),
        approved_faq=approved_faq,
    )


@pytest.fixture
def generation(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def stop_before_provider(*args: Any, **kwargs: Any) -> Any:
        seen.append(resolve_prompt(ROLE_KEY, default_prompt(ROLE_KEY)))
        raise RuntimeError("synthetic generation boundary")

    monkeypatch.setattr(answering, "answer_question_result", stop_before_provider)
    return seen


@pytest.mark.parametrize(
    "error",
    [RuntimeError("synthetic read failure"), NotImplementedError("synthetic adapter loader")],
)
async def test_read_failure_stops_before_generation(
    error: Exception, generation: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    oracle = FakeOracle()
    reader = AsyncMock(side_effect=error)
    monkeypatch.setattr(oracle, "answer_prompt_overrides", reader)
    with pytest.raises(type(error), match=str(error)):
        await _engine(oracle).run(SearchRequest(query="操作 A の手順を教えてください。"))
    reader.assert_awaited_once()
    assert generation == []
    assert saved_prompt_overrides() is None


async def test_missing_adapter_loader_is_not_empty_configuration(generation: list[str]) -> None:
    class MissingLoader:
        def __getattr__(self, name: str) -> Any:
            if name == "answer_prompt_overrides":
                raise AttributeError("answer_prompt_overrides")
            return getattr(backing, name)

    backing = FakeOracle()
    with pytest.raises(AttributeError, match="answer_prompt_overrides"):
        await _engine(MissingLoader()).run(SearchRequest(query="操作 A の手順を教えてください。"))
    assert generation == []


@pytest.mark.parametrize("overrides", [{}, {ROLE_KEY: "保存した合成指示"}])
async def test_successful_load_is_used(
    overrides: dict[str, str], generation: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    oracle = FakeOracle()
    reader = AsyncMock(return_value=overrides)
    monkeypatch.setattr(oracle, "answer_prompt_overrides", reader)
    with pytest.raises(RuntimeError, match="synthetic generation boundary"):
        await _engine(oracle).run(SearchRequest(query="操作 A の手順を教えてください。"))
    assert generation == [overrides.get(ROLE_KEY, default_prompt(ROLE_KEY))]
    reader.assert_awaited_once()
    assert saved_prompt_overrides() is None


@pytest.mark.parametrize("overrides", [{}, {ROLE_KEY: "要求に固定した合成指示"}])
async def test_bound_snapshot_is_not_read_again(
    overrides: dict[str, str], generation: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    oracle = FakeOracle()
    reader = AsyncMock(side_effect=RuntimeError("must not read"))
    monkeypatch.setattr(oracle, "answer_prompt_overrides", reader)
    with (
        bind_prompt_overrides(overrides),
        pytest.raises(RuntimeError, match="synthetic generation boundary"),
    ):
        await _engine(oracle).run(SearchRequest(query="操作 A の手順を教えてください。"))
    reader.assert_not_awaited()
    assert generation == [overrides.get(ROLE_KEY, default_prompt(ROLE_KEY))]
    assert saved_prompt_overrides() is None


async def test_approved_faq_early_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = FakeOracle()
    reader = AsyncMock(side_effect=RuntimeError("not on this path"))
    monkeypatch.setattr(oracle, "answer_prompt_overrides", reader)
    monkeypatch.setattr(faq, "answer_from_approved_faq", lambda *args: "設定 A を有効にします。")
    result = await _engine(oracle, approved_faq=("設定 A は？", "設定 A を有効にします。")).run(
        SearchRequest(query="設定 A の操作を教えてください。")
    )
    assert result.answer == "設定 A を有効にします。\n\n（出典: 承認済み FAQ「設定 A は？」）"
    reader.assert_not_awaited()
