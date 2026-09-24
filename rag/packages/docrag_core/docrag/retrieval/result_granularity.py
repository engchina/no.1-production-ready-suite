"""集計値の引用だけで明細確認を完了扱いしないための限定検査。"""
from __future__ import annotations

import re
from typing import Any, Sequence
from docrag.parsing.vision_prompt_rules import is_vision_enumeration_line


def aggregate_only_evidence(spans: Sequence[dict[str, Any]]) -> bool:
    """本文が集計値だけを示す場合に真を返す。偽は明細の適用証明ではない。

    検索語・見出しmetadataは判定に使わず、未知の表現は独立監査へ残す。
    氏名や明細の存在から最終集計への採否までは推論しない。
    """
    body = '\n'.join(line for span in spans for line in span.get('text', '').splitlines()
                     if not is_vision_enumeration_line(line))
    return bool(re.search(r'人数|人員|件数|合計|集計値', body) and not re.search(
        r'明細|名簿|氏名|取引番号|個人|対象者', body))
