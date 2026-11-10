"""編集欄を外した指示の保存値を消す一度限りの migration（#4782）。

生成の指示は「役割と回答の書き方」（``generation.role``）だけを編集でき、信頼の境界・出力の形・
引用の決まりと、質問・検索の内部の段の文はコードの定数になった。保存値は読まれないので消す
（旧 ``generation.system`` は固定の部分を含むので、新しい key へ読み替えない）。
"""

from __future__ import annotations

# 編集欄を外した key。編集できる key（answer_prompts.EDITABLE_PROMPT_KEYS）とは重ならない。
REMOVED_PROMPT_KEYS = (
    "vlm_answer",
    "generation.system",
    "generation.batchReading",
    "generation.assumptionSystem",
    "generation.evidenceReading",
    "generation.guideRules",
    "support_guide_draft.system",
    "support_guide_draft.instruction",
    "support_guide_conditions.system",
    "support_guide_conditions.answerChoice",
    "rule_selection.system",
    "rule_selection.instruction",
    "history_rewrite.system",
    "field_filter.system",
    "screen_linking.system",
    "article_catalog.documentSystem",
    "article_catalog.system",
    "article_catalog.refineSystem",
    "section_extraction.system",
)


def removed_prompt_overrides_sql() -> str:
    keys = ",\n    ".join(f"'{key}'" for key in REMOVED_PROMPT_KEYS)
    return f"DELETE FROM RAG_ANSWER_PROMPTS\nWHERE PROMPT_KEY IN (\n    {keys}\n)"
