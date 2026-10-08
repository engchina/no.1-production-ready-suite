"""回答の段落の分け方と、主張ではない段落の決定的な判定（#1277・#1306）。

最終の検証（`answer_validation`）は、RAG の `rag_validate_answer` が段落（`answer_quote`）ごとに
返す判定で、根拠で確かめられない段落を回答から外す。ところが主張ではない段落（見出し・出典の行・
利用者への質問・「資料に記載がありません」という不足の文・「確かめられていない点」の節）まで
「確かめられない」として外すと、回答が読みにくくなり、拒答が拒答に見えなくなる（#1289 の評価）。

ここでは、そうした段落をモデルを使わずに判定する。判定は保守的にし、迷うものは主張として扱う
（確かめられない操作の手順・事実の主張は今までどおり外す）。

- 見出し: Markdown の見出し・太字だけの行・【…】・「…：」で終わる短いラベル。述語で終わる文や
  操作の指示を含むものは見出しにしない。
- 出典: 「出典：…」「根拠：…」などのラベルの行、文書名・頁・節を示す括弧だけの行、文書名だけの行、
  リンクだけの行、出典の節（見出しが「出典」「参考資料」など）の行。
- 質問: 「？」で終わる文（後ろの「（はい／いいえ）」などの選択肢の括弧は許す）と、情報を
  求める依頼の文（「教えてください」など）、質問の直後の短い選択肢の箇条書き。主張や操作を抱き合わせた文は除く。
- 不足: 資料に記載が無い・資料からは確かめられないことだけを述べる文。数量・逆接・操作を
  含む文は除く。
- 確かめられていない点: その見出しと、その節の行（操作の指示を除く）。

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
    r"|sources?|references?|citations?)\s*[:：]\s*(?P<rest>.+?)\s*[）)】\]]?",
    re.IGNORECASE,
)
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
    r"資料|文書|マニュアル|ドキュメント|規程|規定|ガイド|ナレッジ|検索|記載|記述|根拠|手順書"
)
_ABSENCE_END = re.compile(
    r"(?:記載|記述|説明|情報|言及|該当(?:する)?(?:箇所|内容|資料|記載)?|根拠|定め|規定)"
    r"[^。]{0,24}?(?:ありません|見つかりません|見当たりません|されていません|含まれていません|無く|なく"
    r"|ない|無い)(?:でした)?[。．]?$"
    r"|(?:確認|特定|判断|判別|把握)(?:でき|出来)ません(?:でした)?[。．]?$"
    r"|確かめられません(?:でした)?[。．]?$"
    r"|(?:分かり|わかり)ません(?:でした)?[。．]?$"
    r"|(?:お答え|回答|ご案内|案内)(?:でき|出来)ません(?:でした)?[。．]?$"
)
# 不足の文に混ざった主張（数量・逆接・「〜は〜で、」の言い切り）。
_ABSENCE_CLAIM = re.compile(
    r"[0-9０-９]+\s*(?:円|日|時間|分|秒|件|%|％|回|か月|ヶ月|カ月|年|名|人|GB|MB)"
    r"|ますが|ですが|ものの|けれど|けど|ただし"
    r"|(?:は|が)[^。、]{1,20}(?:で|であり|となり)、"
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
    """見出し・出典だけの段落か（本文から内容を外した後に、それだけ残っても意味が無いもの）。"""
    return is_heading(text) or is_citation(text)


def is_citation(text: str) -> bool:
    """出典の行（出典のラベル・文書名・頁・節の括弧・文書名・リンクだけの段落）か。"""
    value = _strip_bullet(text)
    if not value or "。" in value.rstrip("。"):
        return False
    label = _CITATION_LABEL.fullmatch(value)
    if label:
        return not _is_sentence(label.group("rest")) and not _OPERATION.search(label.group("rest"))
    if _BRACKETED.fullmatch(value):
        return all(_DOCUMENT_MARK.search(part) for part in _BRACKET_PART.findall(value))
    if _LINK_LINE.fullmatch(value):
        return True
    return bool(_FILE_LINE.fullmatch(value)) and not _is_sentence(value)


def is_question(text: str) -> bool:
    """利用者への質問・情報を求める依頼の文か（主張や操作を抱き合わせた文は除く）。"""
    value = _strip_bullet(text)
    if not (_QUESTION.fullmatch(value) or _REQUEST.fullmatch(value)):
        return False
    return not _ADVERSATIVE.search(value)


def is_absence(text: str) -> bool:
    """資料に記載が無い・資料からは確かめられないことだけを述べる文か。"""
    value = _strip_bullet(text)
    if not _ABSENCE_SUBJECT.search(value) or not _ABSENCE_END.search(value):
        return False
    return not _ABSENCE_CLAIM.search(value) and not _OPERATION.search(value)


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
    原文で決めるため）。節（「確かめられていない点」・出典）は、次の見出しまで続く。
    """
    kinds: dict[str, str] = {}
    claims: set[str] = set()
    section: str | None = None
    options = False
    for line in answer.split("\n"):
        if not line.strip():
            options = False
            continue
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
            kind = passage_kind(text)
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
    "KIND_UNVERIFIED",
    "is_absence",
    "is_citation",
    "is_clarification_only",
    "is_heading",
    "is_question",
    "is_structure",
    "non_claim_passages",
    "passage_kind",
    "passage_spans",
]
