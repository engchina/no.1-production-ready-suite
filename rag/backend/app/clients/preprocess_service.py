"""前処理(Preprocess)ステージのクライアント。

parse の **前** に原本を一度だけ canonical な中間物へ変換する(`先变换、再 parse`)。

- 軽量な `text_normalize`(文字コード/Unicode/空白の正規化)は backend in-process で実行する。
- サービス必須の変換(`office_to_pdf` / `pdf_to_page_images` / `csv_to_json` / `excel_to_json`)は
  **各々独立した**前処理マイクロサービスへ HTTP 委譲する(profile ごとに専用 base URL)。
  サービス無効・未達・timeout・5xx 時は warning を付けて
  **passthrough(原本そのまま parse)** へ縮退する(parser サービスと同じ縮退規約)。

戻り値は `rag_parser_core.ConvertOutcome`。Object Storage 保存後の `SourceDerivation`
(派生系譜)の確定は呼び出し側(ingestion)が行う。
"""

from __future__ import annotations

import json
import logging
import unicodedata

import httpx
from rag_parser_core.preprocess import ConvertOutcome, ConvertResponse, normalize_preprocess_profile

from app.config import Settings
from app.rag.preprocess_strategy import preprocess_service_url
from app.schemas.document import SourceModality, SourceProfile

logger = logging.getLogger(__name__)

# in-process で text_normalize の対象にする modality。
_TEXT_LIKE_MODALITIES = frozenset(
    {SourceModality.TEXT, SourceModality.HTML, SourceModality.EMAIL}
)


def _is_text_like(content_type: str, source_profile: SourceProfile | None) -> bool:
    """text_normalize を in-process で適用できる入力かどうか。"""
    if source_profile is not None:
        return source_profile.modality in _TEXT_LIKE_MODALITIES
    normalized = (content_type or "").strip().casefold()
    return normalized.startswith("text/") or normalized.startswith("message/")


def normalize_text_bytes(source_bytes: bytes, content_type: str) -> tuple[bytes, list[str]]:
    """テキスト原本を UTF-8 + Unicode NFKC + 改行/空白正規化した bytes へ変換する。

    文字コード判定は charset-normalizer(rag_parser_core の既存依存)を使い、失敗時は
    utf-8(置換)で復号する。決定論で外部サービス不要。
    """
    warnings: list[str] = []
    text: str | None = None
    # 大半は UTF-8。まず厳密 UTF-8 を試し(短文での誤判定を避ける)、失敗時のみ推定する。
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            from charset_normalizer import from_bytes

            best = from_bytes(source_bytes).best()
            if best is not None:
                text = str(best)
        except Exception:  # noqa: BLE001 - 判定失敗は utf-8(置換)へ縮退する
            text = None
        if text is None:
            text = source_bytes.decode("utf-8", errors="replace")
        warnings.append("text_normalize_charset_fallback")
    # Unicode 互換正規化(全角英数・互換文字をそろえる)。
    text = unicodedata.normalize("NFKC", text)
    # 改行をそろえ、行末空白を除去し、3 連以上の空行を 2 行に圧縮する。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized_lines: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            normalized_lines.append(line)
        else:
            blank_run += 1
            if blank_run <= 2:
                normalized_lines.append(line)
    normalized = "\n".join(normalized_lines).strip("\n")
    return normalized.encode("utf-8"), warnings


class PreprocessServiceClient:
    """前処理を実行するクライアント(in-process + マイクロサービス委譲)。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(getattr(settings, "rag_preprocess_service_timeout_seconds", 300.0))

    def convert(
        self,
        source_bytes: bytes,
        *,
        content_type: str,
        source_profile: SourceProfile | None = None,
        profile: str | None = None,
    ) -> ConvertOutcome:
        """選択プリセットで原本を変換する。失敗・未対応は passthrough へ安全に縮退する。"""
        resolved = normalize_preprocess_profile(
            profile if profile is not None else getattr(
                self._settings, "rag_preprocess_profile", "passthrough"
            )
        )
        if resolved == "passthrough":
            return ConvertOutcome.passthrough()
        if resolved == "text_normalize":
            if not _is_text_like(content_type, source_profile):
                # テキスト以外に text_normalize は無意味なので no-op。
                return ConvertOutcome.passthrough()
            derived, warnings = normalize_text_bytes(source_bytes, content_type)
            if derived == source_bytes:
                return ConvertOutcome.passthrough()
            return ConvertOutcome(
                converted=True,
                converter_name="text_normalize",
                converter_version="v1",
                derived_bytes=derived,
                derived_content_type=content_type or "text/plain; charset=utf-8",
                warnings=tuple(warnings),
            )
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
            return ConvertOutcome.passthrough(reason="preprocess_service_disabled")
        url = preprocess_service_url(self._settings, profile)  # type: ignore[arg-type]
        if url is None:
            return ConvertOutcome.passthrough(reason="preprocess_service_unconfigured")
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
                response = client.post(f"{url}/convert", files=files, data=data)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "preprocess service call failed",
                extra={"preprocess_profile": profile, "service_url": url, "error": str(exc)},
            )
            return ConvertOutcome.passthrough(reason="preprocess_service_unreachable")
        try:
            convert_response = ConvertResponse.model_validate(payload)
        except ValueError as exc:
            logger.warning(
                "preprocess service returned invalid payload",
                extra={"preprocess_profile": profile, "service_url": url, "error": str(exc)},
            )
            return ConvertOutcome.passthrough(reason="preprocess_service_invalid_response")
        derived = convert_response.derived_bytes()
        if not convert_response.converted or derived is None:
            return ConvertOutcome.passthrough(reason="preprocess_service_no_conversion")
        return ConvertOutcome(
            converted=True,
            converter_name=convert_response.converter_name,
            converter_version=convert_response.converter_version,
            derived_bytes=derived,
            derived_content_type=convert_response.derived_content_type,
            page_map=dict(convert_response.page_map),
            warnings=tuple(convert_response.warnings),
        )
