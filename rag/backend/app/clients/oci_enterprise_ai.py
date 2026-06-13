"""OCI Enterprise AI クライアント（LLM / VLM）。

⚠️ 重要: LLM / VLM は **OCI Enterprise AI** を使う。
OCI Generative AI の chat 推論 API は使わない（AGENTS.md 参照）。
"""

import re

from app.config import Settings, get_settings
from app.schemas.extraction import StructuredExtraction


class OciEnterpriseAiClient:
    """OCI Enterprise AI による LLM / VLM 推論クライアント。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # TODO: OCI SDK / Enterprise AI エンドポイントのクライアント初期化

    async def extract_with_vlm(self, image_bytes: bytes, prompt: str) -> dict[str, object]:
        """VLM で画像から構造化データを抽出する（OCR）。"""
        if self._settings.ai_service_adapter == "oci":
            return await self._extract_with_enterprise_ai(image_bytes, prompt)
        text = _decode_document_bytes(image_bytes)
        extraction = StructuredExtraction(
            raw_text=text,
            document_type=_guess_document_type(text),
            fields=_extract_reference_fields(text),
            confidence=0.62 if text else 0.0,
            warnings=[] if text else ["ローカル抽出ではテキストを取得できませんでした。"],
        )
        return extraction.to_document_fields()

    async def generate(self, prompt: str, context: str) -> str:
        """LLM で回答を生成する。"""
        if self._settings.ai_service_adapter == "oci":
            return await self._generate_with_enterprise_ai(prompt, context)
        if not context.strip():
            return "該当する根拠は見つかりませんでした。条件やキーワードを変えて検索してください。"
        snippets = _extract_context_snippets(context)
        joined = " / ".join(snippets[:3])
        return (
            "検索された根拠に基づく要約です。"
            f"質問「{prompt}」に関連する内容として、{joined} が見つかりました。"
        )

    async def _extract_with_enterprise_ai(
        self, image_bytes: bytes, prompt: str
    ) -> dict[str, object]:
        """OCI Enterprise AI VLM 呼び出し。

        LLM/VLM は OCI Generative AI chat API ではなく Enterprise AI に限定する。
        """
        raise RuntimeError("OCI Enterprise AI VLM adapter is not configured in this build.")

    async def _generate_with_enterprise_ai(self, prompt: str, context: str) -> str:
        """OCI Enterprise AI LLM 呼び出し。"""
        raise RuntimeError("OCI Enterprise AI LLM adapter is not configured in this build.")


def _decode_document_bytes(data: bytes) -> str:
    """ローカル参照実装用にアップロード内容をテキスト化する。"""
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            decoded = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return re.sub(r"\s+", " ", decoded).strip()
    return ""


def _guess_document_type(text: str) -> str:
    """帳票種別をざっくり推定する。"""
    if "請求" in text or "invoice" in text.lower():
        return "請求書"
    if "領収" in text:
        return "領収書"
    if "納品" in text:
        return "納品書"
    return "伝票"


def _extract_reference_fields(text: str) -> dict[str, str | int | float | bool | None]:
    """ローカル VLM の代替として代表的な項目だけ抽出する。"""
    fields: dict[str, str | int | float | bool | None] = {}
    number_pattern = r"(請求書番号|伝票番号|Invoice\s*No\.?)[:：\s]*([A-Za-z0-9-]+)"
    if match := re.search(number_pattern, text, re.I):
        fields["document_number"] = match.group(2)
    if match := re.search(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})", text):
        fields["document_date"] = match.group(1)
    if match := re.search(r"(合計|請求金額|Total)[:：\s]*[¥￥$]?\s*([0-9,]+)", text, re.I):
        fields["total_amount"] = int(match.group(2).replace(",", ""))
    first_line = text.split("。")[0].strip()
    if first_line:
        fields["summary"] = first_line[:120]
    return fields


def _extract_context_snippets(context: str) -> list[str]:
    """生成に渡したコンテキストから短い根拠文を抜き出す。"""
    snippets: list[str] = []
    for line in context.splitlines():
        cleaned = line.strip().removeprefix("-").strip()
        if len(cleaned) >= 12:
            snippets.append(cleaned[:160])
    return snippets or [context[:160]]
