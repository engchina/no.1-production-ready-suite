"""回答の照合ツールを持たない契約（#4714）。"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_rag_mcp_contract_has_no_validate_answer_tool() -> None:
    contract = json.loads((ROOT / "platform/contracts/mcp/rag-tools.json").read_text("utf-8"))
    assert "rag_validate_answer" not in json.dumps(contract)


@pytest.mark.parametrize(
    "module",
    [
        "app.rag.answer_validation",
        "app.rag.answer_checks",
        "app.rag.guide_step_validation",
        "rag_engine.evaluation.answer_validation",
        "rag_engine.evaluation.guide_step_validation",
    ],
)
def test_validation_modules_are_gone(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
