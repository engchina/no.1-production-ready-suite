"""ASR(音声文字起こし)parser マイクロサービス。

audio/video を faster-whisper(GPU)で転写し、共有 contract(`rag_parser_core`)の
`StructuredExtraction` / `ParseResponse` を返す。他 parser と同じ HTTP 契約
(`POST /parse` / `GET /health`)を実装する独立サービスで、faster-whisper の版は単独で
upgrade でき backend / 他 parser に非干渉。

確定スタック非抵触: ローカル OSS(faster-whisper)のみ。OCI AI Speech は backend 側の
service backend(`app.clients.oci_speech`)が担い、本サービスはその fallback。
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from rag_parser_core.asr import ASR_TEMPLATE, build_transcript_extraction
from rag_parser_core.result import ParseHealth, ParseResponse

from app.transcribe import transcribe

_BACKEND = "asr"
app = FastAPI(title="parser-asr")


def _version() -> tuple[bool, str | None]:
    installed = importlib.util.find_spec("faster_whisper") is not None
    version: str | None = None
    if installed:
        try:
            from importlib.metadata import version as dist_version

            version = dist_version("faster-whisper")
        except Exception:  # noqa: BLE001 - version 取得失敗は readiness に影響させない
            version = None
    return installed, version


@app.get("/health", response_model=ParseHealth)
def health() -> ParseHealth:
    installed, version = _version()
    return ParseHealth(
        status="ok" if installed else "degraded",
        backend=_BACKEND,
        package_name="faster-whisper",
        package_version=version,
    )


@app.get("/api/ready", response_model=ParseHealth)
def ready() -> ParseHealth:
    return health()


@app.post("/parse", response_model=ParseResponse)
async def parse(
    file: Annotated[UploadFile, File()],
    content_type: Annotated[str, Form()] = "",
    source_profile: Annotated[str | None, Form()] = None,
) -> ParseResponse:
    _ = (content_type, source_profile)
    source_bytes = await file.read()
    suffix = _suffix_for(file.filename)
    try:
        text, segments, language = await asyncio.to_thread(
            transcribe, source_bytes, suffix=suffix
        )
    except Exception:  # noqa: BLE001 - 転写失敗は backend を fallback させる
        return ParseResponse(
            extraction=None,
            parser_backend=_BACKEND,
            parser_version="faster-whisper",
            fallback_used=True,
            template=ASR_TEMPLATE,
            unsupported_reason="asr_transcription_failed",
        )
    extraction = build_transcript_extraction(
        text=text, segments=segments, language=language, backend=_BACKEND
    )
    return ParseResponse(
        extraction=extraction,
        parser_backend=_BACKEND,
        parser_version="faster-whisper",
        template=ASR_TEMPLATE,
    )


def _suffix_for(filename: str | None) -> str:
    """一時ファイルの拡張子(ffmpeg/whisper のデコード判定用)。"""
    if filename and "." in filename:
        return f".{filename.rsplit('.', 1)[-1].lower()[:8]}"
    return ".bin"
