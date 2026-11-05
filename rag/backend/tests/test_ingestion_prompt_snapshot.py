"""取込の指示読取は、未編集・読取不能・固定済みを区別する。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from rag_engine.resources.runtime import bind_prompt_overrides

from app.rag.ingestion import IngestionPipeline

_KEY = "heading_levels"


def pipeline(oracle: Any) -> IngestionPipeline:
    value = object.__new__(IngestionPipeline)
    value._oracle = oracle
    return value


@pytest.mark.parametrize(
    "error", [RuntimeError("read failure"), NotImplementedError("adapter loader")]
)
async def test_failed_read_is_not_builtin(error: Exception) -> None:
    reader = AsyncMock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        await pipeline(SimpleNamespace(answer_prompt_overrides=reader))._stage_prompt(
            _KEY, "builtin"
        )


async def test_missing_loader_is_not_unedited() -> None:
    with pytest.raises(AttributeError, match="answer_prompt_overrides"):
        await pipeline(object())._stage_prompt(_KEY, "builtin")


@pytest.mark.parametrize("overrides", [{}, {_KEY: "saved"}])
async def test_valid_loaded_configuration(overrides: dict[str, str]) -> None:
    reader = AsyncMock(return_value=overrides)
    assert await pipeline(SimpleNamespace(answer_prompt_overrides=reader))._stage_prompt(
        _KEY, "builtin"
    ) == overrides.get(_KEY, "builtin")
    reader.assert_awaited_once()


@pytest.mark.parametrize("saved", [{}, {_KEY: "bound"}])
async def test_bound_snapshot_is_not_read_again(saved: dict[str, str]) -> None:
    reader = AsyncMock(side_effect=RuntimeError("later read failed"))
    with bind_prompt_overrides(saved):
        assert await pipeline(SimpleNamespace(answer_prompt_overrides=reader))._stage_prompt(
            _KEY, "builtin"
        ) == saved.get(_KEY, "builtin")
    reader.assert_not_awaited()


async def test_bound_snapshot_not_replaced_with_new_saved_value() -> None:
    reader = AsyncMock(return_value={_KEY: "later"})
    with bind_prompt_overrides({_KEY: "bound"}):
        assert (
            await pipeline(SimpleNamespace(answer_prompt_overrides=reader))._stage_prompt(
                _KEY, "builtin"
            )
            == "bound"
        )
    reader.assert_not_awaited()
