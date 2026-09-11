"""根拠・再構築・batch 復旧の回帰テスト。実 OCI/Oracle は使用しない。"""

from __future__ import annotations

import json

import pytest
from test_nl2sql_ontology_build import _FakeEnterpriseAiClient, _wait_for_job
from test_nl2sql_ontology_definitions import definition_payload, runtime

from app.features.nl2sql.ontology_build import OntologyBuildService, _OntologyBuildLlmTask
from app.features.nl2sql.ontology_definition_quality import inspect_definition_quality
from app.features.nl2sql.ontology_definition_service import (
    ProfileOntologyDefinitionService,
    definition_fingerprint,
)
from app.features.nl2sql.ontology_definitions import (
    DefinitionEvidence,
    DefinitionSource,
    MetricDefinitionV2,
    ObjectTypeDefinition,
)
from app.features.nl2sql.ontology_models import OntologyBuildStepName


def test_evidence_must_match_profile_source_location_hash_and_verbatim_excerpt() -> None:
    source = DefinitionSource(
        source_id="manual",
        locator="line:1",
        kind="manual",
        sha256="a" * 64,
        text="契約には更新期限がある。",
    )
    item = ObjectTypeDefinition(
        api_name="Contract",
        name_ja="契約",
        evidence=[
            DefinitionEvidence(source_id="manual", locator="line:1", excerpt_ja="更新期限がある")
        ],
    )
    assert not any(
        f.code == "EVIDENCE_UNRESOLVED" for f in inspect_definition_quality([item], [source])
    )
    assert item.evidence[0].verified
    item.evidence[0].locator = "line:2"
    assert any(
        f.code == "EVIDENCE_UNRESOLVED" for f in inspect_definition_quality([item], [source])
    )
    assert not item.evidence[0].verified
    item.evidence[0].locator = "line:1"
    item.evidence[0].source_sha256 = "b" * 64
    inspect_definition_quality([item], [source])
    assert not item.evidence[0].verified


def test_metric_incomplete_semantics_are_reported_instead_of_assumed() -> None:
    item = MetricDefinitionV2(api_name="sales", name_ja="売上", expression_sql="SUM(amount)")
    fields = {f.field for f in inspect_definition_quality([item], [])}
    assert {
        "grain",
        "aggregation",
        "unit",
        "null_policy_ja",
        "time_policy_ja",
        "additivity",
    } <= fields


def test_rebuild_keeps_reviewed_identity_and_manual_edits_even_when_unchanged() -> None:
    rt, _ = runtime()
    service = ProfileOntologyDefinitionService(rt)
    first = service.save_build(
        profile_id="sales",
        job_id="first",
        definitions=[ObjectTypeDefinition(api_name="Contract", name_ja="契約")],
        schema_fingerprint="schema",
        source_revision_id="legacy",
    )
    first.definitions[0].origin = "manual"
    first.definitions[0].review_status = "reviewed"
    first.review_records = [
        {
            "definition_id": first.definitions[0].id,
            "hash": definition_fingerprint(first.definitions[0].model_dump(mode="json")),
            "actor": "reviewer",
        }
    ]
    record = rt.store.get_artifact(first.id)
    assert record is not None
    record["content"] = first.model_dump_json()
    rt.store.save_artifact(record, expected_etag=record["etag"])
    second = service.save_build(
        profile_id="sales",
        job_id="second",
        definitions=[ObjectTypeDefinition(api_name="Contract", name_ja="契約")],
        schema_fingerprint="schema",
        source_revision_id="legacy",
    )
    assert second.definitions[0].origin == "manual"
    assert second.definitions[0].review_status == "reviewed"
    assert second.review_records == first.review_records
    assert not second.conflicts
    third = service.save_build(
        profile_id="sales",
        job_id="third",
        definitions=[
            ObjectTypeDefinition(api_name="Agreement", aliases=["Contract"], name_ja="合意")
        ],
        schema_fingerprint="schema",
        source_revision_id="legacy",
    )
    assert third.definitions[0].id == first.definitions[0].id
    assert third.definitions[0].name_ja == "契約"
    assert len(third.conflicts) == 1


def test_staged_worker_and_checkpoint_reuse_after_service_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt, legacy = runtime()
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(
        json.dumps({"definitions": definition_payload()})
    )
    service = OntologyBuildService(rt)
    job = _wait_for_job(service, service.start("sales", business_text="受注を承認する。").id)
    assert len(job.definition_phases) == 6
    assert all(phase.status == "succeeded" for phase in job.definition_phases)
    task = _OntologyBuildLlmTask(
        name=OntologyBuildStepName.TEXT_EXTRACTION,
        prompt="検査",
        context="固定入力",
        progress_ja="検査",
    )
    results, warnings = service._execute_llm_task(
        job.id, legacy._enterprise_ai_client, task, label="検査"
    )
    assert results and not warnings
    restarted = OntologyBuildService(rt)

    def forbidden(*args: object, **kwargs: object) -> str:
        raise AssertionError("saved batch must not invoke LLM")

    monkeypatch.setattr(restarted, "_generate_extraction", forbidden)
    restored, warnings = restarted._execute_llm_task(
        job.id, legacy._enterprise_ai_client, task, label="検査"
    )
    assert restored[0].extraction == results[0].extraction
    assert not warnings
    bundle = ProfileOntologyDefinitionService(rt).get("sales", job.result_bundle_id)
    assert any(source.kind == "manual" for source in bundle.sources)
