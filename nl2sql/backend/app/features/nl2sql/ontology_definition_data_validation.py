"""明示的な実データ検証 job。Oracle の読み取りと SHACL 対象 coverage を記録する。"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

from pyshacl import validate as shacl_validate
from rdflib import RDF, Graph, Literal, Namespace, URIRef

from app.security.domain import Principal
from app.security.permissions import SQL_EXECUTE_PERMISSION
from app.security.request_actor import actor_scope
from app.settings import get_settings

from .ontology_definition_artifacts import render_definition_artifacts
from .ontology_definition_service import definition_fingerprint
from .ontology_definition_validation import checked_expression, schema_objects, validate_definitions
from .ontology_definition_workspace import (
    ProfileOntologyWorkspaceService,
    authorize_definition_operation,
)
from .ontology_definitions import (
    DefinitionDataValidationRequest,
    ProfileOntologyBundle,
    PropertyDefinition,
)
from .ontology_service import (
    OntologyGateBlockedError,
    OntologyNotFoundError,
    OntologyVersionConflictError,
)
from .ontology_store import stable_ontology_id


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def check_data(
    runtime: Any, bundle: ProfileOntologyBundle, request: DefinitionDataValidationRequest
) -> dict[str, Any]:
    schema = json.loads(str(runtime.prepare_build_schema_context(bundle.profile_id).schema_context))
    errors = [f for f in validate_definitions(bundle, schema) if f.severity == "error"]
    if errors:
        raise OntologyGateBlockedError(
            "STATIC_VALIDATION_REQUIRED", "実データ検証の前に定義の阻害事項を修正してください。"
        )
    adapter = getattr(runtime.legacy_service, "_oracle_adapter", None)
    if adapter is None:
        raise OntologyGateBlockedError("ORACLE_UNAVAILABLE", "Oracle の実データ接続がありません。")
    graph = Graph()
    ns = Namespace(f"urn:nl2sql:profile:{bundle.profile_id}:")
    by_name = {d.api_name: d for d in bundle.definitions}
    targets, covered, samples, skipped = [], [], 0, []
    for obj in bundle.definitions:
        if obj.kind != "object_type":
            continue
        targets.append(obj.id)
        props = [by_name.get(name) for name in obj.properties]
        if (
            len(obj.mappings) != 1
            or not props
            or any(not isinstance(p, PropertyDefinition) or len(p.mappings) != 1 for p in props)
        ):
            skipped.append(
                {
                    "target": obj.id,
                    "reason_ja": "複数ソースまたは未確定 mapping の検証 SQL が必要です。",
                }
            )
            continue
        mapping = obj.mappings[0]
        mapped = [p for p in props if isinstance(p, PropertyDefinition)]
        if any(
            (p.mappings[0].owner, p.mappings[0].object_name) != (mapping.owner, mapping.object_name)
            for p in mapped
        ):
            skipped.append(
                {"target": obj.id, "reason_ja": "複数ソースを結合する検証 SQL が必要です。"}
            )
            continue
        columns = ", ".join(
            f"{quote_identifier(p.mappings[0].column_name)} AS {quote_identifier(p.id)}"
            for p in mapped
        )
        # Profile 内で解決した識別子は二重引用符で escape。行数は Pydantic の範囲付き整数。
        sql = (
            f"SELECT {columns} FROM {quote_identifier(mapping.owner)}."  # nosec B608
            f"{quote_identifier(mapping.object_name)} FETCH FIRST {request.sample_limit} ROWS ONLY"
        )
        result = adapter.execute_select(sql, request.sample_limit)
        covered.append(obj.id)
        for index, row in enumerate(result.rows):
            subject = URIRef(ns[f"sample:{obj.id}:{index}"])
            graph.add((subject, RDF.type, URIRef(ns[obj.id])))
            samples += 1
            for prop in mapped:
                value = row.get(prop.id)
                if value is not None:
                    graph.add((subject, URIRef(ns[prop.id]), Literal(value)))
    artifacts = render_definition_artifacts(bundle)
    conforms, _report_graph, report_text = shacl_validate(
        graph,
        shacl_graph=Graph().parse(data=artifacts["shacl_turtle"], format="turtle"),
        inference="none",
    )
    cases = []
    allowed = schema_objects(schema)
    for case in request.acceptance_cases:
        independent = not any(case.question_ja in source.text for source in bundle.sources)
        resolved = all(name in by_name for name in case.expected_concepts)
        path_ok = all(name in by_name for name in case.expected_path) and all(
            any(
                link.kind == "link_type" and {link.source, link.target} == {left, right}
                for link in bundle.definitions
            )
            for left, right in zip(case.expected_path, case.expected_path[1:], strict=False)
        )
        sql_ok: bool | None = None
        if case.sql:
            from sqlglot import exp

            tree = checked_expression(case.sql, query=True)
            for table in tree.find_all(exp.Table):
                if f"{table.db}.{table.name}".upper() not in allowed:
                    raise OntologyGateBlockedError(
                        "ACCEPTANCE_SQL_SCOPE",
                        "受入 SQL は Profile の OWNER.TABLE を明記してください。",
                    )
            result = adapter.execute_select(case.sql, request.sample_limit)
            sql_ok = result.rows == case.expected_rows
        cases.append(
            {
                "question_ja": case.question_ja,
                "independent": independent,
                "concepts_pass": resolved,
                "path_pass": path_ok,
                "sql_pass": sql_ok,
                "passed": independent and resolved and path_ok and sql_ok is not False,
            }
        )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "kind": "sampled_data",
        "sample_limit": request.sample_limit,
        "target_nodes": targets,
        "covered_targets": covered,
        "skipped_targets": skipped,
        "instance_count": samples,
        "instance_coverage": len(covered) / len(targets) if targets else 0,
        "coverage_basis": "object_types_sampled",
        "entire_database_validated": False,
        "instance_status": "sampled" if samples else "no_instances",
        "shacl_conforms": bool(conforms) if samples else None,
        "shacl_report": str(report_text),
        "acceptance_cases": cases,
        "acceptance_status": "completed" if cases else "not_run",
        "errors": int(not conforms and samples > 0) + sum(not case["passed"] for case in cases),
    }


def start_validation_job(
    runtime: Any,
    profile_id: str,
    bundle_id: str,
    etag: str,
    request: DefinitionDataValidationRequest,
    key: str,
    actor: Principal | None,
) -> dict[str, Any]:
    who = authorize_definition_operation(profile_id, actor, SQL_EXECUTE_PERMISSION)
    svc = ProfileOntologyWorkspaceService(runtime)
    svc._draft(profile_id, bundle_id, etag)
    if not key:
        raise OntologyGateBlockedError("IDEMPOTENCY_REQUIRED", "Idempotency-Key が必要です。")
    job_id = stable_ontology_id("ontology_validation", profile_id, who, key)
    payload = {
        "bundle_id": bundle_id,
        "etag": etag,
        "actor": who,
        "request": request.model_dump(mode="json"),
    }
    fingerprint = definition_fingerprint(payload)
    existing = runtime.store.get_job(job_id)
    if existing is not None:
        if existing.get("request_hash") != fingerprint:
            raise OntologyVersionConflictError(
                "IDEMPOTENCY_KEY_REUSED", "同じキーを別の検証に使用できません。"
            )
        return dict(existing)
    job = dict(
        runtime.store.save_job(
            {
                "job_id": job_id,
                "job_type": "definition_validation",
                "profile_id": profile_id,
                "status": "queued",
                "payload": payload,
                "request_hash": fingerprint,
            }
        )
    )
    if get_settings().nl2sql_ontology_worker_mode == "inprocess":
        threading.Thread(target=run_validation_job, args=(runtime, job_id), daemon=True).start()
    return job


def run_validation_job(runtime: Any, job_id: str) -> None:
    job = runtime.store.get_job(job_id)
    if (
        job is None
        or job.get("job_type") != "definition_validation"
        or job.get("status") in {"succeeded", "failed", "cancelled"}
    ):
        return
    try:
        job = runtime.store.save_job(
            {**job, "status": "running", "claimed_at": time.time()}, expected_etag=job["etag"]
        )
        payload = job["payload"]
        actor = None
        if get_settings().app_auth_enabled and not get_settings().local_debug_enabled:
            from app.security.service import get_security_service

            actor = get_security_service().principal_for_worker(payload["actor"])
        authorize_definition_operation(job["profile_id"], actor, SQL_EXECUTE_PERMISSION)
        svc = ProfileOntologyWorkspaceService(runtime)
        bundle = svc._draft(job["profile_id"], payload["bundle_id"], payload["etag"])
        with actor_scope(payload["actor"], is_system_admin=bool(actor and actor.is_system_admin)):
            report = check_data(
                runtime, bundle, DefinitionDataValidationRequest.model_validate(payload["request"])
            )
        # Validate scope again after the actual queries; concurrent edits invalidate this report.
        if bundle.profile_fingerprint != definition_fingerprint(
            runtime._strict_profile(bundle.profile_id).model_dump(mode="json")
        ):
            raise OntologyVersionConflictError(
                "VALIDATION_SCOPE_CHANGED", "検証中に Profile が変更されました。"
            )
        if bundle.schema_context_fingerprint != definition_fingerprint(
            str(runtime.prepare_build_schema_context(bundle.profile_id).schema_context)
        ):
            raise OntologyVersionConflictError(
                "VALIDATION_SCOPE_CHANGED", "検証中に Schema が変更されました。"
            )
        if not bundle.validation_report:
            raise OntologyGateBlockedError(
                "STATIC_VALIDATION_REQUIRED", "定義の検証を先に実行してください。"
            )
        bundle.validation_report["data_validation"] = report
        bundle.validation_report["errors"] = (
            sum(f.severity == "error" for f in bundle.findings) + report["errors"]
        )
        svc._commit(bundle, payload["etag"])
        current = runtime.store.get_job(job_id)
        runtime.store.save_job(
            {**current, "status": "succeeded", "report": report}, expected_etag=current["etag"]
        )
    except Exception as exc:
        current = runtime.store.get_job(job_id)
        if current is not None:
            runtime.store.save_job(
                {
                    **current,
                    "status": "failed",
                    "error_message_ja": getattr(exc, "message_ja", "実データ検証に失敗しました。"),
                    "error_code": getattr(exc, "code", type(exc).__name__),
                },
                expected_etag=current["etag"],
            )


def read_validation_job(runtime: Any, profile_id: str, job_id: str) -> dict[str, Any]:
    runtime.ensure_profile(profile_id)
    job = runtime.store.get_job(job_id)
    if (
        job is None
        or job.get("profile_id") != profile_id
        or job.get("job_type") != "definition_validation"
    ):
        raise OntologyNotFoundError("VALIDATION_JOB_NOT_FOUND", "検証 job が見つかりません。")
    return dict(job)
