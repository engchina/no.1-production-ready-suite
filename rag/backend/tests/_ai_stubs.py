"""実 Oracle 26ai と組み合わせて使う決定論的な AI クライアントスタブ。

Oracle はテストでも実 DB を使うが、VLM / embedding / rerank / LLM 回答は
非決定的かつ課金対象のため、エンドポイント経由のテストではこれらの
OCI クライアントを決定論スタブへ差し替える。embedding は全テキストを
同一ベクトルにするため、実 Oracle のベクトル検索でも確実に候補が返る。
"""

from __future__ import annotations

import pytest

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.schemas.extraction import StructuredExtraction


def _decode(image_bytes: bytes) -> str:
    # UTF-8 として解釈できないバイナリは抽出不能（空文字）として扱う。
    try:
        return image_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


def _guess_document_type(text: str) -> str:
    if "規程" in text or "規定" in text:
        return "社内規程"
    return "文書"


class DeterministicEnterpriseAi(OciEnterpriseAiClient):
    """VLM 抽出と LLM 回答を決定論的に返す Enterprise AI スタブ。

    extract_with_vlm は raw_text のみ返し、element/page は raw_text から
    推定させる（既定 page_number=1 → citation の page_start=1 を再現）。
    """

    async def extract_with_vlm(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "application/octet-stream",
    ) -> dict[str, object]:
        text = _decode(image_bytes)
        extraction = StructuredExtraction(
            raw_text=text,
            document_type=_guess_document_type(text),
            confidence=0.62 if text else 0.0,
            warnings=[] if text else ["スタブ抽出ではテキストを取得できませんでした。"],
        )
        return extraction.to_document_payload()

    async def generate(self, prompt: str, context: str) -> str:
        if not context.strip():
            return "該当する根拠は見つかりませんでした。条件やキーワードを変えて検索してください。"
        snippet = " ".join(context.split())[:200]
        return (
            "検索された根拠に基づく要約です。"
            f"質問「{prompt}」に関連する内容として、{snippet} が見つかりました。"
        )


class DeterministicGenAi(OciGenAiClient):
    """埋め込みとリランクを決定論的に返す Generative AI スタブ。"""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        return [[1.0] + [0.0] * 1535 for _ in texts]

    async def rerank(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        count = min(top_n, len(documents))
        return [(index, 1.0 - index * 1e-3) for index in range(count)]


def patch_ai_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """取込・検索パイプラインが既定構築する OCI クライアントを差し替える。"""
    monkeypatch.setattr("app.rag.ingestion.OciEnterpriseAiClient", DeterministicEnterpriseAi)
    monkeypatch.setattr("app.rag.ingestion.OciGenAiClient", DeterministicGenAi)
    monkeypatch.setattr("app.rag.pipeline.OciEnterpriseAiClient", DeterministicEnterpriseAi)
    monkeypatch.setattr("app.rag.pipeline.OciGenAiClient", DeterministicGenAi)
