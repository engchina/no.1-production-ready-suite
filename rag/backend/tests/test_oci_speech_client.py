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


class _Obj:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class _FakeSpeech:
    def __init__(self, status: str) -> None:
        self._status = status

    def create_transcription_job(self, create_transcription_job_details: object) -> _Obj:
        return _Obj(data=_Obj(id="speech-job-1"))

    def get_transcription_job(self, transcription_job_id: str) -> _Obj:
        return _Obj(data=_Obj(lifecycle_state=self._status))


class _FakeSpeechStorage:
    def __init__(self, objects: dict[str, bytes], *, fail_delete: bool = False) -> None:
        self.objects = dict(objects)
        self.deleted: list[tuple[str, str]] = []
        self._fail_delete = fail_delete

    def put_object(self, namespace: str, bucket: str, name: str, body: bytes) -> None:
        self.objects[name] = body

    def get_object(self, namespace: str, bucket: str, name: str) -> _Obj:
        return _Obj(data=_Obj(content=self.objects[name]))

    def delete_object(self, namespace: str, bucket: str, name: str) -> None:
        if self._fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append((bucket, name))
        self.objects.pop(name, None)


def _speech_client(status: str, storage: _FakeSpeechStorage) -> OciSpeechClient:
    settings = _blank_settings(
        oci_speech_compartment_id="ocid1.compartment",
        oci_speech_namespace="ns",
        oci_speech_input_bucket="in-bkt",
        oci_speech_output_bucket="out-bkt",
        oci_speech_poll_interval_seconds=0.01,
        oci_speech_timeout_seconds=5.0,
    )
    return OciSpeechClient(
        settings, speech_client=_FakeSpeech(status), object_storage_client=storage
    )


_RESULT_KEY = "speech/output/ns_out-bkt_speech/input/doc-1.mp3.json"
_RESULT_JSON = b'{"transcriptions": [{"transcription": "x", "tokens": []}]}'


async def test_transcribe_deletes_input_and_result_objects() -> None:
    """文字起こしの後に、job の入力と結果の JSON を bucket から消す（#2440）。"""
    storage = _FakeSpeechStorage({_RESULT_KEY: _RESULT_JSON})
    payload = await _speech_client("SUCCEEDED", storage).transcribe(
        b"audio", content_type="audio/mpeg", document_id="doc-1"
    )
    assert payload is not None
    assert storage.deleted == [("in-bkt", "speech/input/doc-1.mp3"), ("out-bkt", _RESULT_KEY)]
    assert storage.objects == {}


async def test_transcribe_deletes_input_when_job_fails() -> None:
    """job が失敗しても、入力の object を消す。"""
    storage = _FakeSpeechStorage({})
    payload = await _speech_client("FAILED", storage).transcribe(
        b"audio", content_type="audio/mpeg", document_id="doc-1"
    )
    assert payload is None
    assert ("in-bkt", "speech/input/doc-1.mp3") in storage.deleted
    assert "speech/input/doc-1.mp3" not in storage.objects


async def test_transcribe_result_is_kept_when_cleanup_fails() -> None:
    """入出力を消せなくても、文字起こしの結果は返す。"""
    storage = _FakeSpeechStorage({_RESULT_KEY: _RESULT_JSON}, fail_delete=True)
    payload = await _speech_client("SUCCEEDED", storage).transcribe(
        b"audio", content_type="audio/mpeg", document_id="doc-1"
    )
    assert payload is not None
