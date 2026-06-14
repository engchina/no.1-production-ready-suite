"""query expansion のテスト。"""

from app.rag.query_transform import expand_retrieval_queries


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
