"""回答の段落の分け方と、主張ではない段落の決定的な判定（#1277・#1306）。

最終の検証（`answer_validation`）は、RAG の `rag_validate_answer` が段落（`answer_quote`）ごとに
返す判定で、根拠で確かめられない段落を回答から外す。ところが主張ではない段落（見出し・出典の行・
利用者への質問・「資料に記載がありません」という不足の文・「確かめられていない点」の節）まで
「確かめられない」として外すと、回答が読みにくくなり、拒答が拒答に見えなくなる（#1289 の評価）。

ここでは、そうした段落をモデルを使わずに判定する。判定は保守的にし、迷うものは主張として扱う
（確かめられない操作の手順・事実の主張は今までどおり外す）。

- 見出し: Markdown の見出し・太字だけの行・【…】・「…：」で終わる短いラベル。述語で終わる文や
  操作の指示を含むものは見出しにしない。
- 出典: 「出典：…」「根拠：…」「セクション：…」「ページ：…」などのラベルの行（ラベルの太字は許す）、
  文書名・頁・節を示す括弧だけの行、文書名だけの行、リンクだけの行、根拠の ID（【証拠 ID: …】）と
  文の無い「…」の題・頁の括弧だけの行（表の区切りの `|` は許す）、出典の節（見出しが「出典」
  「参考資料」など）の行（#1317）。
- 表の形: Markdown の表の区切りの行（`|---|---|`）・区切り線（`---`）と、区切りの行の直前の
  見出しの行（短い語だけのセル）。表の本文の行は主張のまま（#1317）。
- 質問: 「？」で終わる文（後ろの「（はい／いいえ）」などの選択肢の括弧は許す）と、情報を
  求める依頼の文（「教えてください」など）、質問の直後の短い選択肢の箇条書き。主張や操作を抱き合わせた文は除く。
- 不足: 資料に記載が無い・資料からは確かめられないことだけを述べる文と、答えられないと言い切る
  拒答の文（「…示すことはできません」「回答はできません」。#1317）。数量・逆接・操作を含む文は除く。
- 確かめられていない点: その見出しと、その節の行（操作の指示を除く）。

別に、利用者に現場のデータ・記録の確認を求める段落（「認証ログでエラーの時刻の行を確認して
ください」など）を `environment_check_passages` で判定する（#1317）。こちらは主張のまま（検証で
確かめ、外す）で、回答の対応（`answer_outcome`）が「現場の確認が要る」の印に使う。

段落の分け方は RAG の `rag_engine.generation.operation_audit.answer_passages` と同じ（`answer_quote`
はこの段落の原文。製品をまたいで import しないため写す）。
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
_SENTENCE = re.compile(r"[^。]+(?:。[」』）)]*|$)")
_PASSAGE_CHARS = 600


def passage_spans(line: str) -> list[tuple[int, int, str]]:
    """1 行の段落（行の中の開始・終了の位置と原文）。"""
    if _QUOTE_ONLY_LINE.fullmatch(line):
        pieces = [(0, len(line))]
    else:
        pieces = [(match.start(), match.end()) for match in _SENTENCE.finditer(line)]
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

_CITATION_LABEL = re.compile(
    r"[（(【\[]?\s*(?:出典|根拠|参照|参考|引用|引用元|参考資料|根拠資料|出所|ソース"
    r"|sources?|references?|citations?"
    # 出典の位置のラベル（#1317。「- セクション: 「…」 → 2. …」「- ページ: 1」）。
    r"|セクション|節|章|ページ|頁|文書名|資料名|ファイル名|シート|証拠|証拠\s*ID"
    r"|evidence(?:[ _]?id)?|page|section|document)\s*[:：]\s*(?P<rest>.+?)\s*[）)】\]]?",
    re.IGNORECASE,
)
# Markdown の強調（「**根拠**：…」のラベル。出典の判定だけで外す）。
_EMPHASIS = re.compile(r"\*\*|__|`")
# 根拠の ID の括弧（【証拠 ID: 03ac…:1】・【証拠ID cb0a…:2】・【証拠1】・【evidence_id: …】）。
_EVIDENCE_REF = re.compile(
    r"【\s*(?:証拠|根拠|出典|evidence|source)(?:\s*_?(?:ID|id))?\s*[:：]?"
    r"[\s0-9A-Za-z:_\-,，、…①-⑳]{0,200}】",
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
_QUESTION = re.compile(r".*[？?]\s*(?:[（(][^）)\n]{1,40}[）)])?\s*[。]?")
# 情報を求める依頼の文（利用者に答えてもらう）。操作の依頼（「確認してください」など）は含めない。
_REQUEST = re.compile(
    r".*(?:教えてください|教えていただけますか|お知らせください|お教えください|お聞かせください"
    r"|ご回答ください|ご返答ください|お答えください|確認させてください|お伺いします|伺います)[。．!！]?"
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
)


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
    match = _LABEL_HEADING.fullmatch(_strip_bullet(value))
    if match:
        title = match.group("title").strip().strip("*").strip()
        if title and not _is_sentence(title) and not _OPERATION.search(title):
            return title
    return None


def is_heading(text: str) -> bool:
    """見出しだけの段落か。"""
    return _heading_title(text) is not None


def is_structure(text: str) -> bool:
    """見出し・出典・表の区切りだけの段落か（本文から内容を外した後に、それだけ残っても意味が無いもの）。"""
    return is_heading(text) or is_citation(text) or is_table_rule(text)


def is_table_rule(text: str) -> bool:
    """Markdown の表の区切りの行（`|---|---|`）か区切り線（`---`）か（#1317）。"""
    value = text.strip()
    return bool(_TABLE_SEPARATOR.fullmatch(value) or _RULE.fullmatch(value))


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


def is_citation(text: str) -> bool:
    """出典の行（出典のラベル・文書名・頁・節の括弧・文書名・リンク・根拠の ID だけの段落）か。"""
    value = _EMPHASIS.sub("", _strip_bullet(text)).strip()
    if not value or "。" in value.rstrip("。"):
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
    return _is_reference_only(value)


def is_question(text: str) -> bool:
    """利用者への質問・情報を求める依頼の文か（主張や操作を抱き合わせた文は除く）。"""
    value = _strip_bullet(text)
    if not (_QUESTION.fullmatch(value) or _REQUEST.fullmatch(value)):
        return False
    return not _ADVERSATIVE.search(value)


def is_absence(text: str) -> bool:
    """資料に記載が無い・資料からは確かめられないことだけを述べる文か、答えられないと言い切る拒答の文か。

    強調（`**…**`）と文末の補足の括弧は判定の前に外す（#1317）。
    """
    value = _TRAILING_NOTE.sub("", _EMPHASIS.sub("", _strip_bullet(text)))
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
    """質問の直後の選択肢（短い箇条書き。文・操作の指示は除く）。"""
    if not _BULLET.match(text.strip()):
        return False
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
    section: str | None = None
    options = False
    blank = False
    for line in answer.split("\n"):
        if not line.strip():
            options, blank = False, True
            continue
        if blank and not _BULLET.match(line.strip()):
            section = None
        blank = False
        spans = passage_spans(line)
        if len(spans) == 1:
            title = _heading_title(spans[0][2])
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
        # 質問の直後の箇条書きは選択肢として扱う。
        options = line_question or (options and bool(_BULLET.match(line.strip())))
    return {text: kind for text, kind in kinds.items() if text not in claims}


def environment_check_passages(answer: str) -> set[str]:
    """回答の中の、利用者に現場のデータ・記録の確認を求める段落（主張ではない段落を除く。#1317）。"""
    kinds = non_claim_passages(answer)
    return {
        text
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
        if text not in kinds and asks_environment_data(text)
    }


def is_clarification_only(answer: str) -> bool:
    """利用者への確認の質問だけの回答か（見出しと質問・選択肢だけで、質問が 1 つ以上ある）。"""
    passages = [text for line in answer.split("\n") for _s, _e, text in passage_spans(line)]
    if not passages:
        return False
    kinds = non_claim_passages(answer)
    if any(text not in kinds for text in passages):
        return False
    found = {kinds[text] for text in passages}
    return KIND_QUESTION in found and found <= {KIND_QUESTION, KIND_HEADING}


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
    "is_absence",
    "is_citation",
    "is_clarification_only",
    "is_heading",
    "is_question",
    "is_structure",
    "is_table_rule",
    "non_claim_passages",
    "passage_kind",
    "passage_spans",
    "table_header_lines",
]
