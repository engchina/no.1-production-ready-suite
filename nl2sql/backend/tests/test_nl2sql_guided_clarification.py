"""AI 要件確認の質問計画、回答、scope 境界の回帰テスト。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
import pytest

import app.features.nl2sql.ontology_router as ontology_router_module
from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_clarification import (
    apply_clarification_answer,
    build_clarification_state,
    enrich_guided_intent,
)
from app.features.nl2sql.ontology_models import (
    ClarificationAnswer,
    ClarificationAnswerKind,
    ClarificationCategory,
    ClarificationMode,
    ClarificationOption,
    ClarificationQuestion,
    ClarificationStatus,
    ClarificationTurn,
    ColumnQueryPolicy,
    IntentAmbiguity,
    IntentEntity,
    OntologyNodeKind,
    PhysicalMapping,
    PhysicalObjectRef,
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
from app.features.nl2sql.ontology_store import (
    IDEMPOTENCY_KEY_STORAGE_MAX_BYTES,
    InMemoryOntologyStore,
    OntologyCollection,
)
from app.main import app
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
                        ),
                        SchemaColumn(
                            column_name="ORDER_ID",
                            logical_name="受注ID",
                            data_type="NUMBER",
                            comment="受注を識別する主キー",
                            sample_values=["1001", "1002"],
                        ),
                    ],
                )
            ],
        )
        self._enterprise_ai_client: Any = None

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


def _runtime() -> OntologyApiRuntime:
    return OntologyApiRuntime(
        legacy_service=_GuidedLegacyService(),
        store=InMemoryOntologyStore(),
    )


class _IntentEnterpriseAiClient:
    def __init__(self, response_factory: Callable[[dict[str, Any]], str | Exception]) -> None:
        self.response_factory = response_factory
        self.calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return True

    def model_id(self) -> str:
        return "fake-enterprise-ai-intent"

    def generate(
        self,
        *,
        prompt: str,
        context: str,
        system_prompt: str,
        response_format: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "context": context,
                "system_prompt": system_prompt,
                "response_format": response_format,
                **kwargs,
            }
        )
        response = self.response_factory(json.loads(context))
        if isinstance(response, Exception):
            raise response
        return response


def _create_guided(runtime: OntologyApiRuntime) -> QuerySessionData:
    return runtime.create_session(
        QuerySessionApiCreate(
            question="受注件数を表示",
            profile_id="sales",
            clarification_mode=ClarificationMode.GUIDED,
        ),
        actor_user_uuid="user-1",
    )


def _prepare_guided_output_question(
    runtime: OntologyApiRuntime,
) -> tuple[QuerySessionData, QuerySession, ClarificationQuestion]:
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    columns = [
        node
        for node in ontology.nodes
        if node.kind == OntologyNodeKind.COLUMN
        and node.technical_name in {"APP.ORDERS.STATUS", "APP.ORDERS.ORDER_ID"}
    ]
    intent = created.session.intents[-1].model_copy(deep=True)
    intent.metrics = []
    intent.dimensions = []
    intent.ambiguities = [
        IntentAmbiguity(
            id="embedding-columns-runtime",
            code="ontology_embedding_ambiguous",
            message_ja="Embedding 検索だけでは業務要素を一意に確定できません。",
            options=[node.technical_name for node in columns],
            blocking=True,
        )
    ]
    session = created.session.model_copy(
        deep=True,
        update={"intents": [intent], "clarification_turns": []},
    )
    runtime.sessions.replace_session(session)
    runtime._persist_session(session)
    state = build_clarification_state(session, ontology, created.profile_ontology_view)
    question = state.current_question
    assert question is not None
    return created, session, question


def _frontend_clarification_idempotency_key(session_id: str) -> str:
    return (
        f"nl2sql:/api/nl2sql/query-sessions/{session_id}/clarification-answers:"
        "base_version.free_text.question_id.selected_option_ids:"
        "00000000-0000-0000-0000-000000000000"
    )


def _legacy_ambiguity_intent_response(context: dict[str, Any]) -> str:
    draft = dict(context["deterministic_draft"])
    draft["confidence"] = 0.91
    draft["ambiguities"] = [
        {
            "id": "ai-output-ambiguity",
            "kind": "extract_items",
            "message_ja": "表示する指標を確認してください。",
            "options": [
                {"label_ja": "受注件数", "value": "order_count"},
                {"label_ja": "受注金額", "value": "order_amount"},
            ],
            "blocking": True,
        }
    ]
    return json.dumps(draft, ensure_ascii=False)


def _invalid_secret_intent_response(context: dict[str, Any]) -> str:
    draft = dict(context["deterministic_draft"])
    draft["confidence"] = 0.92
    draft["ambiguities"] = [
        {
            "id": "secret-ambiguity",
            "kind": "unknown_secret_shape",
            "message_ja": "SECRET_FREE_TEXT を含む確認です。",
            "options": [{"label_ja": "SECRET_SAMPLE_VALUE", "value": "secret"}],
            "blocking": True,
        }
    ]
    return json.dumps(draft, ensure_ascii=False)


def _outside_scope_node_response(context: dict[str, Any]) -> str:
    draft = dict(context["deterministic_draft"])
    draft["confidence"] = 0.93
    draft["entities"] = [
        {
            "id": "outside-entity",
            "ontology_node_id": "node-outside-profile-view",
            "name_ja": "SECRET_SCOPE_ENTITY",
            "role": "subject",
            "physical_object_ids": [],
        }
    ]
    return json.dumps(draft, ensure_ascii=False)


def test_guided_enterprise_ai_intent_uses_schema_and_accepts_legacy_ambiguity_shape() -> None:
    runtime = _runtime()
    fake_client = _IntentEnterpriseAiClient(_legacy_ambiguity_intent_response)
    runtime.legacy_service._enterprise_ai_client = fake_client  # noqa: SLF001

    created = _create_guided(runtime)

    call = fake_client.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["name"] == "question_intent_graph_v1"
    assert call["response_format"]["strict"] is True
    contract = json.loads(call["context"])["intent_contract"]
    assert contract["schema_version"] == "question_intent_graph_v1"
    assert contract["valid_minimum_example"]["profile_view_id"] == created.session.profile_view_id
    assert "kind field は使わず" in call["system_prompt"]
    intent = created.session.intents[-1]
    assert intent.confidence == 0.91
    ambiguity = next(item for item in intent.ambiguities if item.id == "ai-output-ambiguity")
    assert ambiguity.code == "OUTPUT_UNCLEAR"
    assert ambiguity.options == ["受注件数", "受注金額"]


def test_guided_enterprise_ai_intent_schema_failure_log_is_sanitized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime()
    runtime.legacy_service._enterprise_ai_client = _IntentEnterpriseAiClient(  # noqa: SLF001
        _invalid_secret_intent_response
    )

    with caplog.at_level(logging.WARNING, logger="app.features.nl2sql.ontology_router"):
        created = _create_guided(runtime)

    assert all(item.id != "secret-ambiguity" for item in created.session.intents[-1].ambiguities)
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "ontology_intent_enterprise_ai_fallback"
    ]
    assert len(records) == 1
    record = cast(Any, records[0])
    assert record.exc_info is None
    assert record.fallback_reason == "schema_validation"
    assert record.schema_version == "question_intent_graph_v1"
    assert record.prompt_version == "question_intent_interpreter_v2"
    assert record.field_error_count >= 1
    assert "SECRET" not in caplog.text
    assert "input_value" not in caplog.text


def test_guided_enterprise_ai_intent_outside_scope_falls_back_without_logging_candidates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = _runtime()
    runtime.legacy_service._enterprise_ai_client = _IntentEnterpriseAiClient(  # noqa: SLF001
        _outside_scope_node_response
    )

    with caplog.at_level(logging.WARNING, logger="app.features.nl2sql.ontology_router"):
        created = _create_guided(runtime)

    assert all(
        item.ontology_node_id != "node-outside-profile-view"
        for item in created.session.intents[-1].entities
    )
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "ontology_intent_enterprise_ai_fallback"
    ]
    assert len(records) == 1
    record = cast(Any, records[0])
    assert record.fallback_reason == "scope_violation"
    assert record.scope_violation_kind == "node"
    assert record.scope_violation_count == 1
    assert "SECRET_SCOPE_ENTITY" not in caplog.text


def test_guided_session_asks_for_missing_time_and_accepts_scoped_option() -> None:
    runtime = _runtime()

    created = _create_guided(runtime)

    assert created.clarification is not None
    assert created.clarification.status == ClarificationStatus.NEEDS_ANSWER
    assert created.clarification.current_question is not None
    assert created.clarification.current_question.category.value == "time_range"
    assert created.clarification.can_generate_sql is False
    assert all(item.key != "limit" for item in created.clarification.intent_summary)
    assert created.clarification.assumptions == []
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
    assert updated.session.clarification_turns[0].prompt_version == "deterministic_first_v2"
    assert updated.session.intents[-1].time_range is not None
    assert updated.session.intents[-1].time_range.relative_expression == "今月"
    assert updated.clarification is not None
    assert updated.clarification.status == ClarificationStatus.READY_TO_CONFIRM
    assert updated.clarification.can_generate_sql is True
    assert all(item.key != "limit" for item in updated.clarification.intent_summary)
    assert updated.clarification.assumptions == []


def test_inferred_summary_requires_an_explicit_answer_before_ready() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    intent = created.session.intents[-1].model_copy(
        deep=True,
        update={
            "question_original": "一覧を表示",
            "question_effective": "一覧を表示",
            "metrics": [],
            "ambiguities": [],
        },
    )
    session = created.session.model_copy(
        deep=True,
        update={"intents": [intent], "clarification_turns": []},
    )

    pending = build_clarification_state(session, ontology, created.profile_ontology_view)

    assert pending.status == ClarificationStatus.NEEDS_ANSWER
    assert pending.can_generate_sql is False
    assert pending.current_question is not None
    assert pending.current_question.summary_key == "entities"
    assert pending.current_question.prompt_ja == "検索対象は「受注」で合っていますか？"
    assert pending.current_question.options[0].label_ja == "はい、この内容で進める"
    assert pending.current_question.options[0].evidence_ja == ""
    summary = next(item for item in pending.intent_summary if item.key == "entities")
    assert summary.confirmed is False
    assert summary.technical_evidence_ja == ""

    answer = ClarificationAnswer(
        question_id=pending.current_question.id,
        selected_option_ids=[pending.current_question.options[0].id],
    )
    confirmed_session = session.model_copy(
        deep=True,
        update={
            "clarification_turns": [
                ClarificationTurn(
                    question=pending.current_question,
                    answer=answer,
                    intent_version=2,
                )
            ]
        },
    )
    confirmed = build_clarification_state(
        confirmed_session,
        ontology,
        created.profile_ontology_view,
    )

    assert confirmed.status == ClarificationStatus.READY_TO_CONFIRM
    assert confirmed.can_generate_sql is True
    assert confirmed.current_question is None
    assert confirmed.required_total == 1
    assert confirmed.required_confirmed == 1
    confirmed_summary = next(item for item in confirmed.intent_summary if item.key == "entities")
    assert confirmed_summary.confirmed is True
    assert confirmed_summary.source == "user"


def test_guided_intent_prefers_business_entity_over_duplicate_physical_entity() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    table_node = next(node for node in ontology.nodes if node.kind == OntologyNodeKind.TABLE)
    business_node = table_node.model_copy(
        deep=True,
        update={
            "id": "business_entity_internal_hash",
            "kind": OntologyNodeKind.BUSINESS_ENTITY,
            "technical_name": "business_entity_internal_hash",
            "business_name_ja": "受注",
            "physical_mappings": [
                PhysicalMapping(
                    object_ref=PhysicalObjectRef(
                        node_id=table_node.id,
                        owner="APP",
                        object_name="ORDERS",
                    )
                )
            ],
        },
    )
    related_business_node = business_node.model_copy(
        deep=True,
        update={
            "id": "business_event_internal_hash",
            "kind": OntologyNodeKind.BUSINESS_EVENT,
            "technical_name": "business_event_internal_hash",
            "business_name_ja": "受注処理",
        },
    )
    ontology.nodes.extend([business_node, related_business_node])
    intent = created.session.intents[-1].model_copy(
        deep=True,
        update={
            "entities": [
                IntentEntity(
                    id="physical-intent",
                    ontology_node_id=table_node.id,
                    name_ja="受注情報",
                    physical_object_ids=[table_node.id],
                ),
                IntentEntity(
                    id="business-intent",
                    ontology_node_id=business_node.id,
                    name_ja="受注",
                    physical_object_ids=[table_node.id],
                ),
                IntentEntity(
                    id="related-business-intent",
                    ontology_node_id=related_business_node.id,
                    name_ja="受注処理",
                    physical_object_ids=[table_node.id],
                ),
            ]
        },
    )

    normalized = enrich_guided_intent(intent, ontology)

    assert [(item.ontology_node_id, item.name_ja) for item in normalized.entities] == [
        (business_node.id, "受注"),
        (related_business_node.id, "受注処理"),
    ]


def test_guided_session_keeps_user_specified_limit_as_user_evidence() -> None:
    runtime = _runtime()

    created = runtime.create_session(
        QuerySessionApiCreate(
            question="受注を上位 10 件表示",
            profile_id="sales",
            clarification_mode=ClarificationMode.GUIDED,
        ),
        actor_user_uuid="user-1",
    )

    assert created.clarification is not None
    limit_item = next(item for item in created.clarification.intent_summary if item.key == "limit")
    assert created.session.intents[-1].limit == 10
    assert limit_item.value_ja == "10 件"
    assert limit_item.source == "user"
    assert limit_item.confirmed is True
    assert created.clarification.assumptions == []


def test_embedding_column_ambiguity_is_presented_as_business_output_selection() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    columns = [
        node
        for node in ontology.nodes
        if node.kind == OntologyNodeKind.COLUMN
        and node.technical_name in {"APP.ORDERS.STATUS", "APP.ORDERS.ORDER_ID"}
    ]
    assert len(columns) == 2
    intent = created.session.intents[-1].model_copy(deep=True)
    intent.metrics = []
    intent.dimensions = []
    intent.ambiguities = [
        IntentAmbiguity(
            id="embedding-columns",
            code="ontology_embedding_ambiguous",
            message_ja="Embedding 検索だけでは業務要素を一意に確定できません。",
            options=[node.technical_name for node in columns],
            blocking=True,
        )
    ]
    session = created.session.model_copy(
        deep=True,
        update={"intents": [intent], "clarification_turns": []},
    )
    state = build_clarification_state(session, ontology, created.profile_ontology_view)

    question = state.current_question
    assert question is not None
    assert question.category == ClarificationCategory.OUTPUT
    assert question.answer_kind == ClarificationAnswerKind.MULTI_SELECT
    assert question.prompt_ja == "検索結果に表示する項目を選んでください。"
    assert "必要な項目をすべて選んでください" in question.reason_ja
    assert state.missing_required == [question.prompt_ja]
    assert "Embedding" not in f"{question.prompt_ja}{question.reason_ja}{state.missing_required}"
    assert {option.label_ja for option in question.options} == {"受注状態", "受注ID"}
    assert all("検索結果に" in option.description_ja for option in question.options)
    assert all("主キー" not in option.description_ja for option in question.options)
    assert {option.evidence_ja for option in question.options} == {
        "APP.ORDERS.STATUS",
        "APP.ORDERS.ORDER_ID",
    }

    option_id_by_label = {option.label_ja: option.id for option in question.options}
    answer = ClarificationAnswer(
        question_id=question.id,
        selected_option_ids=[
            option_id_by_label["受注状態"],
            option_id_by_label["受注ID"],
        ],
    )
    updated_intent = apply_clarification_answer(intent, question, answer, ontology)

    assert {item.name_ja for item in updated_intent.dimensions} == {"受注状態", "受注ID"}
    assert updated_intent.question_effective == (
        "受注件数を表示してください。検索結果には受注状態、受注IDを表示してください。"
    )
    assert "確認事項" not in updated_intent.question_effective


def test_business_target_ambiguity_accepts_multiple_ontology_concepts() -> None:
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
    candidates = [node for node in ontology.nodes if node.kind == OntologyNodeKind.TABLE]
    assert len(candidates) == 2
    intent = created.session.intents[-1].model_copy(deep=True)
    intent.entities = []
    intent.metrics = []
    intent.ambiguities = [
        IntentAmbiguity(
            id="business-targets",
            code="business_meaning_required",
            message_ja="検索対象を確認してください。",
            options=[node.technical_name for node in candidates],
            blocking=True,
        )
    ]
    session = created.session.model_copy(
        deep=True,
        update={"intents": [intent], "clarification_turns": []},
    )
    view = created.profile_ontology_view.model_copy(deep=True)
    view.node_ids = list(dict.fromkeys([*view.node_ids, *(node.id for node in candidates)]))

    state = build_clarification_state(session, ontology, view)

    question = state.current_question
    assert question is not None
    assert question.category == ClarificationCategory.BUSINESS_MEANING
    assert question.answer_kind == ClarificationAnswerKind.MULTI_SELECT
    assert question.prompt_ja == "どの業務対象について調べますか？"
    assert "意図した対象をすべて選んでください" in question.reason_ja
    assert len(question.options) == 2

    updated_intent = apply_clarification_answer(
        intent,
        question,
        ClarificationAnswer(
            question_id=question.id,
            selected_option_ids=[option.id for option in question.options],
        ),
        ontology,
    )

    selected_node_ids = {node.id for node in candidates}
    applied_node_ids = {item.ontology_node_id for item in updated_intent.entities}
    assert selected_node_ids <= applied_node_ids
    assert "、" in updated_intent.question_effective
    assert "確認事項" not in updated_intent.question_effective


def test_guided_output_answer_replaces_quoted_fragment_with_natural_query() -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    intent = created.session.intents[-1].model_copy(
        deep=True,
        update={
            "question_original": '"部署情報"',
            "question_effective": (
                '"部署情報"\n確認事項（検索結果に表示する項目を選んでください。）：部署名'
            ),
        },
    )
    question = ClarificationQuestion(
        id="question-all-columns",
        category=ClarificationCategory.OUTPUT,
        prompt_ja="検索結果に表示する項目を選んでください。",
        answer_kind=ClarificationAnswerKind.SINGLE_SELECT,
        options=[ClarificationOption(id="option-all-columns", label_ja="すべての列")],
    )

    updated = apply_clarification_answer(
        intent,
        question,
        ClarificationAnswer(
            question_id=question.id,
            selected_option_ids=["option-all-columns"],
        ),
        ontology,
    )

    assert updated.question_effective == (
        "部署情報について、検索結果にはすべての列を表示してください。"
    )
    assert "確認事項" not in updated.question_effective
    assert "検索結果に表示する項目を選んでください" not in updated.question_effective


def test_guided_output_multi_select_answer_rebuilds_and_persists_session_state() -> None:
    store = _OracleSizedIdempotencyStore()
    runtime = OntologyApiRuntime(
        legacy_service=_GuidedLegacyService(),
        store=store,
    )
    _created, session, question = _prepare_guided_output_question(runtime)
    idempotency_key = _frontend_clarification_idempotency_key(session.id)
    assert len(idempotency_key.encode("utf-8")) > IDEMPOTENCY_KEY_STORAGE_MAX_BYTES
    updated = runtime.answer_clarification_idempotent(
        session.id,
        ClarificationAnswerRequest(
            base_version=1,
            question_id=question.id,
            selected_option_ids=[option.id for option in question.options],
        ),
        idempotency_key=idempotency_key,
    )
    replay = runtime.answer_clarification_idempotent(
        session.id,
        ClarificationAnswerRequest(
            base_version=1,
            question_id=question.id,
            selected_option_ids=[option.id for option in question.options],
        ),
        idempotency_key=idempotency_key,
    )
    with pytest.raises(OntologyVersionConflictError, match="異なる payload"):
        runtime.answer_clarification_idempotent(
            session.id,
            ClarificationAnswerRequest(
                base_version=1,
                question_id=question.id,
                selected_option_ids=[question.options[0].id],
            ),
            idempotency_key=idempotency_key,
        )

    assert updated.session.current_intent_version == 2
    assert replay.session.current_intent_version == 2
    assert updated.clarification is not None
    assert updated.clarification.status == ClarificationStatus.READY_TO_CONFIRM
    assert {item.name_ja for item in updated.session.intents[-1].dimensions} == {
        "受注状態",
        "受注ID",
    }
    summary = next(
        item for item in updated.clarification.intent_summary if item.key == "dimensions"
    )
    assert summary.confirmed is True
    stored_idempotency = store.list_documents("idempotency")
    assert len(stored_idempotency) == 1
    assert stored_idempotency[0]["idempotency_key"].startswith("sha256:")
    assert (
        len(stored_idempotency[0]["idempotency_key"].encode("utf-8"))
        <= IDEMPOTENCY_KEY_STORAGE_MAX_BYTES
    )


@pytest.mark.asyncio
async def test_guided_output_multi_select_accepts_frontend_idempotency_key_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _OracleSizedIdempotencyStore()
    runtime = OntologyApiRuntime(
        legacy_service=_GuidedLegacyService(),
        store=store,
    )
    _created, session, question = _prepare_guided_output_question(runtime)
    idempotency_key = _frontend_clarification_idempotency_key(session.id)
    monkeypatch.setattr(ontology_router_module, "ontology_runtime", runtime)
    transport = httpx.ASGITransport(app=app)
    payload = {
        "base_version": 1,
        "question_id": question.id,
        "selected_option_ids": [option.id for option in question.options],
        "free_text": "",
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/nl2sql/query-sessions/{session.id}/clarification-answers",
            headers={"Idempotency-Key": idempotency_key},
            json=payload,
        )
        replay = await client.post(
            f"/api/nl2sql/query-sessions/{session.id}/clarification-answers",
            headers={"Idempotency-Key": idempotency_key},
            json=payload,
        )

    assert response.status_code == 200
    assert replay.status_code == 200
    response_data = response.json()["data"]
    assert response_data["clarification"]["status"] == "ready_to_confirm"
    assert {item["name_ja"] for item in response_data["session"]["intents"][-1]["dimensions"]} == {
        "受注状態",
        "受注ID",
    }


@pytest.mark.parametrize(
    ("code", "expected_prompt"),
    [
        ("business_meaning_required", "どの業務対象について調べますか？"),
        ("relationship_path_required", "業務対象をどの関係で結びますか？"),
        ("filter_value_required", "どの条件で絞り込みますか？"),
        ("time_range_required", "どの期間を対象にしますか？"),
        ("granularity_required", "どの単位で集計しますか？"),
        ("output_format_required", "検索結果に何を表示しますか？"),
    ],
)
def test_clarification_categories_use_answerable_business_copy(
    code: str,
    expected_prompt: str,
) -> None:
    runtime = _runtime()
    created = _create_guided(runtime)
    ontology = runtime.ontology_revision(created.session.ontology_revision_id)
    intent = created.session.intents[-1].model_copy(deep=True)
    intent.metrics = []
    intent.ambiguities = [
        IntentAmbiguity(
            id=f"ambiguity-{code}",
            code=code,
            message_ja="Embedding / Ontology / Schema の内部診断です。",
            blocking=True,
        )
    ]
    session = created.session.model_copy(deep=True, update={"intents": [intent]})

    state = build_clarification_state(session, ontology, created.profile_ontology_view)

    assert state.remaining_questions
    question = state.remaining_questions[0]
    assert question.prompt_ja == expected_prompt
    assert not any(
        term in f"{question.prompt_ja}{question.reason_ja}"
        for term in ("Embedding", "Ontology", "Schema", "GROUP BY")
    )


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
    assert first.session.intents[-1].question_effective == (
        "今月を対象に、受注件数を表示してください。"
    )
    assert "確認事項" not in first.session.intents[-1].question_effective
    assert runtime.store.list_documents("idempotency")[0]["idempotency_key"] == "answer-1"
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
