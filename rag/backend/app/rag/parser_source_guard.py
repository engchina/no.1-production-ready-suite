"""選んだ文書解析エンジンで扱えない形式を、取込を始める前に止める判定(#286)。

既定の解析エンジン Docling(parser-docling。DocRAG のレイアウト解析)は PDF と画像だけを解析する。
それ以外(テキスト・Markdown・CSV・JSON・XML・HTML・Office・メール など)を既定のまま取り込むと、
worker が parser サービスを呼んだ後で失敗する。アップロードの結果と、処理レシピの取込の投入の両方で
この判定を使い、取込を始める前に理由と対処(処理レシピで Unstructured を選ぶ・サービスを
起動する)を返す。

判定しない(取込時の判定に任せる)もの:
- ファイル準備(preprocess)が passthrough 以外: 変換後の形式が解析エンジンに渡る(例: Office→PDF)。
- ``source_profile.unsupported_reason`` がある形式(TIFF・旧 Office・Outlook .msg・音声など): 既存の
  「非対応形式」として別に止まる。
- 削除済み・未知の解析エンジン: 取込時に ``engine_removed`` として止まる。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_parser_core.capabilities import ADAPTER_CAPABILITIES, adapter_supports_source

from app.clients.parser_service import supported_formats_label
from app.config import DEFAULT_PARSER_ADAPTER_BACKEND, Settings
from app.rag.preprocess_strategy import resolve_preprocess_profile
from app.schemas.document import SourceModality, SourceProfile

PARSER_SOURCE_UNSUPPORTED_CODE = "parser_source_unsupported"
UNSTRUCTURED_BACKEND = "unstructured"
UNSTRUCTURED_SERVICE_ID = "parser-unstructured"
_BACKEND_LABELS = {
    "docling": "Docling",
    "unstructured": "Unstructured",
    "mineru": "MinerU",
    "dots_ocr": "Dots.OCR",
    "oci_genai_vision": "OCI Generative AI (Vision)",
    "enterprise_ai_vlm": "OCI Enterprise AI (VLM)",
    "oci_document_understanding": "OCI Document Understanding",
}
# サービス管理の状態(app.services.status)のうち、Unstructured を使える状態。
_SERVICE_AVAILABLE_STATUSES = frozenset({"running", "degraded", "starting"})


@dataclass(frozen=True)
class ParserSourceBlock:
    """取込を始める前に止める理由(利用者向けの日本語の文言を含む)。"""

    code: str
    backend: str
    file_format: str
    suggested_backend: str | None
    message: str


def effective_parser_backend(settings: Settings) -> str:
    """取込で使う解析エンジン(ingestion の ``_partition_source`` と同じ正規化)。"""
    backend = (
        str(getattr(settings, "rag_parser_adapter_backend", DEFAULT_PARSER_ADAPTER_BACKEND))
        .strip()
        .casefold()
        or DEFAULT_PARSER_ADAPTER_BACKEND
    )
    # 廃止済み baseline 値 local / local_partition は既定エンジンのサービスへ渡る。
    if backend in {"local", "local_partition"}:
        return DEFAULT_PARSER_ADAPTER_BACKEND
    return backend


def _file_format(source_profile: SourceProfile) -> str:
    return source_profile.extension or source_profile.content_type or "不明な形式"


def _supports(backend: str, source_profile: SourceProfile) -> bool:
    return adapter_supports_source(
        backend, source_profile=source_profile, content_type=source_profile.content_type
    )


def parser_source_block(
    settings: Settings,
    source_profile: SourceProfile,
    *,
    unstructured_service_status: str | None = None,
) -> ParserSourceBlock | None:
    """選んだ解析エンジン(既定は Docling)で扱えない形式なら、止める理由を返す。

    ``unstructured_service_status`` はサービス管理の状態(``app.services.status``)。
    渡すと、Unstructured の案内に「未配備」「停止中」を添える。
    """
    if source_profile.unsupported_reason:
        return None
    if resolve_preprocess_profile(settings) != "passthrough":
        return None
    backend = effective_parser_backend(settings)
    if backend not in ADAPTER_CAPABILITIES:
        return None
    if _supports(backend, source_profile):
        return None
    suggested = (
        UNSTRUCTURED_BACKEND
        if backend != UNSTRUCTURED_BACKEND and _supports(UNSTRUCTURED_BACKEND, source_profile)
        else None
    )
    file_format = _file_format(source_profile)
    return ParserSourceBlock(
        code=PARSER_SOURCE_UNSUPPORTED_CODE,
        backend=backend,
        file_format=file_format,
        suggested_backend=suggested,
        message=parser_source_block_message(
            backend,
            file_format=file_format,
            office=source_profile.modality == SourceModality.OFFICE,
            suggested_backend=suggested,
            unstructured_service_status=unstructured_service_status,
        ),
    )


def parser_source_block_message(
    backend: str,
    *,
    file_format: str,
    office: bool,
    suggested_backend: str | None,
    unstructured_service_status: str | None,
) -> str:
    """利用者向けの理由と対処(画面名はナビ・処理レシピの表記に合わせる)。"""
    label = _BACKEND_LABELS.get(backend, backend)
    # 全角括弧の後は空けない(「Docling（既定の解析エンジン）は」/「Unstructured は」)。
    default_note = "（既定の解析エンジン）" if backend == DEFAULT_PARSER_ADAPTER_BACKEND else " "
    formats = supported_formats_label(backend)
    formats_note = f"{label} が解析できるのは {formats}です。" if formats else ""
    parts = [
        f"文書解析エンジン {label}{default_note}はこのファイル形式（{file_format}）を"
        f"解析できないため、取込を開始しませんでした。{formats_note}"
    ]
    if suggested_backend == UNSTRUCTURED_BACKEND:
        parts.append(
            "この文書の「処理レシピ」で「文書解析」を Unstructured に変えてから"
            "「処理を開始」してください。"
        )
        parts.append(unstructured_service_hint(unstructured_service_status))
    else:
        parts.append(
            "対応形式に変換するか、この文書の「処理レシピ」で別の文書解析エンジンを選んでください。"
        )
    if office and backend == "docling":
        parts.append(
            "Office 文書は「処理レシピ」の「ファイル準備」で Office→PDF を選ぶと、"
            "PDF に変換してから Docling で解析できます。"
        )
    return "".join(part for part in parts if part)


def unstructured_service_hint(status: str | None) -> str:
    """Unstructured の解析サービス(parser-unstructured)の状態に応じた案内。"""
    if status is None or status in _SERVICE_AVAILABLE_STATUSES:
        return ""
    if status == "not_installed":
        return (
            "Unstructured の解析サービス（parser-unstructured）は配備されていません。"
            "本番は Terraform の stack で Unstructured parser（rag_enable_parser_unstructured）を"
            "有効にして再配備し、開発は rag/scripts/rag-services.sh install parser-unstructured で"
            "登録してから、「運用設定 › サービス管理」で起動してください。"
        )
    if status == "unconfigured":
        return (
            "Unstructured の解析サービスの接続先（RAG_PARSER_UNSTRUCTURED_SERVICE_URL）が"
            "未設定です。"
        )
    return (
        "Unstructured の解析サービス（parser-unstructured）は停止しています。"
        "「運用設定 › サービス管理」で Unstructured を起動してください。"
    )


async def check_parser_source(
    settings: Settings, source_profile: SourceProfile
) -> ParserSourceBlock | None:
    """``parser_source_block`` に、Unstructured のサービスの状態(未配備・停止中)を添えて返す。"""
    block = parser_source_block(settings, source_profile)
    if block is None or block.suggested_backend != UNSTRUCTURED_BACKEND:
        return block
    status = await _unstructured_service_status(settings)
    return parser_source_block(settings, source_profile, unstructured_service_status=status)


async def _unstructured_service_status(settings: Settings) -> str | None:
    """サービス管理と同じ判定(systemctl show + /health)で parser-unstructured の状態を返す。"""
    from app.services.catalog import get_catalog_entry
    from app.services.status import probe_service_status

    entry = get_catalog_entry(UNSTRUCTURED_SERVICE_ID)
    if entry is None:
        return None
    try:
        return str(await probe_service_status(settings, entry))
    except Exception:  # noqa: BLE001 - 状態は案内の補足。取れなければ添えない。
        return None
