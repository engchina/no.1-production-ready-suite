"""回答文と引用本文に対する決定的な判定。LLM を呼ばず、入力を変更しない。"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from typing import Any

from rag_engine.parsing.vision_prompt_rules import is_vision_enumeration_line


_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")
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
    Agent の ``answer_passages.sentence_ranges`` と同じ規則（両方のテストが
    platform/contracts/answer-passages/sentence-splits.json の同じ事例で確かめる）。
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


def answer_passages(text: str) -> list[dict[str, str]]:
    """改写せず文・改行で区切る。長文も600文字の連続範囲として保持する。

    句点直後の閉じ括弧は同じ文に含める。括弧の中の「。」では切らない（``sentence_ranges``。#1455）。
    括弧の途中で切ると、検証で前の半分を外したときに閉じ括弧の残る断片が本文に残る。行全体が1つの
    「…」の引用（原文のみ提示の行。表の引用は複数の文を含む）は、引用の途中で切らずに1段落として扱う。
    """
    result = []
    for line in text.split("\n"):
        pieces = [line] if _QUOTE_ONLY_LINE.fullmatch(line) else [
            line[begin:end] for begin, end in sentence_ranges(line)]
        for piece in pieces:
            value = piece.strip()
            for start in range(0, len(value), 600):
                result.append({"id": f"A{len(result) + 1}", "text": value[start:start + 600]})
    return result


def is_heading(text: str) -> bool:
    """短い操作見出しと見出し記法を識別する。普通の文を長さだけで除外しない。"""
    # 確定までの短い節名を本文と誤認すると、未監査の見出しだけで全手順が落ちる。
    # 状態条件（登録済みの場合等）や操作を含む文はこの限定規則に含めない。
    operation_word = r"(?:操作|入力|登録|追加|削除|変更|保存|確定|完了|実行)"
    if re.fullmatch(rf"{operation_word}(?:(?:を|の)?{operation_word}){{0,2}}(?:する|までの|の)?手順", text.strip()):
        return True
    if re.fullmatch(r"(?:確認|変更|操作|設定)(?:手順|方法)|結果の見方|未確定の点|確認が必要な点|注意事項", text.strip()):
        return True
    if re.fullmatch(r"\s*(?:#{1,6}\s+[^。]{1,35}|【[^】]{1,25}】|\*\*[^*。]{1,30}\*\*[:：]?|[^。\n:：]{0,28}(?:内容|点|事項|操作|手順|条件|規則|結果|確認)(?:[（(][^）)\n]{1,20}[）)])?[:：]|(?:操作)?手順|確認事項|注意事項|関連操作|未確定事項)\s*", text):
        return True
    return heading_phrase(text) is not None or is_lead_in(text)


# ---- 見出しの名詞句と導入の文（#1469）。Agent の ``answer_passages`` と同じ規則で、両方のテストが
# platform/contracts/answer-passages/headings.json の同じ事例で確かめる。
# 見出しの印（箇条書き・番号）。
_HEADING_LIST_MARK = re.compile(r"^(?:[-*+・•]\s*|[0-9０-９]{1,3}[.)．）]\s*|[（(][0-9０-９]{1,3}[）)]\s*)")
# 見出しの後ろの注記の括弧（「補足（資料の記載）」「2. 略号の意味（組織規程 第 2 章 部署と略号／1 頁）」）。
_HEADING_NOTE = re.compile(r"[（(](?P<note>[^（）()\n]{1,60})[）)]\s*$")
# 「…」の語（「略号「運」の意味」）。中の「は」「が」は言い切りの助詞に数えない。
_HEADING_QUOTED = re.compile(r"[「『][^「」『』\n]{1,40}[」』]")
# 数量（「30 分」「10 年」。主張の値）。年度（「2026年度」）・行・頁の番号は数えない。
_HEADING_QUANTITY = re.compile(
    r"[0-9０-９]+\s*(?:円|日|時間|分|秒|件|%|％|回|か月|ヶ月|カ月|年(?!度)|名|人|GB|MB)")
# 見出しの語に残してよい番号（章・節の番号と年度）。ほかの数字（値の「3 点」「SYS-103」）を含む行は主張のまま。
_HEADING_NUMBERED_NAME = re.compile(r"第\s*[0-9０-９]+\s*[章節条項]|[0-9０-９]+\s*年度")
# 見出しの名詞句の終わりの語（項目・区分・資料の名前。値の名前「運行管理課長」「極秘」では終わらない）。
_HEADING_END = re.compile(
    r"(?:根拠|補足|注記|備考|参考|前提|結論|回答|まとめ|要点|概要|経緯|理由|背景|資料|台帳|規程|規定|手順書|マニュアル"
    r"|ガイド|文書|内容|事項|点|条件|規則|ルール|手順|方法|手段|流れ|結果|定め|扱い|対応|範囲|対象|期限|期間|時間帯"
    r"|保守枠|日程|重要度|機密区分|区分|部署|担当|承認者|連絡先|窓口|代理|意味|正式名|名称|略号|関係|つながり"
    # 分岐の見出し（「重要度 C のシステムで障害が起きたとき」）と、問いの形の見出し（「承認者がどう決まるか」）。
    r"|とき|場合|か)$")
# 番号（「1.」「（2）」）と箇条書きの印（「- 」「・」。強調の「**」は印に数えない）。番号つき・箇条書きの行は段落
# だけでは決めず、次の行がその下の内容のときだけ見出し（``list_heading_lines``。#1481 / #1482 / #1486）。番号・
# 箇条書きの行が続く一覧の項目は主張のまま。
_HEADING_NUMBER = re.compile(r"^(?:[0-9０-９]{1,3}[.)．）]|[（(][0-9０-９]{1,3}[）)])")
_HEADING_BULLET = re.compile(r"^(?:[-+・•]|\*(?!\*))")
# 「…の…」の名詞句（「重要度 B の復旧目標」「連携元のシステム」）。「ので」「のに」「のみ」は名詞をつなぐ「の」に数えない。
_HEADING_NO_PHRASE = re.compile(r"[^\sの]\s*の(?![でにみ])\s*[^\sの]")
# 番号つきの見出しの下の内容の行（箇条書き・「→」。字下げした行も、字下げした番号の行も下の内容）。箇条書きの
# 見出しの下の内容は、字下げした行と「→」の行だけ（同じ字下げの箇条書きの行は同じ一覧の次の項目。#1486）。
_SUB_CONTENT = re.compile(r"[-*+]\s|[・•→]")
_BULLET_SUB_CONTENT = re.compile(r"→")
# 値の名前（人・組織・役職）の終わり。番号つきの「1. 情報システム部の課長」は見出しではなく値の主張のまま。
_HEADING_VALUE_END = re.compile(r"(?:長|部|課|室|係|班|局|者|員|役|氏|様|さん)$")
# 分岐・問いの形の見出しの終わり（この形だけ、中の「が」「を」を許す）。
_HEADING_CLAUSE_END = re.compile(r"(?:とき|場合|か)$")
# 見出しの中の、分岐の節（「承認者が不在のときの代理」の「が不在のとき」）。
_HEADING_CASE_CLAUSE = re.compile(r"[がを][^はがを、。]{1,15}?(?:とき|場合)")
# 導入の文の先頭の接続の語（「そのため、作業できる時間帯は次のとおりです。」。この後の「、」は主語の区切りに数えない。#1481）。
_LEAD_IN_CONNECTIVE = re.compile(
    r"^(?:そのため|このため|したがって|従って|なお|また|ただし|つまり|具体的には|結果として|以上から|以上より|以上のことから)"
    r"[、，,]\s*")
# 以降の箇条書きを導くだけの導入の文（「根拠は次のとおりです。」「以下に示します。」）。
_LEAD_IN = re.compile(
    r"(?P<subject>[^。！？!?\n]{1,24}?)(?:は|が)、?\s*(?:次|以下)の(?:とおり|通り)(?:です|となります|になります|でした)?[。．:：]?"
    r"|(?:(?P<object>[^。！？!?\n]{1,24}?)を)?(?:次|以下)に(?:示し|まとめ|挙げ|整理し)ます[。．:：]?")
# 利用者の答え・質問への礼の定型の文（「ご回答ありがとうございます。」）。
_COURTESY = re.compile(
    r"(?:ご(?:回答|返答|返信|連絡|質問)|お問い合わせ|お答え)(?:いただき)?、?ありがとうございます[。．!！]?")


def _heading_note_ok(note: str) -> bool:
    """見出しの注記の括弧が、言い切り・操作・数量を含まない補足か（「（承認者は人事部長です）」は主張）。"""
    outside = _HEADING_QUOTED.sub(" ", note)
    return ("。" not in note and not _SENTENCE_END.search(note) and not is_operation_instruction(note)
            and not _HEADING_QUANTITY.search(note) and "は" not in outside)


def heading_phrase(text: str) -> str | None:
    """主張でない見出しの名詞句なら、注記の括弧・番号・強調を除いた見出しの語（見出しでなければ None。#1469）。

    「補足（資料の記載）」「根拠」「回答に使った資料」「**重要度 C のシステムで障害が起きたとき**」のように、句点で
    終わらない短い名詞句で、項目・区分・資料の名前（``_HEADING_END``）か分岐・問いの形（「…とき」「…か」）で終わるもの。述語（です・ます・する…）・
    操作・数量・言い切りの「は」を含む行（「重要度は A です」「承認者は管理本部長」）は主張のまま。番号つき・箇条書きの
    行（「1. 人事評価システムの重要度」「- 組織規程（ご担当部署の承認者）」）は段落だけでは見出しにせず、回答の中の
    次の行で決める（番号・箇条書きの行が続く一覧の項目は主張。``list_heading_lines``。#1481 / #1482 / #1486）。
    """
    found = _heading_value(text)
    if not found or _is_list_item(text):
        return None
    return found[0] if _HEADING_END.search(found[0]) else None


def _is_list_item(text: str) -> bool:
    """番号つきか箇条書きの行か（「1. …」「（2）…」「- …」「・…」。#1482 / #1486）。"""
    value = text.strip()
    return bool(_HEADING_NUMBER.match(value) or _HEADING_BULLET.match(value))


def _list_heading_phrase(text: str) -> str | None:
    """番号つき・箇条書きの見出しの語（「1. 人事評価システムの重要度」「- 重要度 B の復旧目標」。#1481 / #1482 / #1486）。

    終わりの語が一覧（``_HEADING_END``）にあるか、一覧に無くても「…の…」の名詞句のもの。人・組織・役職の名前で
    終わる「…の…」の行（「1. 情報システム部の課長」）と、述語・操作・数量・言い切りの「は」を含む行（「1. **ID 管理
    システムの重要度は A**」）は値の主張のまま。見出しかどうかは次の行で決める（``list_heading_lines``）。
    """
    found = _heading_value(text)
    if not found or not _is_list_item(text):
        return None
    value, outside = found
    if _HEADING_END.search(value):
        return value
    return value if _HEADING_NO_PHRASE.search(outside) and not _HEADING_VALUE_END.search(value) else None


def list_heading_lines(text: str) -> set[str]:
    """回答の中の、番号つき・箇条書きの見出しの行（前後の空白を除いた行。#1481 / #1482 / #1486）。

    番号つき・箇条書きの見出しの語（``_list_heading_phrase``）の行で、次の（空でない）行がその下の内容のもの。番号つきの
    行の下の内容は、字下げした行か、箇条書き・「→」の行。箇条書きの行の下の内容は、字下げした行（下位の箇条書き）か
    「→」の行で、同じ字下げの箇条書きの行は同じ一覧の次の項目。番号・箇条書きの行が続く一覧（「申請書に書く 4 項目」の
    「1. 変更の内容と理由」〜「4. 作業の日時」、「ご確認いただきたい点」の「- 組織規程（ご担当部署の承認者）」）の項目と、
    下に内容の無い行は、終わりの語が一覧にあっても主張のまま（段落だけで決めると、一覧の項目を見出しとして監査から
    外す。#1482 / #1486）。Agent の ``answer_passages.list_heading_lines`` と同じ規則。
    """
    lines = [line for line in text.split("\n") if line.strip()]
    found = set()
    for line, following in zip(lines, lines[1:], strict=False):
        indent = len(line) - len(line.lstrip())
        sub_content = _BULLET_SUB_CONTENT if _HEADING_BULLET.match(line.strip()) else _SUB_CONTENT
        below = len(following) - len(following.lstrip()) > indent or bool(sub_content.match(following.strip()))
        if below and _list_heading_phrase(line):
            found.add(line.strip())
    return found


def _heading_value(text: str) -> tuple[str, str] | None:
    """見出しの名詞句の共通の確かめ（注記の括弧・番号・強調を除いた語と、「…」の語を除いた残り。#1469）。"""
    if is_citation_line(text):
        # 文書名と場所だけの行（「1. **システム変更手順書**（第 2章 申請）」）は出典の行（#1370）。
        return None
    value = _HEADING_LIST_MARK.sub("", text.strip(), count=1)
    value = _EMPHASIS.sub("", value).strip().rstrip(":：").strip()
    while match := _HEADING_NOTE.search(value):
        if not _heading_note_ok(match.group("note")):
            return None
        value = value[:match.start()].strip()
    if not value or len(value) > 30 or re.search(r"[。．！？!?、,，:：]", value):
        return None
    if _SENTENCE_END.search(value) or is_operation_instruction(value) or re.search(
            r"[0-9０-９]", _HEADING_NUMBERED_NAME.sub(" ", value)):
        return None
    outside = _HEADING_QUOTED.sub(" ", value)
    if "は" in outside:
        return None
    # 「が」「を」は、分岐・問いの形の見出しか、「…が不在のとき」の節の中だけに許す。
    if not _HEADING_CLAUSE_END.search(value) and re.search(r"[がを]", _HEADING_CASE_CLAUSE.sub(" ", outside)):
        return None
    return value, outside


def is_lead_in(text: str) -> bool:
    """以降の箇条書きを導くだけの導入の文（「根拠の積み上げは次のとおりです。」）と礼の定型の文か（#1469）。

    先頭の接続の語の後の「、」（「そのため、作業できる時間帯は次のとおりです。」）は許す（#1481）。主語・目的語に
    数量・操作・言い切りの「は」・「、」を含む文（「保管期間は 10 年で、内訳は次のとおりです。」）は主張のまま。
    """
    value = _EMPHASIS.sub("", _HEADING_LIST_MARK.sub("", text.strip(), count=1)).strip()
    if _COURTESY.fullmatch(value):
        return True
    match = _LEAD_IN.fullmatch(_LEAD_IN_CONNECTIVE.sub("", value, count=1))
    if not match:
        return False
    head = match.group("subject") or match.group("object") or ""
    return not (_HEADING_QUANTITY.search(head) or is_operation_instruction(head) or "は" in head or "、" in head)


_LIST_MARK = re.compile(r"^(?:[-*+・•]\s*|[0-9０-９]{1,3}[.)．）]\s*)")
_CITATION_LABEL = re.compile(
    r"[（(【〔\[]?\s*(?:出典|根拠|参照|参考|引用|引用元|参考資料|根拠資料|出所|ソース|sources?|references?|citations?"
    # 出典の位置のラベル（#1317。「- セクション: 「…」 → 2. …」「- ページ: 1」）。
    r"|セクション|節|章|ページ|頁|文書名|資料名|ファイル名|シート|証拠|証拠\s*ID|evidence(?:[ _]?id)?|page|section|document"
    # 要素の定位子のラベル（#1370。「*Locator*: `doc:…/page:3/el:12`」。#1330 の要素の定位子）。
    r"|locator|ロケータ|ロケーター|定位子)"
    r"\s*[:：]\s*(?P<rest>.+?)\s*[）)】〕\]]?",
    re.I)
# Markdown の強調（「**根拠**：…」「*Locator*:」のラベル。出典の判定だけで外す。#1370 で斜体の `*` も外す）。
_EMPHASIS = re.compile(r"\*{1,2}|__|`")
# 根拠の ID の括弧（【証拠 ID: 03ac…:1】・〔証拠 ID: 07e8…:5〕・【証拠ID cb0a…:2】・【証拠1】・【evidence_id: …】。
# 〔〕［］[] は #1370）。
_EVIDENCE_REF = re.compile(
    r"[【〔［\[]\s*(?:証拠|根拠|出典|evidence|source)(?:\s*_?(?:ID|id))?\s*[:：]?[\s0-9A-Za-z:_\-,，、…①-⑳]{0,200}[】〕］\]]",
    re.I)
_TITLE_QUOTE = re.compile(r"[「『]([^「」『』\n]{1,80})[」』]")
_DOCUMENT_TITLE = re.compile(r"手順書|マニュアル|ガイド|規程|規定|資料|文書|メモ|第\s*[0-9０-９]+\s*版")
_SECTION_REF = re.compile(r"(?:第\s*[0-9０-９]+\s*[章節条項]|[0-9０-９]+(?:\.[0-9０-９]+)*[.．]\s*)[^。\n]{0,30}")
_REFERENCE_FILLER = re.compile(r"[|\s→>＞\-–—‑―、,，:：/／]+|同上|(?:ページ|頁|p\.|page)\s*[0-9０-９]+", re.I)
_FILE_NAME = re.compile(r"[^\s「」『』（）()【】\[\]、,，|]+\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b", re.I)
# Markdown の表の区切りの行（`|---|:---:|`）と区切り線（`---`）。
_TABLE_SEPARATOR = re.compile(r"\|?(?:\s*:?-{3,}:?\s*\|)+(?:\s*:?-{3,}:?\s*)?|\|?\s*:?-{3,}:?\s*|\*{3,}|_{3,}")
_TABLE_ROW = re.compile(r"\|.*\|")
# 記号だけの段落（「**」。強調の閉じが句点の後の段落に分かれたもの）。
_MARKUP_ONLY = re.compile(r"[*_`>#|~\-\s]+")
_DOCUMENT_MARK = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b|\bpage\b|\bp\.\s*\d|\bpp\.|ページ|頁|\bsection\b|\bsheet\b|シート|第\s*[0-9０-９]+\s*(?:章|節|条|項)",
    re.I)
_BRACKETED = re.compile(r"(?:[【\[（(][^】\]）)\n]{1,200}[】\]）)][\s、,，]*)+")
_BRACKET_PART = re.compile(r"[【\[（(]([^】\]）)\n]{1,200})[】\]）)]")
_LINK_LINE = re.compile(r"(?:\[[^\]\n]{1,200}\]\([^)\s]{1,500}\)[\s、,，]*)+|https?://\S+")
_SENTENCE_END = re.compile(r"(?:ます|ません|です|でした|ました|ください|ない|無い|ある|する|できる|れる|った|いた|した|だ)[。．.!！]?$")
# 後ろの注記の括弧（「（はい／いいえ）」「【clarification: target】」）は許す (#1317)。
_QUESTION = re.compile(r".*[？?]\s*(?:[（(【][^）)】\n]{1,40}[）)】]\s*)*。?")
_INFO_REQUEST = re.compile(r".*(?:教えてください|お知らせください|お教えください|お聞かせください|ご回答ください|お答えください|確認させてください)[。．!！]?")
_BUNDLED_CLAIM = re.compile(r"ますが|ですが|ましたが|ませんが|ものの|けれど|けど|ただし")


def is_table_rule(text: str) -> bool:
    """Markdown の表の区切りの行（`|---|---|`）・区切り線（`---`）・記号だけの段落（`**`）か (#1317)。"""
    return bool(_TABLE_SEPARATOR.fullmatch(text.strip()) or _MARKUP_ONLY.fullmatch(text.strip()))


def table_header_lines(text: str) -> set[str]:
    """Markdown の表の見出しの行（区切りの行の直前の、短い語だけのセルの行）。表の本文の行は主張のまま (#1317)。"""
    def header_cell(cell: str) -> bool:
        value = _EMPHASIS.sub("", cell).strip()
        return 0 < len(value) <= 20 and "。" not in value and not _SENTENCE_END.search(value) and not is_operation_instruction(value)

    lines = [line.strip() for line in text.split("\n")]
    return {line for line, following in zip(lines, lines[1:], strict=False)
            if _TABLE_ROW.fullmatch(line) and "|" in following and is_table_rule(following)
            and all(header_cell(cell) for cell in line.strip("|").split("|"))}


def _is_reference_only(value: str) -> bool:
    """根拠の ID・文書名・頁の括弧・文の無い「…」の題（と節の参照）だけの段落か (#1317)。

    「| 「1. 前提」 （ページ1）【証拠 ID: 03ac…:1】 |」や「- 「サンプル業務ポータル 運用手順書 第3版」 6. アカウントの削除」。
    文書を示す印（根拠の ID・文書名・頁の括弧・文書の題）が無ければ出典にしない。
    """
    if is_operation_instruction(value) or _SENTENCE_END.search(value.strip("|").strip()):
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
        if "。" in title or _SENTENCE_END.search(title) or is_operation_instruction(title):
            return match.group(0)
        found = found or bool(_DOCUMENT_TITLE.search(title))
        return " "

    rest, count = _EVIDENCE_REF.subn(" ", value)
    found = count > 0
    rest = _TITLE_QUOTE.sub(drop_title, _BRACKET_PART.sub(drop_document, rest))
    rest, count = _FILE_NAME.subn(" ", rest)
    found = found or count > 0
    rest = _REFERENCE_FILLER.sub(" ", rest).strip()
    return found and (not rest or bool(_SECTION_REF.fullmatch(rest) and not _SENTENCE_END.search(rest)))


# 行末の場所の括弧（「（第 2章 申請）」「（p.12）」）。
_LOCATION_TAIL = re.compile(r"[（(〔\[]([^（()）〔〕\[\]\n]{1,80})[）)〕\]]\s*$")
# 文書名に無い、言い切りの助詞（「承認者は課長（第 3 章）」「申請先は規程が定める承認者」は主張）。
_STATEMENT_PARTICLE = re.compile(r"[はがを]")
_ANY_BRACKET = re.compile(r"[（(【〔\[「『][^（()）【】〔〕\[\]「」『』\n]*[）)】〕\]」』]")


def _is_document_location(value: str) -> bool:
    """文書名と場所の括弧だけの段落か（#1370。「システム変更手順書（第 2章 申請）」「運用マニュアル（p.12）」）。

    行末の場所の括弧（章・節・頁・シートなどの印を含むもの）を外した残りが、文書を示す語（手順書・規程・
    ファイル名など）を含む短い名前で、述語・操作・言い切りの助詞（は・が・を）を含まないときだけ出典にする。
    場所の後に本文が続く行（「…（第 2章）では、…」）は主張のまま。
    """
    rest = value.strip().rstrip("。．").strip()
    found = False
    while match := _LOCATION_TAIL.search(rest):
        location = match.group(1)
        if not _DOCUMENT_MARK.search(location) or _SENTENCE_END.search(location) or is_operation_instruction(location):
            break
        rest, found = rest[:match.start()].strip(), True
    if not found or not rest or len(rest) > 80 or "。" in rest:
        return False
    if _SENTENCE_END.search(rest) or is_operation_instruction(rest):
        return False
    outside = rest
    while (stripped := _ANY_BRACKET.sub(" ", outside)) != outside:
        outside = stripped
    if _STATEMENT_PARTICLE.search(outside):
        return False
    return bool(_DOCUMENT_TITLE.search(rest) or _FILE_NAME.search(rest))


def is_citation_line(text: str) -> bool:
    """出典の行（出典・定位子のラベル・文書名・頁・節の括弧・リンク・根拠の ID・文書名と場所だけの段落）か。

    主張を含む文は出典にしない (#1306 / #1317 / #1370)。Agent の ``answer_passages.is_citation`` と同じ規則で、
    両方のテストが platform/contracts/answer-passages/citation-lines.json の同じ事例で確かめる。
    """
    value = _EMPHASIS.sub("", _LIST_MARK.sub("", text.strip(), count=1)).strip()
    if not value or "。" in value.rstrip("。"):
        return False
    label = _CITATION_LABEL.fullmatch(value)
    if label:
        return not _SENTENCE_END.search(label.group("rest")) and not is_operation_instruction(label.group("rest"))
    if _LINK_LINE.fullmatch(value):
        return True
    if _BRACKETED.fullmatch(value) and all(_DOCUMENT_MARK.search(part) for part in _BRACKET_PART.findall(value)):
        return True
    return _is_reference_only(value) or _is_document_location(value)


def is_question_to_user(text: str) -> bool:
    """利用者への確認の質問・情報を求める依頼の文か。主張を抱き合わせた文（「〜できますが、〜か？」）は除く (#1306)。"""
    value = _LIST_MARK.sub("", text.strip(), count=1).strip()
    if not (_QUESTION.fullmatch(value) or _INFO_REQUEST.fullmatch(value)):
        return False
    return not _BUNDLED_CLAIM.search(value)


def is_non_claim_passage(text: str, *, table_headers: Collection[str] = (),
                         heading_lines: Collection[str] = ()) -> bool:
    """回答の最終の検証で主張として監査しない段落（見出し・出典の行・表の区切りと見出しの行・利用者への質問）か (#1306 / #1317)。

    表の見出しの行と、番号つき・箇条書きの見出しの行は次の行で決まるので、呼び出し側が回答全体の
    ``table_header_lines`` と ``list_heading_lines``（#1481 / #1482 / #1486）を渡す。
    """
    value = text.strip()
    return (is_heading(text) or is_citation_line(text) or is_table_rule(text) or value in table_headers
            or value in heading_lines or is_question_to_user(text))


def is_operation_instruction(text: str) -> bool:
    """具体的な操作指示を確認留保として無引用で通さないための補助判定。"""
    return bool(re.search(
        r"(?:開き|押し|選び|選択し|入力し|変更し|修正し|登録し|保存し|実行し|実施し|クリックし)(?:ます|てください|て頂|ていただ|、|て)|"
        r"(?:を開く|を押す|を選択する|を入力する|を保存する|に遷移する|に遷移します)|"
        r"(?:登録|選択|入力|変更|修正|保存|実行)(?:すれば|すると|することで|できます|可能)|"
        r"(?:押下|クリック|印刷|再発行)(?:する|すれば|すると|し|できます|可能)|"
        r"(?:ボタン|画面|タブ).{0,30}(?:を開く|を押す|を選択する)|"
        r"(?:削除|消去)(?:し|する|すれば|すると|できます|可能)|"
        r"\b(?:click|press|navigate to|save changes|delete|erase)\b", text, re.I))


def missing_operation_terms(text: str, cited: list[dict[str, Any]]) -> list[str]:
    """変更・出力操作の語が引用本文に全くない場合を保守的に検出する。

    語の存在は操作の十分な証拠ではなく、適用性・否定・順序は独立監査に残す。
    一般助言という体裁でも実操作を追加させないため、原文にない操作だけを拒む。
    見出し・検索語は支持本文として使わない。
    """
    if not is_operation_instruction(text):
        return []
    body = "\n".join(line for span in cited for line in str(span.get("text", "")).splitlines()
                     if not is_vision_enumeration_line(line))
    families = (("再発行", "再交付"), ("印刷", "プリント", "印字"),
                ("修正", "編集", "訂正", "書き換え", "書換", "変更"),
                ("削除", "消去"), ("保存", "登録", "更新"))
    return [next(term for term in family if term in text) for family in families
            if any(term in text for term in family) and not any(term in body for term in family)]


# 総称語だけの削除対象。「データを削除」は資料の具体名（「履歴情報」「行」）と一致しないので対象にしない (#696)。
# 操作対象の検査（#700）と同じ語彙を使う。
from rag_engine.retrieval.task_contract import GENERIC_TARGET_WORDS as _GENERIC_DELETION_TARGETS


def deletion_targets(question: str) -> list[str]:
    """削除・消去の直前に明示された対象だけを抽出し、未知の対象を推測しない。

    総称語（「データ」「情報」）だけの対象は返さない。検査の趣旨は、質問が具体名を挙げたときに別の対象の
    削除手順を当てないことにあり、総称語には具体名との照合が成り立たない。「〇〇データ」は対象にする。
    """
    text = unicodedata.normalize("NFKC", question)
    targets = re.findall(r'[「『]([^」』]+)[」』](?:を|の)?(?:個別|一括)?(?:削除|消去)', text)
    targets += re.findall(r'([一-龯々ァ-ヶーA-Za-z0-9]+)(?:を|の)(?:個別|一括)?(?:削除|消去)', text)
    return [t for t in dict.fromkeys(targets) if t not in _GENERIC_DELETION_TARGETS]


def deletion_target_supported(question: str, cited: list[dict[str, Any]]) -> bool:
    """対象名は削除操作を支える同じ本文で照合する。資料名だけの一致は使わない。"""
    targets = deletion_targets(question)
    def compact(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
    return not targets or any(
        all(compact(target) in compact(str(s.get('text', ''))) for target in targets)
        and re.search(r'削除|消去', str(s.get('text', '')))
        for s in cited
    )
