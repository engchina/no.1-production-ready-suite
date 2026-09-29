"""Docling parser マイクロサービス(DocRAG 解析実装)。

rag_poc(DocRAG)の Docling 解析(読み順・段組補正、表セル補修、図内 OCR 集約)を実行し、
`StructuredExtraction` を返す。LayoutRecord 全体は parser_artifacts["docrag_layout"] に保持する。
図・画像の Vision は backend の解析後の共通の段が行う(#497)。このサービスは LLM を呼ばず、
parser_options は受け取っても使わない(旧 backend の vision_enabled などは無視する)。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from rag_parser_core.result import ParseHealth, ParseResponse, service_failure_warning
from rag_parser_core.service import _detect_version, _parse_source_profile

from app.extraction import analyze_source

_BACKEND = "docling"
logger = logging.getLogger(__name__)
app = FastAPI(title="parser-docling")


@app.get("/health", response_model=ParseHealth)
async def health() -> ParseHealth:
    installed, version = _detect_version("docling", ("docling",))
    return ParseHealth(
        status="ok" if installed else "degraded",
        backend=_BACKEND,
        package_name="docling",
        package_version=version,
    )


@app.get("/api/ready", response_model=ParseHealth)
async def ready() -> ParseHealth:
    return await health()


@app.post("/parse", response_model=ParseResponse)
async def parse(
    file: Annotated[UploadFile, File()],
    content_type: Annotated[str, Form()] = "",
    source_profile: Annotated[str | None, Form()] = None,
    parser_options: Annotated[str, Form()] = "",
) -> ParseResponse:
    source_bytes = await file.read()
    profile = _parse_source_profile(source_profile)
    effective_content_type = content_type or (profile.content_type if profile else "")
    file_name = (profile.sanitized_file_name if profile else "") or (file.filename or "")
    _ = parser_options  # 旧 backend が送る Vision の指定(#497 で廃止)は使わない。
    _, version = _detect_version("docling", ("docling",))
    try:
        extraction = await asyncio.to_thread(
            analyze_source,
            source_bytes,
            file_name=file_name,
            content_type=effective_content_type,
        )
    except Exception as exc:  # noqa: BLE001 - 縮退せず原因を上流へ返す
        logger.exception("docling parse failed")
        return ParseResponse(
            extraction=None,
            parser_backend=_BACKEND,
            parser_version=version or _BACKEND,
            fallback_used=True,
            template=f"{_BACKEND}_fallback",
            warnings=[service_failure_warning(_BACKEND, exc)],
        )
    return ParseResponse(
        extraction=extraction,
        parser_backend=_BACKEND,
        parser_version=version or _BACKEND,
        template="docling_docrag",
        warnings=list(extraction.warnings),
    )
