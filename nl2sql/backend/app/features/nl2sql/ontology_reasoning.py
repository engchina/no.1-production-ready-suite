"""OWL 2 RL materialization と SHACL Core gate を含む Ontology publish worker。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import uuid4

from app.settings import get_settings

from .ontology_models import (
    OntologyPublishJob,
    OntologyPublishStatus,
    OntologyReasoningStatus,
    OntologyRevisionStatus,
    utc_now,
)
from .ontology_observability import (
    observe_stage,
    record_job,
    record_reasoning_triples,
    record_shacl_validation,
)
from .ontology_semantics import (
    ONTOLOGY_RENDERER_VERSION,
    ShaclValidationResult,
    build_semantic_artifacts,
    materialize_local_owl2rl,
    revision_graph_names,
    validate_shacl_core,
)
from .ontology_store import OntologyStore, canonical_json

logger = logging.getLogger(__name__)


class Owl2RlMaterializer(Protocol):
    def materialize(
        self,
        *,
        asserted_turtle: str,
        rdf_graph_name: str,
        inferred_graph_name: str,
    ) -> str:
        """Materialize and return a Turtle representation of the closure."""


class LocalOwl2RlMaterializer:
    def materialize(
        self,
        *,
        asserted_turtle: str,
        rdf_graph_name: str,
        inferred_graph_name: str,
    ) -> str:
        del rdf_graph_name, inferred_graph_name
        return materialize_local_owl2rl(asserted_turtle)


class OracleOwl2RlMaterializer:
    """Oracle RDF network へ asserted triples を登録し、OWL2RL rules index を作成する。"""

    def __init__(
        self,
        connection_factory: Callable[[], AbstractContextManager[Any]],
        *,
        network_owner: str = "",
        network_name: str = "",
    ) -> None:
        self._connection_factory = connection_factory
        self._network_owner = network_owner or None
        self._network_name = network_name or None

    def materialize(
        self,
        *,
        asserted_turtle: str,
        rdf_graph_name: str,
        inferred_graph_name: str,
    ) -> str:
        from rdflib import Graph

        graph = Graph()
        graph.parse(data=asserted_turtle, format="turtle")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                BEGIN
                  SEM_APIS.CREATE_RDF_GRAPH(
                    rdf_graph_name => :rdf_graph_name,
                    table_name => 'NL2SQL_ONTOLOGY_RDF_DATA',
                    column_name => 'TRIPLE',
                    network_owner => :network_owner,
                    network_name => :network_name
                  );
                END;
                """,
                {
                    "rdf_graph_name": rdf_graph_name,
                    "network_owner": self._network_owner,
                    "network_name": self._network_name,
                },
            )
            insert_sql = """
                INSERT INTO NL2SQL_ONTOLOGY_RDF_DATA (ID, TRIPLE)
                VALUES (
                  NL2SQL_ONTOLOGY_RDF_SEQ.NEXTVAL,
                  SDO_RDF_TRIPLE_S(:rdf_graph_name, :subject, :predicate, :object)
                )
            """
            rows = [
                {
                    "rdf_graph_name": rdf_graph_name,
                    "subject": subject.n3(),
                    "predicate": predicate.n3(),
                    "object": object_.n3(),
                }
                for subject, predicate, object_ in graph
            ]
            if rows:
                cursor.executemany(insert_sql, rows)
            cursor.execute(
                """
                BEGIN
                  SEM_APIS.CREATE_INFERRED_GRAPH(
                    inferred_graph_name => :inferred_graph_name,
                    rdf_graphs_in => SEM_MODELS(:rdf_graph_name),
                    rulebases_in => SEM_RULEBASES('OWL2RL'),
                    passes => SEM_APIS.REACH_CLOSURE,
                    options => 'USER_RULES=F,PROOF=F',
                    network_owner => :network_owner,
                    network_name => :network_name
                  );
                END;
                """,
                {
                    "inferred_graph_name": inferred_graph_name,
                    "rdf_graph_name": rdf_graph_name,
                    "network_owner": self._network_owner,
                    "network_name": self._network_name,
                },
            )
            connection.commit()
        # SHACL は Oracle へ登録したものと同じ asserted graph の標準 OWL2RL closure で行う。
        return materialize_local_owl2rl(asserted_turtle)


class OntologyPublishService:
    def __init__(
        self,
        runtime: Any,
        *,
        materializer: Owl2RlMaterializer | None = None,
    ) -> None:
        self.runtime = runtime
        self.store: OntologyStore = runtime.store
        self._materializer = materializer or self._default_materializer()
        self._jobs: dict[str, OntologyPublishJob] = {}
        self._lock = threading.RLock()

    def _default_materializer(self) -> Owl2RlMaterializer:
        settings = get_settings()
        if settings.nl2sql_ontology_reasoning_profile.strip().lower() != "owl2rl":
            raise RuntimeError("Ontology 推論 profile は OWL 2 RL だけを指定できます。")
        if getattr(self.runtime.store, "mode", "memory") != "oracle":
            return LocalOwl2RlMaterializer()
        adapter = getattr(self.runtime.legacy_service, "_oracle_adapter", None)
        connection_factory = getattr(adapter, "connection", None)
        if not callable(connection_factory):
            raise RuntimeError("Oracle OWL2RL materialization 用 connection factory がありません。")
        return OracleOwl2RlMaterializer(
            connection_factory,
            network_owner=settings.nl2sql_ontology_rdf_network_owner,
            network_name=settings.nl2sql_ontology_rdf_network_name,
        )

    def start(
        self,
        revision_id: str,
        *,
        etag: str,
        idempotency_key: str,
    ) -> OntologyPublishJob:
        request_hash = hashlib.sha256(
            canonical_json({"revision_id": revision_id, "etag": etag}).encode("utf-8")
        ).hexdigest()
        existing = self.store.get_idempotency("publish_ontology", idempotency_key)
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise ValueError("同じ Idempotency-Key が別の公開リクエストに使用されています。")
            restored = self.get(str(existing.get("resource_id") or ""))
            if restored is not None:
                return restored
        ontology = self.runtime.ontology_revision(revision_id)
        if ontology.revision.status != OntologyRevisionStatus.DRAFT:
            raise ValueError("Draft 状態の Ontology revision だけを公開できます。")
        if ontology.revision.etag != etag:
            raise ValueError("Ontology revision が更新されています。再読込してください。")
        job = OntologyPublishJob(
            id=f"ontology_publish_{uuid4().hex}",
            revision_id=revision_id,
            requested_etag=etag,
        )
        self._save_job(job)
        self.store.save_idempotency(
            {
                "operation": "publish_ontology",
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "resource_id": job.id,
                "status": "accepted",
            }
        )
        if get_settings().nl2sql_ontology_worker_mode == "inprocess":
            threading.Thread(
                target=self._run_safely,
                args=(job.id, etag),
                daemon=True,
                name=f"{job.id}-worker",
            ).start()
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> OntologyPublishJob | None:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                return current.model_copy(deep=True)
        document = self.store.get_job(job_id)
        if document is None or document.get("job_type") != "publish":
            return None
        return OntologyPublishJob.model_validate(document["payload"])

    def _save_job(self, job: OntologyPublishJob) -> None:
        with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)
        current = self.store.get_job(job.id)
        document = {
            "job_id": job.id,
            "job_type": "publish",
            "profile_id": "-",
            "status": job.status.value,
            "payload": job.model_dump(mode="json"),
            **(
                {
                    "claimed_by": current.get("claimed_by"),
                    "claimed_at": time.time(),
                }
                if current is not None and current.get("claimed_by")
                else {}
            ),
        }
        self.store.save_job(
            document,
            expected_etag=str(current["etag"]) if current is not None else None,
        )

    def _update(self, job_id: str, **updates: Any) -> OntologyPublishJob:
        current = self.get(job_id)
        if current is None:
            raise RuntimeError("Ontology publish job が見つかりません。")
        updated = current.model_copy(update=updates, deep=True)
        self._save_job(updated)
        return updated

    def _run_safely(self, job_id: str, etag: str) -> None:
        try:
            self.run(job_id, etag=etag)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            logger.exception("Ontology publish worker failed", extra={"job_id": job_id})
            self._update(
                job_id,
                status=OntologyPublishStatus.FAILED,
                error_code="ONTOLOGY_PUBLISH_FAILED",
                error_message_ja=str(exc),
                finished_at=utc_now(),
            )
            failed_job = self.get(job_id)
            if failed_job is not None:
                try:
                    self.runtime.update_reasoning_status(
                        failed_job.revision_id,
                        OntologyReasoningStatus.FAILED,
                    )
                except Exception:  # pragma: no cover - original failure remains primary
                    logger.warning("ontology_reasoning_status_update_failed", exc_info=True)
            record_job(job_type="publish", status="failed", error_code="unexpected")

    def run_persisted(self, job_id: str) -> OntologyPublishJob:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("Ontology publish job が見つかりません。")
        self._run_safely(job_id, job.requested_etag)
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("Ontology publish job の実行結果を取得できません。")
        return result

    def run(self, job_id: str, *, etag: str) -> OntologyPublishJob:
        initial_job = self.get(job_id)
        if initial_job is None:
            raise RuntimeError("Ontology publish job が見つかりません。")
        ontology = self.runtime.validate_ontology_for_publish(
            initial_job.revision_id,
            etag=etag,
        )
        job = self._update(
            job_id,
            status=OntologyPublishStatus.MATERIALIZING,
            started_at=utc_now(),
        )
        self.runtime.update_reasoning_status(
            job.revision_id,
            OntologyReasoningStatus.MATERIALIZING,
        )
        artifacts = build_semantic_artifacts(ontology)
        rdf_graph_name, inferred_graph_name = revision_graph_names(job.revision_id)
        with observe_stage("owl2rl_materialize"):
            inferred_turtle = self._materializer.materialize(
                asserted_turtle=artifacts.owl_turtle,
                rdf_graph_name=rdf_graph_name,
                inferred_graph_name=inferred_graph_name,
            )
        from rdflib import Graph

        inferred_graph = Graph().parse(data=inferred_turtle, format="turtle")
        record_reasoning_triples(len(inferred_graph))
        self._update(
            job_id,
            status=OntologyPublishStatus.VALIDATING,
            rdf_graph_name=rdf_graph_name,
            inferred_graph_name=inferred_graph_name,
        )
        self.runtime.update_reasoning_status(
            job.revision_id,
            OntologyReasoningStatus.VALIDATING,
            rdf_graph_name=rdf_graph_name,
            inferred_graph_name=inferred_graph_name,
        )
        shacl_enabled = get_settings().nl2sql_ontology_shacl_enabled
        if shacl_enabled:
            with observe_stage("shacl_core_validate"):
                validation = validate_shacl_core(
                    asserted_turtle=artifacts.owl_turtle,
                    inferred_turtle=inferred_turtle,
                    shapes_turtle=artifacts.shacl_turtle,
                )
            record_shacl_validation(conforms=validation.conforms)
        else:
            validation = ShaclValidationResult(
                conforms=True,
                report_text="SHACL Core validation is disabled for rollout.",
                report_turtle="# SHACL Core validation is disabled for rollout.\n",
            )
        artifact_values: Mapping[str, str] = {
            "ontology_owl_turtle": artifacts.owl_turtle,
            "ontology_inferred_turtle": inferred_turtle,
            "ontology_shacl_turtle": artifacts.shacl_turtle,
            "ontology_llm_markdown": artifacts.llm_markdown,
            "ontology_mermaid": artifacts.mermaid,
            "ontology_shacl_report": validation.report_turtle,
        }
        report_artifact_id = ""
        for artifact_type, content in artifact_values.items():
            artifact_id = f"ontology_artifact_{uuid4().hex}"
            self.store.save_artifact(
                {
                    "artifact_id": artifact_id,
                    "session_id": job.revision_id,
                    "artifact_type": artifact_type,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content": content,
                    "renderer_version": ONTOLOGY_RENDERER_VERSION,
                    "created_at": utc_now(),
                }
            )
            if artifact_type == "ontology_shacl_report":
                report_artifact_id = artifact_id
        if not validation.conforms:
            self._update(
                job_id,
                status=OntologyPublishStatus.FAILED,
                shacl_conforms=False,
                shacl_report_artifact_id=report_artifact_id,
                error_code="ONTOLOGY_SHACL_VIOLATION",
                error_message_ja="SHACL Core の Violation があるため公開を中止しました。",
                finished_at=utc_now(),
            )
            self.runtime.update_reasoning_status(
                job.revision_id,
                OntologyReasoningStatus.FAILED,
                rdf_graph_name=rdf_graph_name,
                inferred_graph_name=inferred_graph_name,
                shacl_report_artifact_id=report_artifact_id,
            )
            record_job(
                job_type="publish",
                status="failed",
                error_code="ONTOLOGY_SHACL_VIOLATION",
            )
            return self.get(job_id) or job
        self.runtime.finalize_semantic_publish(
            job.revision_id,
            etag=etag,
            semantic_metadata={
                "reasoning_status": OntologyReasoningStatus.READY,
                "rdf_graph_name": rdf_graph_name,
                "inferred_graph_name": inferred_graph_name,
                "shacl_report_artifact_id": report_artifact_id,
                "renderer_version": ONTOLOGY_RENDERER_VERSION,
                "artifact_hashes": {
                    **artifacts.hashes,
                    "inferred_turtle": hashlib.sha256(inferred_turtle.encode("utf-8")).hexdigest(),
                },
            },
        )
        published = self._update(
            job_id,
            status=OntologyPublishStatus.SUCCEEDED,
            rdf_graph_name=rdf_graph_name,
            inferred_graph_name=inferred_graph_name,
            shacl_conforms=True if shacl_enabled else None,
            shacl_report_artifact_id=report_artifact_id,
            warnings_ja=(
                [] if shacl_enabled else ["段階導入設定により SHACL Core 検証をスキップしました。"]
            ),
            finished_at=utc_now(),
        )
        record_job(job_type="publish", status="succeeded")
        return published
