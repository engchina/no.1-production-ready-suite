"""1 つの文書から採る chunk の上限(#2167・#5165・#5170)の設定。

既定 0 = 上限なし(backend と engine で同じ。#5443)、.env とプロファイルで選び、engine へ渡す。
"""

from __future__ import annotations

from typing import Any

import pytest
from rag_engine.config import DEFAULT_MAX_ANCHORS_PER_DOCUMENT

from app.config import Settings
from app.rag.answer_engine import build_engine_settings
from app.rag.search_answer_profile_config import (
    parse_search_answer_profile_config,
    resolve_search_answer_profile_settings,
)


def test_default_is_unlimited_in_backend_and_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_MAX_ANCHORS_PER_DOCUMENT", raising=False)
    assert Settings(_env_file=None).rag_max_anchors_per_document == 0
    assert DEFAULT_MAX_ANCHORS_PER_DOCUMENT == 0


def test_env_selects_the_limit_and_zero_means_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_MAX_ANCHORS_PER_DOCUMENT", "3")
    assert Settings(_env_file=None).rag_max_anchors_per_document == 3
    # 既存の .env に 0 が書かれていれば 0(上限なし)のまま扱う。
    monkeypatch.setenv("RAG_MAX_ANCHORS_PER_DOCUMENT", "0")
    assert Settings(_env_file=None).rag_max_anchors_per_document == 0


def test_profile_selects_the_limit_and_engine_receives_it(tmp_path: Any) -> None:
    base = Settings(_env_file=None, rag_max_anchors_per_document=0)
    selected, applied = resolve_search_answer_profile_settings(
        base,
        parse_search_answer_profile_config(
            {"knowledge_base_ids": ["kb-1"], "query": {"max_anchors_per_document": 4}}
        ),
    )
    assert applied is True
    assert selected.rag_max_anchors_per_document == 4
    assert build_engine_settings(selected, output_dir=tmp_path).max_anchors_per_document == 4
    assert build_engine_settings(base, output_dir=tmp_path).max_anchors_per_document == 0
    # 範囲の外の保存値は読まない(既定に戻る)。
    broken = parse_search_answer_profile_config(
        {"knowledge_base_ids": ["kb-1"], "query": {"max_anchors_per_document": 21}}
    )
    assert broken.query.max_anchors_per_document is None
