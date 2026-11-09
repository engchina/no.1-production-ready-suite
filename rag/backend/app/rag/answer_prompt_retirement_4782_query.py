"""消した段の保存済みのプロンプト（RAG_ANSWER_PROMPTS の行）を消す migration の SQL（#4782）。"""

# 原文の抜き出しの段（evidence.extract）。段を消したので、保存値は読まれない。
RETIRED_SECTION_EXTRACTION_PROMPT_KEYS = ("section_extraction.system",)


def retired_prompt_rows_sql(keys: tuple[str, ...]) -> str:
    """消した段の key の行だけを消す（ほかの段の保存値は変えない）。"""
    quoted = ", ".join(f"'{key}'" for key in keys)
    return f"DELETE FROM RAG_ANSWER_PROMPTS\nWHERE PROMPT_KEY IN ({quoted})"
