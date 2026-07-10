from __future__ import annotations

import importlib
import io
from typing import Any, cast

import httpx
import pytest

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
    PreviewRequest,
    ProfileRecommendationRequest,
    ProfileSelectAiProfileRequest,
    ReverseSqlRequest,
    RewriteRequest,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
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
        self.calls: list[dict[str, str]] = []

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "fake-enterprise-ai"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        self.calls.append(
            {"prompt": prompt, "context": context, "system_prompt": system_prompt}
        )
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


class FakeOracleGenerator:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def generate_select_ai_sql(self, *, profile_name: str, question: str) -> str:
        del profile_name
        self.questions.append(question)
        return "SELECT * FROM INVOICES"

    def run_select_ai_agent_team(
        self, *, team_name: str, question: str, tool_name: str
    ) -> tuple[str, str]:
        del team_name, tool_name
        self.questions.append(question)
        return "SELECT * FROM INVOICES", "conversation-1"


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


def test_profile_upsert_preserves_allowed_views_and_select_ai_config() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    created = service.create_profile(
        Nl2SqlProfile(
            id="finance",
            name="財務プロファイル",
            description="表とビューを併用する profile。",
            allowed_tables=["INVOICES"],
            allowed_views=["V_INVOICE_SUMMARY"],
            select_ai_config={
                "profile_name": "FINANCE_SELECT_AI",
                "region": "ap-osaka-1",
                "model": "cohere.command-r-plus",
                "embedding_model": "cohere.embed-v4.0",
                "max_tokens": 24000,
                "enforce_object_list": True,
                "comments": True,
                "annotations": True,
                "constraints": True,
            },
        )
    )

    assert created.allowed_views == ["V_INVOICE_SUMMARY"]
    assert created.select_ai_config.profile_name == "FINANCE_SELECT_AI"
    assert created.select_ai_config.embedding_model == "cohere.embed-v4.0"

    updated = service.update_profile(
        "finance",
        lambda current: current.model_copy(
            update={
                "allowed_views": ["V_CUSTOMER_BALANCE"],
                "select_ai_config": current.select_ai_config.model_copy(
                    update={"profile_name": "FINANCE_SELECT_AI_V2", "max_tokens": 32000}
                ),
            }
        ),
    )

    assert updated.allowed_views == ["V_CUSTOMER_BALANCE"]
    assert updated.select_ai_config.profile_name == "FINANCE_SELECT_AI_V2"
    assert updated.select_ai_config.max_tokens == 32000


def test_profile_legacy_snapshot_defaults_allowed_views_and_select_ai_config() -> None:
    profile = Nl2SqlProfile.model_validate(
        {
            "id": "legacy",
            "name": "旧 snapshot",
            "description": "allowed_views と select_ai_config が無い旧データ。",
            "allowed_tables": ["INVOICES"],
        }
    )

    assert profile.allowed_views == []
    assert profile.select_ai_config.embedding_model == "cohere.embed-v4.0"
    assert profile.select_ai_config.enforce_object_list is True


def test_profile_select_ai_dry_run_uses_tables_and_views_object_list() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    cast(Any, service)._catalog = SchemaCatalog(
        refreshed_at="2026-07-10T00:00:00+00:00",
        tables=[
            SchemaTable(table_name="INVOICES", logical_name="請求", table_type="TABLE"),
            SchemaTable(
                table_name="V_INVOICE_SUMMARY",
                logical_name="請求サマリ",
                table_type="VIEW",
            ),
        ],
    )
    service.create_profile(
        Nl2SqlProfile(
            id="finance",
            name="財務プロファイル",
            description="Select AI dry-run 対象。",
            allowed_tables=["INVOICES"],
            allowed_views=["V_INVOICE_SUMMARY"],
            select_ai_config={
                "profile_name": "FINANCE_SELECT_AI",
                "region": "ap-osaka-1",
                "model": "cohere.command-r-plus",
                "embedding_model": "cohere.embed-v4.0",
            },
        )
    )

    dry_run = service.upsert_profile_select_ai_profile(
        "finance",
        ProfileSelectAiProfileRequest(execute=False, reason="pytest-dry-run"),
    )

    assert dry_run.executed is False
    assert dry_run.status == "dry_run"
    assert dry_run.profile is not None
    assert dry_run.profile.name == "FINANCE_SELECT_AI"
    assert dry_run.profile.attributes["provider"] == "oci"
    assert dry_run.profile.attributes["embedding_model"] == "cohere.embed-v4.0"
    object_list = dry_run.profile.attributes["object_list"]
    assert [item["name"] for item in object_list] == ["INVOICES", "V_INVOICE_SUMMARY"]
    assert all(item["owner"] for item in object_list)
    assert dry_run.profile.tables == ["INVOICES"]
    assert dry_run.profile.views == ["V_INVOICE_SUMMARY"]


def test_profile_select_ai_execute_requires_confirmation_and_oracle_runtime() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="finance",
            name="財務プロファイル",
            allowed_tables=["INVOICES"],
            allowed_views=["V_INVOICE_SUMMARY"],
            select_ai_config={"profile_name": "FINANCE_SELECT_AI"},
        )
    )

    missing_confirmation = service.upsert_profile_select_ai_profile(
        "finance",
        ProfileSelectAiProfileRequest(execute=True),
    )
    assert missing_confirmation.status == "confirmation_required"
    assert missing_confirmation.executed is False

    requires_oracle = service.upsert_profile_select_ai_profile(
        "finance",
        ProfileSelectAiProfileRequest(
            execute=True,
            confirmation="FINANCE_SELECT_AI",
            reason="pytest-execute",
        ),
    )
    assert requires_oracle.status == "requires_oracle"
    assert requires_oracle.executed is False
    assert any("NL2SQL_RUNTIME_MODE=oracle" in warning for warning in requires_oracle.warnings)


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


def test_synthetic_data_rejects_blob_table_before_oracle_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-06-21T10:00:00Z",
        tables=[
            SchemaTable(
                table_name="DENPYO_FILES",
                logical_name="伝票ファイル",
                columns=[
                    SchemaColumn(column_name="FILE_ID", logical_name="ID", data_type="NUMBER"),
                    SchemaColumn(column_name="FILE_BODY", logical_name="本文", data_type="BLOB"),
                ],
            )
        ],
    )

    class FailIfCalledAdapter:
        def generate_synthetic_data(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("Oracle should not be called for BLOB tables")

    service._oracle_adapter = cast(Any, FailIfCalledAdapter())
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)

    synthetic = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(
            table_name="DENPYO_FILES",
            row_count=1,
            execute=True,
            confirmation="ADMIN_EXECUTE",
        )
    )

    assert synthetic.status == "error"
    assert not synthetic.executed
    assert synthetic.table_name == ""
    assert any("DENPYO_FILES" in warning and "BLOB" in warning for warning in synthetic.warnings)


def test_synthetic_data_skips_unsupported_tables_and_generates_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(
        refreshed_at="2026-06-21T10:00:00Z",
        tables=[
            SchemaTable(
                table_name="DENPYO_FILES",
                logical_name="伝票ファイル",
                columns=[
                    SchemaColumn(column_name="FILE_ID", logical_name="ID", data_type="NUMBER"),
                    SchemaColumn(column_name="FILE_BODY", logical_name="本文", data_type="BLOB"),
                ],
            ),
            SchemaTable(
                table_name="INVOICES",
                logical_name="請求",
                columns=[
                    SchemaColumn(
                        column_name="INVOICE_ID",
                        logical_name="請求ID",
                        data_type="NUMBER",
                    ),
                    SchemaColumn(
                        column_name="CUSTOMER_NAME",
                        logical_name="取引先",
                        data_type="VARCHAR2(120)",
                    ),
                ],
            ),
        ],
    )
    calls: list[dict[str, Any]] = []

    class RecordingAdapter:
        def generate_synthetic_data(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "runtime": "oracle",
                "operation_id": "op-001",
                "table_name": kwargs["table_name"],
                "object_list": kwargs["object_list"],
                "row_count": kwargs["row_count"],
            }

    service._oracle_adapter = cast(Any, RecordingAdapter())
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)

    synthetic = service.generate_synthetic_data(
        SyntheticDataGenerateRequest(
            object_list=["DENPYO_FILES", "INVOICES"],
            row_count=2,
            execute=True,
            confirmation="ADMIN_EXECUTE",
            profile_name="NL2SQL_PROFILE",
        )
    )

    assert synthetic.status == "submitted"
    assert synthetic.executed
    assert synthetic.table_name == "INVOICES"
    assert synthetic.object_list == ["INVOICES"]
    assert calls == [
        {
            "table_name": "INVOICES",
            "object_list": [],
            "row_count": 2,
            "profile_name": "NL2SQL_PROFILE",
            "user_prompt": "",
            "sample_rows": 0,
            "use_comments": True,
        }
    ]
    assert any("DENPYO_FILES" in warning and "BLOB" in warning for warning in synthetic.warnings)


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


def test_legacy_learning_material_imports_exports_and_resolves_by_category() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="osaka",
            name="大阪プロファイル",
            category="OSAKA",
            glossary={"売上": "PROFILE.SALES"},
            sql_rules=["profile 固有ルール"],
        )
    )
    openpyxl = importlib.import_module("openpyxl")
    terms_book = openpyxl.Workbook()
    terms = terms_book.active
    terms.title = "terms"
    terms.append(["TERM", "DEFINITION"])
    terms.append(["粗利", "INVOICES.PROFIT"])
    terms.append(["売上", "LEGACY.SALES"])
    rules_book = openpyxl.Workbook()
    rules = rules_book.active
    rules.title = "rules"
    rules.append(["CATEGORY", "RULE"])
    rules.append(["共通", "共通ルール"])
    rules.append(["OSAKA", "大阪ルール"])
    rules.append(["TOKYO", "東京ルール"])

    material = service.import_legacy_terms(
        filename="terms.xlsx",
        content=_workbook_bytes(terms_book),
    )
    assert material.glossary["粗利"] == "INVOICES.PROFIT"
    material = service.import_legacy_rules(
        filename="rules.xlsx",
        content=_workbook_bytes(rules_book),
    )
    assert len(material.rule_entries) == 3

    profile = service.get_profile("osaka")
    assert cast(Any, service)._effective_glossary(profile)["売上"] == "PROFILE.SALES"
    assert cast(Any, service)._effective_sql_rules(profile) == [
        "共通ルール",
        "大阪ルール",
        "profile 固有ルール",
    ]
    assert "東京ルール" not in cast(Any, service)._append_rules_to_question("質問", profile)

    terms_filename, terms_bytes = service.export_legacy_terms_xlsx()
    rules_filename, rules_bytes = service.export_legacy_rules_xlsx()
    assert terms_filename == "terms.xlsx"
    assert rules_filename == "rules.xlsx"
    terms_export = openpyxl.load_workbook(io.BytesIO(terms_bytes), read_only=True)
    rules_export = openpyxl.load_workbook(io.BytesIO(rules_bytes), read_only=True)
    assert terms_export.active["A1"].value == "TERM"
    assert rules_export.active["A1"].value == "CATEGORY"


def test_oracle_runtime_appends_effective_rules_to_select_ai_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="rules",
            name="ルール検証",
            category="OSAKA",
            allowed_tables=["INVOICES"],
            sql_rules=["profile 固有ルール"],
        )
    )
    service.import_legacy_rules(
        filename="rules.csv",
        content="CATEGORY,RULE\n共通,共通ルール\nOSAKA,大阪ルール\nTOKYO,東京ルール\n".encode(),
    )
    fake_oracle = FakeOracleGenerator()
    cast(Any, service)._oracle_adapter = fake_oracle
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)

    service.preview(
        PreviewRequest(
            question="請求を確認したい",
            engine=Nl2SqlEngine.SELECT_AI,
            profile_id="rules",
        )
    )
    service.preview(
        PreviewRequest(
            question="請求を確認したい",
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            profile_id="rules",
        )
    )

    assert len(fake_oracle.questions) == 2
    for question in fake_oracle.questions:
        assert "=== Rules ===" in question
        assert "共通ルール" in question
        assert "大阪ルール" in question
        assert "profile 固有ルール" in question
        assert "東京ルール" not in question


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
        '"logical_structure":"SQL 論理構造",'
        '"logical_steps":["INVOICES を参照","TOTAL_AMOUNT を選択"]}'
    )

    reversed_sql = service.reverse_sql_deep(request)

    assert reversed_sql.source == "oci_enterprise_ai"
    assert reversed_sql.question == "請求金額を確認したい"
    assert reversed_sql.logical_structure == "SQL 論理構造"
    assert reversed_sql.logical_steps == ["INVOICES を参照", "TOTAL_AMOUNT を選択"]

    cast(Any, service)._enterprise_ai_client = FakeEnterpriseAiClient("not json")

    fallback = service.reverse_sql_deep(request)

    assert fallback.source == "deterministic"
    assert fallback.warnings


def test_reverse_deep_uses_profile_context_and_glossary() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service.create_profile(
        Nl2SqlProfile(
            id="billing",
            name="請求管理",
            description="請求テーブルを扱う profile。",
            allowed_tables=["INVOICES"],
            glossary={"請求金額": "INVOICES.TOTAL_AMOUNT"},
        )
    )
    fake = FakeEnterpriseAiClient(
        '{"question":"請求金額を一覧で確認したい",'
        '"explanation":"請求管理 profile の文脈で逆生成しました。",'
        '"logical_structure":"SQL 論理構造\\n- SELECT: 請求金額",'
        '"logical_steps":["請求金額を選択"]}'
    )
    cast(Any, service)._enterprise_ai_client = fake

    reversed_sql = service.reverse_sql_deep(
        ReverseSqlRequest(
            sql="SELECT TOTAL_AMOUNT FROM INVOICES",
            profile_id="billing",
            use_glossary=True,
        )
    )

    assert reversed_sql.question == "請求金額を一覧で確認したい"
    assert "請求金額" in reversed_sql.logical_structure
    assert fake.calls
    assert "profile: 請求管理" in fake.calls[0]["context"]
    assert "- 請求金額: INVOICES.TOTAL_AMOUNT" in fake.calls[0]["context"]
    assert "logical_structure" in fake.calls[0]["system_prompt"]
