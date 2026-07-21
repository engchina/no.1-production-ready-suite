"""AI オントロジー構築(ontology_build)のテスト。

LLM は fake、store は InMemoryOntologyStore。job → proposal → accept → publish →
再起動復元までの経路と、スコープ外候補の warnings 落ちを検証する。
"""

from __future__ import annotations

import io
import json
import time
from typing import Any

import pytest

from app.features.nl2sql.models import (
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaConstraintDetail,
    SchemaTable,
)
from app.features.nl2sql.ontology_build import (
    OntologyBuildService,
    parse_qa_workbook,
)
from app.features.nl2sql.ontology_models import (
    OntologyBuildStatus,
    OntologyBuildStepName,
    OntologyBuildStepStatus,
    OntologyEdgeKind,
    OntologyNodeKind,
    OntologyProposalKind,
    OntologyReviewStatus,
    QaPair,
)
from app.features.nl2sql.ontology_router import (
    OntologyApiRuntime,
    OntologyPublishRequest,
)
from app.features.nl2sql.ontology_service import OntologyNotFoundError
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.settings import get_settings


class _FakeLegacyNl2SqlService:
    def __init__(self) -> None:
        self._enterprise_ai_client: Any = None
        self.profile = Nl2SqlProfile(
            id="sales",
            name="販売分析",
            allowed_tables=["APP.ORDERS", "APP.CUSTOMERS"],
            default_row_limit=100,
        )
        self.catalog = SchemaCatalog(
            refreshed_at="2026-07-11T00:00:00Z",
            tables=[
                SchemaTable(
                    table_name="ORDERS",
                    logical_name="受注",
                    owner="APP",
                    columns=[
                        SchemaColumn(column_name="ID", logical_name="受注 ID", data_type="NUMBER"),
                        SchemaColumn(
                            column_name="CUSTOMER_ID", logical_name="顧客 ID", data_type="NUMBER"
                        ),
                        SchemaColumn(
                            column_name="AMOUNT", logical_name="受注金額", data_type="NUMBER"
                        ),
                    ],
                ),
                SchemaTable(
                    table_name="CUSTOMERS",
                    logical_name="顧客",
                    owner="APP",
                    columns=[
                        SchemaColumn(column_name="ID", logical_name="顧客 ID", data_type="NUMBER"),
                        SchemaColumn(
                            column_name="NAME", logical_name="顧客名", data_type="VARCHAR2"
                        ),
                    ],
                    constraint_details=[
                        SchemaConstraintDetail(
                            constraint_name="PK_CUSTOMERS",
                            constraint_type="P",
                            owner="APP",
                            table_name="CUSTOMERS",
                            columns=["ID"],
                        )
                    ],
                ),
            ],
        )

    def get_catalog(self) -> SchemaCatalog:
        return self.catalog

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("profile not found")
        return self.profile


class _FakeEnterpriseAiClient:
    def __init__(self, payload: str, *, configured: bool = True) -> None:
        self.payload = payload
        self.configured = configured
        self.calls: list[str] = []

    def is_configured(self) -> bool:
        return self.configured

    def model_id(self) -> str:
        return "fake-enterprise-ai"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        self.calls.append(prompt)
        return self.payload


_EXTRACTION = {
    "entities": [
        {
            "object_name": "APP.ORDERS",
            "business_name_ja": "受注",
            "description_ja": "受注トランザクション",
            "aliases": ["注文"],
            "confidence": 0.9,
        },
        {
            "object_name": "APP.SECRET",
            "business_name_ja": "秘密",
            "description_ja": "profile 範囲外",
            "aliases": [],
            "confidence": 0.9,
        },
    ],
    "relationships": [
        {
            "source_object": "APP.ORDERS",
            "target_object": "APP.CUSTOMERS",
            "relationship_name_ja": "顧客を参照",
            "cardinality": "many_to_one",
            "join_conditions": [
                {"left": "APP.ORDERS.CUSTOMER_ID", "right": "APP.CUSTOMERS.ID", "operator": "="}
            ],
            "evidence_ja": "Q/A の JOIN 句",
            "confidence": 0.8,
        }
    ],
    "metrics": [
        {
            "metric_name_ja": "受注金額合計",
            "expression_sql": "SUM(AMOUNT)",
            "aggregation": "sum",
            "base_columns": ["APP.ORDERS.AMOUNT"],
            "unit": "円",
            "description_ja": "受注金額の合計",
            "evidence_ja": "",
            "confidence": 0.7,
        }
    ],
    "synonyms": [{"target": "APP.ORDERS", "aliases": ["オーダー"], "evidence_ja": ""}],
    "warnings_ja": [],
}
_FENCED_PAYLOAD = "以下が抽出結果です。\n" + json.dumps(_EXTRACTION, ensure_ascii=False) + "\n以上"

_QA_SQL = (
    "SELECT C.NAME, SUM(O.AMOUNT) FROM APP.ORDERS O "
    "JOIN APP.CUSTOMERS C ON O.CUSTOMER_ID = C.ID GROUP BY C.NAME"
)


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    import openpyxl  # type: ignore[import-untyped]

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _wait_for_job(service: OntologyBuildService, job_id: str) -> Any:
    for _ in range(500):
        job = service.get(job_id)
        if job is not None and job.status in {
            OntologyBuildStatus.SUCCEEDED,
            OntologyBuildStatus.FAILED,
        }:
            return job
        time.sleep(0.01)
    raise AssertionError("ontology build job did not finish")


@pytest.fixture
def harness() -> tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService]:
    store = InMemoryOntologyStore()
    legacy = _FakeLegacyNl2SqlService()
    return OntologyApiRuntime(legacy_service=legacy, store=store), store, legacy


# --- Q/A workbook ----------------------------------------------------------------------------


def test_parse_qa_workbook_xlsx_and_csv() -> None:
    xlsx = _xlsx_bytes(
        [
            ["質問", "SQL", "備考"],
            ["顧客別の売上は?", _QA_SQL, "月次で利用"],
            ["削除して", "DELETE FROM APP.ORDERS", ""],
            ["", "", ""],
        ]
    )
    pairs, warnings = parse_qa_workbook("qa.xlsx", xlsx)
    assert [pair.question for pair in pairs] == ["顧客別の売上は?"]
    assert pairs[0].sql == _QA_SQL
    assert pairs[0].note_ja == "月次で利用"
    assert any("SELECT/WITH 以外" in warning for warning in warnings)

    csv_content = "QUESTION,SQL\n受注件数,SELECT COUNT(*) FROM APP.ORDERS\n".encode()
    csv_pairs, csv_warnings = parse_qa_workbook("qa.csv", csv_content)
    assert len(csv_pairs) == 1
    assert csv_warnings == []


def test_parse_qa_workbook_rejects_missing_headers_and_unknown_suffix() -> None:
    pairs, warnings = parse_qa_workbook("qa.csv", b"A,B\n1,2\n")
    assert pairs == []
    assert any("QUESTION" in warning for warning in warnings)

    pairs, warnings = parse_qa_workbook("qa.pdf", b"binary")
    assert pairs == []
    assert any("未対応の形式" in warning for warning in warnings)


# --- job → proposal --------------------------------------------------------------------------


def test_build_job_registers_scoped_proposals_and_drops_outside_candidates(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)

    qa_pairs, _ = parse_qa_workbook("qa.csv", f"QUESTION,SQL\n顧客別売上,{_QA_SQL}\n".encode())
    job = service.start(
        "sales",
        business_text="受注は顧客に紐づく。売上は受注金額の合計。",
        qa_pairs=qa_pairs,
    )
    finished = _wait_for_job(service, job.id)

    assert finished.status == OntologyBuildStatus.SUCCEEDED
    assert [step.name for step in finished.steps] == [
        OntologyBuildStepName.SCHEMA_CONTEXT,
        OntologyBuildStepName.SCHEMA_NAMING,
        OntologyBuildStepName.QA_EXTRACTION,
        OntologyBuildStepName.TEXT_EXTRACTION,
        OntologyBuildStepName.PROPOSAL_REGISTRATION,
    ]
    assert all(step.status == OntologyBuildStepStatus.SUCCEEDED for step in finished.steps)
    # 各ステップに開始・終了時刻が入り、アクティビティタイムラインが時系列で積まれる
    assert all(
        step.started_at is not None and step.finished_at is not None for step in finished.steps
    )
    assert finished.started_at is not None
    assert len(finished.events) >= 5
    event_times = [event.at for event in finished.events]
    assert event_times == sorted(event_times)
    assert any("スキーマ情報を準備しました" in event.message_ja for event in finished.events)
    assert any("提案" in event.message_ja for event in finished.events)
    # スコープ外(APP.SECRET)は proposal 化されず warnings に落ちる
    assert any("APP.SECRET" in warning for warning in finished.warnings_ja)
    assert finished.proposal_ids

    proposals = runtime.list_profile_proposals("sales")
    assert {proposal.id for proposal in proposals} == set(finished.proposal_ids)
    kinds = {proposal.kind for proposal in proposals}
    assert OntologyProposalKind.MAPPING in kinds
    assert OntologyProposalKind.RELATIONSHIP in kinds
    assert OntologyProposalKind.METRIC_DEFINITION in kinds
    # 同一内容(3 ステップとも同じ fake 応答)は payload 単位で dedup される
    relationship_proposals = [
        proposal for proposal in proposals if proposal.kind == OntologyProposalKind.RELATIONSHIP
    ]
    assert len(relationship_proposals) == 1
    # session 非依存の予約プレフィクス
    assert all(proposal.session_id.startswith("ontology_build:") for proposal in proposals)
    # 同義語は entity 候補の aliases に合流している
    mapping = next(
        proposal for proposal in proposals if proposal.kind == OntologyProposalKind.MAPPING
    )
    node_upserts = mapping.proposal_payload.values["node_upserts"]
    assert any(set(node["aliases"]) >= {"注文", "オーダー"} for node in node_upserts)


def test_build_job_fails_gracefully_without_enterprise_ai(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, _store, _legacy = harness
    service = OntologyBuildService(runtime)
    job = service.start("sales", business_text="テスト")
    finished = _wait_for_job(service, job.id)

    assert finished.status == OntologyBuildStatus.FAILED
    assert "Enterprise AI" in finished.error_message_ja
    assert finished.proposal_ids == []
    assert all(step.status == OntologyBuildStepStatus.SKIPPED for step in finished.steps)


def test_build_job_survives_invalid_llm_json(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient("これは JSON ではありません")
    service = OntologyBuildService(runtime)
    job = service.start("sales", business_text="テスト", run_schema_naming=False)
    finished = _wait_for_job(service, job.id)

    text_step = next(
        step for step in finished.steps if step.name == OntologyBuildStepName.TEXT_EXTRACTION
    )
    assert text_step.status == OntologyBuildStepStatus.FAILED
    assert any("抽出に失敗" in warning for warning in finished.warnings_ja)
    assert finished.proposal_ids == []


def test_start_rejects_unknown_profile(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, _store, _legacy = harness
    service = OntologyBuildService(runtime)
    with pytest.raises(OntologyNotFoundError):
        service.start("unknown-profile")


# --- accept → draft → publish → 再起動復元 -----------------------------------------------------


def test_accept_applies_upserts_accumulates_and_publishes(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)
    qa_pairs, _ = parse_qa_workbook("qa.csv", f"QUESTION,SQL\n顧客別売上,{_QA_SQL}\n".encode())
    job = service.start("sales", business_text="受注は顧客に紐づく。", qa_pairs=qa_pairs)
    _wait_for_job(service, job.id)
    proposals = runtime.list_profile_proposals("sales")
    relationship = next(
        proposal for proposal in proposals if proposal.kind == OntologyProposalKind.RELATIONSHIP
    )
    mapping = next(
        proposal for proposal in proposals if proposal.kind == OntologyProposalKind.MAPPING
    )
    metric = next(
        proposal
        for proposal in proposals
        if proposal.kind == OntologyProposalKind.METRIC_DEFINITION
    )

    # 関係提案の accept → 業務ノード + 承認済み関係が draft に入る
    review = runtime.accept_proposal(relationship.id)
    assert review.draft is not None
    draft = review.draft
    business_edges = [
        edge for edge in draft.edges if edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP
    ]
    assert len(business_edges) == 1
    assert business_edges[0].review_status == OntologyReviewStatus.APPROVED
    assert business_edges[0].join_conditions[0].left.column_name == "CUSTOMER_ID"

    # 続けて命名提案を accept → 直前の draft に積み上がり、合成 endpoint が上書きされる
    review2 = runtime.accept_proposal(mapping.id)
    assert review2.draft is not None
    draft2 = review2.draft
    entity_nodes = [node for node in draft2.nodes if node.kind == OntologyNodeKind.BUSINESS_ENTITY]
    orders_entity = next(node for node in entity_nodes if node.technical_name == "APP.ORDERS")
    assert orders_entity.business_name_ja == "受注"
    assert "オーダー" in orders_entity.aliases
    assert not orders_entity.metadata.get("synthetic_endpoint")
    # 関係提案の内容も残っている(最新 revision へ積み上げ)
    assert any(edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP for edge in draft2.edges)

    # 指標提案も accept → metric_definition が node metadata に入る
    review3 = runtime.accept_proposal(metric.id)
    assert review3.draft is not None
    metric_nodes = [node for node in review3.draft.nodes if node.kind == OntologyNodeKind.METRIC]
    assert len(metric_nodes) == 1
    assert metric_nodes[0].metadata["metric_definition"]["expression_sql"] == "SUM(AMOUNT)"

    # publish は全業務要素 APPROVED のため成功する
    published = runtime.publish_ontology_revision(
        review3.draft.revision.id,
        OntologyPublishRequest(etag=review3.draft.revision.etag),
    )
    assert published.revision.status.value == "published"

    # 再起動(同じ store)でも ontology_build 由来 proposal の復元が落ちない
    restarted = OntologyApiRuntime(legacy_service=legacy, store=store)
    restored = restarted.list_profile_proposals("sales")
    assert {proposal.id for proposal in restored} >= {relationship.id, mapping.id, metric.id}


def test_build_job_fails_fast_when_profile_view_is_empty(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """schema 未解決(view 空)のときは LLM を呼ばずに明確なエラーで失敗する。"""

    runtime, _store, legacy = harness
    client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    legacy._enterprise_ai_client = client
    # 空カタログ → profile の対象オブジェクトが公開 Ontology に解決できない状態
    legacy.catalog = SchemaCatalog(refreshed_at="2026-07-12T00:00:00Z", tables=[])

    service = OntologyBuildService(runtime)
    job = service.start("sales", business_text="受注は顧客に紐づく。")
    finished = _wait_for_job(service, job.id)

    assert finished.status == OntologyBuildStatus.FAILED
    assert "スキーマ情報を更新" in finished.error_message_ja
    assert client.calls == []
    schema_step = next(
        step for step in finished.steps if step.name == OntologyBuildStepName.SCHEMA_CONTEXT
    )
    assert schema_step.status == OntologyBuildStepStatus.FAILED
    assert all(
        step.status == OntologyBuildStepStatus.SKIPPED
        for step in finished.steps
        if step.name != OntologyBuildStepName.SCHEMA_CONTEXT
    )


def test_start_returns_immediately_even_if_profile_view_is_slow(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """start() は重いオントロジー同期(profile_view)を待たずに job を返す。"""

    import threading as _threading

    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    release = _threading.Event()
    original_profile_view = runtime.profile_view

    class _SlowRuntime:
        def __getattr__(self, name: str) -> Any:
            return getattr(runtime, name)

        def profile_view(self, profile_id: str) -> Any:
            release.wait(timeout=5)
            return original_profile_view(profile_id)

    service = OntologyBuildService(_SlowRuntime())
    started = time.monotonic()
    job = service.start("sales", business_text="受注は顧客に紐づく。")
    elapsed = time.monotonic() - started

    try:
        # profile_view がブロックしていても POST(start)は即時に返る
        assert elapsed < 1.0
        assert job.status in {OntologyBuildStatus.QUEUED, OntologyBuildStatus.RUNNING}
        assert job.steps[0].name == OntologyBuildStepName.SCHEMA_CONTEXT
        # ポーリングで「スキーマ情報の準備」が実行中と観測できる
        for _ in range(100):
            snapshot = service.get(job.id)
            assert snapshot is not None
            schema_step = snapshot.steps[0]
            if schema_step.status == OntologyBuildStepStatus.RUNNING:
                break
            time.sleep(0.01)
        assert schema_step.status == OntologyBuildStepStatus.RUNNING
    finally:
        release.set()
    finished = _wait_for_job(service, job.id)
    assert finished.status == OntologyBuildStatus.SUCCEEDED


def test_accept_ignores_stale_drafts_from_previous_schema_generations(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """過去スキーマ世代の draft が store に残っていても accept が 409 にならない。"""

    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)

    # 旧スキーマ世代で提案を承認して draft を作る(store に残留する)
    job = service.start("sales", business_text="受注は顧客に紐づく。")
    _wait_for_job(service, job.id)
    old_proposal = runtime.list_profile_proposals("sales")[0]
    runtime.accept_proposal(old_proposal.id)

    # スキーマ drift(列追加で fingerprint が変わる)→ 公開世代が変わる
    legacy.catalog = legacy.catalog.model_copy(
        update={
            "tables": [
                legacy.catalog.tables[0].model_copy(
                    update={
                        "columns": [
                            *legacy.catalog.tables[0].columns,
                            SchemaColumn(
                                column_name="CREATED_AT",
                                logical_name="作成日時",
                                data_type="TIMESTAMP",
                            ),
                        ]
                    }
                ),
                *legacy.catalog.tables[1:],
            ]
        },
        deep=True,
    )
    # 新世代で AI 構築 → 提案は新 published を基準に生成される
    job2 = service.start("sales", business_text="受注は顧客に紐づく。")
    finished2 = _wait_for_job(service, job2.id)
    new_proposals = [
        proposal
        for proposal in runtime.list_profile_proposals("sales")
        if proposal.id in set(finished2.proposal_ids)
    ]
    assert new_proposals

    # 旧世代 draft(business 定義持ち)が store に残っていても、新提案の accept は成功する
    review = runtime.accept_proposal(new_proposals[0].id)
    assert review.draft is not None
    _view, published_graph = runtime.profile_view("sales")
    assert published_graph is not None
    published_fp = published_graph.revision.schema_fingerprint
    assert review.draft.revision.schema_fingerprint == published_fp


def test_batch_accept_creates_single_draft_for_all_proposals(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)
    qa_pairs, _ = parse_qa_workbook("qa.csv", f"QUESTION,SQL\n顧客別売上,{_QA_SQL}\n".encode())
    job = service.start("sales", business_text="受注は顧客に紐づく。", qa_pairs=qa_pairs)
    finished = _wait_for_job(service, job.id)
    assert len(finished.proposal_ids) >= 3

    accepted, draft = runtime.accept_proposals(finished.proposal_ids)

    # すべて同じ draft revision に反映され、全提案が accepted になる
    assert {proposal.status.value for proposal in accepted} == {"accepted"}
    assert {proposal.proposal_payload.values["draft_revision_id"] for proposal in accepted} == {
        draft.revision.id
    }
    # 命名提案の業務名が合成 endpoint に上書きされない
    entity_nodes = [node for node in draft.nodes if node.kind == OntologyNodeKind.BUSINESS_ENTITY]
    orders_entity = next(node for node in entity_nodes if node.technical_name == "APP.ORDERS")
    assert orders_entity.business_name_ja == "受注"
    assert any(edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP for edge in draft.edges)
    # publish まで通る
    published = runtime.publish_ontology_revision(
        draft.revision.id, OntologyPublishRequest(etag=draft.revision.etag)
    )
    assert published.revision.status.value == "published"


def test_rerun_clears_previous_proposals(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """AI 構築を再実行すると、前回のレビュー一覧(承認/却下/レビュー待ち)は一掃され、
    今回の候補だけが残る。SUPERSEDED は再起動後も一覧に復活しない。"""

    runtime, store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)

    first = _wait_for_job(service, service.start("sales", business_text="受注は顧客に紐づく。").id)
    first_proposals = runtime.list_profile_proposals("sales")
    count_after_first = len(first_proposals)
    assert count_after_first == len(first.proposal_ids)
    first_ids = {proposal.id for proposal in first_proposals}
    # 1 件は承認、1 件は却下しておき、それらも次回実行で一掃されることを確認する。
    runtime.accept_proposal(first.proposal_ids[0])
    runtime.reject_proposal(first.proposal_ids[1])

    second = _wait_for_job(service, service.start("sales", business_text="受注は顧客に紐づく。").id)
    assert second.status == OntologyBuildStatus.SUCCEEDED
    # 今回分は新規に登録される(空ではない)。
    assert second.proposal_ids
    after_second = runtime.list_profile_proposals("sales")
    # 前回の提案(承認/却下含む)は一覧から消え、今回の候補だけが残る。
    assert len(after_second) == count_after_first
    assert {proposal.id for proposal in after_second} == set(second.proposal_ids)
    assert first_ids.isdisjoint({proposal.id for proposal in after_second})

    # 再起動(同じ store)でも SUPERSEDED は一覧に復活しない。
    restarted = OntologyApiRuntime(legacy_service=legacy, store=store)
    restored_ids = {proposal.id for proposal in restarted.list_profile_proposals("sales")}
    assert restored_ids == set(second.proposal_ids)
    assert first_ids.isdisjoint(restored_ids)


def test_start_prunes_oldest_finished_jobs(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    from datetime import timedelta

    from app.features.nl2sql.ontology_build import _MAX_FINISHED_JOBS
    from app.features.nl2sql.ontology_models import OntologyBuildJob, utc_now

    runtime, _store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    service = OntologyBuildService(runtime)

    # 完了 job を上限 +2 件、実行中 job を 1 件直接注入する(実 job を回すと遅いため)。
    base = utc_now()
    for index in range(_MAX_FINISHED_JOBS + 2):
        job = OntologyBuildJob(
            id=f"ontology_build_old_{index:03d}",
            profile_id="sales",
            status=OntologyBuildStatus.SUCCEEDED,
            finished_at=base + timedelta(seconds=index),
        )
        service._jobs[job.id] = job
    running = OntologyBuildJob(
        id="ontology_build_running",
        profile_id="sales",
        status=OntologyBuildStatus.RUNNING,
    )
    service._jobs[running.id] = running

    started = service.start("sales", business_text="受注は顧客に紐づく。")

    # 最古の完了 2 件だけが prune され、実行中・新規 job は保護される。
    assert service.get("ontology_build_old_000") is None
    assert service.get("ontology_build_old_001") is None
    assert service.get(f"ontology_build_old_{_MAX_FINISHED_JOBS + 1:03d}") is not None
    assert service.get(running.id) is not None
    assert service.get(started.id) is not None
    # worker thread の終了を待って teardown を安定させる。
    _wait_for_job(service, started.id)


def test_external_worker_rehydrates_persisted_build_input(
    harness: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, store, legacy = harness
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(_FENCED_PAYLOAD)
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    api_service = OntologyBuildService(runtime)
    queued = api_service.start(
        "sales",
        business_text="受注は顧客に紐づく。",
        qa_pairs=[QaPair(question="顧客別売上", sql=_QA_SQL)],
        idempotency_key="external-build-1",
    )
    assert queued.status == OntologyBuildStatus.QUEUED
    persisted = store.get_job(queued.id)
    assert persisted is not None
    assert persisted["input_payload"]["business_text"] == "受注は顧客に紐づく。"

    worker_service = OntologyBuildService(runtime)
    finished = worker_service.run_persisted(queued.id)
    assert finished.status == OntologyBuildStatus.SUCCEEDED
    assert finished.proposal_ids
