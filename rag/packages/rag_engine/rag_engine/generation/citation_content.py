"""引用した原文が構造的な区切り線・書式だけかを検査する。意味支持は判定しない。"""

import re
import unicodedata

from rag_engine.generation.operation_audit import is_table_rule


def citation_content_unusable(quote: str) -> bool:
    """短い文字・数字・記号を一律に除かず、構造的な行だけの範囲を識別する。"""
    lines = [unicodedata.normalize("NFKC", line).strip() for line in quote.splitlines() if line.strip()]
    return bool(lines) and all(
        re.fullmatch(r"(?:=\s*){3,}", line) is not None or (len(re.sub(r"\s", "", line)) >= 3 and is_table_rule(line)) for line in lines
    )
