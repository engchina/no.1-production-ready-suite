"""型付き Q/A 関係の入力 SQL 接地と入力ごとの隔離。"""

import json
from typing import Any

import pytest
from test_nl2sql_ontology_build import _FakeEnterpriseAiClient, _wait_for_job
from test_nl2sql_ontology_definitions import runtime

from app.features.nl2sql.ontology_build import OntologyBuildService
from app.features.nl2sql.ontology_build_grounding import ground_qa_links
from app.features.nl2sql.ontology_definition_service import ProfileOntologyDefinitionService
from app.features.nl2sql.ontology_models import OntologyBuildExtraction, QaPair
from app.settings import get_settings

SCHEMA: dict[str, Any] = {
    "objects": [
        {"owner": "APP", "object_name": "ORDERS", "columns": ["ID", "CUSTOMER_ID", "AMOUNT"]},
        {"owner": "APP", "object_name": "CUSTOMERS", "columns": ["ID", "AMOUNT"]},
        {"owner": "SALES", "object_name": '"Mixed_Case"', "columns": ['"Amount"', "ID"]},
    ]
}
JOIN = "APP.ORDERS.CUSTOMER_ID = APP.CUSTOMERS.ID"


def extraction(expression: str = JOIN) -> OntologyBuildExtraction:
    return OntologyBuildExtraction.model_validate(
        {
            "coverage": [{"kind": "link_type", "status": "generated", "count": 1}],
            "definitions": [
                {
                    "kind": "link_type",
                    "api_name": "OrderCustomer",
                    "name_ja": "受注顧客",
                    "source": "Order",
                    "target": "Customer",
                    "join_expression_sql": expression,
                },
                {
                    "kind": "shared_property",
                    "api_name": "Amount",
                    "name_ja": "金額",
                    "data_type": "number",
                },
            ],
        }
    )


@pytest.mark.parametrize(
    "sql,expression,accepted",
    [
        (["SELECT o.ID FROM APP.ORDERS o JOIN APP.CUSTOMERS c ON o.CUSTOMER_ID=c.ID"], JOIN, True),
        (
            [
                "WITH x AS (SELECT CUSTOMER_ID AS CID FROM APP.ORDERS) "
                "SELECT x.CID FROM x JOIN APP.CUSTOMERS c ON x.CID=c.ID"
            ],
            JOIN,
            True,
        ),
        (["SELECT o.ID FROM APP.ORDERS o, APP.CUSTOMERS c WHERE o.CUSTOMER_ID=c.ID"], JOIN, True),
        (["SELECT ID FROM APP.ORDERS"], "APP.ORDERS.AMOUNT = APP.CUSTOMERS.ID", False),
        (["SELECT ID, AMOUNT FROM APP.CUSTOMERS"], "APP.ORDERS.AMOUNT = APP.CUSTOMERS.ID", False),
        (["SELECT CUSTOMER_ID FROM APP.ORDERS", "SELECT ID FROM APP.CUSTOMERS"], JOIN, False),
        (["SELECT 'AMOUNT' FROM APP.ORDERS"], "APP.ORDERS.AMOUNT = APP.CUSTOMERS.ID", False),
        (["SELECT * FROM APP.ORDERS o JOIN APP.CUSTOMERS c ON o.CUSTOMER_ID=c.ID"], JOIN, True),
        (["SELECT broken FROM"], JOIN, False),
        (["SELECT ID FROM APP.ORDERS"], "", False),
        (["SELECT ID FROM APP.ORDERS"], "1=1", False),
        (
            [
                'SELECT m."Amount", c.ID FROM SALES."Mixed_Case" m '
                'JOIN APP.CUSTOMERS c ON m."Amount"=c.ID'
            ],
            'SALES."Mixed_Case"."Amount" = APP.CUSTOMERS.ID',
            True,
        ),
        (
            ['SELECT m.ID, c.ID FROM SALES."Mixed_Case" m JOIN APP.CUSTOMERS c ON m.ID=c.ID'],
            'SALES."Mixed_Case"."Amount" = APP.CUSTOMERS.ID',
            False,
        ),
    ],
)
def test_typed_link_requires_physical_columns_in_one_qa(
    sql: list[str], expression: str, accepted: bool
) -> None:
    value = extraction(expression)
    result = ground_qa_links(value, sql, SCHEMA)
    assert any(d.kind == "link_type" for d in result.definitions) == accepted
    assert any(d.api_name == "Amount" for d in result.definitions)
    assert bool(result.warnings_ja) != accepted
    if not accepted:
        assert "OrderCustomer / join_expression_sql" in result.warnings_ja[0]
        assert result.coverage[0].status == "insufficient_evidence"
        assert result.coverage[0].count == 0 and result.coverage[0].reason_ja
    assert len(value.definitions) == 2  # checkpoint の入力自体は書き換えない


def test_schema_and_document_links_are_not_filtered_as_qa() -> None:
    value = extraction()
    assert ground_qa_links(value, None, SCHEMA) is value


@pytest.mark.parametrize("mixed", [False, True])
def test_worker_excludes_only_unsupported_qa_link_before_merge(
    mixed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_extraction_gleaning_passes", 0)
    rt, legacy = runtime()
    # Q/A にない関係を返す。同じ関係がスキーマ抽出にもあれば、その独立した結果は保持する。
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(extraction().model_dump_json())
    service = OntologyBuildService(rt)
    job = _wait_for_job(
        service,
        service.start(
            "sales",
            run_schema_naming=mixed,
            qa_pairs=[QaPair(question="受注ID", sql="SELECT ID FROM APP.ORDERS")],
        ).id,
    )
    assert job.status == "succeeded"
    bundle = ProfileOntologyDefinitionService(rt).get("sales", job.result_bundle_id)
    assert any(d.kind == "link_type" for d in bundle.definitions) == mixed
    coverage = next(c for c in bundle.coverage if c.kind == "link_type")
    assert coverage.count == int(mixed)
    if not mixed:
        assert coverage.status == "insufficient_evidence" and coverage.reason_ja
    assert any(d.api_name == "Amount" for d in bundle.definitions)
    assert any("OrderCustomer / join_expression_sql" in w for w in job.warnings_ja)
    if not mixed:
        assert "##### 受注顧客" not in job.markdown_output
    checkpoints = [
        json.loads(a["content"])
        for a in rt.store.list_artifacts(session_id="profile-ontology:sales")
        if a["artifact_type"] == "ontology_build_checkpoint"
    ]
    assert checkpoints and all(
        any(d["kind"] == "link_type" for d in c["definitions"]) for c in checkpoints
    )
