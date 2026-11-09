"""根拠の本文に表の形があるかを見る（語の意味は読まない）。

回答の生成の指示（`generation.grounded`）が、表の読み方の案内を入れるかを決めるのに使う。
"""

from __future__ import annotations

import re

from pr_backend_core.spreadsheet.models import COLUMN_ROLE_TEXT_LABELS

_COLUMN_ROLE_TEXT = re.compile("|".join(f"［{re.escape(label)}］" for label in COLUMN_ROLE_TEXT_LABELS.values()))
# Markdown の表の区切りの行（| --- | :---: |）。
_PIPE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")


def has_table_structure(text: str) -> bool:
    """本文に表の形（HTML の表・Markdown の表の行・列の種類の表示）があるか。語の意味は読まない。"""
    return (
        "<table" in text.lower()
        or _COLUMN_ROLE_TEXT.search(text) is not None
        or any(_PIPE_RULE.match(line) or line.count("|") >= 2 for line in text.splitlines())
    )


__all__ = ["has_table_structure"]
