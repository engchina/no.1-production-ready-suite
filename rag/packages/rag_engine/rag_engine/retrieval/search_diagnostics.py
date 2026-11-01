"""検索の実入出力を制限付きの工程詳細へ変換する。本文・ベクトル・認証設定は受け取らない。"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from rag_engine.retrieval.search_stages import VECTOR_SEARCH_STAGE


def oracle_expression_terms(expression: str) -> list[str]:
    """実際に bind した重み付き ACCUM 式からエスケープ済みの語を読む。再分詞しない。"""
    return [value.replace("}}", "}") for value in re.findall(r"\{((?:[^}]|}})*)\}", expression)]


# 検索範囲の値だけを許可し、利用者や権限判定の内部情報は記録しない。
_SCOPE_KEYS = frozenset(
    {
        "document_id",
        "knowledge_base_id",
        "file_name",
        "page_ranges",
        "page_number_min",
        "page_number_max",
        "large_category",
        "middle_category",
        "small_category",
        "category_name",
        "section_title",
        "section_path",
        "retrieval_scope",
        "chunk_run_id",
        "classification",
        "metadata_filter",
    }
)


def search_diagnostics(
    stage: str,
    *,
    branches: Sequence[Mapping[str, Any]],
    scope: Mapping[str, object],
    limit: int,
    metric: str,
) -> dict[str, Any]:
    """順位は DB の返却順。各分岐は実行済みの入力、原始件数、採用した結果だけを渡す。"""
    vector = stage == VECTOR_SEARCH_STAGE
    fields = [
        {
            "label": "目的",
            "value": "検索文の埋め込みを使い、意味の近い文書の箇所を探します。"
            if vector
            else "作成した検索語と検索式を使い、Oracle Text で文書の箇所を探します。",
        },
        {
            "label": "検索範囲",
            "value": " / ".join(f"{key}: {value}" for key, value in scope.items() if key in _SCOPE_KEYS) or "指定された検索範囲",
        },
        {"label": "アクセス制御", "value": "呼び出し元が適用した権限と明示範囲を維持"},
        {"label": "候補数の上限", "value": f"各検索文 {limit} 件"},
        {"label": "指標", "value": f"{metric}。回答の正確さを表す指標ではありません。"},
    ]
    lines: list[str] = []
    result_lines: list[list[str]] = []
    identifiers: set[str] = set()
    raw_count = 0
    for index, branch in enumerate(branches, 1):
        rows = branch.get("results", ())
        raw = int(branch.get("raw_count", len(rows)))
        raw_count += raw
        input_value = str(branch.get("query") or "検索文は記録されていません（埋め込みを直接指定）")
        if not vector and not branch.get("native"):
            input_value += " / 検索語: " + (" / ".join(oracle_expression_terms(str(branch.get("expression") or ""))) or "なし")
        fields.append({"label": f"検索 {index} の入力", "value": input_value})
        if not vector and branch.get("native"):
            fields.append(
                {"label": f"全文検索入力 {index}", "value": "Oracle原生hybridのsearch_text。内部の検索語・検索式はAPIから返されません。"}
            )
        elif not vector:
            fields.append({"label": f"Oracle Text 検索式 {index}", "value": str(branch.get("expression") or "なし（未実行）")})
        error = str(branch.get("error") or "")
        suffix = f"（失敗: {error}）" if error else "（該当なし）" if not rows else ""
        lines.append(f"検索 {index}: 取得件数（重複除去前） {raw} 件 / 重複除去後 {len(rows)} 件" + suffix)
        branch_lines: list[str] = []
        for rank, row in enumerate(rows, 1):
            identifiers.add(str(row["id"]))
            score = row.get("score")
            branch_lines.append(
                f"検索 {index}・順位 {rank}: {row.get('file') or '文書名の記録なし'} / ページ {row.get('page') or '記録なし'} / "
                f"{metric}: {score if score is not None else '記録なし'} / チャンク ID: {row['id']}"
            )
        result_lines.append(branch_lines)
    # 詳細の表示上限でも各検索文の件数と先頭結果を先に確認できるようにする。
    for rank in range(max((len(rows) for rows in result_lines), default=0)):
        lines.extend(rows[rank] for rows in result_lines if rank < len(rows))
    fields.insert(1, {"label": "結果", "value": f"取得件数（重複除去前） {raw_count} 件 / チャンク ID の重複除去後 {len(identifiers)} 件"})
    return {"fields": fields, "lines": lines}
