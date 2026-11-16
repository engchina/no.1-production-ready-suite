"""指示の文の `## Step N:` の見出しの番号をそろえる。"""

from __future__ import annotations

import itertools
import re

_STEP_HEADING = re.compile(r"^(## Step )(\d+)(:)", re.M)


def continue_prompt_steps(preceding: str, continuation: str) -> str:
    """続きの部品の `## Step N:` の見出しを、前の部分の最後の Step の次から振り直します。

    続きの部品（前提の追加・対象の指示・出力契約など）は既定の本体の Step の数を前提に番号を
    書いてあるため、利用者が本体を保存して Step の数を変えると、つないだ指示の番号が飛ぶか
    重なります。保存値は書き換えず、組み立てる時だけ見出しの番号をそろえます（既定の組み合わせ
    では何も変わりません）。``preceding`` は指示の側の文（差し込み前のテンプレートなど）を渡し、
    根拠データに含まれる見出しで番号を決めないようにします。
    """
    numbers = [int(match.group(2)) for match in _STEP_HEADING.finditer(preceding)]
    following = itertools.count((numbers[-1] if numbers else 0) + 1)
    return _STEP_HEADING.sub(
        lambda match: f"{match.group(1)}{next(following)}{match.group(3)}", continuation
    )
