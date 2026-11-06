"""失敗の型ではなく原因を分類し、例外本文は保存しない。"""

import json
import socket
import ssl

import pytest

from pr_backend_core.observability.failure_diagnosis import (
    diagnose_failure,
    read_failure_diagnosis,
)


@pytest.mark.parametrize(
    ("cause", "kind", "network_cause"),
    [
        (socket.gaierror(-2, "synthetic-secret-value"), "network", "dns"),
        (ConnectionRefusedError(111, "synthetic-secret-value"), "network", "refused"),
        (ssl.SSLCertVerificationError(1, "synthetic-secret-value"), "tls", None),
        (TimeoutError("synthetic-secret-value"), "timeout", None),
        (ValueError("synthetic-secret-value"), "unknown", None),
    ],
)
def test_wrapped_cause_is_classified_without_copying_exception_text(
    cause: Exception, kind: str, network_cause: str | None
) -> None:
    wrapped = RuntimeError("synthetic-secret-value")
    wrapped.__cause__ = cause
    result = diagnose_failure(wrapped, stage="回答の生成").model_dump(mode="json")
    assert result == {
        "kind": kind,
        "network_failure_cause": network_cause,
        "stage": "回答の生成",
    }
    assert "synthetic-secret-value" not in json.dumps(result)


def test_missing_unknown_and_invalid_saved_diagnoses_remain_distinct() -> None:
    assert read_failure_diagnosis(None) is None
    unknown = read_failure_diagnosis({"kind": "unknown"})
    assert unknown is not None and unknown.kind == "unknown"
    invalid = read_failure_diagnosis({"kind": "synthetic-secret-value"})
    assert invalid is not None and invalid.kind == "unknown"


def test_saved_diagnosis_does_not_forward_unrecognized_fields() -> None:
    saved = read_failure_diagnosis(
        {"kind": "network", "message": "synthetic-secret-value", "exception": "secret"}
    )
    assert saved is not None
    assert saved.model_dump(mode="json") == {
        "kind": "network",
        "network_failure_cause": None,
        "stage": None,
    }


def test_plain_runtime_error_does_not_infer_connection_cause() -> None:
    result = diagnose_failure(RuntimeError("synthetic-secret-value"))
    assert result.kind == "unknown"
    assert result.network_failure_cause is None
