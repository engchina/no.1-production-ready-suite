"""回答の段落の分け方と、主張ではない段落の決定的な判定（#1277・#1306）。

最終の検証（`answer_validation`）は、RAG の `rag_validate_answer` が段落（`answer_quote`）ごとに
返す判定で、根拠で確かめられない段落を回答から外す。ところが主張ではない段落（見出し・出典の行・
利用者への質問・「資料に記載がありません」という不足の文・「確かめられていない点」の節）まで
「確かめられない」として外すと、回答が読みにくくなり、拒答が拒答に見えなくなる（#1289 の評価）。

ここでは、そうした段落をモデルを使わずに判定する。判定は保守的にし、迷うものは主張として扱う
（確かめられない操作の手順・事実の主張は今までどおり外す）。

- 見出し: Markdown の見出し・太字だけの行・【…】・「…：」で終わる短いラベル。述語で終わる文や
  操作の指示を含むものは見出しにしない。句点で終わらない短い名詞句（「1. 人事評価システムの
  重要度」「補足（資料の記載）」「根拠」。項目・区分・資料の名前か「…とき」「…か」で終わり、
  述語・操作・数量・言い切りの「は」を含まないもの）と、以降の箇条書きを導くだけの導入の文
  （「根拠は次のとおりです。」）・礼の定型の文も見出しとして扱う（#1469）。規則は RAG の
  `operation_audit.is_heading` とそろえ、両方のテストが
  platform/contracts/answer-passages/headings.json の同じ事例で確かめる。
- 出典: 「出典：…」「根拠：…」「セクション：…」「ページ：…」「*Locator*: …」などの
  ラベルの行（ラベルの太字・斜体は許す）、文書名・頁・節を示す括弧だけの行、文書名だけの行、
  リンクだけの行、根拠の ID（【証拠 ID: …】・〔証拠 ID: …〕）と文の無い「…」の題・頁の括弧
  だけの行（表の区切りの `|` は許す）、文書名と場所の括弧だけの行（「**システム変更手順書**
  （第 2章 申請）」。#1370）、出典の節（見出しが「出典」「参考資料」など）の行（#1317）。
  規則は RAG の `operation_audit.is_citation_line` とそろえ、両方のテストが
  platform/contracts/answer-passages/citation-lines.json の同じ事例で確かめる。
- 表の形: Markdown の表の区切りの行（`|---|---|`）・区切り線（`---`）と、区切りの行の直前の
  見出しの行（短い語だけのセル）。表の本文の行は主張のまま（#1317）。
- 質問: 「？」で終わる文（後ろの「（はい／いいえ）」などの選択肢の括弧は許す）と、情報を
  求める依頼の文（「教えてください」など）、質問の直後の短い選択肢の箇条書き。主張や操作を抱き合わせた文は除く。
- 不足: 資料に記載が無い・資料からは確かめられないことだけを述べる文と、答えられないと言い切る
  拒答の文（「…示すことはできません」「回答はできません」。#1317）。数量・逆接・操作を含む文は除く。
- 確かめられていない点: その見出しと、その節の行（操作の指示を除く）。

補足の段落（見出しが「補足」「注記」「備考」などの節の行と、「なお、」「補足：」で始まる段落）は
`supplement_passages` で判定する（#1475）。主張かどうかは変えず、回答の対応（`answer_outcome`）が、
結論の段落で答えた後の補足の不足の文を不足の印にしないために使う。

別に、利用者に現場のデータ・記録の確認を求める段落（「認証ログでエラーの時刻の行を確認して
ください」など）を `environment_check_passages` で判定する（#1317）。こちらは主張のまま（検証で
確かめ、外す）で、回答の対応（`answer_outcome`）が「現場の確認が要る」の印に使う。

段落の分け方は RAG の `rag_engine.generation.operation_audit.answer_passages` と同じ（`answer_quote`
はこの段落の原文。製品をまたいで import しないため写す）。括弧の中の「。」では分けない（#1455）。
両方のテストが platform/contracts/answer-passages/sentence-splits.json の同じ事例で確かめる。
"""

from __future__ import annotations

import re

KIND_HEADING = "heading"
KIND_CITATION = "citation"
KIND_QUESTION = "question"
KIND_ABSENCE = "absence"
KIND_UNVERIFIED = "unverified_section"
KIND_TABLE = "table"

# ---- 段落の分け方（RAG と同じ規則） ----------------------------------------------

_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")
_PASSAGE_CHARS = 600
# 中の「。」で文を分けない括弧（開きと閉じ。#1455）。
_BRACKET_PAIRS = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】"}
_CLOSING_BRACKETS = frozenset(_BRACKET_PAIRS.values())
# 前の文に付ける断片（太字・強調の印と閉じ括弧・句点だけ。#1455）。
_MARKS_ONLY = re.compile(r"[\s*_`」』）)】。]*")


def sentence_ranges(line: str) -> list[tuple[int, int]]:
    """1 行を文に分けた範囲（開始・終了の位置。前後の空白を含む）。

    「。」で文を分けるが、開いた括弧（「」『』（）()【】）の中の「。」では分けない（括弧の深さを数える。
    #1455）。閉じない括弧は行の終わりまで続くとみなし、行の終わりで切る。開いていない閉じ括弧は数えない。
    文の区切りの「。」の直後の閉じ括弧は前の文に含め、太字の印（`**`）・閉じ括弧だけの断片は前の文に付ける。
    """
    ranges: list[tuple[int, int]] = []
    expected: list[str] = []
    start = index = 0
    while index < len(line):
        char = line[index]
        if char in _BRACKET_PAIRS:
            expected.append(_BRACKET_PAIRS[char])
        elif char in _CLOSING_BRACKETS and char in expected:
            # 対応する開きの括弧まで閉じる（その間の閉じていない括弧も閉じたとみなす）。
            while expected.pop() != char:
                pass
        elif char == "。" and not expected:
            end = index + 1
            while end < len(line) and line[end] in _CLOSING_BRACKETS:
                end += 1
            ranges.append((start, end))
            start = index = end
            continue
        index += 1
    if start < len(line):
        ranges.append((start, len(line)))
    merged: list[tuple[int, int]] = []
    for begin, end in ranges:
        if merged and _MARKS_ONLY.fullmatch(line[begin:end]):
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return merged


def passage_spans(line: str) -> list[tuple[int, int, str]]:
    """1 行の段落（行の中の開始・終了の位置と原文）。"""
    pieces = [(0, len(line))] if _QUOTE_ONLY_LINE.fullmatch(line) else sentence_ranges(line)
    spans: list[tuple[int, int, str]] = []
    for start, end in pieces:
        raw = line[start:end]
        value = raw.strip()
        offset = start + len(raw) - len(raw.lstrip())
        for index in range(0, len(value), _PASSAGE_CHARS):
            text = value[index : index + _PASSAGE_CHARS]
            spans.append((offset + index, offset + index + len(text), text))
    return spans


# ---- 主張ではない段落の判定 --------------------------------------------------------

# 箇条書き・番号の印。
_BULLET = re.compile(r"^(?:[-*+・•]\s*|[0-9０-９]{1,3}[.)．）]\s*|[（(][0-9０-９]{1,3}[）)]\s*)")
# 操作の指示（RAG の `is_operation_instruction` と同じ考え方。これを含む段落は主張として確かめる）。
_OPERATION = re.compile(
    r"(?:開き|押し|選び|選択し|入力し|変更し|修正し|登録し|保存し|実行し|実施し|クリックし|削除し"
    r"|申請し|設定し|付与し|解除し|無効にし|有効にし)(?:ます|てください|て頂|ていただ|、|て)"
    r"|(?:を開く|を押す|を選択する|を入力する|を保存する|を削除する|に遷移する|に遷移します)"
    r"|(?:登録|選択|入力|変更|修正|保存|実行|削除|消去|申請|設定)(?:すれば|すると|することで|できます|可能)"
    r"|(?:押下|クリック|印刷|再発行)(?:する|すれば|すると|し|できます|可能)"
    r"|\b(?:click|press|navigate to|save changes|delete|erase)\b",
    re.IGNORECASE,
)
# 述語で終わる（見出し・ラベル・出典ではなく文）。
_PREDICATE_END = re.compile(
    r"(?:ます|ません|です|でした|ました|ください|ない|無い|ある|する|できる|れる|った|いた|した|だ)"
    r"[。．.!！]?$"
)
_MD_HEADING = re.compile(r"#{1,6}\s+(?P<title>[^\n]{1,60})")
_BOLD_LINE = re.compile(r"\*\*(?P<title>[^*\n]{1,40})\*\*\s*[:：]?")
_BRACKET_HEADING = re.compile(r"【(?P<title>[^】。\n]{1,30})】\s*[:：]?")
_LABEL_HEADING = re.compile(r"(?P<title>[^。！？!?\n:：]{1,30})[:：]")
# ラベルの「：」の直前の注記の括弧（「補足（資料の注記。同シート A1:A2）：」。#1455）。
_LABEL_NOTE = re.compile(r"[（(](?P<note>[^（）()\n]{1,60})[）)](?=\s*[:：]\s*$)")

_CITATION_LABEL = re.compile(
    r"[（(【〔\[]?\s*(?:出典|根拠|参照|参考|引用|引用元|参考資料|根拠資料|出所|ソース"
    r"|sources?|references?|citations?"
    # 出典の位置のラベル（#1317。「- セクション: 「…」 → 2. …」「- ページ: 1」）。
    r"|セクション|節|章|ページ|頁|文書名|資料名|ファイル名|シート|証拠|証拠\s*ID"
    r"|evidence(?:[ _]?id)?|page|section|document"
    # 要素の定位子のラベル（#1370。「*Locator*: `doc:…/page:3/el:12`」。#1330）。
    r"|locator|ロケータ|ロケーター|定位子)\s*[:：]\s*(?P<rest>.+?)\s*[）)】〕\]]?",
    re.IGNORECASE,
)
# Markdown の強調（「**根拠**：…」「*Locator*:」のラベル。出典の判定だけで外す。
# #1370 で斜体の `*` も外す）。
_EMPHASIS = re.compile(r"\*{1,2}|__|`")
# 質問の直後の、選択肢を並べた行（「選択肢: **個別**、**グループ**」。#1322 の確認の質問の次の手は
# 選択肢を添えさせる）。
_OPTIONS_LINE = re.compile(r"^(?:選択肢|候補)\s*[:：]\s*\S")
# 根拠の ID の括弧（【証拠 ID: 03ac…:1】・〔証拠 ID: 07e8…:5〕・【証拠ID cb0a…:2】・【証拠1】・
# 【evidence_id: …】。〔〕［］[] は #1370）。
_EVIDENCE_REF = re.compile(
    r"[【〔［\[]\s*(?:証拠|根拠|出典|evidence|source)(?:\s*_?(?:ID|id))?\s*[:：]?"
    r"[\s0-9A-Za-z:_\-,，、…①-⑳]{0,200}[】〕］\]]",
    re.IGNORECASE,
)
# 「…」の題（文ではないもの）。
_TITLE_QUOTE = re.compile(r"[「『]([^「」『』\n]{1,80})[」』]")
# 節の参照（「6. アカウントの削除」「第 3 章」）。
_SECTION_REF = re.compile(
    r"(?:第\s*[0-9０-９]+\s*[章節条項]|[0-9０-９]+(?:\.[0-9０-９]+)*[.．]\s*)[^。\n]{0,30}"
)
# 出典の行に残ってよい記号（表の区切り・矢印・句読点・「同上」・頁の数字）。
_REFERENCE_FILLER = re.compile(
    r"[|\s→>＞\-–—‑―、,，:：/／]+|同上|(?:ページ|頁|p\.|page)\s*[0-9０-９]+",
    re.IGNORECASE,
)
# Markdown の表の区切りの行（`|---|:---:|`）と区切り線（`---`・`***`）。
_TABLE_SEPARATOR = re.compile(r"\|?(?:\s*:?-{3,}:?\s*\|)+(?:\s*:?-{3,}:?\s*)?|\|?\s*:?-{3,}:?\s*")
_RULE = re.compile(r"(?:-{3,}|\*{3,}|_{3,})")
_TABLE_ROW = re.compile(r"\|.*\|")
_DOCUMENT_MARK = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b|(?:\bpage\b|\bp\.\s*\d|\bpp\.|ページ|頁"
    r"|\bsection\b|\bsheet\b|シート|第\s*[0-9０-９]+\s*(?:章|節|条|項))",
    re.IGNORECASE,
)
_BRACKETED = re.compile(r"(?:[【\[（(][^】\]）)\n]{1,200}[】\]）)][\s、,，]*)+")
_BRACKET_PART = re.compile(r"[【\[（(]([^】\]）)\n]{1,200})[】\]）)]")
_FILE_LINE = re.compile(
    r"\S[^。\n]{0,160}\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b[^。\n]{0,80}", re.IGNORECASE
)
_FILE_NAME = re.compile(
    r"[^\s「」『』（）()【】\[\]、,，|]+\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b", re.I
)
_LINK_LINE = re.compile(r"(?:\[[^\]\n]{1,200}\]\([^)\s]{1,500}\)[\s、,，]*)+|https?://\S+")
# 出典の節の見出し。
_CITATION_SECTION = re.compile(
    r"出典|参照|参考資料|参考|根拠資料|引用元|sources?|references?", re.IGNORECASE
)
# 「確かめられていない点」の節の見出し（Agent の注記と、RAG・モデルが付ける同じ意味の見出し）。
_UNVERIFIED_SECTION = re.compile(
    r"確かめられていない点|確かめられない点|確認できていない点|確認できない点|未確認の(?:点|事項)"
    r"|資料からは確認できない点|資料で確かめられない点|不明な点"
)

# 「？」で終わる質問（後ろの選択肢の括弧は許す）。
# 後ろの注記の括弧（「（はい／いいえ）」「【clarification: target】」。#1317）は許す。
_QUESTION = re.compile(r".*[？?]\s*(?:[（(【][^）)】\n]{1,40}[）)】]\s*)*[。]?")
# 情報を求める依頼の文（利用者に答えてもらう）。操作の依頼（「確認してください」など）は含めない。
_REQUEST = re.compile(
    r".*(?:教えてください|教えていただけますか|お知らせください|お教えください|お聞かせください"
    r"|ご回答ください|ご返答ください|お答えください|確認させてください|お伺いします|伺います"
    # 確認の質問に添える回答の依頼（「ご回答をお願いします。」。#1322）。
    r"|(?:ご回答|ご返答|お答え|お返事|ご返信)を?お願い(?:します|いたします|致します)"
    # 選択肢から選んでもらう依頼（「ご希望の方をお選びください。」。#1322）。
    r"|(?:どちらか|いずれか|ご希望の(?:方|もの))を?お選びください)[。．!！]?"
)
# 質問に抱き合わせた主張（「〜できますが、よろしいですか？」）。
_ADVERSATIVE = re.compile(r"(?:ますが|ですが|ましたが|ませんが|ものの|けれど|けど|ただし)")

_ABSENCE_SUBJECT = re.compile(
    r"資料|文書|マニュアル|ドキュメント|規程|規定|ガイド|ナレッジ|検索|記載|記述|根拠|証拠|手順書"
)
_ABSENCE_END = re.compile(
    r"(?:記載|記述|説明|情報|言及|該当(?:する)?(?:箇所|内容|資料|記載)?|根拠|定め|規定)"
    r"[^。]{0,24}?(?:ありません|見つかりません|見当たりません|されていません|含まれていません|無く|なく"
    r"|ない|無い)(?:でした)?[。．]?$"
    r"|(?:確認|特定|判断|判別|把握)(?:でき|出来)ません(?:でした)?[。．]?$"
    r"|確かめられません(?:でした)?[。．]?$"
    r"|(?:分かり|わかり)ません(?:でした)?[。．]?$"
    r"|(?:お答え|回答|ご案内|案内)(?:でき|出来)ません(?:でした)?[。．]?$"
    # 資料に記載が無いので分からない（#1317。「…資料に記載がないため、不明です。」）。
    r"|不明(?:です|でした)?[。．]?$"
)
# 文末の補足の括弧（「…含まれていませんでした（取得した全証拠の抜粋を参照）。」。#1317）。
_TRAILING_NOTE = re.compile(r"\s*[（(][^（()）\n]{1,60}[）)](?=\s*[。．]?\s*$)")
# 検索の結果・資料に該当が無かった文（#1317。「検索結果でも、該当する業務ガイドは返ってきません
# でした。」）。画面の検索の振る舞い（「利用者を検索してもヒットしません」）と区別するため、主語を
# 検索の結果・資料に限る。
_NOT_FOUND_SUBJECT = re.compile(r"検索結果|業務ガイド|資料|文書|根拠|証拠|ナレッジ")
_NOT_FOUND_END = re.compile(r"(?:返って(?:き|こ)|返され|ヒットし|得られ)ません(?:でした)?[。．]?$")
# 答えられないと言い切る拒答の文（#1317。「推測で金額を示すことはできません。」「根拠のある回答は
# できません。」）。答える・示す動詞に限る（「削除することはできません」のような事実の主張は除く）。
_REFUSAL_END = re.compile(
    r"(?:(?:示す|お示しする|提示する|明示する|答える|お答えする|回答する|案内する|ご案内する"
    r"|伝える|お伝えする|断定する)こと(?:は|が|も)?"
    r"|(?:回答|ご回答|お答え|ご案内|案内|お示し|提示|明示|お伝え|断定)(?:は|を|も)?)"
    r"(?:でき|出来)(?:ません|かねます)(?:でした)?[。．]?$"
    r"|(?:示せ|答えられ)ません(?:でした)?[。．]?$"
)
# 不足の文に混ざった主張（数量・逆接・「〜は〜で、」の言い切り）。
_ABSENCE_CLAIM = re.compile(
    r"[0-9０-９]+\s*(?:円|日|時間|分|秒|件|%|％|回|か月|ヶ月|カ月|年|名|人|GB|MB)"
    r"|ますが|ですが|ものの|けれど|けど|ただし"
    r"|(?:は|が)[^。、]{1,20}(?:で|であり|となり)、"
)
# 分岐の見出し・ラベル（#1317。「個別利用者に付与する場合」「原因 A の場合の対処：」）。
_CASE_LABEL = re.compile(
    r"(?P<label>[^。\n]{1,40}?(?:場合|とき)(?:の(?:手順|対処|操作|対応|方法))?)\s*(?:は)?\s*[:：]?"
)
_CASE_LEAD = re.compile(
    r"(?P<label>[^。:：\n]{1,40}?(?:場合|とき)(?:の(?:手順|対処|操作|対応|方法))?)\s*[:：]"
)
# 現場のデータ・記録の確認を求める段落（#1317）の、確かめる対象と確かめる依頼の語。
# 「ログイン」「ログアウト」はログではない。文書・資料を読むよう促す文（知識の不足）は含めない。
_ENVIRONMENT_TARGET = re.compile(
    r"ログ(?!イン|アウト|ラム)|\.log\b|記録|履歴|実データ|実際の(?:値|データ|件数|合計|明細|設定)"
    r"|設定値|(?:現在|今)の(?:値|設定|状態)|件数|明細|ステータス|画面で",
    re.IGNORECASE,
)
_ENVIRONMENT_CHECK = re.compile(
    r"ご確認ください|(?:確認|照合|比較|突き合わせ|チェック)(?:して(?:ください|下さい|いただ)|します|し[、て])"
    r"|確か(?:めてください|めます|め[、て])"
    r"|(?:確認|照合|比較|チェック)(?:する必要が(?:あります|ある)|が必要(?:です)?)"
    r"|確かめる必要が(?:あります|ある)"
)
# 出典の行に無い述語（括弧の外にあれば文。「…は **毎月 10 日** です（…）【….pdf】.」。#1317）。
_PREDICATE_IN = re.compile(r"です|ます|ません|でした|ました|ください")
_BRACKETS_ANY = re.compile(r"[（(【〔\[「『][^（()）【】〔〕\[\]「」『』\n]*[）)】〕\]」』]")
# 括弧で囲まれた段落（「（根拠：検索結果に…記述は含まれていません）」。#1317）。
_ENCLOSED = re.compile(r"[（(](?P<inner>[^\n]+)[）)]")
# 記号だけの段落（「**」「---」「>」。強調の閉じが句点の後の段落に分かれたもの。#1317）。
_MARKUP_ONLY = re.compile(r"[*_`>#|~\-\s]+")


def _strip_bullet(text: str) -> str:
    return _BULLET.sub("", text.strip(), count=1).strip()


def _is_sentence(title: str) -> bool:
    return bool(_PREDICATE_END.search(title.strip().rstrip("*").strip()))


def _heading_title(text: str) -> str | None:
    """見出しなら見出しの語（見出しでなければ None）。"""
    value = text.strip()
    for pattern in (_MD_HEADING, _BOLD_LINE, _BRACKET_HEADING):
        match = pattern.fullmatch(value)
        if match:
            title = match.group("title").strip().strip("*").strip()
            if title and not _is_sentence(title) and not _OPERATION.search(title):
                return title
            return None
    label = _strip_bullet(value)
    match = _LABEL_HEADING.fullmatch(label)
    note = _LABEL_NOTE.search(label) if match is None else None
    if note and not _is_sentence(note.group("note")) and not _OPERATION.search(note.group("note")):
        # ラベルの後ろの注記の括弧（「補足（資料の注記。同シート A1:A2）：」）は除いて確かめる。
        match = _LABEL_HEADING.fullmatch(label[: note.start()] + label[note.end() :])
    if match:
        title = match.group("title").strip().strip("*").strip()
        if title and not _is_sentence(title) and not _OPERATION.search(title):
            return title
    phrase = heading_phrase(value)
    if phrase is not None:
        return phrase
    return value if is_lead_in(value) else None


# ---- 見出しの名詞句と導入の文（#1469）。RAG の `operation_audit.heading_phrase` /
# `is_lead_in` と同じ規則で、両方のテストが platform/contracts/answer-passages/headings.json の
# 同じ事例で確かめる。
# 見出しの後ろの注記の括弧（「補足（資料の記載）」
# 「2. 略号の意味（組織規程 第 2 章 部署と略号／1 頁）」）。
_HEADING_NOTE = re.compile(r"[（(](?P<note>[^（）()\n]{1,60})[）)]\s*$")
# 「…」の語（「略号「運」の意味」）。中の「は」「が」は言い切りの助詞に数えない。
_HEADING_QUOTED = re.compile(r"[「『][^「」『』\n]{1,40}[」』]")
# 数量（「30 分」「10 年」。主張の値）。年度（「2026年度」）・行・頁の番号は数えない。
_HEADING_QUANTITY = re.compile(
    r"[0-9０-９]+\s*(?:円|日|時間|分|秒|件|%|％|回|か月|ヶ月|カ月|年(?!度)|名|人|GB|MB)"
)
# 見出しの語に残してよい番号（章・節の番号と年度）。ほかの数字（値の「3 点」「SYS-103」）を
# 含む行は主張のまま。
_HEADING_NUMBERED_NAME = re.compile(r"第\s*[0-9０-９]+\s*[章節条項]|[0-9０-９]+\s*年度")
# 見出しの名詞句の終わりの語（項目・区分・資料の名前。値の名前「運行管理課長」「極秘」では
# 終わらない）。
_HEADING_END = re.compile(
    r"(?:根拠|補足|注記|備考|参考|前提|結論|回答|まとめ|要点|概要|経緯|理由|背景|資料|台帳|規程|規定|手順書"
    r"|マニュアル|ガイド|文書|内容|事項|点|条件|規則|ルール|手順|方法|手段|流れ|結果|定め|扱い|対応|範囲|対象"
    r"|期限|期間|時間帯|保守枠|日程|重要度|機密区分|区分|部署|担当|承認者|連絡先|窓口|代理|意味|正式名|名称"
    r"|略号|関係|つながり"
    # 分岐の見出し（「重要度 C のシステムで障害が起きたとき」）と、問いの形の見出し
    # （「承認者がどう決まるか」）。
    r"|とき|場合|か)$"
)
# 番号（「1.」「（2）」）。番号つきの行は段落だけでは決めず、次の行がその下の内容のときだけ見出し
# （`numbered_heading_lines`。#1481 / #1482）。番号の行が続く一覧の項目は主張のまま。
_HEADING_NUMBER = re.compile(r"^(?:[0-9０-９]{1,3}[.)．）]|[（(][0-9０-９]{1,3}[）)])")
# 「…の…」の名詞句（「重要度 B の復旧目標」「連携元のシステム」）。「ので」「のに」「のみ」は名詞を
# つなぐ「の」に数えない。
_HEADING_NO_PHRASE = re.compile(r"[^\sの]\s*の(?![でにみ])\s*[^\sの]")
# 番号つきの見出しの下の内容の行（箇条書き・「→」。字下げした行も、字下げした番号の行も下の内容）。
_SUB_CONTENT = re.compile(r"[-*+]\s|[・•→]")
# 値の名前（人・組織・役職）の終わり。番号つきの「1. 情報システム部の課長」は見出しではなく値の
# 主張のまま。
_HEADING_VALUE_END = re.compile(r"(?:長|部|課|室|係|班|局|者|員|役|氏|様|さん)$")
# 分岐・問いの形の見出しの終わり（この形だけ、中の「が」「を」を許す）。
_HEADING_CLAUSE_END = re.compile(r"(?:とき|場合|か)$")
# 見出しの中の、分岐の節（「承認者が不在のときの代理」の「が不在のとき」）。
_HEADING_CASE_CLAUSE = re.compile(r"[がを][^はがを、。]{1,15}?(?:とき|場合)")
# 導入の文の先頭の接続の語（「そのため、作業できる時間帯は次のとおりです。」。この後の「、」は
# 主語の区切りに数えない。#1481）。
_LEAD_IN_CONNECTIVE = re.compile(
    r"^(?:そのため|このため|したがって|従って|なお|また|ただし|つまり|具体的には|結果として"
    r"|以上から|以上より|以上のことから)[、，,]\s*"
)
# 以降の箇条書きを導くだけの導入の文（「根拠は次のとおりです。」「以下に示します。」）。
_LEAD_IN = re.compile(
    r"(?P<subject>[^。！？!?\n]{1,24}?)(?:は|が)、?\s*(?:次|以下)の(?:とおり|通り)"
    r"(?:です|となります|になります|でした)?[。．:：]?"
    r"|(?:(?P<object>[^。！？!?\n]{1,24}?)を)?(?:次|以下)に(?:示し|まとめ|挙げ|整理し)ます[。．:：]?"
)
# 利用者の答え・質問への礼の定型の文（「ご回答ありがとうございます。」）。
_COURTESY = re.compile(
    r"(?:ご(?:回答|返答|返信|連絡|質問)|お問い合わせ|お答え)(?:いただき)?、?ありがとうございます[。．!！]?"
)


def _heading_note_ok(note: str) -> bool:
    """見出しの注記の括弧が、言い切り・操作・数量を含まない補足か（「（承認者は人事部長です）」は主張）。"""
    outside = _HEADING_QUOTED.sub(" ", note)
    return (
        "。" not in note
        and not _is_sentence(note)
        and not _OPERATION.search(note)
        and not _HEADING_QUANTITY.search(note)
        and "は" not in outside
    )


def heading_phrase(text: str) -> str | None:
    """主張でない見出しの名詞句なら、注記の括弧・番号・強調を除いた見出しの語（#1469）。

    見出しでなければ None。「補足（資料の記載）」「根拠」「回答に使った資料」「**重要度 C の
    システムで障害が起きたとき**」のように、句点で終わらない短い名詞句で、項目・区分・資料の名前
    （`_HEADING_END`）か分岐・問いの形（「…とき」「…か」）で終わるもの。述語（です・ます・する…）・
    操作・数量・言い切りの「は」を含む行（「重要度は A です」「承認者は管理本部長」）は主張のまま。
    番号つきの行（「1. 人事評価システムの重要度」）は段落だけでは見出しにせず、回答の中の次の行で
    決める（番号の行が続く一覧の「1. 変更の内容と理由」は主張。`numbered_heading_lines`。
    #1481 / #1482）。
    """
    found = _heading_value(text)
    if not found or _HEADING_NUMBER.match(text.strip()):
        return None
    return found[0] if _HEADING_END.search(found[0]) else None


def _numbered_heading_phrase(text: str) -> str | None:
    """番号つきの見出しの語（「1. 人事評価システムの重要度」。#1481 / #1482）。

    終わりの語が一覧（`_HEADING_END`）にあるか、一覧に無くても「…の…」の名詞句のもの
    （「2. 重要度 B の復旧目標」）。人・組織・役職の名前で終わる「…の…」の行（「1. 情報システム部の
    課長」）と、述語・操作・数量・言い切りの「は」を含む行（「1. **ID 管理システムの重要度は A**」）
    は値の主張のまま。見出しかどうかは次の行で決める（`numbered_heading_lines`）。
    """
    found = _heading_value(text)
    if not found or not _HEADING_NUMBER.match(text.strip()):
        return None
    value, outside = found
    if _HEADING_END.search(value):
        return value
    if _HEADING_NO_PHRASE.search(outside) and not _HEADING_VALUE_END.search(value):
        return value
    return None


def numbered_heading_lines(text: str) -> set[str]:
    """回答の中の、番号つきの見出しの行（前後の空白を除いた行。#1481 / #1482）。

    番号つきの見出しの語（`_numbered_heading_phrase`）の行で、次の（空でない）行がその下の内容
    （字下げした行か、箇条書き・「→」の行）のもの。番号の行が続く一覧（「申請書に書く 4 項目」の
    「1. 変更の内容と理由」〜「4. 作業の日時」）の項目と、下に内容の無い行は、終わりの語が一覧に
    あっても主張のまま（段落だけで決めると、一覧の項目を見出しとして監査から外す。#1482）。RAG の
    `operation_audit.numbered_heading_lines` と同じ規則。
    """
    lines = [line for line in text.split("\n") if line.strip()]
    found = set()
    for line, following in zip(lines, lines[1:], strict=False):
        indent = len(line) - len(line.lstrip())
        below = len(following) - len(following.lstrip()) > indent or bool(
            _SUB_CONTENT.match(following.strip())
        )
        if below and _numbered_heading_phrase(line):
            found.add(line.strip())
    return found


def _heading_value(text: str) -> tuple[str, str] | None:
    """見出しの名詞句の共通の確かめ（注記の括弧・番号・強調を除いた語と、「…」の語を除いた残り）。"""
    if is_citation(text):
        # 文書名と場所だけの行（「1. **システム変更手順書**（第 2章 申請）」）は出典の行（#1370）。
        return None
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip().rstrip(":：").strip()
    while match := _HEADING_NOTE.search(value):
        if not _heading_note_ok(match.group("note")):
            return None
        value = value[: match.start()].strip()
    if not value or len(value) > 30 or re.search(r"[。．！？!?、,，:：]", value):
        return None
    if (
        _is_sentence(value)
        or _OPERATION.search(value)
        or re.search(r"[0-9０-９]", _HEADING_NUMBERED_NAME.sub(" ", value))
    ):
        return None
    outside = _HEADING_QUOTED.sub(" ", value)
    if "は" in outside:
        return None
    # 「が」「を」は、分岐・問いの形の見出しか、「…が不在のとき」の節の中だけに許す。
    if not _HEADING_CLAUSE_END.search(value) and re.search(
        r"[がを]", _HEADING_CASE_CLAUSE.sub(" ", outside)
    ):
        return None
    return value, outside


def is_lead_in(text: str) -> bool:
    """以降の箇条書きを導くだけの導入の文（「根拠の積み上げは次のとおりです。」）と礼の定型の文か。

    先頭の接続の語の後の「、」（「そのため、作業できる時間帯は次のとおりです。」）は許す（#1481）。
    主語・目的語に数量・操作・言い切りの「は」・「、」を含む文（「保管期間は 10 年で、内訳は
    次のとおりです。」）は主張のまま（#1469）。
    """
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
    if _COURTESY.fullmatch(value):
        return True
    match = _LEAD_IN.fullmatch(_LEAD_IN_CONNECTIVE.sub("", value, count=1))
    if not match:
        return False
    head = match.group("subject") or match.group("object") or ""
    return not (
        _HEADING_QUANTITY.search(head) or _OPERATION.search(head) or "は" in head or "、" in head
    )


def is_heading(text: str) -> bool:
    """見出しだけの段落か。"""
    return _heading_title(text) is not None


def is_structure(text: str) -> bool:
    """見出し・出典・表の区切りだけの段落か（本文から内容を外した後に、それだけ残っても意味が無いもの）。"""
    return is_heading(text) or is_citation(text) or is_table_rule(text)


def is_table_rule(text: str) -> bool:
    """表の区切りの行（`|---|---|`）・区切り線（`---`）・記号だけの段落（`**`）か（#1317）。"""
    value = text.strip()
    return bool(
        _TABLE_SEPARATOR.fullmatch(value) or _RULE.fullmatch(value) or _MARKUP_ONLY.fullmatch(value)
    )


def _outside_brackets(value: str) -> str:
    """括弧（（）【】「」[]）の中を除いた文字列。"""
    while True:
        stripped = _BRACKETS_ANY.sub(" ", value)
        if stripped == value:
            return value
        value = stripped


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_header_cell(cell: str) -> bool:
    value = _EMPHASIS.sub("", cell).strip()
    return (
        0 < len(value) <= 20
        and "。" not in value
        and not _is_sentence(value)
        and not _OPERATION.search(value)
    )


def table_header_lines(answer: str) -> set[str]:
    """Markdown の表の見出しの行（区切りの行の直前の、短い語だけのセルの行。#1317）。

    見出しかどうかは次の行で決まるので、回答全体から判定する。表の本文の行は主張のまま。
    """
    lines = [line.strip() for line in answer.split("\n")]
    return {
        line
        for line, following in zip(lines, lines[1:], strict=False)
        if _TABLE_ROW.fullmatch(line)
        and "|" in following
        and is_table_rule(following)
        and all(_is_header_cell(cell) for cell in _table_cells(line))
    }


def _document_title(title: str) -> bool:
    return bool(
        re.search(r"手順書|マニュアル|ガイド|規程|規定|資料|文書|メモ|第\s*[0-9０-９]+\s*版", title)
    )


def _is_reference_only(value: str) -> bool:
    """根拠の ID・文書名・頁の括弧・文の無い「…」の題（と節の参照）だけの段落か（#1317）。

    「| 「1. 前提」 （ページ1）【証拠 ID: 03ac…:1】 |」（表のセルに分かれた出典）や
    「- 「サンプル業務ポータル 運用手順書 第3版」 6. アカウントの削除」。文書を示す印（根拠の ID・
    文書名・頁の括弧・文書の題）が 1 つも無ければ出典にしない（短い「…」だけの行は主張のまま）。
    """
    if _OPERATION.search(value) or _is_sentence(value.strip("|").strip()):
        return False
    found = False

    def drop_document(match: re.Match[str]) -> str:
        nonlocal found
        if _DOCUMENT_MARK.search(match.group(1)):
            found = True
            return " "
        return match.group(0)

    def drop_title(match: re.Match[str]) -> str:
        nonlocal found
        title = match.group(1)
        if "。" in title or _is_sentence(title) or _OPERATION.search(title):
            return match.group(0)
        found = found or _document_title(title)
        return " "

    rest, count = _EVIDENCE_REF.subn(" ", value)
    found = count > 0
    rest = _BRACKET_PART.sub(drop_document, rest)
    rest = _TITLE_QUOTE.sub(drop_title, rest)
    rest, count = _FILE_NAME.subn(" ", rest)
    found = found or count > 0
    rest = _REFERENCE_FILLER.sub(" ", rest).strip()
    if not found:
        return False
    return not rest or bool(_SECTION_REF.fullmatch(rest) and not _is_sentence(rest))


# 行末の場所の括弧（「（第 2章 申請）」「（p.12）」。#1370）。
_LOCATION_TAIL = re.compile(r"[（(〔\[]([^（()）〔〕\[\]\n]{1,80})[）)〕\]]\s*$")
# 文書名に無い、言い切りの助詞（「承認者は課長（第 3 章）」「申請先は規程が定める承認者」は主張）。
_STATEMENT_PARTICLE = re.compile(r"[はがを]")


def _is_document_location(value: str) -> bool:
    """文書名と場所の括弧だけの段落か（#1370）。

    「システム変更手順書（第 2章 申請）」「運用マニュアル（p.12）」。

    行末の場所の括弧（章・節・頁・シートなどの印を含むもの）を外した残りが、文書を示す語（手順書・規程・
    ファイル名など）を含む短い名前で、述語・操作・言い切りの助詞（は・が・を）を含まないときだけ出典にする。
    場所の後に本文が続く行（「…（第 2章）では、…」）は主張のまま。RAG の
    `operation_audit._is_document_location` と同じ規則。
    """
    rest = value.strip().rstrip("。．").strip()
    found = False
    while match := _LOCATION_TAIL.search(rest):
        location = match.group(1)
        if (
            not _DOCUMENT_MARK.search(location)
            or _is_sentence(location)
            or _OPERATION.search(location)
        ):
            break
        rest, found = rest[: match.start()].strip(), True
    if not found or not rest or len(rest) > 80 or "。" in rest:
        return False
    if _is_sentence(rest) or _OPERATION.search(rest):
        return False
    if _STATEMENT_PARTICLE.search(_outside_brackets(rest)):
        return False
    return bool(_document_title(rest) or _FILE_NAME.search(rest))


def is_citation(text: str) -> bool:
    """出典の行か。

    出典・定位子のラベル・文書名・頁・節の括弧・リンク・根拠の ID・文書名と場所だけの段落。
    RAG の `operation_audit.is_citation_line` と同じ規則で、両方のテストが
    platform/contracts/answer-passages/citation-lines.json の同じ事例で確かめる（#1370）。
    """
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
    if not value or "。" in value.rstrip("。"):
        return False
    # 括弧の外に述語があれば文（出典を添えた主張）。
    if _PREDICATE_IN.search(_outside_brackets(value)):
        return False
    label = _CITATION_LABEL.fullmatch(value)
    if label:
        return not _is_sentence(label.group("rest")) and not _OPERATION.search(label.group("rest"))
    if _LINK_LINE.fullmatch(value):
        return True
    if _BRACKETED.fullmatch(value) and all(
        _DOCUMENT_MARK.search(part) for part in _BRACKET_PART.findall(value)
    ):
        return True
    if _FILE_LINE.fullmatch(value) and not _is_sentence(value):
        return True
    return _is_reference_only(value) or _is_document_location(value)


def is_question(text: str) -> bool:
    """利用者への質問・情報を求める依頼の文か（主張や操作を抱き合わせた文は除く）。"""
    value = _strip_bullet(text)
    if not (_QUESTION.fullmatch(value) or _REQUEST.fullmatch(value)):
        return False
    return not _ADVERSATIVE.search(value)


def is_absence(text: str) -> bool:
    """資料に記載が無い・資料からは確かめられないことだけを述べる文か、答えられないと言い切る拒答の文か。

    強調（`**…**`）・段落を囲む括弧・文末の補足の括弧は判定の前に外す（#1317）。
    """
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
    enclosed = _ENCLOSED.fullmatch(value)
    if enclosed:
        value = enclosed.group("inner").strip()
    value = _TRAILING_NOTE.sub("", value)
    absent = (
        (_ABSENCE_SUBJECT.search(value) and _ABSENCE_END.search(value))
        or (_NOT_FOUND_SUBJECT.search(value) and _NOT_FOUND_END.search(value))
        or _REFUSAL_END.search(value)
    )
    if not absent:
        return False
    return not _ABSENCE_CLAIM.search(value) and not _OPERATION.search(value)


def case_label(text: str) -> str | None:
    """分岐の見出し・ラベル（「2. **個別に付与する場合**」「- グループの場合: …」）の語（#1317）。

    見出し・ラベルだけの段落か、段落の先頭の「…場合：」のラベルで、「場合」「とき」で終わるもの。
    文（述語のあるもの）は分岐のラベルにしない。
    """
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
    match = _CASE_LABEL.fullmatch(value) or _CASE_LEAD.match(value)
    if not match:
        return None
    label = match.group("label").strip()
    return None if _is_sentence(label) or _OPERATION.search(label) else label


def case_labels(answer: str) -> set[str]:
    """回答の中の、分岐の見出し・ラベルの語（異なる語の集合。#1317）。"""
    return {
        label
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
        if (label := case_label(text)) is not None
    }


def asks_environment_data(text: str) -> bool:
    """利用者に現場のデータ・記録（ログ・設定値・明細など）の確認を求める段落か（#1317）。

    「認証ログ（auth.log）でエラーの時刻の行を確認し、…」のように、確かめる対象と確かめる依頼の
    語の両方がある段落。操作の指示・質問は除く。主張かどうかとは別の印で、検証では主張として
    確かめる（資料に同じ見分け方があれば supported になる）。
    """
    value = _strip_bullet(text)
    return (
        bool(_ENVIRONMENT_TARGET.search(value) and _ENVIRONMENT_CHECK.search(value))
        and not _OPERATION.search(value)
        and not is_question(value)
    )


def _is_option(text: str) -> bool:
    """質問の直後の選択肢（短い箇条書きか「選択肢: …」の行。文・操作の指示は除く）。"""
    if not _BULLET.match(text.strip()):
        value = _EMPHASIS.sub("", text).strip()
        return (
            bool(_OPTIONS_LINE.match(value))
            and len(value) <= 80
            and "。" not in value
            and not _OPERATION.search(value)
        )
    value = _strip_bullet(text)
    return (
        0 < len(value) <= 40
        and "。" not in value
        and not _OPERATION.search(value)
        and not re.search(r"(?:ます|です)$", value)
    )


def passage_kind(text: str) -> str | None:
    """行の前後に依らない、1 つの段落の判定（主張なら None）。"""
    if is_table_rule(text):
        return KIND_TABLE
    if _heading_title(text) is not None:
        return KIND_HEADING
    if is_citation(text):
        return KIND_CITATION
    if is_question(text):
        return KIND_QUESTION
    if is_absence(text):
        return KIND_ABSENCE
    return None


def non_claim_passages(answer: str) -> dict[str, str]:
    """回答の中の、主張ではない段落（原文 → 判定）。

    同じ原文の段落が、ある場所では主張ではなく、別の場所では主張なら、主張として扱う（外すかどうかは
    原文で決めるため）。節（「確かめられていない点」・出典）は、次の見出しか、空行の後の箇条書きでは
    ない行まで続く。表の見出しの行（区切りの行の直前の行）は表の形として扱う（#1317）。
    """
    kinds: dict[str, str] = {}
    claims: set[str] = set()
    headers = table_header_lines(answer)
    numbered = numbered_heading_lines(answer)
    section: str | None = None
    options = False
    blank = False
    for line in answer.split("\n"):
        if not line.strip():
            # 質問と選択肢の間の空行では、選択肢の続きを切らない（#1322）。
            blank = True
            continue
        if blank and not _BULLET.match(line.strip()):
            section = None
        blank = False
        spans = passage_spans(line)
        if len(spans) == 1:
            title = _heading_title(spans[0][2])
            if title is None and spans[0][2] in numbered:
                title = spans[0][2]
            if title is not None:
                if _UNVERIFIED_SECTION.search(title):
                    section = KIND_UNVERIFIED
                elif _CITATION_SECTION.search(title):
                    section = KIND_CITATION
                else:
                    section = None
                options = title.endswith(("？", "?"))
                kinds.setdefault(
                    spans[0][2], KIND_UNVERIFIED if section == KIND_UNVERIFIED else KIND_HEADING
                )
                continue
        line_question = False
        kind: str | None
        for _start, _end, text in spans:
            kind = KIND_TABLE if text in headers else passage_kind(text)
            if kind is None and options and _is_option(text):
                kind = KIND_QUESTION
            if kind is None and section == KIND_UNVERIFIED and not _OPERATION.search(text):
                kind = KIND_UNVERIFIED
            if kind is None and section == KIND_CITATION and not _is_sentence(text):
                kind = KIND_CITATION
            if kind is None:
                claims.add(text)
            else:
                kinds.setdefault(text, kind)
            line_question = kind == KIND_QUESTION
        # 質問の直後の箇条書き（空行を挟んでもよい）は選択肢として扱う。
        options = line_question or (options and bool(_BULLET.match(line.strip())))
    return {text: kind for text, kind in kinds.items() if text not in claims}


# 補足の節の見出しの語（「補足（資料の記載）」の注記の括弧は見出しの語から外れる。
# 「参考」は出典の節）。
_SUPPLEMENT_SECTION = re.compile(r"(?:補足|注記|備考|注意|留意|その他)(?:事項|点|情報)?")
# 補足を導く段落の頭（「なお、…」「補足：…」「注記（同シート A1:A2）：…」）。
_SUPPLEMENT_LEAD = re.compile(
    r"(?:なお|ちなみに)[、,]"
    r"|(?:補足|注記|備考|注意|留意)(?:事項|点|情報)?\s*(?:[（(][^（）()\n]{1,60}[）)])?\s*[:：]"
)


def supplement_passages(answer: str) -> set[str]:
    """回答の中の、補足の段落（補足の節の行と、「なお、」「補足：」で始まる段落。#1475）。

    回答の対応（`answer_outcome`）が、結論の段落で答えた後の補足の不足の文（「現在の各環境の
    設定値は資料からは分かりません。」）を不足の印にしないために使う。節（見出しが「補足」「注記」
    「備考」など）は、`non_claim_passages` の節と同じく、次の見出しか、空行の後の箇条書きではない行
    まで続く。同じ原文の段落が補足の外にもあれば、補足として扱わない（保守的にする）。
    """
    inside: set[str] = set()
    outside: set[str] = set()
    numbered = numbered_heading_lines(answer)
    section = False
    blank = False
    for line in answer.split("\n"):
        if not line.strip():
            blank = True
            continue
        if blank and not _BULLET.match(line.strip()):
            section = False
        blank = False
        spans = passage_spans(line)
        if len(spans) == 1:
            title = _heading_title(spans[0][2])
            if title is None and spans[0][2] in numbered:
                title = spans[0][2]
            if title is not None:
                section = bool(_SUPPLEMENT_SECTION.fullmatch(_EMPHASIS.sub("", title).strip()))
                continue
        for _start, _end, text in spans:
            value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
            if section or _SUPPLEMENT_LEAD.match(value):
                inside.add(text)
            else:
                outside.add(text)
    return inside - outside


def environment_check_passages(answer: str) -> set[str]:
    """回答の中の、利用者に現場のデータ・記録の確認を求める段落（主張ではない段落を除く。#1317）。"""
    kinds = non_claim_passages(answer)
    return {
        text
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
        if text not in kinds and asks_environment_data(text)
    }


# 確認の質問に添える前置きと答え方の案内（#1454）。確認の質問だけの回答かの判定にだけ使い、
# 段落の判定（`non_claim_passages`）は変えない。
_CLARIFY_NOTE = re.compile(
    # 前置き（「条件を 1 つ確かめさせてください。」「その点がまだ分かっていません。」）。
    r".*(?:確認|確かめ)させて(?:ください|下さい|いただきます|いただけますか)[。．]?"
    r"|.*(?:が|は)(?:まだ)?(?:分かって|わかって|決まって|伺って|お聞きして)いません[。．]?"
    # 答え方の案内（「「個別」または「グループ」でお答えください。」）。
    r"|.*(?:お答え|ご回答|ご返答|お選び|お知らせ)(?:ください|下さい)[。．]?"
    # 答えた後の案内（「お答えいただいた内容に応じて、手順と必要な承認をご案内します。」）。
    r"|(?:お答え|ご回答|ご返答|回答)(?:いただいた|いただければ|いただき次第|の内容|に応じて)"
    r".*(?:ご案内|案内|お伝え|お知らせ|お答え|回答)(?:します|いたします|できます)[。．]?"
    # 答えが分かった後の案内の結び（「付与先が分かりましたら、その場合の手順をご案内します。」。
    # 回答が自ら案内する敬語の述語だけ。資料の主張の「案内します」は数えない）。
    r"|[^。]{0,30}?(?:分かり|わかり|決まり|確認でき|お分かりになり)(?:ましたら|次第)"
    r"[、,]?[^。]*(?:ご案内|ご説明|お伝え|お知らせ|お答え)(?:します|いたします|できます)[。．]?"
    r"|[^。]{0,30}?(?:分かれば|わかれば|決まれば|伺えれば|お聞きできれば"
    r"|(?:教えて|お知らせ|ご確認)(?:いただければ|いただけましたら|いただけると))"
    r"[、,]?[^。]*(?:ご案内|ご説明|お伝え|お知らせ|お答え)(?:します|いたします|できます)[。．]?"
    # 確かめる理由（「この条件によって手順・答えが変わるため、まず確認しています。」）。
    r"|.*(?:によって|に応じて|次第で)[^。]*(?:変わる|異なる|分かれる)ため、?[^。]*"
    r"(?:確認|確かめ|お伺い|お聞き)(?:しています|します|いたします|させてください)[。．]?"
)
_CLARIFY_NOTE_EDGES = "（()）"
# 前置き・案内に含まれると資料の主張とみなす数量（「3 営業日以内の手順」。`_ABSENCE_CLAIM` の
# 数量に「営業日」などの複合の単位を足したもの。「条件を 1 つ」は数えない）。
_CLARIFY_QUANTITY = re.compile(
    r"[0-9０-９]+\s*(?:営業日|営業時間|円|日|時間|分|秒|件|%|％|回|か月|ヶ月|カ月|年|名|人|GB|MB)"
)


def _is_clarification_question(text: str) -> bool:
    """確認の質問の段落か（太字の質問「**…ですか？**（はい／いいえ）」も含める。#1454）。"""
    return is_question(_EMPHASIS.sub("", text).strip())


def _is_clarification_note(text: str) -> bool:
    """確認の質問に添える前置き・答え方の案内か（#1454。操作・数量・逆接を含む文は除く）。"""
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip().strip(_CLARIFY_NOTE_EDGES).strip()
    # 括弧の中の「。」では段落を分けないので（#1455）、括弧の中の文ごとに確かめる。
    sentences = [value[start:end].strip() for start, end in sentence_ranges(value)]
    return (
        bool(value)
        and all(_CLARIFY_NOTE.fullmatch(sentence) for sentence in sentences if sentence)
        and not _OPERATION.search(value)
        and not _ABSENCE_CLAIM.search(value)
        and not _CLARIFY_QUANTITY.search(value)
    )


def is_clarification_only(answer: str) -> bool:
    """利用者への確認の質問だけの回答か（見出しと質問・選択肢だけで、質問が 1 つ以上ある）。

    質問の前置き（「条件を 1 つ確かめさせてください。」「その点がまだ分かっていません。」）と、
    答え方の案内・確かめる理由の括弧（「（「個別」または「グループ」でお答えください。…）」）も
    質問の一部として扱う（#1454。これらが主張として残ると、確認の質問に「資料の根拠を使って
    いない」の注記が付く）。
    """
    passages = [text for line in answer.split("\n") for _s, _e, text in passage_spans(line)]
    if not passages:
        return False
    kinds = non_claim_passages(answer)
    asked = False
    for text in passages:
        kind = kinds.get(text)
        if kind == KIND_QUESTION or _is_clarification_question(text):
            asked = True
        elif kind != KIND_HEADING and not _is_clarification_note(text):
            return False
    return asked


__all__ = [
    "KIND_ABSENCE",
    "KIND_CITATION",
    "KIND_HEADING",
    "KIND_QUESTION",
    "KIND_TABLE",
    "KIND_UNVERIFIED",
    "asks_environment_data",
    "case_label",
    "case_labels",
    "environment_check_passages",
    "heading_phrase",
    "is_absence",
    "is_citation",
    "is_clarification_only",
    "is_heading",
    "is_lead_in",
    "is_question",
    "is_structure",
    "is_table_rule",
    "non_claim_passages",
    "numbered_heading_lines",
    "passage_kind",
    "passage_spans",
    "supplement_passages",
    "table_header_lines",
]
