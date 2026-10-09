"""回答文と引用本文に対する決定的な判定。LLM を呼ばず、入力を変更しない。"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from typing import Any

from rag_engine.parsing.vision_prompt_rules import is_vision_enumeration_line


_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")


def answer_passages(text: str) -> list[dict[str, str]]:
    """改写せず文・改行で区切る。長文も600文字の連続範囲として保持する。

    句点直後の閉じ括弧は同じ文に含める。「…。」の引用を句点で切ると、閉じ括弧だけの
    段落ができて主張監査が完了しない。行全体が1つの「…」の引用（原文のみ提示の行。表の引用は
    複数の文を含む）は、引用の途中で切らずに1段落として扱う。
    """
    result = []
    for line in text.split("\n"):
        pieces = [line] if _QUOTE_ONLY_LINE.fullmatch(line) else [
            match.group() for match in re.finditer(r"[^。]+(?:。[」』）)]*|$)", line)]
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
    return bool(re.fullmatch(r"\s*(?:#{1,6}\s+[^。]{1,35}|【[^】]{1,25}】|\*\*[^*。]{1,30}\*\*[:：]?|[^。\n:：]{0,28}(?:内容|点|事項|操作|手順|条件|規則|結果|確認)(?:[（(][^）)\n]{1,20}[）)])?[:：]|(?:操作)?手順|確認事項|注意事項|関連操作|未確定事項)\s*", text))


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


def is_non_claim_passage(text: str, *, table_headers: Collection[str] = ()) -> bool:
    """回答の最終の検証で主張として監査しない段落（見出し・出典の行・表の区切りと見出しの行・利用者への質問）か (#1306 / #1317)。

    表の見出しの行は次の行（区切りの行）で決まるので、呼び出し側が回答全体の ``table_header_lines`` を渡す。
    """
    return (is_heading(text) or is_citation_line(text) or is_table_rule(text) or text.strip() in table_headers
            or is_question_to_user(text))


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
