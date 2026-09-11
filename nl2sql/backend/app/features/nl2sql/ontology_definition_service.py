"""型付き成果物の Profile 所有境界。既存 artifact store を利用し DDL を実行しない。"""

from __future__ import annotations

import hashlib
from typing import Any

from .ontology_definitions import (
    DEFINITION_KINDS,
    BusinessDefinition,
    ConceptCoverage,
    DefinitionConflict,
    ProfileOntologyBundle,
)
from .ontology_service import OntologyNotFoundError
from .ontology_store import canonical_json, stable_ontology_id


def definition_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class ProfileOntologyDefinitionService:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.store = runtime.store

    def _session(self, profile_id: str) -> str:
        self.runtime.ensure_profile(profile_id)
        return f"profile-ontology:{profile_id}"

    def list_results(self, profile_id: str) -> list[ProfileOntologyBundle]:
        documents = self.store.list_artifacts(self._session(profile_id))
        bundles = [
            ProfileOntologyBundle.model_validate_json(document["content"])
            for document in documents
            if document.get("artifact_type") == "profile_ontology_bundle_v2"
            and document.get("profile_id") == profile_id
        ]
        return sorted(bundles, key=lambda item: (item.created_at, item.id), reverse=True)

    def get(self, profile_id: str, bundle_id: str) -> ProfileOntologyBundle:
        session = self._session(profile_id)
        document = self.store.get_artifact(bundle_id)
        if (
            document is None
            or document.get("session_id") != session
            or document.get("profile_id") != profile_id
            or document.get("artifact_type") != "profile_ontology_bundle_v2"
        ):
            raise OntologyNotFoundError("ONTOLOGY_RESULT_NOT_FOUND", "構築結果が見つかりません。")
        return ProfileOntologyBundle.model_validate_json(document["content"])

    def save_build(
        self,
        *,
        profile_id: str,
        job_id: str,
        definitions: list[BusinessDefinition],
        schema_fingerprint: str,
        source_revision_id: str,
        coverage: list[ConceptCoverage] | None = None,
    ) -> ProfileOntologyBundle:
        session = self._session(profile_id)
        bundle_id = stable_ontology_id("profile_ontology_bundle", profile_id, job_id)
        existing = self.store.get_artifact(bundle_id)
        if existing is not None:
            return self.get(profile_id, bundle_id)
        previous = self.list_results(profile_id)
        prior = previous[0] if previous else None
        old = {item.id: item for item in prior.definitions} if prior else {}
        normalized: dict[str, BusinessDefinition] = {}
        conflicts: list[DefinitionConflict] = []
        for definition in definitions:
            item = definition.model_copy(deep=True)
            item.id = stable_ontology_id("profile_concept", profile_id, item.kind, item.api_name)
            item.review_status = "unreviewed"
            item.origin = "ai"
            for evidence in item.evidence:
                evidence.verified = False
            current = old.get(item.id) or normalized.get(item.id)
            if (
                current is not None
                and (
                    current.origin == "manual"
                    or current.review_status == "reviewed"
                    or item.id in normalized
                )
                and current.model_dump(exclude={"review_status", "origin"})
                != item.model_dump(exclude={"review_status", "origin"})
            ):
                conflicts.append(
                    DefinitionConflict(
                        definition_id=item.id,
                        current=current,
                        proposed=item,
                    )
                )
                normalized[item.id] = current
                continue
            normalized[item.id] = item
        # 新しい抽出で言及されなかった人手編集は削除しない。
        for item_id, item in old.items():
            if item_id not in normalized and (
                item.origin == "manual" or item.review_status == "reviewed"
            ):
                normalized[item_id] = item
        counts = {
            kind: sum(item.kind == kind for item in normalized.values())
            for kind in DEFINITION_KINDS
        }
        declared = {item.kind: item for item in coverage or []}
        final_coverage = [
            (
                ConceptCoverage(kind=kind, status="generated", count=counts[kind])
                if counts[kind]
                else ConceptCoverage(
                    kind=kind,
                    status=(
                        declared[kind].status
                        if kind in declared and declared[kind].status != "generated"
                        else "insufficient_evidence"
                    ),
                    reason_ja=(
                        declared[kind].reason_ja
                        if kind in declared
                        else "定義の根拠となる資料が不足しています。"
                    ),
                )
            )
            for kind in DEFINITION_KINDS
        ]
        profile = self.runtime._strict_profile(profile_id)
        bundle = ProfileOntologyBundle(
            id=bundle_id,
            profile_id=profile_id,
            job_id=job_id,
            schema_fingerprint=schema_fingerprint,
            profile_fingerprint=definition_fingerprint(profile.model_dump(mode="json")),
            source_revision_id=source_revision_id,
            parent_id=prior.id if prior else "",
            definitions=list(normalized.values()),
            coverage=final_coverage,
            conflicts=conflicts,
        )
        bundle.etag = definition_fingerprint(bundle.model_dump(mode="json", exclude={"etag"}))
        self.store.save_artifact(
            {
                "artifact_id": bundle.id,
                "session_id": session,
                "profile_id": profile_id,
                "artifact_type": "profile_ontology_bundle_v2",
                "content_hash": bundle.etag,
                "content": bundle.model_dump_json(),
            }
        )
        return bundle
