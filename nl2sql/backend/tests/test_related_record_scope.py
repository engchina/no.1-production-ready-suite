"""関連先の結果集合を起点だけに狭める SQL の回帰。"""

from __future__ import annotations

from typing import Any

import pytest
from test_nl2sql_guided_clarification import _item, _output
from test_sql_requirement_columns import _context, _table

from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlEngine,
    Nl2SqlProfile,
    SqlRequirementSpec,
)
from app.features.nl2sql.query_scope import (
    QueryScopeViolation,
    RelatedRecordScope,
    related_scope_violations,
)
from app.features.nl2sql.service import GeneratedSql, Nl2SqlService
from app.features.nl2sql.sql_requirement import (
    LlmRequirementOutput,
    RequirementAiUnavailableError,
    build_draft,
)
from app.features.nl2sql.store import MemoryNl2SqlStore

SCOPE = RelatedRecordScope(
    source_quote="A と同じキーを持つ全件",
    anchor_predicate="APP.TABLE_A.ID = 'A'",
    result_key_columns=["APP.TABLE_A.ID"],
)
RELATED = "t.SHARED_KEY IN (SELECT SHARED_KEY FROM APP.TABLE_A WHERE ID = 'A')"
BAD = "SELECT t.ID FROM APP.TABLE_A t WHERE t.ID = 'A' AND " + RELATED
GOOD = "SELECT t.ID FROM APP.TABLE_A t WHERE " + RELATED


@pytest.mark.parametrize(
    "sql",
    [
        BAD,
        "SELECT t.ID FROM APP.TABLE_A t WHERE 'A' = t.ID AND " + RELATED,
        "SELECT t.ID FROM APP.TABLE_A t WHERE t.ID IN ('A') AND " + RELATED,
        "WITH x AS (SELECT ID AS k,SHARED_KEY FROM APP.TABLE_A) SELECT k FROM x WHERE k='A'",
        "SELECT x.k FROM (SELECT ID AS k FROM APP.TABLE_A WHERE ID='A') x",
        "SELECT t.ID FROM APP.TABLE_A t JOIN APP.TABLE_B b ON t.ID='A' AND b.ID=t.ID",
    ],
)
def test_anchor_restriction_in_result_is_detected(sql: str) -> None:
    assert related_scope_violations(sql, [SCOPE], owner="APP")


@pytest.mark.parametrize(
    "sql",
    [
        GOOD,
        (
            "WITH anchor AS (SELECT SHARED_KEY FROM APP.TABLE_A WHERE ID='A') "
            "SELECT t.ID FROM APP.TABLE_A t JOIN anchor a ON a.SHARED_KEY=t.SHARED_KEY"
        ),
        "SELECT t.ID FROM APP.TABLE_A t WHERE t.ID='A' OR " + RELATED,
        "SELECT t.ID FROM APP.TABLE_A t LEFT JOIN APP.TABLE_B b ON b.ID='A'",
        "SELECT t.ID FROM APP.TABLE_A t WHERE t.SHARED_KEY='A'",
        "SELECT t.ID FROM APP.TABLE_A t WHERE t.ID='B'",
    ],
)
def test_anchor_lookup_and_other_conditions_are_preserved(sql: str) -> None:
    assert not related_scope_violations(sql, [SCOPE], owner="APP")


def test_anchor_only_request_is_not_treated_as_related_scope() -> None:
    assert not related_scope_violations(BAD, [], owner="APP")


def test_generation_retries_once_and_never_returns_narrowed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(service, "_current_schema_owner", lambda: "APP")
    calls = []

    def generated(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=BAD if len(calls) == 1 else GOOD,
            explanation="",
            engine_meta={},
        )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generated)
    spec = SqlRequirementSpec(
        session_id="s",
        profile_id="p",
        clarified_question=SCOPE.source_quote,
        related_scopes=[SCOPE],
    )
    result = service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        SCOPE.source_quote,
        Nl2SqlProfile(id="p", name="P"),
        AllowedObjects(),
        100,
        [],
        sql_requirement=spec,
    )
    assert result.generated_sql == GOOD
    assert result.engine_meta["scope_generation_attempts"] == 2
    assert result.engine_meta["scope_rejected_sql"] == BAD
    assert calls[0]["generation_feedback"] is None
    assert "RELATED_SCOPE_ANCHOR_RESTRICTION" in calls[1]["generation_feedback"]["violations"][0]
    monkeypatch.setattr(
        service,
        "_generate_sql_with_engine",
        lambda *a, **kw: GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=BAD,
            explanation="",
            engine_meta={},
        ),
    )
    with pytest.raises(QueryScopeViolation, match="SQL_SCOPE_MISMATCH"):
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            SCOPE.source_quote,
            Nl2SqlProfile(id="p", name="P"),
            AllowedObjects(),
            100,
            [],
            sql_requirement=spec,
        )


def test_interpretation_is_grounded_in_question_and_physical_columns() -> None:
    context = _context([_table(columns=("ID", "SHARED_KEY"))])
    output = _output(
        _item("from", "from", "confirmed", tables=["APP.TABLE_A"], value="APP.TABLE_A"),
        _item("select", "select", "confirmed", columns=["APP.TABLE_A.ID"], value="ID"),
        clarified=SCOPE.source_quote,
    )
    output["related_scopes"] = [SCOPE.model_dump()]
    draft = build_draft(
        LlmRequirementOutput.model_validate(output), context, question=SCOPE.source_quote
    )
    assert draft.related_scopes == [SCOPE]
    from app.features.nl2sql.ontology_models import QuerySession
    from app.features.nl2sql.sql_requirement import generation_spec

    session = QuerySession.model_construct(
        id="session",
        profile_id="p",
        profile_view_id="view",
        ontology_revision_id="revision",
        original_question=SCOPE.source_quote,
        requirement=draft,
    )
    assert generation_spec(session).related_scopes == [SCOPE]
    patches: list[dict[str, Any]] = [
        {"source_quote": "原質問に無い文"},
        {"result_key_columns": ["APP.TABLE_A.MISSING"]},
        {"anchor_predicate": "APP.TABLE_A.ID='B'"},
    ]
    for patch in patches:
        output["related_scopes"] = [SCOPE.model_copy(update=patch).model_dump()]
        with pytest.raises(RequirementAiUnavailableError, match="対象範囲"):
            build_draft(
                LlmRequirementOutput.model_validate(output), context, question=SCOPE.source_quote
            )


def test_inner_join_key_equivalence_is_checked() -> None:
    scope = SCOPE.model_copy(update={"result_key_columns": ["APP.TABLE_B.ITEM_ID"]})
    sql = "SELECT t.ID FROM APP.TABLE_A t JOIN APP.TABLE_B b ON b.ITEM_ID=t.ID WHERE t.ID='A'"
    assert related_scope_violations(sql, [scope], owner="APP")


def test_derived_star_keeps_key_lineage() -> None:
    sql = "SELECT x.ID FROM (SELECT * FROM APP.TABLE_A) x WHERE x.ID='A'"
    assert related_scope_violations(sql, [SCOPE], owner="APP")


def test_scope_only_contract_does_not_create_an_empty_table_restriction() -> None:
    spec = SqlRequirementSpec(
        session_id="", profile_id="p", clarified_question=SCOPE.source_quote, related_scopes=[SCOPE]
    )
    assert spec.prompt_block() == ""
    assert spec.question_with_requirements(SCOPE.source_quote) == SCOPE.source_quote
