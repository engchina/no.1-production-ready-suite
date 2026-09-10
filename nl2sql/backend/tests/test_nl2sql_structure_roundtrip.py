"""三段階の情報伝達、構造からの再生成と安全・認可境界を検証する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request
from test_nl2sql_reverse_access import _principal, _request, _service

from app.features.nl2sql import router
from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import ReverseSqlRequest, StructureToSqlRequest
from app.features.nl2sql.reverse_prompts import source_prompt


class StagedClient:
    def __init__(self, outputs: list[Any], *, configured: bool = True):
        self.outputs = iter(outputs)
        self.calls: list[dict[str, Any]] = []
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def generate(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return json.dumps(output, ensure_ascii=False)


def test_sql_assist_source_prompts_match_pinned_manifest() -> None:
    root = Path(__file__).parents[1] / "app/features/nl2sql/prompts/sql_assist"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["revision"] == "cacd0959a5fa2a279cb1147de2d04e5a9b01815f"
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((root / filename).read_bytes()).hexdigest() == digest
        assert source_prompt(filename.removesuffix(".txt"))


def test_three_stages_pass_complete_outputs_and_regeneration_uses_only_edited_structure() -> None:
    service = _service()
    sql = (
        "SELECT ID, SUM(AMOUNT) OVER (ORDER BY ID ROWS UNBOUNDED PRECEDING) AS TOTAL "
        "FROM APP.INVOICES WHERE NAME LIKE 'A_%' AND (AMOUNT >= 100 OR AMOUNT IS NULL) "
        "ORDER BY ID DESC NULLS LAST OFFSET 2 ROWS FETCH NEXT 5 ROWS ONLY"
    )
    physical = "全物理構造: " + sql
    logical = "全論理構造: " + sql
    edited = logical.replace("AMOUNT >= 100", "AMOUNT >= 200")
    regenerated = sql.replace("AMOUNT >= 100", "AMOUNT >= 200")
    client = StagedClient(
        [
            {"logical_structure": physical},
            {"logical_structure": logical},
            {
                "question": "全条件を満たす請求情報を取得したい",
                "logical_steps": ["請求情報を絞り込む"],
            },
            {"sql": regenerated, "explanation": "閾値を200に変更"},
        ]
    )
    cast(Any, service)._enterprise_ai_client = client
    data = service.reverse_sql_deep(ReverseSqlRequest(sql=sql, profile_id="finance"))
    assert data.logical_structure == logical
    assert data.logical_structure_items == []
    assert client.calls[0]["prompt"] == sql
    assert json.loads(client.calls[1]["prompt"]) == {"sql_structure": physical, "sql": sql}
    assert client.calls[2]["prompt"] == logical
    assert all("schema:" in call["context"] for call in client.calls)
    result = service.structure_to_sql(
        StructureToSqlRequest(logical_structure=edited, profile_id="finance")
    )
    assert result.sql == regenerated
    assert client.calls[3]["prompt"] == edited
    assert "LIKE は前方・後方・部分一致" in client.calls[3]["system_prompt"]
    assert "AMOUNT >= 100" not in client.calls[3]["context"]
    assert result.source == "oci_enterprise_ai"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM APP.INVOICES",
        "SELECT ID FROM APP.INVOICES; DELETE FROM APP.INVOICES",
        "SELECT ID FROM APP.ORDERS",
        "SELECT UTL_HTTP.REQUEST('https://example.com') FROM APP.INVOICES",
        "not sql",
    ],
)
def test_regeneration_rejects_unsafe_or_out_of_profile_sql(sql: str) -> None:
    service = _service()
    cast(Any, service)._enterprise_ai_client = StagedClient([{"sql": sql}])
    with pytest.raises(ValueError):
        service.structure_to_sql(
            StructureToSqlRequest(logical_structure="請求一覧", profile_id="finance")
        )


@pytest.mark.parametrize("output", [{}, {"sql": []}, {"sql": " "}, {"sql": ""}])
def test_regeneration_rejects_invalid_response(output: dict[str, Any]) -> None:
    service = _service()
    cast(Any, service)._enterprise_ai_client = StagedClient([output])
    with pytest.raises(ValueError):
        service.structure_to_sql(
            StructureToSqlRequest(logical_structure="請求一覧", profile_id="finance")
        )


def test_unconfigured_and_empty_input_never_fabricate_regenerated_sql() -> None:
    service = _service()
    client = StagedClient([], configured=False)
    cast(Any, service)._enterprise_ai_client = client
    for structure in [" ", "請求一覧"]:
        with pytest.raises(ValueError):
            service.structure_to_sql(
                StructureToSqlRequest(logical_structure=structure, profile_id="finance")
            )
    assert client.calls == []


@pytest.mark.parametrize("fail_at", [0, 1, 2])
def test_failed_stage_preserves_completed_structure_or_exact_original_sql(fail_at: int) -> None:
    service = _service()
    sql = "WITH x AS (SELECT ID, 'INVOICES;漢字' AS LABEL FROM APP.INVOICES) SELECT * FROM x"
    outputs: list[Any] = [{"logical_structure": "physical"}, {"logical_structure": "logical"}]
    outputs[fail_at:] = [EnterpriseAiDirectError("unavailable")]
    cast(Any, service)._enterprise_ai_client = StagedClient(outputs)
    result = service.reverse_sql_deep(ReverseSqlRequest(sql=sql, profile_id="finance"))
    assert result.warnings
    assert (
        (result.logical_structure == "logical")
        if fail_at == 2
        else (sql in result.logical_structure)
    )


def test_structure_endpoint_requires_profile_access_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    client = StagedClient([{"sql": "SELECT ID FROM APP.INVOICES"}])
    cast(Any, service)._enterprise_ai_client = client
    monkeypatch.setattr(router, "nl2sql_service", service)
    req = StructureToSqlRequest(logical_structure="請求一覧", profile_id="finance")
    with pytest.raises(HTTPException) as exc:
        router.structure_to_sql(req, cast(Request, _request(_principal({"sales"}))))
    assert exc.value.status_code == 403
    assert client.calls == []
    result = router.structure_to_sql(req, cast(Request, _request(_principal({"finance"}))))
    assert result.data is not None
    assert result.data.sql == "SELECT ID FROM APP.INVOICES"


@pytest.mark.parametrize("use_glossary", [False, True])
def test_original_glossary_prompt_is_applied_only_when_requested(use_glossary: bool) -> None:
    service = _service()
    client = StagedClient(
        [
            {"logical_structure": "physical"},
            {"logical_structure": "logical"},
            {"question": "請求一覧"},
        ]
    )
    cast(Any, service)._enterprise_ai_client = client
    result = service.reverse_sql_deep(
        ReverseSqlRequest(
            sql="SELECT ID FROM APP.INVOICES", profile_id="finance", use_glossary=use_glossary
        )
    )
    assert result.source == "oci_enterprise_ai"
    assert ("本タスクでは逆最適化を行います" in client.calls[2]["system_prompt"]) == use_glossary
    assert ("- 請求: INVOICES" in client.calls[2]["context"]) == use_glossary


def test_structure_endpoint_handles_provider_failure_and_retains_menu_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.security.permissions import permission_for_route

    service = _service()
    cast(Any, service)._enterprise_ai_client = StagedClient(
        [EnterpriseAiDirectError("provider unavailable")]
    )
    monkeypatch.setattr(router, "nl2sql_service", service)
    with pytest.raises(HTTPException) as exc:
        router.structure_to_sql(
            StructureToSqlRequest(logical_structure="請求一覧", profile_id="finance"),
            cast(Request, _request(_principal({"finance"}))),
        )
    assert exc.value.status_code == 502
    assert "再試行" in exc.value.detail
    assert permission_for_route("POST", "/nl2sql/reverse/sql") == frozenset(
        {"menu.sql_to_question"}
    )
