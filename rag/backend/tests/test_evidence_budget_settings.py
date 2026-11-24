"""生成に渡す根拠の文字予算(#3776)は回答のモデルの入力の上限の 1/2(#5813。設定の項目は無い)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import config as config_module
from app.config import Settings
from app.rag.answer_engine import build_engine_settings


def test_backend_has_no_evidence_budget_setting() -> None:
    assert "rag_evidence_budget_chars" not in Settings.model_fields
    example = (config_module.BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")
    assert not any(line.startswith("RAG_EVIDENCE_BUDGET_CHARS=") for line in example.splitlines())


def test_engine_budget_is_half_of_the_answer_model_input_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配備済みの .env・環境に古いキーが残っていても読まず、モデルの入力の上限から決める。"""
    monkeypatch.setenv("RAG_EVIDENCE_BUDGET_CHARS", "80000")
    monkeypatch.setenv("RAG_ENGINE_EVIDENCE_BUDGET_CHARS", "12000")
    settings = Settings()
    assert not hasattr(settings, "rag_evidence_budget_chars")
    engine = build_engine_settings(settings, output_dir=tmp_path)
    assert engine.evidence_budget_chars == engine.answer_max_input_tokens // 2
