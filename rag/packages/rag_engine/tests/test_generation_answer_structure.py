"""生成の指示が、条件つき・資料不足の回答の構成と、名称の表記の揺れの扱いを 1 か所ずつ渡す（#5274・#5275）。

文の言い回しは固定しない。規則がどの Step に 1 回だけあり、保存した役割の文でも落ちないかを確かめる。
"""

import re

from rag_engine.generation import grounded
from rag_engine.resources.runtime import bind_prompt_overrides

NEXT_ACTIONS = ("先に確かめること", "確かめ方", "足りない資料")


def _step(prompt: str, title: str) -> str:
    """見出しが ``title`` の Step の本文（次の Step の見出しの前まで）。"""
    match = re.search(rf"^## Step \d+: {re.escape(title)}\n(.*?)(?=^## Step \d+:|\Z)", prompt, re.M | re.S)
    assert match, title
    return match.group(1)


def test_conditional_answer_is_composed_as_next_actions_in_the_fixed_part() -> None:
    """結論が未確認の条件・資料で分かれる回答は、確かめること・確かめ方・足りない資料を 1 つのまとまりにする（#5274）。

    本文の構成の決まりなので、編集できる役割の文ではなく固定の部分（本文を完成させる Step）に 1 回だけ置く。
    """
    body = _step(grounded.generation_system_prompt(), "本文を完成させて引用を選ぶ")
    lines = [line for line in body.splitlines() if all(word in line for word in NEXT_ACTIONS)]
    assert len(lines) == 1, body
    # 確かめ方は資料にある手順だけで、無ければ無いと書く（手順を創作しない）。
    assert "資料に無ければ無いと書く" in lines[0] and "手順を創作しない" in lines[0]
    assert not any(word in grounded.GENERATION_ROLE_PROMPT for word in NEXT_ACTIONS)
    with bind_prompt_overrides({"generation.role": "## Step 1: 保存済みの指示"}):
        saved = grounded.generation_system_prompt()
    assert lines[0] in saved


def test_similar_names_keep_the_possibility_of_a_notation_variance() -> None:
    """名称の字だけが少し違う記載は、別の対象・別の原因とも同じ対象とも断定せず、表記の揺れの可能性を示す（#5275）。

    似た名称を同じとみなさない規則と同じ行に置く（判定は語の一覧や字数の閾値でコードに書かず、モデルに任せる）。
    """
    body = _step(grounded.generation_system_prompt(), "原文から言えることを決める")
    rule = [line for line in body.splitlines() if "似た名称" in line]
    assert len(rule) == 1, body
    assert "表記の揺れの可能性" in rule[0] and "別の原因" in rule[0]
    assert sum("表記の揺れ" in line for line in grounded.generation_system_prompt().splitlines()) == 1
