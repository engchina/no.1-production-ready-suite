"""取込の各段で OCI Enterprise AI へ渡す固定のプロンプト。

回答プロンプトの画面の「各段のプロンプト」(``answer_prompts.readonly_prompt_stages``)に
そのまま載せるため、呼び出しの中に文字列を書かず、ここに定数として置く(#2054)。

どの段もシステムの文として渡し、文書の内容は未信頼のデータとして別に渡す(#4776)。応答の形が
決まっている段は JSON Schema で渡すので、形をここに書かない(#4782)。
"""

from __future__ import annotations

# 見出しの階層の推定(#2277)。データは見出しの JSON(``[{"i": 番号, "text": 見出し}]``)。
HEADING_LEVEL_PROMPT = (
    "## Step 1: 見出しの入力を読む\n"
    "データは、1 つの文書の見出しを読み順に並べた JSON です(i は見出しの番号、text は"
    "見出しの文字列)。\n"
    "\n"
    "## Step 2: 階層を決める\n"
    "- 文書の章・節の構成として自然になるように、各見出しの階層を 1(最上位)から "
    "6(最も深い)の整数で決めてください。\n"
)

# 項目抽出(メタデータ/項目抽出)。末尾に「抽出する field 定義: …」を足して渡す。データは文書の本文。
FIELD_EXTRACTION_PROMPT = (
    "## Step 1: 抽出対象を確認する\n"
    "データの文書から、末尾の定義にある field を抽出してください。見つからない field は"
    "省略してください。\n"
    "\n"
    "## Step 2: 値を整える\n"
    "- value は、value_type が date なら YYYY-MM-DD、number なら桁区切りと単位のない数字、"
    "bool なら true か false で出力してください。\n"
)
# 項目抽出で本文を分けて渡すとき(#2720)の、各部分の頭書き。位置(i/n)と、その部分の最初の
# 要素の見出しの位置を付ける(文書の冒頭ではないことと、どの章の中かをモデルに示す)。
FIELD_EXTRACTION_PART_HEADER = "[文書の一部 {index}/{total}]"
FIELD_EXTRACTION_PART_PATH = " 見出し: {path}"

# ナビゲーション要約(章節木): 章節ごとの要約。データは章節の本文。
NAVIGATION_SUMMARY_PROMPT = (
    "## Step 1: 章節を要約する\n"
    "データの章節の内容を、その章節と同じ言語で 1〜2 文に要約してください。\n"
    "- 原文にない情報は補わないでください。\n"
)

# 文書全体の要約(#4793)。取込の後に 1 文書につき 1 回だけ呼ぶ。データは
# ``app.rag.document_summary.build_summary_input`` が作る文書の本文(と見出しの一覧)。
DOCUMENT_SUMMARY_PROMPT = (
    "## Step 1: 文書の入力を読む\n"
    "データは 1 つの文書です。document_body は文書の本文で、truncated が true のときは文書の"
    "先頭の一部だけです。document_outline は文書全体の見出しの一覧で、本文を省いたときだけ"
    "付きます。\n"
    "\n"
    "## Step 2: 文書全体を要約する\n"
    "- summary には、何について書かれた文書か、主な内容、結論や決まっていることを 3〜5 文で"
    "書いてください。\n"
    "- 本文を省いた文書では、省いた部分の内容を見出しの一覧から読み取れる範囲で含めてください。\n"
    "- topics には、この文書を探す人が使う話題の語を、本文の語のまま 3〜7 個挙げてください。\n"
    "- 入力にない情報は補わないでください。\n"
    "- 要約できる内容がないときは、summary を空の文字列、topics を空の配列にしてください。\n"
    "\n"
    "## Step 3: 文書の言語で書く\n"
    "summary と topics は、文書の本文と同じ言語で書いてください。\n"
)

__all__ = [
    "DOCUMENT_SUMMARY_PROMPT",
    "FIELD_EXTRACTION_PART_HEADER",
    "FIELD_EXTRACTION_PART_PATH",
    "FIELD_EXTRACTION_PROMPT",
    "HEADING_LEVEL_PROMPT",
    "NAVIGATION_SUMMARY_PROMPT",
]
