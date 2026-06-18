"""前処理(Preprocess)マイクロサービス共通の FastAPI app factory。

`service.py`(parser サービス factory)と同型。前処理サービスは本 factory を使って
同一の HTTP 契約(`POST /convert` / `GET /health`)を公開する。fastapi は optional
extra `service` でのみ要求し、契約 schema(`preprocess.py`)とは分離して core の依存
(pydantic + charset-normalizer)を軽量に保つ。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import ValidationError

from rag_parser_core.preprocess import (
    DEFAULT_PREPROCESS_PROFILE,
    Converter,
    ConvertHealth,
    ConvertResponse,
    HealthProbe,
    normalize_preprocess_profile,
)
from rag_parser_core.source import SourceProfile


def _parse_source_profile(raw: str | None) -> SourceProfile | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() == "null":
        return None
    try:
        return SourceProfile.model_validate(json.loads(text))
    except (ValidationError, json.JSONDecodeError):
        return None


def create_preprocess_app(
    *,
    converter: Converter,
    health_probe: HealthProbe,
    title: str | None = None,
) -> FastAPI:
    """前処理サービス用の FastAPI app を生成する(converter 注入式)。

    converter は各サービスが重い変換依存(LibreOffice / pymupdf 等)を内包して実装し、
    本 factory は HTTP 契約(`POST /convert` / `GET /health`)だけを担う。
    """
    app = FastAPI(title=title or "preprocess")

    @app.get("/health", response_model=ConvertHealth)
    def health() -> ConvertHealth:
        return health_probe()

    @app.get("/api/ready", response_model=ConvertHealth)
    def ready() -> ConvertHealth:
        return health_probe()

    @app.post("/convert", response_model=ConvertResponse)
    async def convert(
        file: Annotated[UploadFile, File()],
        content_type: Annotated[str, Form()] = "",
        preprocess_profile: Annotated[str, Form()] = DEFAULT_PREPROCESS_PROFILE,
        source_profile: Annotated[str | None, Form()] = None,
    ) -> ConvertResponse:
        source_bytes = await file.read()
        profile = _parse_source_profile(source_profile)
        effective_content_type = content_type or (
            profile.content_type if profile is not None else ""
        )
        outcome = converter(
            source_bytes,
            effective_content_type,
            normalize_preprocess_profile(preprocess_profile),
            profile,
        )
        return ConvertResponse.from_outcome(outcome)

    return app
