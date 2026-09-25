from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from app.features.nl2sql.ontology_worker import OntologyWorker
from app.settings import get_settings


class _JobStore:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = {str(item["job_id"]): dict(item) for item in documents}

    def list_documents(self, _collection: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(document)
            for document in self.documents.values()
            if all(document.get(key) == value for key, value in filters.items())
        ]

    def save_job(
        self,
        document: dict[str, Any],
        *,
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        current = self.documents[str(document["job_id"])]
        if expected_etag != current["etag"]:
            raise RuntimeError("etag conflict")
        saved = {**document, "etag": f"{current['etag']}-next"}
        self.documents[str(document["job_id"])] = saved
        return dict(saved)


def _worker(documents: list[dict[str, Any]]) -> OntologyWorker:
    runtime = SimpleNamespace(store=_JobStore(documents))
    return OntologyWorker(runtime, SimpleNamespace(), SimpleNamespace())


def test_worker_reclaims_expired_claim(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_claim_timeout_seconds", 60.0)
    worker = _worker(
        [
            {
                "job_id": "stale",
                "job_type": "build",
                "status": "claimed",
                "claimed_at": time.time() - 61,
                "created_at": "2026-01-01T00:00:00Z",
                "etag": "v1",
            }
        ]
    )

    claimed = worker.claim_next()

    assert claimed is not None
    assert claimed["job_id"] == "stale"
    assert claimed["status"] == "claimed"
    assert claimed["claimed_by"] == worker.worker_id


def test_worker_does_not_steal_live_claim(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_claim_timeout_seconds", 60.0)
    worker = _worker(
        [
            {
                "job_id": "live",
                "job_type": "publish",
                "status": "claimed",
                "claimed_at": time.time(),
                "created_at": "2026-01-01T00:00:00Z",
                "etag": "v1",
            }
        ]
    )

    assert worker.claim_next() is None


def test_worker_reclaims_expired_in_flight_publish(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_claim_timeout_seconds", 60.0)
    worker = _worker(
        [
            {
                "job_id": "materializing",
                "job_type": "publish",
                "status": "materializing",
                "claimed_by": "stopped-worker",
                "claimed_at": time.time() - 61,
                "created_at": "2026-01-01T00:00:00Z",
                "etag": "v1",
            }
        ]
    )

    claimed = worker.claim_next()

    assert claimed is not None
    assert claimed["job_id"] == "materializing"
    assert claimed["claimed_by"] == worker.worker_id
