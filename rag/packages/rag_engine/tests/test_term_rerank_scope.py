"""質問に用語・同義語を足した検索文も、rerank の比較文に使う（原質問との最高値で並べる）。"""

from types import SimpleNamespace

from rag_engine.generation import answering
from rag_engine.generation.answering import AnswerRecord, rerank_records
from rag_engine.models.llm import RerankTextRank


def test_term_expanded_question_is_used_as_a_rerank_query(monkeypatch):
    original = "項目 A と B の条件を比較してください。"
    extra = "項目 A と B の条件を比較してください。\n項目 A 別名 A 項目 B 別名 B"
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
    settings = SimpleNamespace(rerank_model="synthetic", rerank_enabled=True, retrieval_query_sources={extra: ("原質問 + 用語・同義語",)})
    calls = []
    monkeypatch.setattr(answering, "_rerank_configured", lambda settings: True)

    def rank(query, documents, settings, top_n):
        calls.append(query)
        scores = [0.68, 0.64, 0.59] if query == original else [0.85, 0.84, 0.99]
        return [RerankTextRank(i, scores[i]) for i in sorted(range(3), key=lambda i: -scores[i])]

    monkeypatch.setattr(answering, "rerank_text_with_scores", rank)
    ranked = rerank_records(original, records, settings, enabled=True, extra_queries=[extra])
    assert [row.id for row in ranked] == ["D", "A", "B"]
    assert calls == [original, extra]
    assert [record.id for record in records] == ["A", "B", "D"]
