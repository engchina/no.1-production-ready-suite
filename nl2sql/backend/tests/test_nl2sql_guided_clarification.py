"""AI要件確認（SQL の要件の下書き）の回帰テスト（#1439）。

LLM は決定論のスタブ。表のデータを LLM に渡さない・範囲の外と実在しない名前・未承認の結合・根拠の
無い候補を拒む・相対的な期間を具体的な日付で確かめる・回答の後の再検査・上限・SELECT / FROM の
最低の基準・物理名・危険な問い合わせの確認・SQL の骨組みを確かめる。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

import app.features.nl2sql.ontology_router as ontology_router_module
from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaConstraintDetail,
    SchemaTable,
)
from app.features.nl2sql.ontology_models import (
    ClarificationMode,
    QuerySessionStatus,
    RequirementItem,
    RequirementPhase,
    RequirementSection,
    RequirementState,
    RequirementStatus,
    SqlRequirementDraft,
)
from app.features.nl2sql.ontology_router import (
    ClarificationAnswerRequest,
    GenerateSqlRequest,
    OntologyApiRuntime,
    QuerySessionApiCreate,
    QuerySessionData,
    RequirementApplyRequest,
)
from app.features.nl2sql.ontology_service import (
    OntologyGateBlockedError,
    OntologyIntegrityError,
    OntologyStateConflictError,
    OntologyVersionConflictError,
)
from app.features.nl2sql.ontology_store import (
    IDEMPOTENCY_KEY_STORAGE_MAX_BYTES,
    InMemoryOntologyStore,
    OntologyCollection,
)
from app.features.nl2sql.sql_requirement import (
    LlmRequirementOutput,
    RequirementAiUnavailableError,
    build_draft,
    build_requirement_context,
    period_ranges,
)
from app.main import app
from app.settings import get_settings

TODAY = date(2026, 10, 11)


@pytest.fixture(autouse=True)
def _disable_profile_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", False)
    monkeypatch.setattr(get_settings(), "nl2sql_requirement_max_review_rounds", 3)


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        refreshed_at="2026-10-11T00:00:00Z",
        schema_fingerprint="requirement-schema",
        tables=[
            SchemaTable(
                table_name="ORDERS",
                owner="APP",
                logical_name="受注",
                comment="受注の明細",
                columns=[
                    SchemaColumn(
                        column_name="ORDER_ID",
                        logical_name="受注ID",
                        data_type="NUMBER",
                        nullable=False,
                        comment="受注を識別する主キー",
                        sample_values=["1001"],
                    ),
                    SchemaColumn(
                        column_name="CUSTOMER_ID",
                        logical_name="顧客ID",
                        data_type="NUMBER",
                        comment="顧客",
                    ),
                    SchemaColumn(
                        column_name="ORDER_DATE",
                        logical_name="受注日",
                        data_type="DATE",
                        comment="受注した日",
                    ),
                    SchemaColumn(
                        column_name="SHIP_DATE",
                        logical_name="出荷日",
                        data_type="DATE",
                        comment="出荷した日",
                    ),
                    SchemaColumn(
                        column_name="STATUS",
                        logical_name="受注状態",
                        data_type="VARCHAR2",
                        comment="受注の状態（SHIPPED: 出荷済み、CANCELLED: 取消）",
                        sample_values=["SECRET_SAMPLE_VALUE"],
                    ),
                    SchemaColumn(
                        column_name="AMOUNT",
                        logical_name="受注金額",
                        data_type="NUMBER",
                        comment="税込の金額（円）",
                    ),
                ],
                constraint_details=[
                    SchemaConstraintDetail(
                        constraint_name="PK_ORDERS",
                        constraint_type="P",
                        owner="APP",
                        table_name="ORDERS",
                        columns=["ORDER_ID"],
                    ),
                    SchemaConstraintDetail(
                        constraint_name="FK_ORDERS_CUSTOMER",
                        constraint_type="R",
                        owner="APP",
                        table_name="ORDERS",
                        columns=["CUSTOMER_ID"],
                        referenced_owner="APP",
                        referenced_table="CUSTOMERS",
                        referenced_columns=["CUSTOMER_ID"],
                    ),
                ],
            ),
            SchemaTable(
                table_name="CUSTOMERS",
                owner="APP",
                logical_name="顧客",
                comment="顧客の台帳",
                columns=[
                    SchemaColumn(
                        column_name="CUSTOMER_ID",
                        logical_name="顧客ID",
                        data_type="NUMBER",
                        nullable=False,
                        comment="顧客の主キー",
                    ),
                    SchemaColumn(
                        column_name="REGION",
                        logical_name="地域",
                        data_type="VARCHAR2",
                        comment="顧客の地域",
                    ),
                ],
                constraint_details=[
                    SchemaConstraintDetail(
                        constraint_name="PK_CUSTOMERS",
                        constraint_type="P",
                        owner="APP",
                        table_name="CUSTOMERS",
                        columns=["CUSTOMER_ID"],
                    )
                ],
            ),
            SchemaTable(
                table_name="SECRET_TABLE",
                owner="APP",
                logical_name="秘密",
                comment="業務プロファイルの範囲の外",
                columns=[
                    SchemaColumn(column_name="ID", logical_name="ID", data_type="NUMBER"),
                ],
            ),
        ],
    )


class _LegacyService:
    uses_incremental_store = False

    def __init__(self, *, glossary: dict[str, str] | None = None) -> None:
        self.profile = Nl2SqlProfile(
            id="sales",
            name="販売分析",
            description="受注と顧客を分析する",
            allowed_tables=["APP.ORDERS", "APP.CUSTOMERS"],
            glossary=glossary or {"売上": "受注金額（AMOUNT）の合計"},
            sql_rules=["取消（STATUS = 'CANCELLED'）を除く"],
            default_row_limit=100,
        )
        self.catalog = _catalog()
        self._enterprise_ai_client: Any = None

    def get_catalog(self) -> SchemaCatalog:
        return self.catalog.model_copy(deep=True)

    def get_profile(self, profile_id: str, **_kwargs: Any) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("profile not found")
        return self.profile.model_copy(deep=True)

    def resolve_allowed_objects(self, profile_id: str, requested: AllowedObjects) -> AllowedObjects:
        self.get_profile(profile_id)
        if requested.table_names:
            return requested.model_copy(deep=True)
        return AllowedObjects(
            table_names=list(self.profile.allowed_tables), enforce_table_scope=True
        )


class _ScriptedLlm:
    """呼ばれた順に応答を返すスタブ。応答は context（dict）から作る関数でもよい。"""

    def __init__(self, *responses: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return True

    def model_id(self) -> str:
        return "stub-requirement-llm"

    def generate(self, *, prompt: str, context: str, system_prompt: str, **kwargs: Any) -> str:
        payload = json.loads(context)
        self.calls.append(
            {"prompt": prompt, "context": payload, "raw_context": context, "system": system_prompt}
        )
        if not self.responses:
            raise AssertionError("LLM がスクリプトより多く呼ばれた")
        response = self.responses.pop(0)
        value = response(payload) if callable(response) else response
        return json.dumps(value, ensure_ascii=False)


def _item(
    key: str,
    section: str,
    status: str,
    *,
    label: str = "",
    value: str = "",
    tables: list[str] | None = None,
    columns: list[str] | None = None,
    relationship_ids: list[str] | None = None,
    sql_hint: str = "",
    question: str = "",
    candidates: list[dict[str, Any]] | None = None,
    period: dict[str, Any] | None = None,
    answer_kind: str = "single_select",
) -> dict[str, Any]:
    return {
        "key": key,
        "section": section,
        "label_ja": label or key,
        "value_ja": value,
        "status": status,
        "reason_ja": "",
        "question_ja": question,
        "answer_kind": answer_kind,
        "source": "question",
        "tables": tables or [],
        "columns": columns or [],
        "relationship_ids": relationship_ids or [],
        "sql_hint": sql_hint,
        "period": period,
        "candidates": candidates or [],
    }


def _candidate(
    label: str,
    *,
    evidence: str = "列のコメント",
    tables: list[str] | None = None,
    columns: list[str] | None = None,
    sql_hint: str = "",
    source: str = "schema",
) -> dict[str, Any]:
    return {
        "label_ja": label,
        "description_ja": "",
        "evidence_ja": evidence,
        "source": source,
        "tables": tables or [],
        "columns": columns or [],
        "relationship_ids": [],
        "sql_hint": sql_hint,
    }


def _output(*items: dict[str, Any], clarified: str = "", unanswerable: str = "") -> dict[str, Any]:
    return {
        "clarified_question_ja": clarified,
        "items": list(items),
        "unanswerable_reason_ja": unanswerable,
    }


def _from_orders(status: str = "confirmed") -> dict[str, Any]:
    return _item(
        "from_orders", "from", status, label="使う表", value="APP.ORDERS", tables=["APP.ORDERS"]
    )


def _select_total(status: str = "confirmed") -> dict[str, Any]:
    return _item(
        "select_total",
        "select",
        status,
        label="受注金額の合計",
        value="SUM(ORDERS.AMOUNT)",
        columns=["APP.ORDERS.AMOUNT"],
        sql_hint="SUM(ORDERS.AMOUNT)",
    )


def _where_shipped() -> dict[str, Any]:
    return _item(
        "where_status",
        "where",
        "confirmed",
        label="受注状態",
        value="出荷済み",
        columns=["APP.ORDERS.STATUS"],
        sql_hint="ORDERS.STATUS = 'SHIPPED'",
    )


def _runtime(llm: _ScriptedLlm, *, glossary: dict[str, str] | None = None) -> OntologyApiRuntime:
    legacy = _LegacyService(glossary=glossary)
    legacy._enterprise_ai_client = llm
    runtime = OntologyApiRuntime(legacy_service=legacy, store=InMemoryOntologyStore())
    runtime._requirement_today = lambda: TODAY  # type: ignore[method-assign]
    return runtime


def _create(
    runtime: OntologyApiRuntime, question: str = "出荷済みの受注金額の合計"
) -> QuerySessionData:
    return runtime.create_session(
        QuerySessionApiCreate(
            question=question,
            profile_id="sales",
            clarification_mode=ClarificationMode.GUIDED,
        ),
        actor_user_uuid="user-1",
    )


def _draft(data: QuerySessionData) -> SqlRequirementDraft:
    assert data.session.requirement is not None
    return data.session.requirement


def _items(data: QuerySessionData) -> list[RequirementItem]:
    return _draft(data).items


def _state(data: QuerySessionData) -> RequirementState:
    assert data.requirement_state is not None
    return data.requirement_state


def _answer(
    runtime: OntologyApiRuntime,
    data: QuerySessionData,
    *,
    item_id: str | None = None,
    candidate_index: int | None = 0,
    free_text: str = "",
) -> QuerySessionData:
    state = data.requirement_state
    assert state is not None
    item = (
        next(value for value in _items(data) if value.id == item_id)
        if item_id
        else state.current_item
    )
    assert item is not None
    selected = (
        [item.candidates[candidate_index].id]
        if candidate_index is not None and not free_text and item.candidates
        else []
    )
    return runtime.answer_clarification(
        data.session.id,
        ClarificationAnswerRequest(
            base_version=data.session.current_intent_version,
            item_id=item.id,
            selected_candidate_ids=selected,
            free_text=free_text,
        ),
    )


# --- 1. 表のデータを LLM の入力に入れない ---------------------------------------------


def test_llm_input_uses_schema_without_table_data() -> None:
    llm = _ScriptedLlm(_output(_from_orders(), _select_total(), _where_shipped()))
    runtime = _runtime(llm)

    created = _create(runtime)

    assert len(llm.calls) == 1
    raw = llm.calls[0]["raw_context"]
    assert "SECRET_SAMPLE_VALUE" not in raw
    assert "1001" not in raw
    assert "sample_values" not in raw
    assert "row_count" not in raw
    context = llm.calls[0]["context"]
    tables = {item["table"] for item in context["schema"]}
    # 業務プロファイルの範囲の表だけ（範囲の外の表は名前も渡さない）。
    assert tables == {"APP.ORDERS", "APP.CUSTOMERS"}
    columns = {column["name"] for item in context["schema"] for column in item["columns"]}
    assert "APP.ORDERS.ORDER_DATE" in columns
    orders = next(item for item in context["schema"] if item["table"] == "APP.ORDERS")
    assert orders["primary_key"] == ["ORDERS.ORDER_ID"]
    assert any(
        relation["condition"] == "ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID"
        for relation in context["relationships"]
    )
    assert context["profile"]["glossary"] == {"売上": "受注金額（AMOUNT）の合計"}
    assert context["reference_date"] == "2026-10-11"
    assert created.requirement_state is not None


def test_stale_published_ontology_is_skipped_but_schema_is_still_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import ontology_markdown_workspace, ontology_published_context

    llm = _ScriptedLlm(
        _output(_from_orders(), _select_total(), _where_shipped()),
        _output(_from_orders(), _select_total(), _where_shipped()),
    )
    runtime = _runtime(llm)
    revision_id = runtime._query_ontology("sales").revision.id
    monkeypatch.setattr(
        ontology_markdown_workspace.MarkdownOntologyWorkspace,
        "head",
        lambda self, profile_id: {"snapshot_id": revision_id},
    )
    monkeypatch.setattr(
        ontology_markdown_workspace.MarkdownOntologyWorkspace,
        "snapshot",
        lambda self, profile_id, *args, **kwargs: None,
    )

    def stale(*_args: Any, **_kwargs: Any) -> Any:
        raise OntologyGateBlockedError(
            "MARKDOWN_STALE",
            "Profile または Schema が変更されました。Markdown を再検証してください。",
        )

    monkeypatch.setattr(ontology_published_context, "published_context", stale)
    created = _create(runtime)
    draft = _draft(created)
    # オントロジーは「あれば使う」。公開版が古ければ業務の概念を使わず、Schema で確かめる。
    assert any("オントロジーの業務の概念を使わずに" in warning for warning in draft.warnings_ja)
    assert llm.calls[0]["context"]["ontology"] == []
    assert _state(created).can_apply is True

    # 確認だけの session（review_only）は、これまでどおり止める。
    with pytest.raises(OntologyGateBlockedError):
        runtime.create_session(
            QuerySessionApiCreate(question="受注", profile_id="sales"), actor_user_uuid="user-1"
        )


def test_llm_not_configured_does_not_fall_back_to_rule_draft() -> None:
    legacy = _LegacyService()
    runtime = OntologyApiRuntime(legacy_service=legacy, store=InMemoryOntologyStore())
    with pytest.raises(RequirementAiUnavailableError) as raised:
        _create(runtime)
    assert raised.value.code == "REQUIREMENT_AI_NOT_CONFIGURED"


def test_invalid_llm_output_is_rejected() -> None:
    class _BrokenLlm(_ScriptedLlm):
        def generate(self, **kwargs: Any) -> str:
            return '{"items": "not-a-list"}'

    runtime = _runtime(_BrokenLlm())
    with pytest.raises(RequirementAiUnavailableError) as raised:
        _create(runtime)
    assert raised.value.code == "REQUIREMENT_AI_INVALID_OUTPUT"


# --- 2. 範囲の外・実在しない名前・未承認の結合を拒む ---------------------------------


def test_out_of_scope_and_unknown_names_are_rejected() -> None:
    llm = _ScriptedLlm(
        _output(
            _item(
                "from_tables",
                "from",
                "confirmed",
                tables=["APP.ORDERS", "APP.SECRET_TABLE", "APP.NO_SUCH_TABLE"],
            ),
            _item(
                "select_amount",
                "select",
                "confirmed",
                columns=["APP.ORDERS.AMOUNT", "APP.ORDERS.NO_SUCH_COLUMN"],
                sql_hint="SUM(ORDERS.NO_SUCH_COLUMN)",
            ),
        )
    )
    created = _create(_runtime(llm))
    draft = created.session.requirement
    assert draft is not None
    from_item = next(item for item in draft.items if item.key == "from_tables")
    assert from_item.tables == ["APP.ORDERS"]
    # 実在しない名前があった項目は、推測のままにせず確認に回す。
    assert from_item.status == RequirementStatus.AMBIGUOUS
    assert "APP.SECRET_TABLE" in from_item.reason_ja
    select_item = next(item for item in draft.items if item.key == "select_amount")
    assert select_item.columns == ["APP.ORDERS.AMOUNT"]
    assert select_item.sql_hint == ""
    assert any("APP.NO_SUCH_TABLE" in warning for warning in draft.warnings_ja)


def test_unapproved_join_is_rejected_and_approved_relationship_is_used() -> None:
    llm = _ScriptedLlm(
        _output(
            _item("from_tables", "from", "confirmed", tables=["APP.ORDERS", "APP.CUSTOMERS"]),
            _item("select_region", "select", "confirmed", columns=["APP.CUSTOMERS.REGION"]),
            _item(
                "join_orders_customers",
                "join",
                "confirmed",
                relationship_ids=["made_up_relationship"],
                sql_hint="ORDERS.ORDER_ID = CUSTOMERS.CUSTOMER_ID",
            ),
        )
    )
    created = _create(_runtime(llm), "地域ごとの受注")
    draft = created.session.requirement
    assert draft is not None
    join = next(item for item in draft.items if item.key == "join_orders_customers")
    assert join.relationship_ids == []
    assert join.status == RequirementStatus.AMBIGUOUS
    assert "承認済み" in join.reason_ja


def test_unconnectable_tables_are_unanswerable() -> None:
    llm = _ScriptedLlm(
        _output(
            _item("from_tables", "from", "confirmed", tables=["APP.ORDERS", "APP.CUSTOMERS"]),
            _select_total(),
        )
    )
    legacy = _LegacyService()
    # 外部キーを外す（承認済みの関係が無い）。
    legacy.catalog.tables[0].constraint_details = legacy.catalog.tables[0].constraint_details[:1]
    legacy._enterprise_ai_client = llm
    runtime = OntologyApiRuntime(legacy_service=legacy, store=InMemoryOntologyStore())
    runtime._requirement_today = lambda: TODAY  # type: ignore[method-assign]

    created = _create(runtime)
    state = created.requirement_state
    assert state is not None
    assert state.status == RequirementPhase.UNANSWERABLE
    assert "この業務プロファイルでは答えられません" in state.message_ja
    assert state.can_apply is False


# --- 3. 根拠の無い候補・マスクの値を出さない ------------------------------------------


def test_candidates_without_evidence_or_grounded_value_are_dropped() -> None:
    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _item(
                "where_status",
                "where",
                "ambiguous",
                question="どの受注状態に絞りますか？",
                columns=["APP.ORDERS.STATUS"],
                candidates=[
                    _candidate(
                        "出荷済み（SHIPPED）",
                        columns=["APP.ORDERS.STATUS"],
                        sql_hint="ORDERS.STATUS = 'SHIPPED'",
                    ),
                    _candidate("根拠の無い候補", evidence="", sql_hint="ORDERS.STATUS = 'X'"),
                    _candidate(
                        "データからの推測",
                        columns=["APP.ORDERS.STATUS"],
                        sql_hint="ORDERS.STATUS = 'SECRET_SAMPLE_VALUE'",
                    ),
                    _candidate("範囲の外", tables=["APP.SECRET_TABLE"]),
                ],
            ),
        )
    )
    created = _create(_runtime(llm))
    state = created.requirement_state
    assert state is not None and state.current_item is not None
    assert state.current_item.key == "where_status"
    assert [candidate.label_ja for candidate in state.current_item.candidates] == [
        "出荷済み（SHIPPED）"
    ]


def test_masked_column_values_are_not_offered() -> None:
    profile = _LegacyService().profile
    context = build_requirement_context(
        question="状態で絞る",
        profile=profile,
        catalog_tables=_catalog().tables,
        allowed=AllowedObjects(),
        ontology=None,
        view=None,
        today=TODAY,
    )
    context.tables["APP.ORDERS"].columns["APP.ORDERS.STATUS"].masked = True
    output = LlmRequirementOutput.model_validate(
        _output(
            _from_orders(),
            _select_total(),
            _item(
                "where_status",
                "where",
                "inferred",
                columns=["APP.ORDERS.STATUS"],
                sql_hint="ORDERS.STATUS = 'SHIPPED'",
                candidates=[
                    _candidate(
                        "出荷済み",
                        columns=["APP.ORDERS.STATUS"],
                        sql_hint="ORDERS.STATUS = 'SHIPPED'",
                    )
                ],
            ),
        )
    )
    draft = build_draft(output, context, question="状態で絞る")
    item = next(value for value in draft.items if value.key == "where_status")
    assert item.candidates == []
    assert item.sql_hint == ""
    assert item.status == RequirementStatus.AMBIGUOUS
    assert "マスク" in item.reason_ja


# --- 4. 相対的な期間を具体的な日付の範囲で確かめる ------------------------------------


def _fiscal_period_item(status: str = "confirmed") -> dict[str, Any]:
    return _item(
        "period_this_fiscal_year",
        "period",
        status,
        label="期間",
        value="今年度",
        period={
            "expression_ja": "今年度",
            "relative_kind": "this_fiscal_year",
            "count": 0,
            "fiscal_year_start_month": 0,
            "date_column": "APP.ORDERS.ORDER_DATE",
        },
    )


def test_relative_period_is_always_confirmed_as_concrete_dates() -> None:
    llm = _ScriptedLlm(_output(_from_orders(), _select_total(), _fiscal_period_item()))
    created = _create(_runtime(llm), "今年度の受注金額の合計")
    draft = created.session.requirement
    assert draft is not None
    # 日付の列が 2 つ（ORDER_DATE・SHIP_DATE）あるので、まず列を確かめる。
    column_item = next(item for item in draft.items if item.key == "period_date_column")
    assert column_item.status == RequirementStatus.AMBIGUOUS
    assert column_item.candidates[0].label_ja == "ORDERS.ORDER_DATE（受注日）"
    assert "AI の推測" in column_item.candidates[0].description_ja
    period = next(item for item in draft.items if item.key == "period_this_fiscal_year")
    # LLM が confirmed と返しても、相対的な期間は必ず確かめる。
    assert period.status == RequirementStatus.AMBIGUOUS
    labels = [candidate.label_ja for candidate in period.candidates]
    # 年度の開始月が定義されていないので、4 月始まりと 1 月始まりを具体的な日付で出す。
    assert "2026-04-01〜2027-03-31" in labels
    assert "2026-01-01〜2026-12-31" in labels
    assert "データの最新日を基準にする" in labels
    assert "実行日（2026-10-11）" in period.reason_ja
    assert "開始日と終了日を含みます" in period.reason_ja


def test_fiscal_year_start_from_glossary_is_the_candidate() -> None:
    llm = _ScriptedLlm(_output(_from_orders(), _select_total(), _fiscal_period_item()))
    runtime = _runtime(llm, glossary={"年度": "4月1日から翌年3月31日までの期間"})
    created = _create(runtime, "今年度の受注金額の合計")
    draft = created.session.requirement
    assert draft is not None
    period = next(item for item in draft.items if item.key == "period_this_fiscal_year")
    labels = [candidate.label_ja for candidate in period.candidates]
    assert "2026-01-01〜2026-12-31" not in labels
    assert labels[0] == "2026-04-01〜2027-03-31"
    assert period.candidates[0].source.value == "glossary"
    assert "用語集の定義（4月）" in period.reason_ja


@pytest.mark.parametrize(
    ("kind", "count", "expected"),
    [
        ("last_month", None, [("2026-09-01", "2026-09-30")]),
        ("this_month", None, [("2026-10-01", "2026-10-11"), ("2026-10-01", "2026-10-31")]),
        (
            "last_n_months",
            3,
            [
                ("2026-07-12", "2026-10-11"),
                ("2026-07-01", "2026-09-30"),
                ("2026-08-01", "2026-10-11"),
            ],
        ),
        ("last_fiscal_year", None, [("2025-04-01", "2026-03-31"), ("2025-01-01", "2025-12-31")]),
        (
            "same_period_last_year",
            None,
            [
                ("2025-10-01", "2025-10-31"),
                ("2025-10-01", "2025-10-11"),
                ("2025-01-01", "2025-10-11"),
            ],
        ),
    ],
    ids=["last_month", "this_month", "last_3_months", "last_fiscal_year", "same_period_last_year"],
)
def test_period_ranges_are_computed_from_reference_date(
    kind: str, count: int | None, expected: list[tuple[str, str]]
) -> None:
    ranges = period_ranges(kind, count, TODAY, None)
    assert [(start.isoformat(), end.isoformat()) for _label, start, end in ranges] == expected


# --- 5. 回答・再検査・上限 -------------------------------------------------------------


def test_answer_then_review_adds_question_and_stops_when_clear() -> None:
    def first_review(context: dict[str, Any]) -> dict[str, Any]:
        answered = {item["key"] for item in context["answered_items"]}
        assert "where_status" in answered
        return _output(
            # 答えた項目を変えようとしても保たれる。
            _item("where_status", "where", "ambiguous", question="変えてはいけない"),
            _from_orders(),
            _select_total(),
            _item(
                "other_currency",
                "other",
                "missing",
                question="金額は税込と税抜のどちらですか？",
                answer_kind="free_text",
            ),
            clarified="出荷済みの ORDERS の AMOUNT の合計",
        )

    def second_review(context: dict[str, Any]) -> dict[str, Any]:
        answered = {item["key"] for item in context["answered_items"]}
        assert answered == {"where_status", "other_currency"}
        return _output(
            _from_orders(),
            _select_total(),
            clarified="出荷済み（ORDERS.STATUS = 'SHIPPED'）の ORDERS.AMOUNT（税込）の合計",
        )

    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _item(
                "where_status",
                "where",
                "ambiguous",
                question="どの受注状態に絞りますか？",
                columns=["APP.ORDERS.STATUS"],
                candidates=[
                    _candidate(
                        "出荷済み（SHIPPED）",
                        columns=["APP.ORDERS.STATUS"],
                        sql_hint="ORDERS.STATUS = 'SHIPPED'",
                    )
                ],
            ),
        ),
        first_review,
        second_review,
    )
    runtime = _runtime(llm)
    created = _create(runtime)
    assert created.requirement_state is not None
    assert created.requirement_state.status == RequirementPhase.NEEDS_ANSWER
    assert created.requirement_state.can_apply is False

    after_first = _answer(runtime, created)
    assert len(llm.calls) == 2
    state = after_first.requirement_state
    assert state is not None
    assert state.review_round == 1
    assert state.current_item is not None and state.current_item.key == "other_currency"
    kept = next(item for item in _items(after_first) if item.key == "where_status")
    assert kept.answered and kept.sql_hint == "ORDERS.STATUS = 'SHIPPED'"

    after_second = _answer(runtime, after_first, free_text="税込")
    assert len(llm.calls) == 3
    final = after_second.requirement_state
    assert final is not None
    assert final.status == RequirementPhase.READY
    assert final.review_round == 2
    assert final.can_apply is True
    assert final.unresolved_items == []


def test_review_limit_lists_remaining_items_and_allows_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_requirement_max_review_rounds", 1)
    still_missing = _output(
        _from_orders(),
        _select_total(),
        _where_shipped(),
        _item("other_unit", "other", "missing", question="単位は？", answer_kind="free_text"),
    )
    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _where_shipped(),
            _item("other_currency", "other", "missing", question="税込？", answer_kind="free_text"),
        ),
        still_missing,
    )
    runtime = _runtime(llm)
    created = _create(runtime)
    after = _answer(runtime, created, free_text="税込")
    state = after.requirement_state
    assert state is not None
    assert state.review_limit_reached is True
    assert [item.key for item in state.unresolved_items] == ["other_unit"]
    assert "上限（1 回）" in state.message_ja
    # SELECT と FROM が明確なら、残りを AI に任せて進めるかを利用者が決められる。
    assert state.can_apply is True

    # 上限の後は回答しても LLM を呼ばない。
    final = _answer(runtime, after, free_text="円")
    assert len(llm.calls) == 2
    assert final.requirement_state is not None
    assert final.requirement_state.status == RequirementPhase.READY


def test_select_and_from_must_be_clear_before_apply() -> None:
    llm = _ScriptedLlm(
        _output(
            _item("from_tables", "from", "missing", question="どの表？"),
        )
    )
    runtime = _runtime(llm)
    created = _create(runtime, "いい感じに出して")
    state = created.requirement_state
    assert state is not None
    assert state.select_from_clear is False
    assert len(state.minimum_missing_ja) == 2
    assert state.can_apply is False
    # FROM の候補は範囲の表（物理名）、SELECT の不足もプログラムが質問にする。
    from_item = next(
        item for item in state.unresolved_items if item.section == RequirementSection.FROM
    )
    assert {candidate.label_ja for candidate in from_item.candidates} == {
        "APP.ORDERS（受注）",
        "APP.CUSTOMERS（顧客）",
    }
    assert any(item.key == "select_output" for item in state.unresolved_items)
    with pytest.raises(OntologyGateBlockedError) as raised:
        runtime.apply_requirement(created.session.id, RequirementApplyRequest(base_version=1))
    assert raised.value.code == "GUIDED_CLARIFICATION_INCOMPLETE"


def test_review_limit_still_blocks_apply_without_select() -> None:
    llm = _ScriptedLlm(_output(_from_orders(), _where_shipped()))
    state = _create(_runtime(llm)).requirement_state
    assert state is not None
    assert "何を表示するか（SELECT）が明確ではありません。" in state.minimum_missing_ja
    assert state.can_apply is False


# --- 6. 物理名 -------------------------------------------------------------------------


def test_screen_text_and_summary_use_physical_names() -> None:
    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _where_shipped(),
            _fiscal_period_item(),
            # 業務名だけで書いた質問の文。
            clarified="今年度の出荷済みの受注金額の合計",
        )
    )
    created = _create(_runtime(llm), "今年度の出荷済みの売上")
    draft = created.session.requirement
    assert draft is not None
    select_item = next(item for item in draft.items if item.key == "select_total")
    assert select_item.refs_ja == "ORDERS.AMOUNT（受注金額）"
    from_item = next(item for item in draft.items if item.key == "from_orders")
    assert from_item.refs_ja == "APP.ORDERS（受注）"
    # 書き直した質問の文に物理名を添える。
    assert "ORDERS" in draft.clarified_question_ja
    assert "ORDERS.AMOUNT" in draft.clarified_question_ja
    state = created.requirement_state
    assert state is not None
    assert "APP.ORDERS" in state.summary_ja
    assert "ORDERS.STATUS" in state.summary_ja
    column_item = next(item for item in draft.items if item.key == "period_date_column")
    assert {candidate.label_ja for candidate in column_item.candidates} == {
        "ORDERS.ORDER_DATE（受注日）",
        "ORDERS.SHIP_DATE（出荷日）",
    }


# --- 7. 危険・重い問い合わせの確認 -------------------------------------------------------


def test_detail_without_filter_or_limit_is_confirmed() -> None:
    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _item(
                "select_columns",
                "select",
                "confirmed",
                columns=["APP.ORDERS.ORDER_ID", "APP.ORDERS.AMOUNT"],
                sql_hint="ORDERS.ORDER_ID, ORDERS.AMOUNT",
            ),
        )
    )
    created = _create(_runtime(llm), "受注の一覧")
    state = created.requirement_state
    assert state is not None
    keys = [item.key for item in state.unresolved_items]
    assert "risk_no_filter" in keys
    assert "risk_no_limit" in keys
    no_limit = next(item for item in state.unresolved_items if item.key == "risk_no_limit")
    assert no_limit.candidates[0].label_ja == "100 件まで"
    no_filter = next(item for item in state.unresolved_items if item.key == "risk_no_filter")
    assert "APP.ORDERS" in no_filter.reason_ja
    assert "件数は読まずに" in no_filter.reason_ja


def test_join_without_condition_is_confirmed_as_cartesian_risk() -> None:
    llm = _ScriptedLlm(
        _output(
            _item("from_tables", "from", "confirmed", tables=["APP.ORDERS", "APP.CUSTOMERS"]),
            _item(
                "select_region_total",
                "select",
                "confirmed",
                columns=["APP.CUSTOMERS.REGION", "APP.ORDERS.AMOUNT"],
                sql_hint="CUSTOMERS.REGION, SUM(ORDERS.AMOUNT)",
            ),
            _item("group_region", "group_by", "confirmed", sql_hint="CUSTOMERS.REGION"),
            _item("join_type", "join", "confirmed", label="結合の種類", value="INNER JOIN"),
            _where_shipped(),
        )
    )
    state = _create(_runtime(llm), "地域ごとの受注金額").requirement_state
    assert state is not None
    risk = next(item for item in state.unresolved_items if item.key == "risk_cartesian_join")
    assert "デカルト積" in risk.reason_ja
    assert risk.candidates[0].label_ja == "ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID"


# --- 8. SQL の骨組み -------------------------------------------------------------------


def test_sql_skeleton_is_built_deterministically_from_requirements() -> None:
    llm = _ScriptedLlm(
        _output(
            _item("from_tables", "from", "confirmed", tables=["APP.ORDERS", "APP.CUSTOMERS"]),
            _item(
                "select_region_total",
                "select",
                "confirmed",
                sql_hint="SELECT CUSTOMERS.REGION, SUM(ORDERS.AMOUNT)",
                columns=["APP.CUSTOMERS.REGION", "APP.ORDERS.AMOUNT"],
            ),
            _item("group_region", "group_by", "confirmed", sql_hint="GROUP BY CUSTOMERS.REGION"),
            _item("order_total", "order_by", "inferred", sql_hint="SUM(ORDERS.AMOUNT) DESC"),
            _where_shipped(),
            _fiscal_period_item(),
        ),
        _output(_from_orders(), clarified="今年度の地域ごとの ORDERS.AMOUNT の合計"),
    )
    runtime = _runtime(llm)
    created = _create(runtime, "今年度の地域ごとの売上")
    skeleton = _state(created).sql_skeleton
    assert "FROM APP.ORDERS" in skeleton
    assert "JOIN APP.CUSTOMERS ON ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID" in skeleton
    # 未確定（期間）は目印にする。
    assert "⟨未確定: 期間⟩" in skeleton

    after_column = _answer(runtime, created)  # 日付の列: ORDERS.ORDER_DATE
    after_period = _answer(runtime, after_column)  # 2026-04-01〜2027-03-31（4 月始まり・全体）
    state = after_period.requirement_state
    assert state is not None
    assert state.status == RequirementPhase.READY, state.message_ja
    skeleton = state.sql_skeleton
    assert skeleton.startswith("SELECT CUSTOMERS.REGION, SUM(ORDERS.AMOUNT)")
    assert (
        "ORDERS.ORDER_DATE >= DATE '2026-04-01' AND ORDERS.ORDER_DATE < DATE '2027-04-01'"
        in skeleton
    )
    assert "ORDERS.STATUS = 'SHIPPED'" in skeleton
    assert "GROUP BY CUSTOMERS.REGION" in skeleton
    assert "ORDER BY SUM(ORDERS.AMOUNT) DESC" in skeleton
    assert "⟨未確定" not in skeleton
    # LLM が句のキーワードを付けても、骨組みで二重にしない。
    assert "SELECT SELECT" not in skeleton
    assert "GROUP BY GROUP BY" not in skeleton
    # 相対的な期間は、確かめた具体的な日付の範囲を書き直した質問の文にも書く。
    assert "2026-04-01〜2027-03-31" in _draft(after_period).clarified_question_ja
    # 同じ要件からは同じ骨組み（決定的）。
    again = runtime.get_session(after_period.session.id).requirement_state
    assert again is not None and again.sql_skeleton == skeleton


# --- 9. 反映・SQL の生成に渡す要件 -----------------------------------------------------


def test_apply_closes_session_and_builds_generation_spec() -> None:
    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _where_shipped(),
            clarified="出荷済み（ORDERS.STATUS = 'SHIPPED'）の ORDERS.AMOUNT の合計",
        )
    )
    runtime = _runtime(llm)
    created = _create(runtime)
    state = created.requirement_state
    assert state is not None
    assert state.can_apply, state.message_ja
    applied = runtime.apply_requirement(created.session.id, RequirementApplyRequest(base_version=1))
    assert applied.session.status == QuerySessionStatus.DONE
    assert applied.session.requirement_applied_at is not None
    # 反映した session には答えられない。
    with pytest.raises(OntologyStateConflictError):
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(
                base_version=1,
                item_id=_items(applied)[0].id,
                free_text="x",
            ),
        )
    question = _draft(applied).clarified_question_ja
    spec = runtime.applied_requirement_spec(
        created.session.id,
        actor_user_uuid="user-1",
        actor_is_system_admin=False,
        profile_id="sales",
        question=question,
    )
    assert spec.tables == ["APP.ORDERS"]
    assert any(item.sql_hint == "ORDERS.STATUS = 'SHIPPED'" for item in spec.items)
    block = spec.prompt_block()
    assert "必ず守る条件" in block
    assert "APP.ORDERS" in block
    assert "SUM(ORDERS.AMOUNT)" in block
    for kwargs, code in [
        ({"question": "書き換えた質問"}, "REQUIREMENT_QUESTION_CHANGED"),
        ({"actor_user_uuid": "other-user"}, "REQUIREMENT_SESSION_FORBIDDEN"),
        ({"profile_id": "other"}, "REQUIREMENT_PROFILE_MISMATCH"),
    ]:
        arguments: dict[str, Any] = {
            "actor_user_uuid": "user-1",
            "actor_is_system_admin": False,
            "profile_id": "sales",
            "question": question,
            **kwargs,
        }
        with pytest.raises(OntologyStateConflictError) as raised:
            runtime.applied_requirement_spec(created.session.id, **arguments)
        assert raised.value.code == code


def test_guided_session_does_not_generate_sql_inside_session() -> None:
    llm = _ScriptedLlm(_output(_from_orders(), _select_total(), _where_shipped()))
    runtime = _runtime(llm)
    created = _create(runtime)
    with pytest.raises(OntologyStateConflictError) as raised:
        runtime.generate_sql(
            created.session.id,
            GenerateSqlRequest(
                base_version=1,
                intent_version=1,
                confirm_intent=True,
                ontology_revision_id=created.session.ontology_revision_id,
            ),
        )
    assert raised.value.code == "GUIDED_SESSION_GENERATES_IN_JOB"


# --- 10. 回答の検査・版・二重の送信 -------------------------------------------------------


def _ambiguous_status_output() -> dict[str, Any]:
    return _output(
        _from_orders(),
        _select_total(),
        _item(
            "where_status",
            "where",
            "ambiguous",
            question="どの受注状態に絞りますか？",
            answer_kind="multi_select",
            candidates=[
                _candidate(
                    "出荷済み", columns=["APP.ORDERS.STATUS"], sql_hint="ORDERS.STATUS = 'SHIPPED'"
                ),
                _candidate(
                    "取消", columns=["APP.ORDERS.STATUS"], sql_hint="ORDERS.STATUS = 'CANCELLED'"
                ),
            ],
        ),
    )


def test_answer_validation_rejects_unknown_and_mixed_inputs() -> None:
    runtime = _runtime(_ScriptedLlm(_ambiguous_status_output()))
    created = _create(runtime)
    item = _state(created).current_item
    assert item is not None
    cases: list[tuple[dict[str, Any], str]] = [
        ({"selected_candidate_ids": ["unknown"]}, "表示されていない選択肢"),
        ({"selected_candidate_ids": [item.candidates[0].id], "free_text": "x"}, "どちらか 1 つ"),
        ({}, "選択肢を選ぶか"),
    ]
    for request, message in cases:
        with pytest.raises(OntologyIntegrityError) as raised:
            runtime.answer_clarification(
                created.session.id,
                ClarificationAnswerRequest(base_version=1, item_id=item.id, **request),
            )
        assert message in raised.value.message_ja
    with pytest.raises(OntologyVersionConflictError):
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(base_version=9, item_id=item.id, free_text="x"),
        )


def test_multi_select_answer_keeps_all_selected_conditions() -> None:
    llm = _ScriptedLlm(_ambiguous_status_output(), _output(_from_orders(), _select_total()))
    runtime = _runtime(llm)
    created = _create(runtime)
    item = _state(created).current_item
    assert item is not None
    updated = runtime.answer_clarification(
        created.session.id,
        ClarificationAnswerRequest(
            base_version=1,
            item_id=item.id,
            selected_candidate_ids=[candidate.id for candidate in item.candidates],
        ),
    )
    answered = next(value for value in _items(updated) if value.key == "where_status")
    assert answered.value_ja == "出荷済み、取消"
    assert answered.status == RequirementStatus.CONFIRMED
    assert updated.session.current_intent_version == 2


def test_correcting_inferred_item_from_summary_replaces_only_that_item() -> None:
    llm = _ScriptedLlm(
        _output(_from_orders(), _select_total("inferred"), _where_shipped()),
        _output(_from_orders(), _where_shipped()),
    )
    runtime = _runtime(llm)
    created = _create(runtime)
    select_item = next(item for item in _items(created) if item.key == "select_total")
    corrected = _answer(
        runtime, created, item_id=select_item.id, free_text="ORDERS.AMOUNT の平均を表示する"
    )
    items = {item.key: item for item in _items(corrected)}
    assert items["select_total"].answered is True
    assert items["select_total"].value_ja == "ORDERS.AMOUNT の平均を表示する"
    assert items["select_total"].columns == ["APP.ORDERS.AMOUNT"]
    assert items["where_status"].sql_hint == "ORDERS.STATUS = 'SHIPPED'"


class _OracleSizedIdempotencyStore(InMemoryOntologyStore):
    """Oracle schema の IDEMPOTENCY_KEY VARCHAR2(160) 制約を再現する。"""

    def save_document(
        self,
        collection: OntologyCollection,
        document: Mapping[str, Any] | Any,
        *,
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        if collection == "idempotency":
            storage_key = str(document["idempotency_key"])
            if len(storage_key.encode("utf-8")) > IDEMPOTENCY_KEY_STORAGE_MAX_BYTES:
                raise RuntimeError("ORA-12899: value too large for IDEMPOTENCY_KEY")
        return super().save_document(collection, document, expected_etag=expected_etag)


async def test_answer_and_apply_are_idempotent_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _ScriptedLlm(_ambiguous_status_output(), _output(_from_orders(), _select_total()))
    legacy = _LegacyService()
    legacy._enterprise_ai_client = llm
    runtime = OntologyApiRuntime(legacy_service=legacy, store=_OracleSizedIdempotencyStore())
    runtime._requirement_today = lambda: TODAY  # type: ignore[method-assign]
    created = _create(runtime)
    item = _state(created).current_item
    assert item is not None
    monkeypatch.setattr(ontology_router_module, "ontology_runtime", runtime)
    transport = httpx.ASGITransport(app=app)
    key = (
        f"nl2sql:/api/nl2sql/query-sessions/{created.session.id}/clarification-answers:"
        "base_version.free_text.item_id.selected_candidate_ids:"
        "00000000-0000-0000-0000-000000000000"
    )
    payload = {
        "base_version": 1,
        "item_id": item.id,
        "selected_candidate_ids": [item.candidates[0].id],
        "free_text": "",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        path = f"/api/nl2sql/query-sessions/{created.session.id}"
        response = await client.post(
            f"{path}/clarification-answers", headers={"Idempotency-Key": key}, json=payload
        )
        replay = await client.post(
            f"{path}/clarification-answers", headers={"Idempotency-Key": key}, json=payload
        )
        assert response.status_code == 200, response.text
        assert replay.status_code == 200
        data = response.json()["data"]
        assert data["requirement_state"]["status"] == "ready"
        assert data["requirement_state"]["can_apply"] is True
        applied = await client.post(
            f"{path}/apply",
            headers={"Idempotency-Key": "apply-1"},
            json={"base_version": data["session"]["current_intent_version"]},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["data"]["session"]["status"] == "done"
    # 回答の後の再検査を含めて LLM は 2 回だけ（再送では呼ばない）。
    assert len(llm.calls) == 2


# --- 11. 期限切れ・中止 ---------------------------------------------------------------


def _expire_after_ttl(created: QuerySessionData) -> Any:
    ttl = get_settings().nl2sql_query_session_idle_ttl_seconds
    return created.session.updated_at + timedelta(seconds=ttl + 1)


def test_idle_guided_session_expires_and_rejects_answers_without_llm() -> None:
    from app.features.nl2sql.ontology_service import QUERY_SESSION_EXPIRED_CODE

    llm = _ScriptedLlm(_ambiguous_status_output())
    runtime = _runtime(llm)
    created = _create(runtime)
    item = _state(created).current_item
    assert item is not None
    assert runtime.expire_idle_sessions(now=_expire_after_ttl(created)) == 1
    with pytest.raises(OntologyStateConflictError) as raised:
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(base_version=1, item_id=item.id, free_text="今月"),
        )
    assert raised.value.code == QUERY_SESSION_EXPIRED_CODE
    assert len(llm.calls) == 1
    assert runtime.cancel_session(created.session.id).session.status == QuerySessionStatus.CANCELLED


@pytest.mark.parametrize(
    "status",
    [QuerySessionStatus.EXECUTING, QuerySessionStatus.DONE, QuerySessionStatus.ERROR],
    ids=["executing", "done", "error"],
)
def test_session_expiry_skips_executing_and_finished_sessions(status: QuerySessionStatus) -> None:
    runtime = _runtime(_ScriptedLlm(_ambiguous_status_output()))
    created = _create(runtime)
    session = created.session.model_copy(deep=True, update={"status": status})
    runtime.sessions.replace_session(session)
    runtime._persist_session(session)
    assert runtime.expire_idle_sessions(now=_expire_after_ttl(created)) == 0
    assert runtime.get_session(created.session.id).session.status == status


def test_cancelled_guided_session_is_terminal() -> None:
    runtime = _runtime(_ScriptedLlm(_ambiguous_status_output()))
    created = _create(runtime)
    cancelled = runtime.cancel_session(created.session.id)
    assert cancelled.session.status == QuerySessionStatus.CANCELLED
    with pytest.raises(OntologyStateConflictError):
        runtime.apply_requirement(created.session.id, RequirementApplyRequest(base_version=1))


# --- 12. SQL の生成の job に渡す -------------------------------------------------------


def test_job_request_takes_requirement_only_from_applied_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.features.nl2sql import router as nl2sql_router
    from app.features.nl2sql.models import JobCreateRequest, SqlRequirementSpec

    llm = _ScriptedLlm(
        _output(
            _from_orders(),
            _select_total(),
            _where_shipped(),
            clarified="出荷済みの ORDERS.AMOUNT の合計",
        )
    )
    runtime = _runtime(llm)
    created = _create(runtime)
    applied = runtime.apply_requirement(created.session.id, RequirementApplyRequest(base_version=1))
    assert applied.session.requirement is not None
    question = _draft(applied).clarified_question_ja
    monkeypatch.setattr(ontology_router_module, "ontology_runtime", runtime)
    principal = SimpleNamespace(user_uuid="user-1", is_system_admin=False)
    forged = SqlRequirementSpec(
        session_id="forged", profile_id="sales", clarified_question="x", tables=["APP.SECRET"]
    )

    # session の無い要件の本文（画面が送ったもの）は使わない。
    plain = nl2sql_router._with_requirement(
        JobCreateRequest(question=question, profile_id="sales", sql_requirement=forged), principal
    )
    assert plain.sql_requirement is None

    request = JobCreateRequest(
        question=question,
        profile_id="sales",
        requirement_session_id=created.session.id,
        sql_requirement=forged,
    )
    resolved = nl2sql_router._with_requirement(request, principal)
    assert resolved.sql_requirement is not None
    assert resolved.sql_requirement.session_id == created.session.id
    assert resolved.sql_requirement.tables == ["APP.ORDERS"]
    assert resolved.allowed_objects.table_names == ["APP.ORDERS"]

    changed = request.model_copy(update={"question": "書き換えた質問"})
    with pytest.raises(HTTPException) as raised:
        nl2sql_router._with_requirement(changed, principal)
    assert raised.value.status_code == 409
