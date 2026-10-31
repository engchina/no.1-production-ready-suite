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
