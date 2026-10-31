"""用語の検索用表現が回答の起点を辞書資料へ置き換えないことを確認する。"""

from types import SimpleNamespace

import pytest

from rag_engine.generation import answering
from rag_engine.generation.answering import AnswerRecord, rerank_records
from rag_engine.models.llm import RerankTextRank


@pytest.mark.parametrize(
    "sources,expected",
    [
        (("用語・同義語だけ",), ["A", "B", "D"]),
        (("原質問 + 用語・同義語",), ["D", "A", "B"]),
        (("質問の言い換え・分解",), ["D", "A", "B"]),
        (("用語・同義語だけ", "質問の言い換え・分解"), ["D", "A", "B"]),
        ((), ["D", "A", "B"]),
        (("原質問 + 用語・同義語 + 確認で選んだ条件",), ["D", "A", "B"]),
    ],
)
def test_search_only_term_queries_do_not_displace_both_subjects(monkeypatch, sources, expected):
    original = "項目 A と B の条件を比較してください。"
    extra = "項目 A 別名 A 項目 B 別名 B"
    records = [
        AnswerRecord(
            id=key,
            engine="synthetic",
            engine_label="Synthetic",
            page=1,
            seq_no=i,
            category="Chunk",
            text=f"合成資料 {key}",
            source=f"{key}.txt",
        )
        for i, key in enumerate(("A", "B", "D"))
    ]
    settings = SimpleNamespace(rerank_model="synthetic", rerank_enabled=True, retrieval_query_sources={extra: sources})
    calls = []
    stages = []
    monkeypatch.setattr(answering, "record_retrieval_stage", stages.append)
    monkeypatch.setattr(answering, "_rerank_configured", lambda settings: True)

    def rank(query, documents, settings, top_n):
        calls.append(query)
        scores = [0.68, 0.64, 0.59] if query == original else [0.85, 0.84, 0.99]
        return [RerankTextRank(i, scores[i]) for i in sorted(range(3), key=lambda i: -scores[i])]

    monkeypatch.setattr(answering, "rerank_text_with_scores", rank)
    ranked = rerank_records(original, records, settings, enabled=True, extra_queries=[extra])
    assert [row.id for row in ranked] == expected
    assert calls == ([original] if expected[0] == "A" else [original, extra])
    assert len(ranked) == 3
    selection = next(row for row in stages if row["stage"] == "rerank_query_selection")
    assert selection["ranking_queries"] == calls
    assert selection["retrieval_only_queries"] == ([extra] if expected[0] == "A" else [])
    # 入力の出所と候補は変更しない。
    assert settings.retrieval_query_sources == {extra: sources}
    assert [record.id for record in records] == ["A", "B", "D"]
