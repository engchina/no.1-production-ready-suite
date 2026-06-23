from __future__ import annotations

import importlib
import io
from typing import Any, cast

import httpx

from app.features.nl2sql.enterprise_ai_client import EnterpriseAiDirectError
from app.features.nl2sql.models import (
    AnnotationApplyItem,
    AnnotationApplyRequest,
    AssetCleanupData,
    ClassifierPredictRequest,
    ClassifierTrainRequest,
    CommentSuggestionRequest,
    DbAdminExecuteRequest,
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfileRecommendationRequest,
    ReverseSqlRequest,
    RewriteRequest,
    SampleDataMutationRequest,
    SampleDataStep,
    SelectAiDbProfileUpsertRequest,
    SyntheticDataGenerateRequest,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.main import app


class FakeEnterpriseAiClient:
    def __init__(self, *responses: str | Exception, configured: bool = True) -> None:
        self.responses = list(responses)
        self.configured = configured

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "fake-enterprise-ai"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        if not self.responses:
            return ""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeEmbeddingClient:
    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * 1536
            vector[1 if "入金" in text or "支払" in text else 2] = 1.0
            vectors.append(vector)
        return vectors


def _workbook_bytes(workbook: Any) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _import_sample(service: Nl2SqlService) -> None:
    service.import_sample_data(
        SampleDataMutationRequest(
            step=SampleDataStep.ALL,
            execute=True,
            confirmation="SQL_ASSIST_SAMPLE",
        )
    )


def test_classifier_training_predicts_and_drives_profile_recommendation() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._embedding_client = FakeEmbeddingClient()
    service.create_profile(
        Nl2SqlProfile(
            id="payment",
            name="入金管理",
            description="入金遅延と支払状況を扱う profile。",
            allowed_tables=["PAYMENTS", "INVOICES"],
            glossary={"入金": "PAYMENTS.PAID_AT"},
        )
    )
    payload = "\n".join(
        [
            "CATEGORY,TEXT",
            "標準業務プロファイル,請求金額が大きい取引先を見たい",
            "標準業務プロファイル,売上合計を顧客別に確認したい",
            "入金管理,入金が遅れている請求を確認したい",
            "入金管理,未入金の支払状況を見たい",
        ]
    ).encode()

    imported = service.import_classifier_training_data(
        filename="training_data.csv",
        content=payload,
        replace=True,
    )
    assert imported.imported_count == 4

    status = service.train_classifier(ClassifierTrainRequest())
    assert status.ready
    assert status.example_count == 4
    assert status.category_count == 2

    prediction = service.predict_classifier(
        ClassifierPredictRequest(question="未入金の請求を確認したい")
    )
    assert prediction.recommendation_source == "classifier"
    assert prediction.candidates

    recommendation = service.recommend_profile(
        ProfileRecommendationRequest(question="未入金の請求を確認したい")
    )
    assert recommendation.recommendation_source == "classifier"
    assert recommendation.classifier_version
    assert recommendation.category_scores

    models = service.list_classifier_models()
    assert models.active_version == status.classifier_version
    assert models.models

    activated = service.activate_classifier_model(status.classifier_version)
    assert activated.active_version == status.classifier_version

    deleted = service.delete_classifier_model(status.classifier_version)
    assert deleted.active_version == ""


def test_db_admin_executor_requires_confirmation_for_non_select() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    selected = service.execute_db_admin_sql(
        DbAdminExecuteRequest(sql="SELECT * FROM INVOICES", row_limit=3)
    )
    assert selected.executed is True
    assert selected.select_result is not None
    assert selected.statements[0].statement_type == "SELECT"

    dry_run = service.execute_db_admin_sql(
        DbAdminExecuteRequest(sql="COMMENT ON TABLE \"INVOICES\" IS '請求';")
    )
    assert dry_run.executed is False
    assert dry_run.statements[0].status == "dry_run"

    blocked = service.execute_db_admin_sql(
        DbAdminExecuteRequest(
            sql="COMMENT ON TABLE \"INVOICES\" IS '請求';",
            execute=True,
        )
    )
    assert blocked.executed is False
    assert blocked.statements[0].status == "confirmation_required"


def test_select_ai_profile_json_and_synthetic_object_list_dry_run() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    profile = service.upsert_select_ai_db_profile(
        SelectAiDbProfileUpsertRequest(
            profile_name="LOW_LEVEL_PROFILE",
            attributes={"object_list": [{"owner": "APP", "name": "INVOICES"}]},
            description="low level",
            category="test",
        )
    )
    assert profile.status == "dry_run"
    assert profile.profile is not None
    assert profile.profile.attributes["object_list"][0]["name"] == "INVOICES"

    synthetic = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(
            object_list=["INVOICES", "CUSTOMERS"],
            rows_per_table=5,
            profile_name="LOW_LEVEL_PROFILE",
        )
    )
    assert synthetic.executed is False
    assert synthetic.object_list == ["INVOICES", "CUSTOMERS"]
    assert synthetic.row_count == 5


def test_classifier_training_data_xlsx_accepts_legacy_headers_and_blanks() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "training_data"
    sheet.append(["category", "QUESTION"])
    sheet.append(["標準業務プロファイル", "請求金額を確認したい"])
    sheet.append(["入金管理", "未入金の請求を確認したい"])
    sheet.append(["入金管理", "未入金の請求を確認したい"])
    sheet.append(["", "質問だけの空行"])
    sheet.append(["標準業務プロファイル", ""])

    imported = service.import_classifier_training_data(
        filename="training_data.xlsx",
        content=_workbook_bytes(workbook),
        replace=True,
    )

    assert imported.imported_count == 3
    assert imported.skipped_count == 2
    assert imported.categories == ["入金管理", "標準業務プロファイル"]


def test_annotations_and_synthetic_data_support_dry_run_without_oracle() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)

    suggestions = service.suggest_annotations()
    assert suggestions.suggestions
    first = suggestions.suggestions[0]

    applied = service.apply_annotations(
        AnnotationApplyRequest(
            items=[
                AnnotationApplyItem(
                    object_name=first.object_name,
                    object_type=first.object_type,
                    annotation_name=first.annotation_name,
                    annotation_value=first.annotation_value,
                )
            ],
            execute=False,
        )
    )
    assert not applied.executed
    assert applied.statements
    assert "ANNOTATIONS" in applied.statements[0].sql

    synthetic = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(table_name="EMPLOYEE", row_count=5, execute=False)
    )
    assert synthetic.status == "dry_run"
    assert synthetic.table_name == "EMPLOYEE"
    assert synthetic.row_count == 5


def test_profile_learning_material_imports_csv_and_exports_xlsx() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    imported = service.import_profile_learning_material(
        profile_id="default",
        filename="terms.csv",
        content="TERM,DEFINITION\n粗利,INVOICES.PROFIT\n".encode(),
        mode="merge",
    )
    assert imported.imported_terms == 1
    assert imported.profile.glossary["粗利"] == "INVOICES.PROFIT"

    imported_rules = service.import_profile_learning_material(
        profile_id="default",
        filename="rules.csv",
        content="CATEGORY,RULE\n共通,日付条件は TRUNC を使う\n".encode(),
        mode="merge",
    )
    assert imported_rules.imported_rules == 1
    assert "日付条件は TRUNC を使う" in imported_rules.profile.sql_rules

    filename, workbook_bytes = service.export_profile_learning_material_xlsx("default")
    assert filename.endswith("_learning_material.xlsx")
    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes), read_only=True)
    assert {"terms", "rules", "few_shot"}.issubset(set(workbook.sheetnames))


def test_profile_learning_material_xlsx_handles_multi_sheet_dedupe_and_replace() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="compat",
            name="互換性検証",
            glossary={"既存": "OLD.VALUE"},
            sql_rules=["既存ルール"],
            few_shot_examples=[{"question": "既存質問", "sql": "SELECT 1 FROM DUAL"}],
        )
    )
    openpyxl = importlib.import_module("openpyxl")
    workbook = openpyxl.Workbook()
    terms = workbook.active
    terms.title = "Terms"
    terms.append(["term", "definition"])
    terms.append(["粗利", "INVOICES.PROFIT"])
    terms.append(["粗利", "INVOICES.PROFIT"])
    terms.append(["空定義", ""])
    rules = workbook.create_sheet("Rules")
    rules.append(["Category", "Text"])
    rules.append(["共通", "日付条件は TRUNC を使う"])
    rules.append(["共通", "日付条件は TRUNC を使う"])
    examples = workbook.create_sheet("few_shot")
    examples.append(["question", "expected_sql"])
    examples.append(["粗利を見たい", "SELECT PROFIT FROM INVOICES"])
    examples.append(["粗利を見たい", "SELECT PROFIT FROM INVOICES"])

    merged = service.import_profile_learning_material(
        profile_id="compat",
        filename="legacy_learning.xlsx",
        content=_workbook_bytes(workbook),
        mode="merge",
    )

    assert merged.imported_terms == 1
    assert merged.imported_rules == 1
    assert merged.imported_examples == 1
    assert merged.skipped_count == 1
    assert merged.profile.glossary["既存"] == "OLD.VALUE"
    assert merged.profile.glossary["粗利"] == "INVOICES.PROFIT"
    assert merged.profile.sql_rules == ["既存ルール", "日付条件は TRUNC を使う"]
    assert merged.profile.few_shot_examples == [
        {"question": "既存質問", "sql": "SELECT 1 FROM DUAL"},
        {"question": "粗利を見たい", "sql": "SELECT PROFIT FROM INVOICES"},
    ]

    replaced = service.import_profile_learning_material(
        profile_id="compat",
        filename="terms.csv",
        content="term,definition\n売上,INVOICES.TOTAL_AMOUNT\n".encode(),
        mode="replace",
    )

    assert replaced.profile.glossary == {"売上": "INVOICES.TOTAL_AMOUNT"}
    assert replaced.profile.sql_rules == []
    assert replaced.profile.few_shot_examples == []


async def test_db_profile_drop_endpoint_supports_dry_run_and_execute_mock(
    monkeypatch: Any,
) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        dry_run = await client.post(
            "/api/nl2sql/select-ai/db-profiles/NL2SQL_DEFAULT_PROFILE/drop",
            json={"execute": False},
        )
        assert dry_run.status_code == 200
        dry_run_data = dry_run.json()["data"]
        assert dry_run_data["executed"] is False
        assert dry_run_data["status"] == "dry_run"

        from app.features.nl2sql import router as nl2sql_router

        captured: dict[str, object] = {}

        def fake_drop(
            profile_name: str,
            execute: bool,
            confirmation: str = "",
            reason: str = "",
        ) -> AssetCleanupData:
            captured["profile_name"] = profile_name
            captured["execute"] = execute
            captured["confirmation"] = confirmation
            captured["reason"] = reason
            return AssetCleanupData(
                engine=Nl2SqlEngine.SELECT_AI,
                executed=execute,
                status="cleaned" if execute else "dry_run",
                profile_name=profile_name,
                asset_names={"profile": profile_name},
                engine_meta={"runtime": "mock"},
            )

        monkeypatch.setattr(
            cast(Any, nl2sql_router).nl2sql_service,
            "drop_select_ai_db_profile",
            fake_drop,
        )
        executed = await client.post(
            "/api/nl2sql/select-ai/db-profiles/NL2SQL_DEFAULT_PROFILE/drop",
            json={
                "execute": True,
                "confirmation": "NL2SQL_DEFAULT_PROFILE",
                "reason": "test",
            },
        )

    assert executed.status_code == 200
    assert executed.json()["data"]["status"] == "cleaned"
    assert captured == {
        "profile_name": "NL2SQL_DEFAULT_PROFILE",
        "execute": True,
        "confirmation": "NL2SQL_DEFAULT_PROFILE",
        "reason": "test",
    }


def test_comment_llm_and_agent_privilege_checks_fallback_without_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    monkeypatch.setattr(
        cast(Any, service),
        "_enterprise_ai_client",
        FakeEnterpriseAiClient(configured=False),
    )
    _import_sample(service)

    comments = service.suggest_comments(CommentSuggestionRequest(use_llm=True))
    assert comments.suggestions
    assert comments.source == "deterministic"
    assert comments.warnings

    privileges = service.check_select_ai_agent_privileges()
    assert privileges.runtime == "deterministic"
    assert privileges.status == "warning"
    assert privileges.checks


def test_comment_llm_generation_uses_enterprise_ai_and_falls_back_on_bad_json() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        '{"suggestions":[{"object_name":"EMPLOYEE","object_type":"table",'
        '"suggested_comment":"社員情報のヘッダ"}]}'
    )

    generated = service.suggest_comments(CommentSuggestionRequest(use_llm=True, max_items=2))

    assert generated.source == "oci_enterprise_ai"
    assert generated.suggestions[0].object_name == "EMPLOYEE"
    assert generated.suggestions[0].suggested_comment == "社員情報のヘッダ"

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        '{"suggestions":[{"object_name":"EMPLOYEE","object_type":"invalid",'
        '"suggested_comment":"壊れた候補"}]}'
    )

    fallback = service.suggest_comments(CommentSuggestionRequest(use_llm=True, max_items=2))

    assert fallback.source == "deterministic"
    assert fallback.warnings
    assert "fallback" in fallback.warnings[0]


def test_rewrite_uses_enterprise_ai_and_falls_back_on_generation_error() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        "請求金額を税込金額として一覧したい"
    )

    rewritten = service.rewrite(
        RewriteRequest(question="請求金額を一覧で見たい", profile_id="default")
    )

    assert rewritten.source == "oci_enterprise_ai"
    assert rewritten.model == "fake-enterprise-ai"
    assert rewritten.rewritten_question == "請求金額を税込金額として一覧したい"

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        EnterpriseAiDirectError("boom")
    )

    fallback = service.rewrite(
        RewriteRequest(question="請求金額を一覧で見たい", profile_id="default")
    )

    assert fallback.source == "deterministic"
    assert fallback.warnings


def test_reverse_deep_uses_enterprise_ai_and_falls_back_on_invalid_json() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    request = ReverseSqlRequest(sql="SELECT TOTAL_AMOUNT FROM INVOICES")
    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient(
        '{"question":"請求金額を確認したい",'
        '"explanation":"INVOICES から TOTAL_AMOUNT を取得します。",'
        '"logical_steps":["INVOICES を参照","TOTAL_AMOUNT を選択"]}'
    )

    reversed_sql = service.reverse_sql_deep(request)

    assert reversed_sql.source == "oci_enterprise_ai"
    assert reversed_sql.question == "請求金額を確認したい"
    assert reversed_sql.logical_steps == ["INVOICES を参照", "TOTAL_AMOUNT を選択"]

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient("not json")

    fallback = service.reverse_sql_deep(request)

    assert fallback.source == "deterministic"
    assert fallback.warnings
