"""parser マイクロサービスを呼ぶ HTTP クライアント。

backend は外部 adapter(docling/marker/unstructured/mineru/dots_ocr)を同一プロセスで
import せず、各 parser サービスへ HTTP で委譲する。サービスは `StructuredExtraction` を
返すため remap 忠実度を維持できる。接続失敗・timeout・5xx 時は warning を付けた
fallback(`extraction=None`)を返し、既存の local / Enterprise AI VLM fallback へ縮退する。

`parse_with_registry(..., external_adapter_runner=client.runner)` の形で注入する。
"""

from __future__ import annotations

import json
import logging

import httpx
from rag_parser_core.registry import ParserRegistryResult
from rag_parser_core.result import ParseResponse

from app.config import Settings
from app.schemas.document import SourceProfile

logger = logging.getLogger(__name__)

# 設定の service URL フィールド名(backend ごと)。
_SERVICE_URL_FIELDS: dict[str, str] = {
    "docling": "rag_parser_docling_service_url",
    "marker": "rag_parser_marker_service_url",
    "unstructured": "rag_parser_unstructured_service_url",
    "mineru": "rag_parser_mineru_service_url",
    "dots_ocr": "rag_parser_dots_ocr_service_url",
    "glm_ocr": "rag_parser_glm_ocr_service_url",
}


class ParserServiceClient:
    """設定された parser サービス群を呼ぶ同期 HTTP クライアント。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(settings.rag_parser_service_timeout_seconds)

    def service_url(self, backend: str) -> str | None:
        field = _SERVICE_URL_FIELDS.get(backend)
        if field is None:
            return None
        # dev では catalog の dev_port から 127.0.0.1:<port> に解決する(docker 名は
        # ホストから引けないため)。prod は設定値そのまま。
        from app.services.catalog import resolve_service_base_url

        url = resolve_service_base_url(self._settings, field)
        return url or None

    def runner(
        self,
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
    ) -> ParserRegistryResult:
        """`ExternalAdapterRunner` 互換: 1 backend を HTTP で実行する。"""
        url = self.service_url(backend)
        if url is None:
            return _fallback(backend, f"{backend}_adapter_service_unconfigured")
        files = {
            "file": (
                source_profile.sanitized_file_name if source_profile is not None else "upload",
                source_bytes,
                content_type or "application/octet-stream",
            )
        }
        data = {
            "content_type": content_type,
            "source_profile": (
                source_profile.model_dump_json() if source_profile is not None else "null"
            ),
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{url}/parse", files=files, data=data)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "parser service call failed",
                extra={"parser_backend": backend, "service_url": url, "error": str(exc)},
            )
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        try:
            return ParseResponse.model_validate(payload).to_result()
        except ValueError as exc:
            logger.warning(
                "parser service returned invalid payload",
                extra={"parser_backend": backend, "service_url": url, "error": str(exc)},
            )
            return _fallback(backend, f"{backend}_adapter_service_invalid_response")


def _fallback(backend: str, warning: str) -> ParserRegistryResult:
    return ParserRegistryResult(
        extraction=None,
        parser_backend=backend,
        parser_version="service_unavailable",
        fallback_used=True,
        template=f"{backend}_fallback",
        warnings=(warning,),
    )
