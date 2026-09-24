"""前処理(Preprocess)ステージのクライアント。

parse の **前** に原本を一度だけ canonical な中間物へ変換する(`先变换、再 parse`)。

- `passthrough`(既定)は変換せず原本のまま parse する(no-op)。
- サービス必須の変換(`office_to_pdf` / `pdf_to_page_images` / `csv_to_json` / `excel_to_json` /
  `url_to_markdown` / `image_enhance` / `pii_redact`)は **各々独立した**前処理マイクロサービスへ
  HTTP 委譲する(profile ごとに専用 base URL)。in-process 変換・local fallback は持たない。
  サービス無効・未達・timeout・5xx 時は、選択した前処理を別経路へ黙って縮退せず、
  利用者向けエラーとして呼び出し側へ伝える。

戻り値は `rag_parser_core.ConvertOutcome`。Object Storage 保存後の `SourceDerivation`
(派生系譜)の確定は呼び出し側(ingestion)が行う。
"""

from __future__ import annotations

import json
import logging

import httpx
from rag_parser_core.preprocess import ConvertOutcome, ConvertResponse, normalize_preprocess_profile

from app.clients.http_retry import request_with_retry, retry_config_from_settings
from app.config import Settings
from app.rag.preprocess_strategy import preprocess_service_url
from app.schemas.document import SourceProfile

logger = logging.getLogger(__name__)


class PreprocessServiceClient:
    """前処理を実行するクライアント(全変換を前処理マイクロサービスへ委譲)。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(getattr(settings, "rag_preprocess_service_timeout_seconds", 300.0))
        self._retry = retry_config_from_settings(settings)

    def convert(
        self,
        source_bytes: bytes,
        *,
        content_type: str,
        source_profile: SourceProfile | None = None,
        profile: str | None = None,
    ) -> ConvertOutcome:
        """選択プリセットで原本を変換する。選択したサービス処理の失敗は例外にする。"""
        resolved = normalize_preprocess_profile(
            profile
            if profile is not None
            else getattr(self._settings, "rag_preprocess_profile", "passthrough")
        )
        if resolved == "passthrough":
            # 廃止済み text_normalize は normalize_preprocess_profile で passthrough へ寄る。
            return ConvertOutcome.passthrough()
        # office_to_pdf / pdf_to_page_images / csv_to_json / excel_to_json / url_to_markdown /
        # image_enhance / pii_redact: 各専用サービスへ委譲。
        return self._convert_via_service(
            resolved,
            source_bytes,
            content_type=content_type,
            source_profile=source_profile,
        )

    def _convert_via_service(
        self,
        profile: str,
        source_bytes: bytes,
        *,
        content_type: str,
        source_profile: SourceProfile | None,
    ) -> ConvertOutcome:
        if not getattr(self._settings, "rag_preprocess_enabled", False):
            raise PreprocessServiceError(profile, "disabled")
        url = preprocess_service_url(self._settings, profile)  # type: ignore[arg-type]
        if url is None:
            raise PreprocessServiceError(profile, "unconfigured")
        files = {
            "file": (
                source_profile.sanitized_file_name if source_profile is not None else "upload",
                source_bytes,
                content_type or "application/octet-stream",
            )
        }
        data = {
            "content_type": content_type,
            "preprocess_profile": profile,
            "source_profile": (
                source_profile.model_dump_json() if source_profile is not None else "null"
            ),
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = request_with_retry(
                    client,
                    "POST",
                    f"{url}/convert",
                    retry=self._retry,
                    logger=logger,
                    log_extra={
                        "preprocess_profile": profile,
                        "service_url": url,
                    },
                    files=files,
                    data=data,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "preprocess service call failed",
                extra={"preprocess_profile": profile, "service_url": url, "error": str(exc)},
            )
            raise PreprocessServiceError(profile, "unreachable", service_url=url) from exc
        try:
            convert_response = ConvertResponse.model_validate(payload)
        except ValueError as exc:
            logger.warning(
                "preprocess service returned invalid payload",
                extra={"preprocess_profile": profile, "service_url": url, "error": str(exc)},
            )
            raise PreprocessServiceError(profile, "invalid_response", service_url=url) from exc
        derived = convert_response.derived_bytes()
        if not convert_response.converted or derived is None:
            raise PreprocessServiceError(profile, "no_conversion", service_url=url)
        return ConvertOutcome(
            converted=True,
            converter_name=convert_response.converter_name,
            converter_version=convert_response.converter_version,
            derived_bytes=derived,
            derived_content_type=convert_response.derived_content_type,
            page_map=dict(convert_response.page_map),
            warnings=tuple(convert_response.warnings),
        )


class PreprocessServiceError(RuntimeError):
    """選択した前処理サービスを実行できないため取込を止めるエラー。"""

    safe_for_user = True

    def __init__(
        self,
        profile: str,
        reason: str,
        *,
        service_url: str | None = None,
    ) -> None:
        self.profile = profile
        self.reason = reason
        self.service_url = service_url
        super().__init__(_preprocess_error_message(profile, reason, service_url=service_url))


def _preprocess_error_message(
    profile: str,
    reason: str,
    *,
    service_url: str | None = None,
) -> str:
    label = profile.replace("_", " ")
    service_id = f"preprocess-{profile.replace('_', '-')}"
    suffix = f" 接続先: {service_url}" if service_url else ""
    if reason == "disabled":
        return (
            f"選択した前処理（{label}）を実行できません。"
            "前処理サービスが無効です。システム設定で前処理サービスを有効にしてから"
            "再実行してください。"
        )
    if reason == "unconfigured":
        return (
            f"選択した前処理（{label}）の接続先 URL が未設定です。"
            "システム設定で前処理サービスの URL を設定してから再実行してください。"
        )
    if reason == "invalid_response":
        return (
            f"選択した前処理（{label}）から不正な応答を受信しました。"
            f"サービス管理画面で {service_id} のログを確認し、修正してから"
            f"再実行してください。{suffix}"
        )
    if reason == "no_conversion":
        return (
            f"選択した前処理（{label}）が変換結果を返しませんでした。"
            "別経路には切り替えずに取込を停止しました。"
            f"サービス管理画面で {service_id} のログを確認してから再実行してください。"
            f"{suffix}"
        )
    return (
        f"選択した前処理（{label}）サービスに接続できないか、処理に失敗しました。"
        "別経路には切り替えずに取込を停止しました。"
        f"サービス管理画面で {service_id} の状態とログを確認してから再実行してください。"
        f"{suffix}"
    )
