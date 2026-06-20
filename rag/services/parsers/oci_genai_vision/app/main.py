"""OCI Generative AI (Vision) parser マイクロサービス。

共有 core(rag_parser_core.oci_enterprise_ai)を env 由来 config で駆動し、OCI Generative
AI の Chat/Responses 推論(+ Files API)を Vision モデルで呼んで文書ページを構造化抽出し、
`StructuredExtraction`(ParseResponse wire 形式)で返す。OCI 認証(API キー/endpoint/モデル)は
メインプロジェクトの OCI env を継承する(個別設定なし)。未設定/失敗時は extraction=None の
fallback を返し、backend 側で既存 in-process VLM(PDF 分割込み)/ローカルフローへ安全に縮退する。

PDF 分割・checkpoint/segment の取込オーケストレーションは backend(DB 結合)側に残し、本
サービスは単一入力の VLM 抽出 leaf のみを担う。
"""

from rag_parser_core.extraction import StructuredExtraction
from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiClient, OciEnterpriseAiConfig
from rag_parser_core.result import ParseResponse
from rag_parser_core.service import create_service_parse_app
from rag_parser_core.source import SourceProfile

_BACKEND = "oci_genai_vision"
_CONFIG = OciEnterpriseAiConfig.from_env()
_DEFAULT_PROMPT = "この文書を日本語優先で OCR し、読み順の本文と構造を抽出してください。"


def _client() -> OciEnterpriseAiClient:
    return OciEnterpriseAiClient(_CONFIG)


def _is_configured() -> bool:
    return bool(
        _CONFIG.oci_enterprise_ai_endpoint.strip()
        and _CONFIG.oci_enterprise_ai_api_key.strip()
        and _CONFIG.vision_model_id.strip()
    )


async def _parse(
    source_bytes: bytes,
    content_type: str,
    source_profile: SourceProfile | None,
    document_id: str,
    prompt: str,
) -> ParseResponse:
    _ = document_id  # VLM は Files API の生成ファイル名を使い document_id は不要。
    parser_profile = (
        source_profile.parser_profile if source_profile is not None else ""
    ) or "enterprise_ai_generic"
    try:
        payload = await _client().extract_with_vlm(
            source_bytes,
            prompt.strip() or _DEFAULT_PROMPT,
            mime_type=content_type,
            parser_profile=parser_profile,
        )
    except Exception as exc:  # noqa: BLE001 - 失敗時は安全に縮退する
        return ParseResponse(
            extraction=None,
            parser_backend=_BACKEND,
            parser_version=_BACKEND,
            fallback_used=True,
            template=f"{_BACKEND}_fallback",
            warnings=[f"{_BACKEND}_unavailable: {type(exc).__name__}"],
        )
    return ParseResponse(
        extraction=StructuredExtraction.model_validate(payload),
        parser_backend=_BACKEND,
        parser_version=_BACKEND,
        template=_BACKEND,
    )


app = create_service_parse_app(
    backend=_BACKEND,
    parse=_parse,
    is_configured=_is_configured,
    title="parser-oci-genai-vision",
)
