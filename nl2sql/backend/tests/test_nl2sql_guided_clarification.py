"""AI 要件確認の質問計画、回答、scope 境界の回帰テスト。"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_clarification import build_clarification_state
from app.features.nl2sql.ontology_models import (
    ClarificationAnswer,
    ClarificationMode,
    ClarificationStatus,
    ClarificationTurn,
    ColumnQueryPolicy,
    OntologyNodeKind,
    QuerySession,
    QuerySessionStatus,
)
from app.features.nl2sql.ontology_router import (
    ClarificationAnswerRequest,
    GenerateSqlRequest,
    OntologyApiRuntime,
    QuerySessionApiCreate,
    QuerySessionData,
)
from app.features.nl2sql.ontology_service import (
    OntologyGateBlockedError,
    OntologyIntegrityError,
    OntologyStateConflictError,
    OntologyVersionConflictError,
)
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _disable_profile_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", False)


class _GuidedLegacyService:
    uses_incremental_store = False

    def __init__(self) -> None:
        self.profile = Nl2SqlProfile(
            id="sales",
            name="販売分析",
            description="受注件数と金額を確認する Profile",
            allowed_tables=["APP.ORDERS"],
            glossary={"売上": "受注金額"},
            sql_rules=["取消済みを除外する"],
            few_shot_examples=[{"question": "先月の受注件数", "sql": "SELECT COUNT(*)"}],
            default_row_limit=100,
        )
        self.catalog = SchemaCatalog(
            refreshed_at="2026-09-06T00:00:00Z",
            schema_fingerprint="guided-schema",
            tables=[
                SchemaTable(
                    table_name="ORDERS",
                    owner="APP",
                    table_type="table",
                    logical_name="受注",
                    comment="受注データ",
                    columns=[
                        SchemaColumn(
                            column_name="STATUS",
                            logical_name="受注状態",
                            data_type="VARCHAR2",
                            comment="受注の状態",
                            sample_values=["CONFIRMED", "SECRET_VALUE"],
                        )
                    ],
                )
            ],
        )

    def get_catalog(self) -> SchemaCatalog:
        return self.catalog.model_copy(deep=True)

    def get_profile(self, profile_id: str, **_kwargs: Any) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("profile not found")
        return self.profile.model_copy(deep=True)

    def resolve_allowed_objects(
        self,
        profile_id: str,
        requested: AllowedObjects,
    ) -> AllowedObjects:
        self.get_profile(profile_id)
        return (
            requested.model_copy(deep=True)
            if requested.table_names
            else AllowedObjects(
                table_names=["APP.ORDERS"],
                enforce_table_scope=True,
            )
        )


def _runtime() -> OntologyApiRuntime:
    return OntologyApiRuntime(
        legacy_service=_GuidedLegacyService(),
        store=InMemoryOntologyStore(),
    )


def _create_guided(runtime: OntologyApiRuntime) -> QuerySessionData:
    return runtime.create_session(
        QuerySessionApiCreate(
            question="受注件数を表示",
            profile_id="sales",
            clarification_mode=ClarificationMode.GUIDED,
        ),
        actor_user_uuid="user-1",
    )


def test_guided_session_asks_for_missing_time_and_accepts_scoped_option() -> None:
    runtime = _runtime()

    created = _create_guided(runtime)

    assert created.clarification is not None
    assert created.clarification.status == ClarificationStatus.NEEDS_ANSWER
    assert created.clarification.current_question is not None
    assert created.clarification.current_question.category.value == "time_range"
    assert created.clarification.can_generate_sql is False
    option = created.clarification.current_question.options[0]

    updated = runtime.answer_clarification(
        created.session.id,
        ClarificationAnswerRequest(
            base_version=1,
            question_id=created.clarification.current_question.id,
            selected_option_ids=[option.id],
        ),
    )

    assert updated.session.current_intent_version == 2
    assert len(updated.session.clarification_turns) == 1
    assert updated.session.intents[-1].time_range is not None
    assert updated.session.intents[-1].time_range.relative_expression == "今月"
    assert updated.clarification is not None
    assert updated.clarification.status == ClarificationStatus.READY_TO_CONFIRM
    assert updated.clarification.can_generate_sql is True


def test_guided_answer_rejects_option_not_returned_by_current_question() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    assert created.clarification is not None
    assert created.clarification.current_question is not None

    with pytest.raises(OntologyIntegrityError, match="表示されていない選択肢"):
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(
                base_version=1,
                question_id=created.clarification.current_question.id,
                selected_option_ids=["outside-option"],
            ),
        )


def test_guided_answer_is_idempotent_and_rejects_stale_version() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    assert created.clarification is not None
    current = created.clarification.current_question
    assert current is not None
    request = ClarificationAnswerRequest(
        base_version=1,
        question_id=current.id,
        selected_option_ids=[current.options[0].id],
    )

    first = runtime.answer_clarification_idempotent(
        created.session.id,
        request,
        idempotency_key="answer-1",
    )
    replay = runtime.answer_clarification_idempotent(
        created.session.id,
        request,
        idempotency_key="answer-1",
    )

    assert first.session.current_intent_version == 2
    assert replay.session.current_intent_version == 2
    with pytest.raises(OntologyVersionConflictError):
        runtime.answer_clarification_idempotent(
            created.session.id,
            request,
            idempotency_key="answer-stale",
        )


def test_guided_generate_sql_is_blocked_before_required_answers() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)

    with pytest.raises(OntologyGateBlockedError, match="必要な条件"):
        runtime.generate_sql(
            created.session.id,
            GenerateSqlRequest(
                base_version=1,
                intent_version=1,
                ontology_revision_id=created.session.ontology_revision_id,
                confirm_intent=True,
            ),
        )


def test_guided_schema_context_omits_masked_sample_values() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    view = created.profile_ontology_view.model_copy(deep=True)
    status_node = next(
        node
        for node in ontology.nodes
        if node.kind == OntologyNodeKind.COLUMN and node.technical_name.endswith(".STATUS")
    )
    view.column_policies[status_node.id] = ColumnQueryPolicy(masked=True)

    context = runtime._guided_schema_context(runtime._strict_profile("sales"), ontology, view)

    status_column = context[0]["columns"][0]
    assert status_column["name"] == "STATUS"
    assert status_column["sample_values"] == []
    assert "SECRET_VALUE" not in str(context)


def test_guided_schema_context_prefers_question_relevant_profile_objects() -> None:
    runtime = _runtime()
    runtime.legacy_service.profile.allowed_tables.append("APP.CUSTOMERS")
    runtime.legacy_service.catalog.tables.append(
        SchemaTable(
            table_name="CUSTOMERS",
            owner="APP",
            table_type="table",
            logical_name="顧客",
            comment="顧客データ",
            columns=[
                SchemaColumn(
                    column_name="CUSTOMER_ID",
                    logical_name="顧客 ID",
                    data_type="NUMBER",
                )
            ],
        )
    )
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)

    context = runtime._guided_schema_context(
        runtime._strict_profile("sales"),
        ontology,
        created.profile_ontology_view,
        question="受注件数を表示",
    )

    assert [item["object_name"] for item in context] == ["ORDERS"]
    assert "CUSTOMERS" not in str(context)


def test_cancelled_guided_session_is_terminal() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)

    cancelled = runtime.cancel_session(created.session.id)

    assert cancelled.session.status == QuerySessionStatus.CANCELLED
    assert cancelled.session.cancelled_at is not None
    with pytest.raises(OntologyStateConflictError):
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(
                base_version=1,
                question_id="obsolete",
                free_text="今月",
            ),
        )


def test_guided_answer_rejects_multiple_input_kinds() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    assert created.clarification is not None
    question = created.clarification.current_question
    assert question is not None

    with pytest.raises(OntologyIntegrityError, match="いずれか 1 つ"):
        runtime.answer_clarification(
            created.session.id,
            ClarificationAnswerRequest(
                base_version=1,
                question_id=question.id,
                selected_option_ids=[question.options[0].id],
                free_text="今月",
            ),
        )


def test_after_four_turns_returns_all_remaining_questions_for_manual_completion() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    assert created.clarification is not None
    question = created.clarification.current_question
    assert question is not None
    answer = ClarificationAnswer(
        question_id=question.id,
        selected_option_ids=[question.options[0].id],
    )
    session = created.session.model_copy(
        deep=True,
        update={
            "clarification_turns": [
                ClarificationTurn(question=question, answer=answer, intent_version=index + 2)
                for index in range(4)
            ]
        },
    )

    state = build_clarification_state(
        session,
        runtime.ontology_revision(session.ontology_revision_id),
        created.profile_ontology_view,
    )

    assert state.manual_completion_required is True
    assert state.current_question is not None
    assert state.remaining_questions == [state.current_question]
    assert state.can_generate_sql is False


def test_legacy_query_session_payload_uses_review_only_defaults() -> None:
    runtime = _runtime()
    payload = _create_guided(runtime).session.model_dump(mode="json")
    payload.pop("clarification_mode")
    payload.pop("clarification_turns")
    payload.pop("cancelled_at")

    restored = QuerySession.model_validate(payload)

    assert restored.clarification_mode == ClarificationMode.REVIEW_ONLY
    assert restored.clarification_turns == []
    assert restored.cancelled_at is None
