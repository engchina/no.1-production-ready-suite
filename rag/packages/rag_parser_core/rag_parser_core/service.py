"""parser マイクロサービス共通の FastAPI app factory。

各 parser サービス(docling/marker/unstructured/mineru/dots_ocr)は本 factory を
使って同一の HTTP 契約(`POST /parse` / `GET /health`)を公開する。fastapi は
optional extra `service` でのみ要求し、core の依存(pydantic + charset-normalizer)は
軽量に保つ。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from pydantic import ValidationError

from rag_parser_core.registry import ParserRegistryResult, run_external_adapter
from rag_parser_core.result import ParseHealth, ParseResponse
from rag_parser_core.source import SourceProfile

# service 系 backend(OCI クラウド)の /parse ハンドラ。bytes + content_type +
# source_profile + document_id を受け取り ParseResponse(StructuredExtraction wire 形式)
# を返す。document_id は OCI 入力 object 名の一意化などに使う。
ServiceParseHandler = Callable[
    [bytes, str, "SourceProfile | None", str], Awaitable[ParseResponse]
]


def _detect_version(
    import_name: str,
    distribution_names: Sequence[str],
) -> tuple[bool, str | None]:
    """import 可否と配布 package version を返す(readiness 表示用)。"""
    installed = importlib.util.find_spec(import_name) is not None
    for distribution_name in distribution_names:
        try:
            return installed, importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return installed, None


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


def create_parse_app(
    *,
    backend: str,
    import_name: str,
    distribution_names: Sequence[str] = (),
    title: str | None = None,
) -> FastAPI:
    """1 parser サービス用の FastAPI app を生成する。

    backend: adapter 名(docling/marker/unstructured/mineru/dots_ocr)。
    import_name / distribution_names: readiness 表示の version 検出に使う。
    """
    app = FastAPI(title=title or f"parser-{backend}")

    @app.get("/health", response_model=ParseHealth)
    def health() -> ParseHealth:
        installed, version = _detect_version(import_name, distribution_names)
        return ParseHealth(
            status="ok" if installed else "degraded",
            backend=backend,
            package_name=import_name,
            package_version=version,
        )

    # readiness は HEAD/GET いずれでも疎通確認できるよう alias を用意。
    @app.get("/api/ready", response_model=ParseHealth)
    def ready() -> ParseHealth:
        return health()

    @app.post("/parse", response_model=ParseResponse)
    async def parse(
        file: Annotated[UploadFile, File()],
        content_type: Annotated[str, Form()] = "",
        source_profile: Annotated[str | None, Form()] = None,
    ) -> ParseResponse:
        source_bytes = await file.read()
        profile = _parse_source_profile(source_profile)
        effective_content_type = content_type or (
            profile.content_type if profile is not None else ""
        )
        result: ParserRegistryResult = run_external_adapter(
            backend,
            source_bytes,
            profile,
            effective_content_type,
        )
        return ParseResponse.from_result(result)

    return app


def create_service_parse_app(
    *,
    backend: str,
    parse: ServiceParseHandler,
    is_configured: Callable[[], bool] | None = None,
    title: str | None = None,
) -> FastAPI:
    """OCI クラウド service 系 backend 用の FastAPI app を生成する。

    package readiness ではなく `is_configured`(OCI 設定の充足)で /health を返す。
    `parse` は OCI を呼んで ParseResponse を返す非同期ハンドラ(env 由来 config で構築)。
    """
    app = FastAPI(title=title or f"parser-{backend}")

    @app.get("/health", response_model=ParseHealth)
    def health() -> ParseHealth:
        configured = is_configured() if is_configured is not None else True
        return ParseHealth(
            status="ok" if configured else "degraded",
            backend=backend,
            package_name=None,
            package_version=None,
        )

    @app.get("/api/ready", response_model=ParseHealth)
    def ready() -> ParseHealth:
        return health()

    @app.post("/parse", response_model=ParseResponse)
    async def parse_endpoint(
        file: Annotated[UploadFile, File()],
        content_type: Annotated[str, Form()] = "",
        source_profile: Annotated[str | None, Form()] = None,
        document_id: Annotated[str | None, Form()] = None,
    ) -> ParseResponse:
        source_bytes = await file.read()
        profile = _parse_source_profile(source_profile)
        effective_content_type = content_type or (
            profile.content_type if profile is not None else ""
        )
        effective_document_id = (document_id or "").strip() or (
            profile.content_sha256 if profile is not None else ""
        ) or "document"
        return await parse(source_bytes, effective_content_type, profile, effective_document_id)

    return app
