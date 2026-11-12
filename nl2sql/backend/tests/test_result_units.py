"""実データや列名の意味に依存しない、結果列の単位伝播。"""

from __future__ import annotations

import pytest

from app.features.nl2sql.models import AllowedObjects, QueryResults
from app.features.nl2sql.ontology_definitions import MetricDefinitionV2, PropertyDefinition
from app.features.nl2sql.result_units import result_column_metadata


def prop(
    name: str, *, unit: str = "u", difference: str = "du", reviewed: bool = True, table: str = "A"
) -> PropertyDefinition:
    return PropertyDefinition(
        api_name=f"p_{table}_{name}",
        name_ja="合成項目",
        unit=unit,
        difference_unit=difference,
        review_status="reviewed" if reviewed else "unreviewed",
        mappings=[{"owner": "APP", "object_name": table, "column_name": name}],
    )


def metadata(
    sql: str,
    columns: list[str],
    definitions: list | None = None,
    allowed: AllowedObjects | None = None,
):
    return result_column_metadata(
        sql,
        columns,
        definitions=definitions if definitions is not None else [prop("X"), prop("Y")],
        allowed=allowed or AllowedObjects(table_names=["APP.A", "APP.B"]),
        release_id="release_synthetic",
    )


def test_position_alias_and_difference_are_bound_without_name_guessing():
    result = metadata(
        "SELECT a.x AS anything, a.x-a.y AS actual_pct, a.y FROM app.a a",
        ["ANYTHING", "ACTUAL_PCT", "Y"],
    )
    assert [r.unit for r in result] == ["u", "du", "u"]
    assert result[1].source_columns == ["APP.A.X", "APP.A.Y"]
    assert result[1].provenance == ["release_synthetic:p_A_X", "release_synthetic:p_A_Y"]
    assert [r.name for r in result] == ["ANYTHING", "ACTUAL_PCT", "Y"]


@pytest.mark.parametrize("function", ["SUM", "AVG", "MIN", "MAX"])
def test_aggregates_keep_declared_units(function):
    assert (
        metadata(f"SELECT {function}(a.x) AS aggregate FROM app.a a", ["AGGREGATE"])[0].unit == "u"
    )


def test_cte_column_alias_and_subquery_keep_snapshot_units():
    sql = (
        "WITH c(v,w) AS (SELECT x,y FROM app.a) SELECT q.v-q.w AS d FROM (SELECT c.v,c.w FROM c) q"
    )
    assert metadata(sql, ["D"])[0].unit == "du"


@pytest.mark.parametrize(
    "definitions",
    [
        [prop("X", reviewed=False), prop("Y")],
        [prop("X", difference=""), prop("Y")],
        [prop("X"), prop("Y", unit="other")],
        [prop("X"), prop("Y", difference="other_delta")],
        [prop("X"), prop("X", unit="conflict"), prop("Y")],
    ],
)
def test_unreviewed_missing_and_conflicting_declarations_are_unknown(definitions):
    result = metadata("SELECT x-y AS d FROM app.a", ["D"], definitions)[0]
    assert result.unit is None and result.unit_status == "unknown"


@pytest.mark.parametrize("expression", ["x/y", "x*2", "x+1", "ABS(x)", "CAST(x AS NUMBER)", "42"])
def test_arbitrary_expressions_do_not_invent_units(expression):
    assert metadata(f"SELECT {expression} AS PCT FROM app.a", ["PCT"])[0].unit_status == "unknown"


def test_hidden_columns_never_escape_in_metadata():
    allowed = AllowedObjects(table_names=["APP.A"], columns={"APP.A": ["X"]})
    result = metadata("SELECT x,y,x-y AS d FROM app.a", ["X", "Y", "D"], allowed=allowed)
    assert result[0].unit == "u"
    assert result[1].source_columns == [] and result[1].unit is None
    assert result[2].source_columns == ["APP.A.X"] and result[2].unit is None


def test_same_alias_different_table_and_ambiguous_unqualified_column():
    definitions = [prop("X"), prop("X", unit="different", table="B")]
    result = metadata(
        "SELECT a.x AS x,b.x AS x,x AS ambiguous FROM app.a a JOIN app.b b ON a.x=b.x",
        ["X", "X", "AMBIGUOUS"],
        definitions,
    )
    assert [r.unit for r in result] == [None, None, None]
    distinct = metadata(
        "SELECT a.x AS left_value,b.x AS right_value FROM app.a a JOIN app.b b ON a.x=b.x",
        ["LEFT_VALUE", "RIGHT_VALUE"],
        definitions,
    )
    assert [r.unit for r in distinct] == ["u", "different"]


def test_quoted_names_are_not_folded_to_unquoted_names():
    definition = PropertyDefinition(
        api_name="quoted",
        name_ja="合成項目",
        unit="q",
        review_status="reviewed",
        mappings=[{"owner": "APP", "object_name": '"Mixed"', "column_name": '"v"'}],
    )
    allowed = AllowedObjects(table_names=['APP."Mixed"'])
    assert metadata('SELECT t."v" FROM APP."Mixed" t', ["v"], [definition], allowed)[0].unit == "q"
    assert metadata('SELECT t.V FROM APP."Mixed" t', ["V"], [definition], allowed)[0].unit is None


def test_registered_metric_only_exact_expression_and_reviewed():
    metric = MetricDefinitionV2(
        api_name="metric",
        name_ja="合成指標",
        unit="ratio",
        review_status="reviewed",
        expression_sql="APP.A.X / APP.A.Y",
        mappings=[{"owner": "APP", "object_name": "A"}],
    )
    assert metadata("SELECT a.x/a.y AS r FROM app.a a", ["R"], [metric])[0].unit == "ratio"
    assert metadata("SELECT a.y/a.x AS r FROM app.a a", ["R"], [metric])[0].unit is None
    metric.review_status = "unreviewed"
    assert metadata("SELECT a.x/a.y AS r FROM app.a a", ["R"], [metric])[0].unit is None


def test_legacy_results_and_unknown_definition_are_optional():
    legacy = QueryResults(columns=["X"], rows=[{"X": 1}], total=1)
    assert legacy.column_metadata == []
    assert metadata("SELECT x FROM app.a", ["X"], [])[0].unit_status == "unknown"
    assert metadata("SELECT x,y FROM app.a", ["X"])[0].unit is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x FROM app.a UNION ALL SELECT x FROM app.a",
        "SELECT (SELECT MAX(b.x) FROM app.b b WHERE b.x=a.x) AS v FROM app.a a",
        "SELECT * FROM app.a",
        'SELECT x FROM "invalid',
    ],
)
def test_unsupported_query_shapes_stay_unknown(sql):
    assert metadata(sql, ["X"])[0].unit_status == "unknown"


def test_quoted_owner_and_cte_duplicate_alias_are_isolated():
    definition = PropertyDefinition(
        api_name="quoted_owner",
        name_ja="合成項目",
        unit="q",
        review_status="reviewed",
        mappings=[{"owner": '"Tenant"', "object_name": "A", "column_name": "X"}],
    )
    assert (
        metadata(
            'SELECT x FROM "Tenant".A',
            ["X"],
            [definition],
            AllowedObjects(table_names=['"Tenant".A']),
        )[0].unit
        == "q"
    )
    assert (
        metadata(
            "SELECT x FROM TENANT.A",
            ["X"],
            [definition],
            AllowedObjects(table_names=['"Tenant".A']),
        )[0].unit
        is None
    )
    assert (
        metadata("WITH c AS(SELECT x AS v,y AS v FROM app.a)SELECT v FROM c", ["V"])[0].unit is None
    )


def test_reviewed_metric_can_declare_subtraction_without_operand_inference():
    metric = MetricDefinitionV2(
        api_name="difference",
        name_ja="合成指標",
        unit="explicit",
        review_status="reviewed",
        expression_sql="APP.A.X-APP.A.Y",
    )
    assert metadata("SELECT a.x-a.y AS d FROM app.a a", ["D"], [metric])[0].unit == "explicit"


def test_published_review_hash_is_required_and_bound_to_fixed_release(monkeypatch):
    from types import SimpleNamespace

    from app.features.nl2sql import ontology_definition_workspace as workspace
    from app.features.nl2sql import ontology_published_context as context
    from app.features.nl2sql.ontology_definition_service import definition_fingerprint
    from app.features.nl2sql.result_units import published_unit_definitions

    accepted = prop("X")
    accepted.id = "accepted"
    tampered = prop("Y")
    tampered.id = "tampered"
    records = [
        {
            "definition_id": accepted.id,
            "hash": definition_fingerprint(accepted.model_dump(mode="json")),
        },
        {"definition_id": tampered.id, "hash": "stale"},
    ]
    runtime = SimpleNamespace(
        store=SimpleNamespace(
            get_artifact=lambda identity: {"artifact_type": "ontology_release_v2"}
        )
    )
    calls = []

    def release(self, profile_id, release_id):
        calls.append((profile_id, release_id))
        return {"bundle": True}

    monkeypatch.setattr(
        workspace.ProfileOntologyWorkspaceService, "__init__", lambda self, runtime: None
    )
    monkeypatch.setattr(workspace.ProfileOntologyWorkspaceService, "release", release)
    monkeypatch.setattr(
        context,
        "require_current_scope",
        lambda *args: SimpleNamespace(definitions=[accepted, tampered], review_records=records),
    )
    assert published_unit_definitions(runtime, "p", "pinned_release") == [accepted]
    assert calls == [("p", "pinned_release")]
    accepted.unit = "changed"
    assert published_unit_definitions(runtime, "p", "pinned_release") == []
    runtime.store.get_artifact = lambda identity: {"artifact_type": "unknown_legacy"}
    assert published_unit_definitions(runtime, "p", "snapshot") == []
    assert published_unit_definitions(runtime, "p", "") == []
    assert published_unit_definitions(runtime, "p", "ontology_revision_old") == []


def test_prior_review_hash_without_new_optional_field_remains_usable(monkeypatch):
    from types import SimpleNamespace

    from app.features.nl2sql import ontology_definition_workspace as workspace
    from app.features.nl2sql import ontology_published_context as context
    from app.features.nl2sql.ontology_definition_service import definition_fingerprint
    from app.features.nl2sql.result_units import published_unit_definitions

    old = prop("X").model_dump(mode="json")
    old.pop("difference_unit")
    definition = PropertyDefinition.model_validate(old)
    runtime = SimpleNamespace(
        store=SimpleNamespace(
            get_artifact=lambda identity: {"artifact_type": "ontology_release_v2"}
        )
    )
    monkeypatch.setattr(
        workspace.ProfileOntologyWorkspaceService, "__init__", lambda self, runtime: None
    )
    monkeypatch.setattr(
        workspace.ProfileOntologyWorkspaceService, "release", lambda *args: {"bundle": True}
    )
    monkeypatch.setattr(
        context,
        "require_current_scope",
        lambda *args: SimpleNamespace(
            definitions=[definition],
            review_records=[{"definition_id": definition.id, "hash": definition_fingerprint(old)}],
        ),
    )
    assert published_unit_definitions(runtime, "p", "r") == [definition]


@pytest.mark.parametrize("expression", ["APP.A.X - APP.A.Y", "SUM(APP.A.X)"])
def test_registered_metric_conflicting_with_known_physical_inference_is_unknown(expression):
    metric = MetricDefinitionV2(
        api_name="conflict",
        name_ja="合成指標",
        unit="different",
        review_status="reviewed",
        expression_sql=expression,
    )
    assert (
        metadata(
            "SELECT " + expression + " AS VALUE FROM APP.A",
            ["VALUE"],
            [prop("X"), prop("Y"), metric],
        )[0].unit
        is None
    )


def test_result_position_and_explicit_alias_mismatch_is_unknown():
    result = metadata(
        "SELECT x AS first_value, y AS second_value FROM APP.A", ["SECOND_VALUE", "FIRST_VALUE"]
    )
    assert all(column.unit_status == "unknown" for column in result)


def test_metric_with_hidden_mapping_does_not_disclose_provenance():
    metric = MetricDefinitionV2(
        api_name="hidden",
        name_ja="合成指標",
        unit="r",
        review_status="reviewed",
        expression_sql="APP.A.X/APP.A.Y",
        mappings=[{"owner": "PRIVATE", "object_name": "HIDDEN"}],
    )
    result = metadata("SELECT a.x/a.y AS r FROM app.a a", ["R"], [metric])[0]
    assert result.unit is None and result.provenance == [] and result.source_columns == []
