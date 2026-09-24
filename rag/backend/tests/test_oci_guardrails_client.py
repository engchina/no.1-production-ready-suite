"""OCI Guardrails クライアントのテスト(SDK 非依存に挙動を検証)。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.clients.oci_guardrails import (
    OciGuardrailsClient,
    OciGuardrailsUnavailableError,
    _parse_results,
)
from app.config import Settings


def test_is_configured_requires_backend_and_compartment() -> None:
    # backend=local は常に未設定扱い。
    assert OciGuardrailsClient(Settings(rag_guardrail_backend="local")).is_configured() is False
    # backend=oci_guardrails でも compartment が無ければ未設定。
    assert (
        OciGuardrailsClient(
            Settings(rag_guardrail_backend="oci_guardrails", oci_compartment_id="")
        ).is_configured()
        is False
    )
    # backend + compartment が揃えば設定済み。
    assert (
        OciGuardrailsClient(
            Settings(rag_guardrail_backend="oci_guardrails", oci_compartment_id="ocid1.compartment")
        ).is_configured()
        is True
    )


def test_inspect_text_raises_safe_error_when_unconfigured() -> None:
    client = OciGuardrailsClient(Settings(rag_guardrail_backend="local"))
    with pytest.raises(OciGuardrailsUnavailableError):
        client.inspect_text("some text")


def test_inspect_text_empty_returns_none() -> None:
    client = OciGuardrailsClient(
        Settings(rag_guardrail_backend="oci_guardrails", oci_compartment_id="ocid1.compartment")
    )
    assert client.inspect_text("   ") is None


def test_parse_results_flags_injection_by_threshold() -> None:
    results = SimpleNamespace(
        content_moderation=SimpleNamespace(categories=["HATE"]),
        prompt_injection=SimpleNamespace(score=0.8, flagged_modalities=[]),
        personally_identifiable_information=[
            SimpleNamespace(label="EMAIL_ADDRESS", offset=4, length=16, text="should-not-be-kept"),
        ],
    )
    inspection = _parse_results(results, threshold=0.5)
    assert inspection.prompt_injection is True
    assert inspection.moderation_categories == ("HATE",)
    assert inspection.pii_labels == ("EMAIL_ADDRESS",)
    assert inspection.pii_spans[0].offset == 4
    assert inspection.pii_spans[0].length == 16
    assert "should-not-be-kept" not in repr(inspection)
    assert inspection.flagged is True


def test_parse_results_below_threshold_not_flagged() -> None:
    results = SimpleNamespace(
        content_moderation=SimpleNamespace(categories=[]),
        prompt_injection=SimpleNamespace(score=0.2, flagged_modalities=[]),
        personally_identifiable_information=[],
    )
    inspection = _parse_results(results, threshold=0.5)
    assert inspection.prompt_injection is False
    assert inspection.flagged is False
