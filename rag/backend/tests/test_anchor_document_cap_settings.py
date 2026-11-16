"""1 つの文書から採る chunk の上限(#2167・#5165)の設定。

既定 0、.env とプロファイルで選び、engine へ渡す。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.rag.answer_engine import build_engine_settings
from app.rag.search_answer_profile_config import (
    parse_search_answer_profile_config,
    resolve_search_answer_profile_settings,
)


def test_default_is_unlimited_and_env_selects_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_MAX_ANCHORS_PER_DOCUMENT", raising=False)
    assert Settings(_env_file=None).rag_max_anchors_per_document == 0
    monkeypatch.setenv("RAG_MAX_ANCHORS_PER_DOCUMENT", "3")
    assert Settings(_env_file=None).rag_max_anchors_per_document == 3


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
