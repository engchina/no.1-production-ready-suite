"""OCI AI Speech クライアントのテスト(SDK 非依存に挙動を検証)。"""

from __future__ import annotations

from typing import Any

from app.clients.oci_speech import (
    OciSpeechClient,
    _segments_from_tokens,
    speech_result_to_extraction,
)
from app.config import Settings


def _blank_settings(**overrides: Any) -> Settings:
    """.env を無視して空設定から組む(is_configured 判定をローカル環境非依存にする)。"""
    base: dict[str, Any] = {
        "oci_speech_compartment_id": "",
        "oci_compartment_id": "",
        "oci_speech_namespace": "",
        "object_storage_namespace": "",
        "oci_speech_input_bucket": "",
        "object_storage_bucket": "",
    }
    base.update(overrides)
    return Settings(**base)


def test_is_configured_requires_compartment_namespace_bucket() -> None:
    assert OciSpeechClient(_blank_settings()).is_configured() is False
    configured = OciSpeechClient(
        _blank_settings(
            oci_speech_compartment_id="ocid1.compartment",
            oci_speech_namespace="ns",
            oci_speech_input_bucket="bkt",
        )
    )
    assert configured.is_configured() is True


def test_falls_back_to_generic_object_storage_settings() -> None:
    client = OciSpeechClient(
        _blank_settings(
            oci_compartment_id="ocid1.compartment",
            object_storage_namespace="ns",
            object_storage_bucket="bkt",
        )
    )
    assert client.is_configured() is True


def test_speech_result_to_extraction_remap() -> None:
    result = {
        "transcriptions": [
            {
                "transcription": "こんにちは 世界 です",
                "tokens": [
                    {"token": "こんにちは", "startTimeInMs": 0, "endTimeInMs": 800},
                    {"token": "世界", "startTimeInMs": 800, "endTimeInMs": 1500},
                    {"token": "です", "startTimeInMs": 1500, "endTimeInMs": 2000},
                ],
            }
        ]
    }
    payload = speech_result_to_extraction(result, language="ja")
    assert payload["document_type"] == "文字起こし"
    assert "こんにちは" in payload["raw_text"]
    assert payload["parser_artifacts"]["asr_backend"] == "oci_speech"
    assert payload["parser_artifacts"]["asr_language"] == "ja"


def test_segments_from_tokens_windows() -> None:
    tokens = [
        {"token": f"w{i}", "startTimeInMs": i * 100, "endTimeInMs": (i + 1) * 100}
        for i in range(60)
    ]
    segments = _segments_from_tokens(tokens, window=25)
    # 60 語 / 窓 25 → 3 segment(25 + 25 + 10)。
    assert len(segments) == 3
    assert segments[0].start == 0.0
    assert segments[0].end is not None


def test_speech_result_empty_transcriptions_is_safe() -> None:
    payload = speech_result_to_extraction({"transcriptions": []}, language="ja")
    assert payload["raw_text"] == ""
    assert payload["elements"] == []
