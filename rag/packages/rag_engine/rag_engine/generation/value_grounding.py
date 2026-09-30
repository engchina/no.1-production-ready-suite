"""表示例だけから数値制約・現在値を断定する回答を保守的に検出する。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence


_IMAGE_MARKER = re.compile(r'回答用本文:|視覚種別:|画面/メニュー:|OCR抽出テキスト:')
_RULE = re.compile(r'許容範囲|入力範囲|入力可能範囲|上限|下限|最大|最小|必須値|初期値|既定値|デフォルト|[0-9]+\s*桁')
_DATE = re.compile(r'(?:平成|令和|昭和|西暦|[RHS])?\s*[0-9]+\s*(?:年度|年|月|日)(?:\s*[0-9]+\s*(?:月|日))*')
_DENIAL = re.compile(r'(?:確認|判断|断定|特定)できません|不明|未確認|とは限りません')


def _numbers(text: str) -> set[str]:
    """和暦年月の区切りを小数と誤認せず、質問の年月と同じ値として照合する。"""
    text = re.sub(r'(?:R|H|S)([0-9]+)\.([0-9]{1,2})(?![0-9])', r'\1年\2月', text)
    return set(re.findall(r'[0-9]+(?:\.[0-9]+)?', text))


def unsupported_document_value_claim(text: str, cited: Sequence[dict[str, Any]], question: str) -> str:
    """明示数値の制約・現在値に、生成画像説明以外の必要な裏付けがあるか確認する。

    日本語の代表的な制約語と数値に限定する補助ガード。意味上の適用性は独立監査を
    継続する。本文に明示した制約と利用者の値は保持し、OCRやVisionの値だけで
    規則へ昇格させない。原文・入力を変更せず、未裏付けの場合だけ理由を返す。
    """
    normalized = unicodedata.normalize('NFKC', text)
    normalized = re.sub(r'(?<=半角数字)\s*\(0[〜~～–-]9\)', '', normalized)
    normalized = re.sub(r'^\s*[0-9]+[.)]\s+', '', normalized)
    normalized = re.sub(r'(?:PDF\s*)?p\.?\s*[0-9]+(?:\s*[-–]\s*[0-9]+)?', '', normalized, flags=re.I)
    # 非数値の所属・選択状態も、図の例から今回の実値へ昇格させない。
    actual = re.search(r'(?:今回|現在|対象|あなた|利用者)[^。\n]{0,30}(?:所属|グループ|担当者|氏名|区分|状態)(?:は|が|=|:)[「『]([^」』]+)[」』]', normalized)
    if actual and actual.group(1) not in question and not re.search(r'場合|なら|未確認|不明|とは限', normalized):
        value = actual.group(1)
        if not any(value in line and not re.search(r'例|図|スクリーンショット', line)
                   for span in cited if span.get('origin') != 'image_extraction'
                   for line in re.split(r'[。\n]', _IMAGE_MARKER.split(span.get('text', ''), maxsplit=1)[0])):
            return '文書の表示例・Vision説明だけでは今回の所属・選択状態を確認できない'
    numbers = _numbers(normalized)
    if not numbers:
        return ''
    # 文書の範囲に限定した未確認の説明は、数値規則の肯定主張ではない。
    rules = list(_RULE.finditer(normalized))
    if rules and all(re.match(r'(?:か(?:どうか)?(?:は)?|とは)(?:資料からは?)?(?:確認|判断|断定|特定)できません',
                              normalized[m.end():].strip()) for m in rules):
        return ''
    native = []
    for span in cited:
        if span.get('origin') == 'image_extraction':
            continue
        source = unicodedata.normalize('NFKC', span.get('text', ''))
        if span.get('origin') != 'document_text':
            # 旧metadataでも生成説明の境界が明示される場合は、その前の本文だけを使う。
            source = _IMAGE_MARKER.split(source, maxsplit=1)[0]
        native.extend(re.split(r'[。\n]', source))
    if _RULE.search(normalized):
        supported = set()
        for sentence in native:
            if _RULE.search(sentence) and not _DENIAL.search(sentence):
                supported.update(_numbers(sentence))
        # 「平成27年度改正で上限額は100万円から120万円に」の 27 は日付で、規則値ではない。
        # 規則文での裏付けは、日付を除いた数字にだけ要求する。質問に明示された数値（「年３回から４回」の 4）は
        # 利用者が与えた条件で、図の例示値ではない (#629)。
        supported |= _numbers(unicodedata.normalize('NFKC', question))
        if not _numbers(_DATE.sub('', normalized)) <= supported:
            return '文書の表示例・Vision説明だけでは数値の入力規則を確認できない'
    if re.search(r'(?:あなた|利用者|今回|現在)(?:の|は|が)', normalized):
        known = _numbers(unicodedata.normalize('NFKC', question))
        if not numbers <= known and not any((_RULE.search(s) or re.search(r'場合|なら', s))
                and numbers <= _numbers(s) for s in native):
            return '文書の表示例・Vision説明だけでは今回の実値を確認できない'
    return ''
