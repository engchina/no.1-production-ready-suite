"""取込の各段で OCI Enterprise AI へ渡す固定のプロンプト。

回答プロンプトの画面の「各段のプロンプト」(``answer_prompts.readonly_prompt_stages``)に
そのまま載せるため、呼び出しの中に文字列を書かず、ここに定数として置く(#2054)。
"""

from __future__ import annotations

# 項目抽出(メタデータ/項目抽出)。末尾に「抽出する field 定義: …」を足して渡す。
FIELD_EXTRACTION_PROMPT = (
    "次の文書から、指定された field を抽出してください。"
    '各 field は {"name", "value", "value_type", "confidence"} の '
    "JSON 配列だけで出力し、見つからない field は省略してください。"
    "value は、value_type が date なら YYYY-MM-DD、number なら桁区切りと単位の"
    "ない数字、bool なら true か false で出力してください。"
)

# ナビゲーション要約(章節木): 章節ごとの要約。
NAVIGATION_SUMMARY_PROMPT = (
    "次の章節の内容を日本語で1〜2文に要約してください。原文にない情報は補わないでください。"
)

# RAPTOR 再帰要約索引: chunk の群の要約。
RAPTOR_SUMMARY_SYSTEM_PROMPT = "あなたは文書を階層的に要約するアシスタントです。"
RAPTOR_SUMMARY_PROMPT = "次のテキスト群の要点を簡潔な日本語で要約してください。"

__all__ = [
    "FIELD_EXTRACTION_PROMPT",
    "NAVIGATION_SUMMARY_PROMPT",
    "RAPTOR_SUMMARY_PROMPT",
    "RAPTOR_SUMMARY_SYSTEM_PROMPT",
]
