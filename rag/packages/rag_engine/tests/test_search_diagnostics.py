"""実検索の指標・入力・返却件数だけを表示し、本文と権限の内部情報を除外する。"""

from rag_engine.retrieval.search_diagnostics import oracle_expression_terms, search_diagnostics
from rag_engine.retrieval.search_stages import TEXT_SEARCH_STAGE, VECTOR_SEARCH_STAGE


def test_actual_expression_terms_and_separate_result_counts() -> None:
    expression = "{項目A}*2 ACCUM {補助}*0.5 ACCUM {a}}b}"
    assert oracle_expression_terms(expression) == ["項目A", "補助", "a}b"]
    result = search_diagnostics(
        TEXT_SEARCH_STAGE,
        branches=[
            {
                "query": "合成の質問 A",
                "expression": expression,
                "raw_count": 3,
                "results": [{"id": "chunk-a", "file": "A.pdf", "page": 2, "score": 7}],
            },
            {
                "query": "合成の質問 B",
                "expression": "{項目B}",
                "raw_count": 1,
                "results": [{"id": "chunk-a", "file": "A.pdf", "page": 2, "score": 4}],
            },
            {"query": "合成の質問 C", "expression": "{項目C}", "raw_count": 0, "results": []},
        ],
        scope={"file_name": "A.pdf", "source_acl": "must-not-record", "token": "hidden"},
        limit=12,
        metric="Oracle Text SCORE（大きい順）",
    )
    fields = {field["label"]: field["value"] for field in result["fields"]}
    assert fields["結果"] == "取得件数（重複除去前） 4 件 / チャンク ID の重複除去後 1 件"
    assert fields["Oracle Text 検索式 1"] == expression
    assert "検索語: 項目A / 補助 / a}b" in fields["検索 1 の入力"]
    assert "検索 3: 取得件数（重複除去前） 0 件 / 重複除去後 0 件（該当なし）" in result["lines"]
    assert "must-not-record" not in str(result)
    assert "hidden" not in str(result)
    assert "順位 1: A.pdf / ページ 2" in result["lines"][3]


def test_vector_trace_does_not_describe_sudachi_or_invent_missing_query() -> None:
    result = search_diagnostics(
        VECTOR_SEARCH_STAGE, branches=[{"raw_count": 0, "results": []}], scope={}, limit=3, metric="COSINE 距離（小さい順）"
    )
    assert "埋め込みを直接指定" in str(result)
    assert "Sudachi" not in str(result)
    assert "正確さを表す指標ではありません" in str(result)


def test_native_threshold_trace_uses_actual_counts_and_channel_contributions() -> None:
    from rag_engine.retrieval.search_stages import HYBRID_SEARCH_STAGE

    details = search_diagnostics(
        HYBRID_SEARCH_STAGE,
        branches=[
            {
                "query": "合成の質問",
                "native": True,
                "raw_count": 4,
                "vector_threshold": {
                    "min_similarity": 0.3,
                    "raw_count": 4,
                    "retained_count": 2,
                    "excluded_count": 2,
                    "rejected_vector_count": 3,
                    "preserved_text_count": 1,
                    "missing_similarity_count": 1,
                },
                "results": [
                    {
                        "id": "text",
                        "file": "合成 A.txt",
                        "score": 0.001,
                        "vector_rank": 0,
                        "text_rank": 2,
                        "cosine_similarity": None,
                        "native_wrrf_score": 98.53,
                        "vector_contribution": 0,
                        "keyword_contribution": 0.001,
                    }
                ],
            }
        ],
        scope={},
        limit=4,
        metric="有効 RRF 寄与",
    )
    fields = {entry["label"]: entry["value"] for entry in details["fields"]}
    assert fields["コサイン類似度の下限"] == "0.3"
    assert "判定前 4 件 / 採用 2 件 / 候補除外 2 件" in fields["類似度による選別"]
    assert "全文命中で保持 1 件 / 類似度未取得 1 件" in fields["類似度による選別"]
    rendered = str(details)
    assert "ベクトル順位: 該当なし" in rendered
    assert "Oracle 原始 WRRF: 98.53" in rendered
    assert "有効ベクトル寄与: 0 / 有効全文寄与: 0.001" in rendered
    assert "コサイン類似度: 未取得" in rendered
