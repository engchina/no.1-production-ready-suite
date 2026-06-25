"""parser マイクロサービスを呼ぶ HTTP クライアント。

backend は外部 adapter(docling/marker/unstructured/unlimited_ocr/mineru/dots_ocr)を同一プロセスで
import せず、各 parser サービスへ HTTP で委譲する。サービスは `StructuredExtraction` を
返すため remap 忠実度を維持できる。接続失敗・timeout・retry 後の 5xx 時は通常 warning
付き fallback(`extraction=None`)を返す。ユーザーが明示選択した backend は fail-fast にし、
友好的なエラーで取込を止める。

`parse_with_registry(..., external_adapter_runner=client.runner)` の形で注入する。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

import httpx
from rag_parser_core.registry import ParserRegistryResult
from rag_parser_core.result import ParseResponse

from app.clients.http_retry import request_with_retry, retry_config_from_settings
from app.config import Settings
from app.schemas.document import SourceProfile

logger = logging.getLogger(__name__)

# 設定の service URL フィールド名(backend ごと)。
_SERVICE_URL_FIELDS: dict[str, str] = {
    "docling": "rag_parser_docling_service_url",
    "marker": "rag_parser_marker_service_url",
    "unstructured": "rag_parser_unstructured_service_url",
    "unlimited_ocr": "rag_parser_unlimited_ocr_service_url",
    "mineru": "rag_parser_mineru_service_url",
    "dots_ocr": "rag_parser_dots_ocr_service_url",
    "glm_ocr": "rag_parser_glm_ocr_service_url",
    "asr": "rag_parser_asr_service_url",
    # OCI クラウド service 系 backend(薄いプロキシ microservice)。
    "oci_genai_vision": "rag_parser_oci_genai_vision_service_url",
    "oci_document_understanding": "rag_parser_oci_document_understanding_service_url",
}


_SERVICE_LABELS: dict[str, str] = {
    "docling": "Docling",
    "marker": "Marker",
    "unstructured": "Unstructured",
    "unlimited_ocr": "Unlimited-OCR",
    "mineru": "MinerU",
    "dots_ocr": "Dots.OCR",
    "glm_ocr": "GLM-OCR",
    "asr": "ASR",
    "oci_genai_vision": "OCI Generative AI Vision",
    "oci_document_understanding": "OCI Document Understanding",
}


class ParserServiceUnavailableError(RuntimeError):
    """明示選択された parser サービスを利用できないため取込を止めるエラー。"""

    safe_for_user = True

    def __init__(
        self,
        backend: str,
        reason: str,
        *,
        service_url: str | None = None,
        status_code: int | None = None,
        attempts: int = 1,
        warning_code: str | None = None,
    ) -> None:
        self.backend = backend
        self.reason = reason
        self.service_url = service_url
        self.status_code = status_code
        self.attempts = attempts
        self.warning_code = warning_code
        super().__init__(
            _service_unavailable_message(
                backend,
                reason,
                service_url=service_url,
                status_code=status_code,
                attempts=attempts,
                warning_code=warning_code,
            )
        )


class ParserServiceClient:
    """設定された parser サービス群を呼ぶ同期 HTTP クライアント。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(settings.rag_parser_service_timeout_seconds)
        self._retry = retry_config_from_settings(settings)

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
        *,
        fail_fast: bool = False,
    ) -> ParserRegistryResult:
        """`ExternalAdapterRunner` 互換: 1 backend を HTTP で実行する。"""
        url = self.service_url(backend)
        if url is None:
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "unconfigured",
                    service_url=url,
                )
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
            payload = self._post_parse_json(backend, url, files=files, data=data)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.warning(
                "parser service returned error status",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "status_code": status_code,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "http_error",
                    service_url=url,
                    status_code=status_code,
                    attempts=self._retry.attempts,
                ) from exc
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except httpx.TimeoutException as exc:
            logger.warning(
                "parser service call timed out",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "timeout",
                    service_url=url,
                    attempts=self._retry.attempts,
                ) from exc
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except httpx.HTTPError as exc:
            logger.warning(
                "parser service call failed",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "unreachable",
                    service_url=url,
                    attempts=self._retry.attempts,
                ) from exc
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "parser service returned invalid JSON",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "invalid_response",
                    service_url=url,
                    attempts=self._retry.attempts,
                ) from exc
            return _fallback(backend, f"{backend}_adapter_service_invalid_response")
        try:
            result = ParseResponse.model_validate(payload).to_result()
        except ValueError as exc:
            logger.warning(
                "parser service returned invalid payload",
                extra={"parser_backend": backend, "service_url": url, "error": str(exc)},
            )
            if fail_fast:
                raise ParserServiceUnavailableError(
                    backend,
                    "invalid_response",
                    service_url=url,
                    attempts=self._retry.attempts,
                ) from exc
            return _fallback(backend, f"{backend}_adapter_service_invalid_response")
        if fail_fast and result.extraction is None:
            warning_code = result.warnings[0] if result.warnings else None
            raise ParserServiceUnavailableError(
                backend,
                _parser_result_failure_reason(result),
                service_url=url,
                attempts=self._retry.attempts,
                warning_code=warning_code,
            )
        return result

    def run_service_backend(
        self,
        backend: str,
        source_bytes: bytes,
        *,
        content_type: str,
        document_id: str,
        prompt: str = "",
    ) -> ParserRegistryResult:
        """OCI クラウド service 系 backend を microservice へ HTTP 委譲する。

        ``runner`` と異なり source_profile 不要で document_id(OCI 入力 object 名の一意化用)
        と prompt(VLM 抽出指示)を渡す。未到達/失敗/未設定時は extraction=None の fallback を
        返し、呼び出し側で既存 in-process フローへ安全に縮退させる。
        """
        url = self.service_url(backend)
        if url is None:
            return _fallback(backend, f"{backend}_adapter_service_unconfigured")
        files = {"file": ("upload", source_bytes, content_type or "application/octet-stream")}
        data = {"content_type": content_type, "document_id": document_id, "prompt": prompt}
        try:
            payload = self._post_parse_json(backend, url, files=files, data=data)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "parser service returned error status",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "status_code": exc.response.status_code,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except httpx.TimeoutException as exc:
            logger.warning(
                "parser service call timed out",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except httpx.HTTPError as exc:
            logger.warning(
                "parser service call failed",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            return _fallback(backend, f"{backend}_adapter_service_unreachable")
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "parser service returned invalid JSON",
                extra={
                    "parser_backend": backend,
                    "service_url": url,
                    "attempts": self._retry.attempts,
                    "error": str(exc),
                },
            )
            return _fallback(backend, f"{backend}_adapter_service_invalid_response")
        try:
            return ParseResponse.model_validate(payload).to_result()
        except ValueError as exc:
            logger.warning(
                "parser service returned invalid payload",
                extra={"parser_backend": backend, "service_url": url, "error": str(exc)},
            )
            return _fallback(backend, f"{backend}_adapter_service_invalid_response")

    def _post_parse_json(
        self,
        backend: str,
        url: str,
        *,
        files: Mapping[str, tuple[str, bytes, str]],
        data: Mapping[str, str],
    ) -> object:
        with httpx.Client(timeout=self._timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                f"{url}/parse",
                retry=self._retry,
                logger=logger,
                log_extra={
                    "parser_backend": backend,
                    "service_url": url,
                },
                files=files,
                data=data,
            )
            response.raise_for_status()
            return response.json()


def _fallback(backend: str, warning: str) -> ParserRegistryResult:
    return ParserRegistryResult(
        extraction=None,
        parser_backend=backend,
        parser_version="service_unavailable",
        fallback_used=True,
        template=f"{backend}_fallback",
        warnings=(warning,),
    )


def _parser_result_failure_reason(result: ParserRegistryResult) -> str:
    """HTTP 200 でも parser が extraction を返せない理由を user-facing reason へ寄せる。"""
    warnings = tuple(result.warnings)
    if any(warning.endswith("_adapter_package_missing") for warning in warnings):
        return "adapter_package_missing"
    if any(warning.endswith("_adapter_source_unsupported") for warning in warnings):
        return "adapter_source_unsupported"
    if any(warning.endswith("_adapter_feature_flag_disabled") for warning in warnings):
        return "adapter_feature_flag_disabled"
    if any(warning.endswith("_adapter_service_unconfigured") for warning in warnings):
        return "unconfigured"
    if any(warning.endswith("_adapter_service_invalid_response") for warning in warnings):
        return "invalid_response"
    if any(warning.endswith("_adapter_service_unreachable") for warning in warnings):
        return "unreachable"
    if any(warning.endswith("_adapter_failed") for warning in warnings):
        return "adapter_failed"
    if result.unsupported_reason:
        return "adapter_source_unsupported"
    return "adapter_empty_result"


def _service_unavailable_message(
    backend: str,
    reason: str,
    *,
    service_url: str | None = None,
    status_code: int | None = None,
    attempts: int = 1,
    warning_code: str | None = None,
) -> str:
    label = _SERVICE_LABELS.get(backend, backend)
    service_id = f"parser-{backend.replace('_', '-')}"
    retry_suffix = f"{attempts} 回試行しました。" if attempts > 1 else ""
    warning_suffix = f" エラーコード: {warning_code}" if warning_code else ""
    if reason == "unconfigured":
        return (
            f"選択した文書解析サービス（{label}）の接続先 URL が未設定です。"
            "システム設定で parser サービスの URL を設定してから再実行してください。"
        )
    if reason == "adapter_package_missing":
        return (
            f"選択した文書解析サービス（{label}）の実行に必要なパッケージが"
            "サービス内に見つかりません。"
            f"サービス管理画面で {service_id} のイメージ・依存関係を確認し、"
            "再ビルドまたは再起動してから再実行してください。"
            f"{warning_suffix}"
        )
    if reason == "adapter_source_unsupported":
        return (
            f"選択した文書解析サービス（{label}）はこのファイル形式を処理できません。"
            "別の解析エンジンを選ぶか、対応形式に変換してから再実行してください。"
            f"{warning_suffix}"
        )
    if reason == "adapter_feature_flag_disabled":
        return (
            f"選択した文書解析サービス（{label}）が無効になっています。"
            "ナレッジベースまたはシステム設定でこの解析エンジンを有効にしてから"
            f"再実行してください。{warning_suffix}"
        )
    if reason == "adapter_failed":
        return (
            f"選択した文書解析サービス（{label}）で解析処理が失敗しました。"
            f"サービス管理画面で {service_id} のログを確認し、原因を修正してから"
            f"再実行してください。{warning_suffix}"
        )
    if reason == "adapter_empty_result":
        return (
            f"選択した文書解析サービス（{label}）が抽出結果を返しませんでした。"
            f"サービス管理画面で {service_id} のログを確認してから再実行してください。"
            f"{warning_suffix}"
        )
    if reason == "timeout":
        suffix = f" 接続先: {service_url}" if service_url else ""
        return (
            f"選択した文書解析サービス（{label}）の応答がタイムアウトしました。"
            f"{retry_suffix}サービス管理画面で {service_id} が running か、"
            "対象ファイルの解析に時間がかかっていないか確認してください。"
            f"{suffix}"
        )
    if reason == "http_error":
        status_text = f"HTTP {status_code}" if status_code is not None else "HTTP error"
        suffix = f" 接続先: {service_url}" if service_url else ""
        return (
            f"選択した文書解析サービス（{label}）の /parse が {status_text} を返しました。"
            f"{retry_suffix}/health が OK でも、解析処理側で一時的なエラーや依存関係の"
            "初期化失敗が発生している可能性があります。"
            f"サービス管理画面で {service_id} のログを確認し、必要なら再起動してから"
            "再実行してください。"
            f"{suffix}"
        )
    if reason == "invalid_response":
        return (
            f"選択した文書解析サービス（{label}）から不正な応答を受信しました。"
            f"{retry_suffix}サービス管理画面で {service_id} の状態を確認し、"
            "サービスを再起動してから"
            "再実行してください。"
        )
    suffix = f" 接続先: {service_url}" if service_url else ""
    return (
        f"選択した文書解析サービス（{label}）に接続できません。"
        f"{retry_suffix}サービス管理画面で {service_id} を起動し、running になってから"
        "再実行してください。"
        f"{suffix}"
    )
