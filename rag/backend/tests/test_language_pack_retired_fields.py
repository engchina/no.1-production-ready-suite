"""言語資源の保存データから廃止した項目を取り除く migration を、合成の行で確かめる(#4666)。"""

from __future__ import annotations

import copy
import re
from dataclasses import replace
from typing import Any

import pytest
from rag_parser_core.language import build_pack, builtin_pack

from app.rag import language_pack_retired_fields as module
from app.rag import system_schema
from app.rag.language_pack_retired_fields import (
    RETIRED_QUESTION_CUE_FIELDS,
    remove_retired_language_pack_fields,
)
from app.rag.oracle_schema import oracle_schema_migration_sections, split_sql_statements
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.rag.system_schema import SystemSchemaManager
from tests.test_system_schema_manager import _FakeDatabase

_NAME = "20261109_4666_language_pack_retired_question_cues"
_KEEP = "hypothesis_cues"


def _with_retired(pack: dict[str, Any]) -> dict[str, Any]:
    old = copy.deepcopy(pack)
    old["question_cues"].update({key: ["合成語"] for key in RETIRED_QUESTION_CUE_FIELDS})
    return old


class _Cursor:
    def __init__(self, db: _Db) -> None:
        self.db = db
        self.rows: list[tuple[Any, ...]] = []
        self.description: list[tuple[str, ...]] = []

    def setinputsizes(self, **_: object) -> None:
        return None

    def close(self) -> None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def execute(self, sql: str, binds: dict[str, Any] | None = None, **_: object) -> None:
        import json

        binds = binds or {}
        self.rows = []
        if "user_tables" in sql:
            self.description = [("FOUND",)]
            self.rows = [(1,)] if binds["name"] in self.db.tables else []
            return
        table = next(name for name in self.db.tables if name.lower() in sql.lower())
        rows = self.db.tables[table]
        if sql.startswith("SELECT"):
            columns = re.match(r"SELECT (.*?) FROM", sql)
            assert columns is not None
            names = [c.strip() for c in columns.group(1).split(",")]
            self.description = [(name.upper(),) for name in names]
            self.rows = [
                tuple(copy.deepcopy(row[name]) for name in names)
                for row in rows
                if "JSON_EXISTS" not in sql or row["profile_config"].get("language_pack")
            ]
            return
        assert sql.startswith("UPDATE")
        self.db.updates += 1
        assignments = re.findall(r"(\w+)=:(\w+)", sql.split(" WHERE ")[0])
        key, bind = re.findall(r"(\w+)=:(\w+)", sql.split(" WHERE ")[1])[0]
        for row in rows:
            if row[key] == binds[bind]:
                for column, name in assignments:
                    value = binds[name]
                    row[column] = (
                        json.loads(value) if column.endswith(("json", "config")) else value
                    )


class _Db:
    def __init__(self, **tables: list[dict[str, Any]]) -> None:
        self.tables = {name.upper(): rows for name, rows in tables.items()}
        self.commits = 0
        self.updates = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1


def _db(*releases: dict[str, Any], draft: dict[str, Any], profile: dict[str, Any]) -> _Db:
    return _Db(
        rag_language_pack_releases=list(releases),
        rag_language_pack_state=[
            {"singleton_id": 1, "draft_revision": 7, "draft_effective_json": draft}
        ],
        rag_search_answer_profiles=[{"search_answer_profile_id": "p", "profile_config": profile}],
    )


def _release(effective: dict[str, Any], *, content_hash: str = "stale") -> dict[str, Any]:
    return {
        "release_id": "r1",
        "version": 3,
        "effective_json": effective,
        "content_hash": content_hash,
        "ingest_hash": "ingest",
    }


def test_retired_fields_are_removed_from_snapshots_and_hash_is_recomputed() -> None:
    current = builtin_pack().to_dict()
    old = _with_retired(current)
    profile = {
        "query": {"marker": 1},
        "language_pack": {
            "question_cues": {"wish_endings": ["合成語"], _KEEP: ["合成語"]},
            "search_terms": {"stopwords": ["合成語"]},
        },
    }
    db = _db(_release(old), draft=old, profile=profile)

    remove_retired_language_pack_fields(db)

    release = db.tables["RAG_LANGUAGE_PACK_RELEASES"][0]
    assert release["effective_json"] == current
    pack = build_pack(release["effective_json"])
    assert pack.to_dict() == current
    assert release["content_hash"] == pack.hash
    assert (release["version"], release["ingest_hash"]) == (3, "ingest")
    state = db.tables["RAG_LANGUAGE_PACK_STATE"][0]
    assert state["draft_effective_json"] == current
    assert state["draft_revision"] == 7
    config = db.tables["RAG_SEARCH_ANSWER_PROFILES"][0]["profile_config"]
    assert config == {
        "query": {"marker": 1},
        "language_pack": {
            "question_cues": {_KEEP: ["合成語"]},
            "search_terms": {"stopwords": ["合成語"]},
        },
    }


def test_running_twice_gives_the_same_result() -> None:
    old = _with_retired(builtin_pack().to_dict())
    db = _db(_release(old), draft=old, profile={"language_pack": {}})
    remove_retired_language_pack_fields(db)
    snapshot = copy.deepcopy(db.tables)
    updates = db.updates

    remove_retired_language_pack_fields(db)

    assert db.tables == snapshot
    assert db.updates == updates


def test_profile_section_with_only_retired_fields_becomes_no_override() -> None:
    profile = {"language_pack": {"question_cues": {"means_cues": ["合成語"]}}}
    db = _db(draft=builtin_pack().to_dict(), profile=profile)
    remove_retired_language_pack_fields(db)
    config = db.tables["RAG_SEARCH_ANSWER_PROFILES"][0]["profile_config"]
    assert config == {"language_pack": {}}
    assert SearchAnswerProfileConfig.model_validate(config).language_pack == {}


def test_rows_without_retired_fields_are_not_touched() -> None:
    current = builtin_pack().to_dict()
    db = _db(_release(current, content_hash="kept"), draft=current, profile={})
    remove_retired_language_pack_fields(db)
    assert db.updates == 0
    assert db.tables["RAG_LANGUAGE_PACK_RELEASES"][0]["content_hash"] == "kept"


def test_missing_tables_and_empty_tables_do_not_fail() -> None:
    remove_retired_language_pack_fields(_Db())
    db = _Db(
        rag_language_pack_releases=[], rag_language_pack_state=[], rag_search_answer_profiles=[]
    )
    remove_retired_language_pack_fields(db)
    assert db.updates == 0


def test_release_unreadable_for_another_reason_keeps_its_hash() -> None:
    old = _with_retired(builtin_pack().to_dict())
    del old["question_cues"][_KEEP]
    db = _db(_release(old, content_hash="kept"), draft=builtin_pack().to_dict(), profile={})
    remove_retired_language_pack_fields(db)
    release = db.tables["RAG_LANGUAGE_PACK_RELEASES"][0]
    assert not RETIRED_QUESTION_CUE_FIELDS & release["effective_json"]["question_cues"].keys()
    assert release["content_hash"] == "kept"


def test_retired_fields_are_no_longer_part_of_the_pack() -> None:
    assert not RETIRED_QUESTION_CUE_FIELDS & builtin_pack().to_dict()["question_cues"].keys()
    assert module.without_retired_fields({"question_cues": {_KEEP: []}}) is None


def test_migration_is_registered_as_a_destructive_data_step() -> None:
    section = next(s for s in oracle_schema_migration_sections() if s.name == _NAME)
    assert section.data_step is remove_retired_language_pack_fields
    assert section.destructive_note
    assert len(statements := split_sql_statements(section.sql)) == 1
    assert statements[0].startswith("BEGIN")
    for field in RETIRED_QUESTION_CUE_FIELDS:
        assert field in section.sql


def test_data_step_runs_once_when_the_migration_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        system_schema,
        "MIGRATIONS",
        tuple(
            replace(item, data_step=calls.append) if item.name == _NAME else item
            for item in system_schema.MIGRATIONS
        ),
    )
    database = _FakeDatabase()
    manager = SystemSchemaManager(database.connection)
    manager.initialize()
    assert calls == []  # 未初期化の DB は記録だけで、データの移行を呼ばない。

    database.migrations.pop(_NAME)
    manager.initialize(allow_destructive=True)
    assert len(calls) == 1
    assert _NAME in database.migrations

    manager.initialize()
    assert len(calls) == 1


def test_answer_check_fields_and_guide_check_section_are_retired() -> None:
    """回答の生成後の確認の項目と節を除き、作り直した公開版は今の契約で読める(#4714)。"""
    pack = builtin_pack().to_dict()
    old = copy.deepcopy(pack)
    old["answer_labels"]["removed_section"] = "合成語"
    old["guide_check_cues"] = {"approval_terms": ["合成語"]}
    stripped = module.without_retired_fields(old)
    assert stripped is not None
    assert "guide_check_cues" not in stripped
    assert "removed_section" not in stripped["answer_labels"]
    assert build_pack(stripped).hash == build_pack(pack).hash
    assert module.without_retired_fields(pack) is None


_UNREAD_NAME = "20261109_4668_language_pack_retired_unread_checks"


def test_unread_fields_and_section_are_retired() -> None:
    """動いていない語の判定だけが読んでいた項目と節を除き、作り直した公開版は今の契約で読める。"""
    pack = builtin_pack().to_dict()
    old = copy.deepcopy(pack)
    old["operation_audit_cues"] = {"instruction_phrases": ["合成語"]}
    for section, fields in (
        ("answer_check_cues", module.RETIRED_UNREAD_ANSWER_CHECK_CUE_FIELDS),
        ("search_terms", module.RETIRED_UNREAD_SEARCH_TERM_FIELDS),
        ("evidence_cues", module.RETIRED_UNREAD_EVIDENCE_CUE_FIELDS),
    ):
        old[section].update({key: ["合成語"] for key in fields})
    old["answer_labels"].update(
        {key: "合成語" for key in module.RETIRED_UNREAD_ANSWER_LABEL_FIELDS}
    )
    stripped = module.without_retired_fields(old)
    assert stripped is not None
    assert "operation_audit_cues" not in stripped
    for section, fields in (
        ("answer_check_cues", module.RETIRED_UNREAD_ANSWER_CHECK_CUE_FIELDS),
        ("search_terms", module.RETIRED_UNREAD_SEARCH_TERM_FIELDS),
        ("evidence_cues", module.RETIRED_UNREAD_EVIDENCE_CUE_FIELDS),
        ("answer_labels", module.RETIRED_UNREAD_ANSWER_LABEL_FIELDS),
    ):
        assert not fields & stripped[section].keys()
    assert build_pack(stripped).hash == build_pack(pack).hash
    assert not module.RETIRED_UNREAD_FIELDS & {
        key for fields in pack.values() if isinstance(fields, dict) for key in fields
    }
    assert "operation_audit_cues" not in pack


def test_unread_fields_migration_is_a_destructive_data_step() -> None:
    section = next(s for s in oracle_schema_migration_sections() if s.name == _UNREAD_NAME)
    assert section.data_step is remove_retired_language_pack_fields
    assert section.destructive_note
    assert len(split_sql_statements(section.sql)) == 1
    for field in (*module.RETIRED_UNREAD_FIELDS, *module.RETIRED_UNREAD_SECTIONS):
        assert field in section.sql


def test_unread_fields_are_removed_from_synthetic_rows_and_hash_is_recomputed() -> None:
    """公開版・下書き・プロファイルの合成の行から、#4668 で廃止した項目と節を除く。"""
    current = builtin_pack().to_dict()
    old = copy.deepcopy(current)
    old["operation_audit_cues"] = {"instruction_phrases": ["合成語"]}
    old["answer_check_cues"]["unexplained_words"] = ["合成語"]
    old["answer_labels"]["predicate_endings"] = ["合成語"]
    old["search_terms"]["feedback_split_words"] = ["合成語"]
    old["evidence_cues"]["link_verbs"] = ["合成語"]
    profile = {
        "language_pack": {
            "operation_audit_cues": {"instruction_phrases": ["合成語"]},
            "evidence_cues": {"link_verbs": ["合成語"]},
            "answer_check_cues": {"unexplained_words": ["合成語"]},
        }
    }
    db = _db(_release(old), draft=old, profile=profile)

    remove_retired_language_pack_fields(db)

    release = db.tables["RAG_LANGUAGE_PACK_RELEASES"][0]
    assert release["effective_json"] == current
    assert release["content_hash"] == build_pack(release["effective_json"]).hash
    assert release["ingest_hash"] == "ingest"
    assert db.tables["RAG_LANGUAGE_PACK_STATE"][0]["draft_effective_json"] == current
    config = db.tables["RAG_SEARCH_ANSWER_PROFILES"][0]["profile_config"]
    assert config == {"language_pack": {}}


def test_crag_labels_are_retired_by_a_destructive_data_step() -> None:
    """CRAG の工程と一緒に、表示の文の保存値も消す（#4749）。"""
    old = builtin_pack().to_dict()
    old["answer_labels"].update({key: "合成語" for key in module.RETIRED_CRAG_ANSWER_LABEL_FIELDS})
    stripped = module.without_retired_fields(old)
    assert stripped is not None
    assert not module.RETIRED_CRAG_ANSWER_LABEL_FIELDS & stripped["answer_labels"].keys()
    section = next(
        s
        for s in oracle_schema_migration_sections()
        if s.name == "20261110_4749_language_pack_retired_crag_labels"
    )
    assert section.data_step is remove_retired_language_pack_fields
    assert section.destructive_note
