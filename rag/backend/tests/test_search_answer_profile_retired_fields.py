"""プロファイルと KB の保存値から、定義から消した欄を除く migration を合成の行で確かめる(#4784)。"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.rag.kb_adapter_config import parse_adapter_config
from app.rag.oracle_schema import oracle_schema_migration_sections, split_sql_statements
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.rag.search_answer_profile_retired_fields import (
    KEPT_LANGUAGE_PACK_SECTIONS,
    KEPT_QUERY_FIELDS,
    remove_retired_search_answer_profile_fields,
    removed_names,
    without_knowledge_base_query,
    without_retired_profile_fields,
)
from tests.test_language_pack_retired_fields import _Db

_NAME = "20261110_4784_search_answer_profile_retired_fields"

# 残す欄・効く節の上書き(値は変えない)。
_KEPT_PROFILE: dict[str, Any] = {
    "version": 1,
    "answer_model_id": "synthetic-model",
    "knowledge_base_ids": ["kb-1", "kb-2"],
    "query": {"max_anchors_per_document": 3, "support_guide_enabled": True},
    "clarification_policy": "assume",
    "language_pack": {
        "question_cues": {"comma_chars": ["、"]},
        "search_terms": {"stopwords": ["合成語"]},
    },
    "contact_override": "合成の窓口",
}


def _old_profile() -> dict[str, Any]:
    """消す欄(null の値を含む)と、効かない節の上書きを足した保存値。"""
    old = copy.deepcopy(_KEPT_PROFILE)
    old["query"].update(
        {
            "neighbor_child_count": 0,
            "rerank_enabled": None,
            "graph_expansion_max_chunks": 3,
            "support_guide_semantic_match_enabled": True,
            "support_guide_close_match_clarification_enabled": None,
            "vector_index_profile": "accurate",
            "answer_engine": "standard",
        }
    )
    old["language_pack"]["guide_import_cues"] = {"process_headings": ["合成の見出し"]}
    return old


def _db(*profiles: dict[str, Any], knowledge_bases: list[dict[str, Any]] | None = None) -> _Db:
    return _Db(
        rag_search_answer_profiles=[
            {"search_answer_profile_id": f"p{index}", "profile_config": profile}
            for index, profile in enumerate(profiles)
        ],
        rag_knowledge_bases=[
            {"knowledge_base_id": f"kb{index}", "retrieval_config": config}
            for index, config in enumerate(knowledge_bases or [])
        ],
    )


def _profiles(db: _Db) -> list[Any]:
    return [row["profile_config"] for row in db.tables["RAG_SEARCH_ANSWER_PROFILES"]]


def test_only_retired_fields_are_removed_and_kept_values_do_not_change() -> None:
    db = _db(
        _old_profile(),
        copy.deepcopy(_KEPT_PROFILE),
        {},
        knowledge_bases=[
            {
                "version": 2,
                "ingestion": {"chunk_size": 1200},
                "query": {"rerank_enabled": False},
                "structure_index_enabled": True,
            },
            {"ingestion": {"chunk_size": 800}},
        ],
    )

    remove_retired_search_answer_profile_fields(db)

    assert _profiles(db) == [_KEPT_PROFILE, _KEPT_PROFILE, {}]
    assert [row["retrieval_config"] for row in db.tables["RAG_KNOWLEDGE_BASES"]] == [
        {"version": 2, "ingestion": {"chunk_size": 1200}, "structure_index_enabled": True},
        {"ingestion": {"chunk_size": 800}},
    ]
    # 消すものが無い行は書き換えない。
    assert db.updates == 2
    # 消した後の値は、今の定義で検証エラーにならずに読める。
    config = SearchAnswerProfileConfig.model_validate(_profiles(db)[0])
    assert config.model_dump(mode="json") == {**_KEPT_PROFILE, "escalation_model_id": None}
    restored = parse_adapter_config(db.tables["RAG_KNOWLEDGE_BASES"][0]["retrieval_config"])
    assert restored.ingestion.chunk_size == 1200


def test_dry_run_reports_only_the_names_that_will_be_removed() -> None:
    old = _old_profile()
    stripped = without_retired_profile_fields(old)
    assert stripped is not None
    assert removed_names(old, stripped) == [
        "language_pack.guide_import_cues",
        "query.answer_engine",
        "query.graph_expansion_max_chunks",
        "query.neighbor_child_count",
        "query.rerank_enabled",
        "query.support_guide_close_match_clarification_enabled",
        "query.support_guide_semantic_match_enabled",
        "query.vector_index_profile",
    ]
    kb = {"ingestion": {"chunk_size": 800}, "query": {"rerank_enabled": False}}
    assert removed_names(kb, without_knowledge_base_query(kb) or {}) == ["query"]


def test_old_profile_is_a_validation_error_before_the_migration() -> None:
    """消す前の保存値は今の定義で読めない(だから migration で消す)。"""
    with pytest.raises(ValidationError):
        SearchAnswerProfileConfig.model_validate(_old_profile())


def test_running_twice_gives_the_same_result() -> None:
    db = _db(_old_profile(), knowledge_bases=[{"query": {}}])
    remove_retired_search_answer_profile_fields(db)
    snapshot = copy.deepcopy(db.tables)
    updates = db.updates

    remove_retired_search_answer_profile_fields(db)

    assert db.tables == snapshot
    assert db.updates == updates


def test_section_with_only_retired_values_becomes_empty() -> None:
    db = _db(
        {
            "query": {"rerank_enabled": False},
            "language_pack": {"guide_import_cues": {"process_headings": ["合成の見出し"]}},
        }
    )
    remove_retired_search_answer_profile_fields(db)
    assert _profiles(db) == [{"query": {}, "language_pack": {}}]
    assert SearchAnswerProfileConfig.model_validate(_profiles(db)[0]) == SearchAnswerProfileConfig()


def test_missing_tables_and_empty_tables_do_not_fail() -> None:
    remove_retired_search_answer_profile_fields(_Db())
    db = _db()
    remove_retired_search_answer_profile_fields(db)
    assert db.updates == 0


def test_migration_is_registered_as_a_destructive_data_step() -> None:
    section = next(s for s in oracle_schema_migration_sections() if s.name == _NAME)
    assert section.data_step is remove_retired_search_answer_profile_fields
    assert section.destructive_note
    assert len(statements := split_sql_statements(section.sql)) == 1
    assert statements[0].startswith("BEGIN")
    # 残す欄と節の一覧は SQL(checksum)に入る。
    for name in KEPT_QUERY_FIELDS | KEPT_LANGUAGE_PACK_SECTIONS:
        assert name in section.sql
