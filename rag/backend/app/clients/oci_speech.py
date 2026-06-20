"""OCI AI Speech クライアント(音声文字起こし)。

OCI AI Speech(`oci.ai_speech`)の **非同期 transcription job** で音声/動画を文字起こしし、
共有 remap(`rag_parser_core.asr.build_transcript_extraction`)で StructuredExtraction 互換
payload を返す。入出力は Object Storage を経由する(Document Understanding と同型)。

これは確定スタックに無い **追加 OCI サービス**(LLM/VLM=Enterprise AI、embedding/rerank=
OCI GenAI、ベクトル DB=Oracle 26ai は不変)で、**ユーザ明示要望による**音声モダリティ拡張。
別 LLM provider・外部ベクトル DB は導入しない。未設定・SDK/通信失敗・job 失敗・timeout 時は
``None`` を返し、呼び出し側でローカル faster-whisper(parser-asr)へ安全に縮退する。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from rag_parser_core.asr import TranscriptSegment, build_transcript_extraction

from app.clients.oci_auth import load_oci_config_without_prompt
from app.config import Settings

logger = logging.getLogger(__name__)


class OciSpeechClient:
    """OCI AI Speech を非同期 transcription job で呼ぶクライアント。"""

    def __init__(
        self,
        settings: Settings,
        *,
        speech_client: Any | None = None,
        object_storage_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._speech_client = speech_client
        self._object_storage_client = object_storage_client

    # --- 設定解決 ---
    def _compartment_id(self) -> str:
        return (
            str(getattr(self._settings, "oci_speech_compartment_id", "") or "").strip()
            or str(getattr(self._settings, "oci_compartment_id", "") or "").strip()
        )

    def _namespace(self) -> str:
        return (
            str(getattr(self._settings, "oci_speech_namespace", "") or "").strip()
            or str(getattr(self._settings, "object_storage_namespace", "") or "").strip()
        )

    def _input_bucket(self) -> str:
        return (
            str(getattr(self._settings, "oci_speech_input_bucket", "") or "").strip()
            or str(getattr(self._settings, "object_storage_bucket", "") or "").strip()
        )

    def _output_bucket(self) -> str:
        return (
            str(getattr(self._settings, "oci_speech_output_bucket", "") or "").strip()
            or self._input_bucket()
        )

    def _language(self) -> str:
        return str(getattr(self._settings, "oci_speech_language", "") or "ja").strip() or "ja"

    def is_configured(self) -> bool:
        """job を投入できる最小設定(compartment / namespace / 入力 bucket)が揃うか。"""
        return bool(self._compartment_id() and self._namespace() and self._input_bucket())

    # --- 公開 API ---
    async def transcribe(
        self,
        source_bytes: bytes,
        *,
        content_type: str,
        document_id: str,
    ) -> dict[str, object] | None:
        """音声/動画を文字起こしし、StructuredExtraction 互換 payload を返す。

        未設定・SDK/通信失敗・job 失敗・timeout のときは ``None``。
        """
        if not self.is_configured():
            logger.info("OCI AI Speech 未設定のため縮退します。")
            return None
        object_name = self._input_object_name(document_id, content_type)
        try:
            job_id = await asyncio.to_thread(self._submit_job, source_bytes, object_name)
        except Exception as exc:  # noqa: BLE001 - 失敗時は安全に縮退する
            logger.warning("Speech job 投入に失敗しました。", extra={"error": str(exc)})
            return None
        try:
            await self._await_job(job_id)
            result = await asyncio.to_thread(self._read_result, job_id, object_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Speech job の完了待ち/結果取得に失敗しました。",
                extra={"error": str(exc), "job_id": job_id},
            )
            return None
        if result is None:
            return None
        extraction = speech_result_to_extraction(result, language=self._language())
        return extraction

    # --- SDK 解決 ---
    def _config(self) -> dict[str, Any]:
        oci_config = importlib.import_module("oci.config")
        return load_oci_config_without_prompt(
            oci_config,
            getattr(self._settings, "oci_config_file", "~/.oci/config") or "~/.oci/config",
            getattr(self._settings, "oci_config_profile", "DEFAULT") or "DEFAULT",
        )

    def _speech(self) -> Any:
        if self._speech_client is not None:
            return self._speech_client
        ai_speech = importlib.import_module("oci.ai_speech")
        self._speech_client = ai_speech.AIServiceSpeechClient(self._config())
        return self._speech_client

    def _storage(self) -> Any:
        if self._object_storage_client is not None:
            return self._object_storage_client
        object_storage = importlib.import_module("oci.object_storage")
        self._object_storage_client = object_storage.ObjectStorageClient(self._config())
        return self._object_storage_client

    # --- job ライフサイクル ---
    def _input_object_name(self, document_id: str, content_type: str) -> str:
        raw = str(getattr(self._settings, "oci_speech_input_prefix", "") or "speech/input")
        prefix = raw.strip("/")
        extension = _EXTENSION_BY_CONTENT_TYPE.get(content_type.split(";", 1)[0].strip(), "bin")
        return f"{prefix}/{document_id}.{extension}"

    def _output_prefix(self) -> str:
        raw = str(getattr(self._settings, "oci_speech_output_prefix", "") or "speech/output")
        return raw.strip("/")

    def _submit_job(self, source_bytes: bytes, object_name: str) -> str:
        """入力を put し、transcription job を作成して job id を返す(同期)。"""
        models = importlib.import_module("oci.ai_speech.models")
        namespace = self._namespace()
        self._storage().put_object(
            namespace,
            self._input_bucket(),
            object_name,
            source_bytes,
        )
        input_location = models.ObjectListInlineInputLocation(
            location_type="OBJECT_LIST_INLINE_INPUT_LOCATION",
            object_locations=[
                models.ObjectLocation(
                    namespace_name=namespace,
                    bucket_name=self._input_bucket(),
                    object_names=[object_name],
                )
            ],
        )
        output_location = models.OutputLocation(
            namespace_name=namespace,
            bucket_name=self._output_bucket(),
            prefix=self._output_prefix(),
        )
        details = models.CreateTranscriptionJobDetails(
            compartment_id=self._compartment_id(),
            input_location=input_location,
            output_location=output_location,
            model_details=models.TranscriptionModelDetails(language_code=self._language()),
        )
        job = self._speech().create_transcription_job(create_transcription_job_details=details)
        return str(job.data.id)

    async def _await_job(self, job_id: str) -> None:
        """job が SUCCEEDED になるまで poll する(失敗/timeout は例外)。"""
        poll = float(getattr(self._settings, "oci_speech_poll_interval_seconds", 5.0))
        timeout = float(getattr(self._settings, "oci_speech_timeout_seconds", 900.0))
        deadline = time.monotonic() + timeout
        while True:
            status = await asyncio.to_thread(self._job_status, job_id)
            if status == "SUCCEEDED":
                return
            if status in {"FAILED", "CANCELED"}:
                raise RuntimeError(f"speech job {status.lower()}")
            if time.monotonic() >= deadline:
                raise TimeoutError("speech job timeout")
            await asyncio.sleep(poll)

    def _job_status(self, job_id: str) -> str:
        job = self._speech().get_transcription_job(transcription_job_id=job_id)
        return str(getattr(job.data, "lifecycle_state", "") or "").upper()

    def _read_result(self, job_id: str, object_name: str) -> Mapping[str, object] | None:
        """出力 bucket の transcription JSON を読み込む(無ければ None)。"""
        _ = job_id
        namespace = self._namespace()
        bucket = self._output_bucket()
        result_object = f"{self._output_prefix()}/{namespace}_{bucket}_{object_name}.json"
        response = self._storage().get_object(namespace, bucket, result_object)
        data = getattr(getattr(response, "data", None), "content", None)
        if data is None:
            return None
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, Mapping) else None


# 入力 object の拡張子(OCI Speech のデコード判定用)。
_EXTENSION_BY_CONTENT_TYPE: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


def speech_result_to_extraction(
    result: Mapping[str, object], *, language: str | None = None
) -> dict[str, object]:
    """OCI Speech の transcription JSON を StructuredExtraction 互換 payload へ remap する。

    OCI Speech 出力は ``transcriptions[].transcription``(全文)と ``tokens[]``(語ごとの
    開始/終了 ms)を持つ。token 群を文区切りに依らず一定窓で segment 化し、共有 remap で
    文字起こし extraction を組み立てる。
    """
    transcriptions = result.get("transcriptions")
    if not isinstance(transcriptions, Sequence) or not transcriptions:
        return build_transcript_extraction(language=language).model_dump()
    first = transcriptions[0]
    full_text = ""
    segments: list[TranscriptSegment] = []
    if isinstance(first, Mapping):
        full_text = str(first.get("transcription", "") or "").strip()
        segments = _segments_from_tokens(first.get("tokens"))
    extraction = build_transcript_extraction(
        text=full_text, segments=segments, language=language, backend="oci_speech"
    )
    return extraction.model_dump()


def _segments_from_tokens(tokens: object, *, window: int = 25) -> list[TranscriptSegment]:
    """token 配列を ``window`` 語ごとの segment へまとめる(開始/終了秒付き)。"""
    if not isinstance(tokens, Sequence):
        return []
    segments: list[TranscriptSegment] = []
    words: list[str] = []
    start_ms: float | None = None
    end_ms: float | None = None
    for token in tokens:
        if not isinstance(token, Mapping):
            continue
        token_text = str(token.get("token", "") or "").strip()
        if not token_text:
            continue
        if start_ms is None:
            # 0ms を falsy 扱いしないよう明示的に None 判定する。
            start_ms = _as_float(_first_present(token, "startTimeInMs", "startTime"))
        end_ms = _as_float(_first_present(token, "endTimeInMs", "endTime"))
        words.append(token_text)
        if len(words) >= window:
            segments.append(_make_segment(words, start_ms, end_ms))
            words, start_ms, end_ms = [], None, None
    if words:
        segments.append(_make_segment(words, start_ms, end_ms))
    return segments


def _make_segment(
    words: list[str], start_ms: float | None, end_ms: float | None
) -> TranscriptSegment:
    return TranscriptSegment(
        text=" ".join(words),
        start=start_ms / 1000.0 if start_ms is not None else None,
        end=end_ms / 1000.0 if end_ms is not None else None,
    )


def _first_present(token: Mapping[str, object], *keys: str) -> object:
    """最初に存在する key の値を返す(0 も有効値として扱う)。"""
    for key in keys:
        if key in token and token[key] is not None:
            return token[key]
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
