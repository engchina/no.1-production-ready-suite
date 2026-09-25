"""query expansion のテスト。"""

from app.rag.query_transform import (
    ExpandedQueryVariants,
    expand_retrieval_queries,
    expand_retrieval_queries_with_llm,
)


def test_expand_retrieval_queries_adds_business_synonyms() -> None:
    """業務語彙に一致した場合だけ同義語 variant を作る。"""
    variants = expand_retrieval_queries("invoice storage", max_variants=3)

    assert variants[0] == "invoice storage"
    assert len(variants) == 3
    assert "請求書" in variants[1]
    assert "保管" in variants[1]
    assert variants[2] == "請求書 インボイス bill 保管 保存 格納 archive"


def test_expand_retrieval_queries_adds_figure_synonyms() -> None:
    """図・画像への質問は multimodal chunk を拾える語彙へ展開する。"""
    variants = expand_retrieval_queries("構成図", max_variants=3)

    assert variants[0] == "構成図"
    assert "画像" in variants[1]
    assert "figure" in variants[1]
    assert variants[2] == "図版 画像 figure image"


def test_expand_retrieval_queries_can_be_disabled() -> None:
    """無効化時は元 query だけを返す。"""
    assert expand_retrieval_queries("請求書 保管", enabled=False) == ["請求書 保管"]


def test_expand_retrieval_queries_respects_max_variants() -> None:
    """variant 数は設定上限で制御する。"""
    assert expand_retrieval_queries("請求書 保管", max_variants=1) == ["請求書 保管"]
    assert len(expand_retrieval_queries("請求書 保管", max_variants=2)) == 2


def test_expand_retrieval_queries_returns_original_for_unknown_terms() -> None:
    """辞書にない query は検索コストを増やさない。"""
    assert expand_retrieval_queries("完全に未知の質問") == ["完全に未知の質問"]


class _StubExpander:
    """LLM マルチクエリ拡張のスタブ(決定論)。"""

    def __init__(self, variants: list[str] | None = None, error: Exception | None = None) -> None:
        self.variants = variants or []
        self.error = error
        self.calls = 0

    async def expand_search_query(self, query: str, *, max_variants: int = 3) -> list[str]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.variants


async def test_llm_expansion_prepends_original_and_dedupes() -> None:
    """LLM 変種は元 query を先頭に融合し、重複除去 + max_variants で cap する。"""
    llm = _StubExpander(
        ["経費精算の承認フロー", "経費 精算  承認フロー", "経費申請の承認手順", "余剰"]
    )
    variants = await expand_retrieval_queries_with_llm(
        "経費精算の承認フロー", llm=llm, max_variants=3
    )
    assert variants[0] == "経費精算の承認フロー"
    assert len(variants) == 3
    assert "余剰" not in variants  # cap で切られる
    assert llm.calls == 1


async def test_llm_expansion_failure_falls_back_to_empty() -> None:
    """LLM 失敗時は空 list(呼び出し側が決定論展開へ縮退)。"""
    llm = _StubExpander(error=RuntimeError("boom"))
    assert await expand_retrieval_queries_with_llm("承認 手順", llm=llm, max_variants=3) == []


async def test_llm_expansion_empty_or_identical_returns_empty() -> None:
    """変種なし・元 query と同一だけの応答は空 list を返す。"""
    assert (
        await expand_retrieval_queries_with_llm("承認", llm=_StubExpander([]), max_variants=3) == []
    )
    assert (
        await expand_retrieval_queries_with_llm("承認", llm=_StubExpander(["承認"]), max_variants=3)
        == []
    )


async def test_llm_expansion_schema_rejects_oversized_payload() -> None:
    """件数上限超過の LLM 出力はスキーマ検証で拒否し空 list へ縮退する。"""
    llm = _StubExpander([f"variant {i}" for i in range(20)])
    assert await expand_retrieval_queries_with_llm("承認 手順", llm=llm, max_variants=3) == []


def test_expanded_query_variants_schema_cleans_values() -> None:
    """スキーマは空白正規化・空要素除去・長さ上限・重複除去を強制する。"""
    validated = ExpandedQueryVariants(variants=["  a  b ", "", "a b", "x" * 1000])
    assert validated.variants[0] == "a b"
    assert len(validated.variants) == 2
    assert len(validated.variants[1]) == 500
