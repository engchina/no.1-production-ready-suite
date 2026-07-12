"""低カーディナリティの Ontology query observability helpers。"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

ONTOLOGY_SESSION_TRANSITIONS = Counter(
    "nl2sql_ontology_session_transitions_total",
    "Ontology query session state transitions.",
    ("state",),
)
ONTOLOGY_VALIDATION_FINDINGS = Counter(
    "nl2sql_ontology_validation_findings_total",
    "Ontology validation findings by severity and code.",
    ("severity", "code"),
)
ONTOLOGY_STAGE_SECONDS = Histogram(
    "nl2sql_ontology_stage_duration_seconds",
    "Ontology query stage duration.",
    ("stage",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)


def record_transition(*, session_id: str, revision_id: str, state: str) -> None:
    """状態遷移を記録する。質問や filter 値はログへ含めない。"""

    ONTOLOGY_SESSION_TRANSITIONS.labels(state=state).inc()
    logger.info(
        "NL2SQL Ontology session transitioned",
        extra={
            "session_id": session_id,
            "ontology_revision_id": revision_id,
            "ontology_state": state,
        },
    )


def record_findings(findings: list[object]) -> None:
    for finding in findings:
        severity = str(getattr(finding, "severity", "unknown"))
        code = str(getattr(finding, "code", "unknown"))[:64]
        ONTOLOGY_VALIDATION_FINDINGS.labels(severity=severity, code=code).inc()


@contextmanager
def observe_stage(stage: str) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        ONTOLOGY_STAGE_SECONDS.labels(stage=stage).observe(time.monotonic() - started)
