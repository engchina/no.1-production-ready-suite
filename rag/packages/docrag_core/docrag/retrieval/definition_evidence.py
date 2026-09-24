"""短い画面項目の定義を、質問と原文の構造から照合する。業務用語は固定しない。"""
from __future__ import annotations

import re
import unicodedata

_LABEL = r'[一-龯々ァ-ヶーA-Za-z0-9_]{1,24}'


def definition_labels(question: str) -> list[str]:
    """意味を尋ねる質問から明示項目だけを返す。普通の文章中の一文字は採らない。"""
    text = unicodedata.normalize('NFKC', question)
    if not re.search(r'意味|定義|とは|何を(?:示|表)|何です|どのような|どういう|どういった|どんな|どのような条件|どんな条件', text):
        return []
    labels = re.findall(r'[「『【](' + _LABEL + r')[」』】]', text)
    for match in re.finditer(r'(' + _LABEL + r'(?:\s*[、,/・]\s*' + _LABEL + r')+)\s*(?:の)?(?:項目|列|欄)', text):
        labels.extend(re.split(r'\s*[、,/・]\s*', match.group(1)))
    if not labels:
        # 引用符なしの単独項目も、項目・列・欄という構造が明示された時だけ採る。
        labels.extend(re.findall(r'(?:^|[での\s])(' + _LABEL + r')(?:の)?(?:項目|列|欄)(?=[にはがの\s])', text))
    if not labels:
        labels.extend(re.findall(r'(?:^|[での\s])(' + _LABEL + r')(?:の(?:意味|マーク)|(?:列|欄)(?:の|は)|とは)', text))
    return list(dict.fromkeys(labels))


def label_mentioned(text: str, label: str) -> bool:
    """取消の中の「消」のような部分一致を除外し、単独の項目表記を照合する。"""
    text = unicodedata.normalize('NFKC', text)
    return bool(re.search(r'(?<![一-龯々ァ-ヶーA-Za-z0-9_])' + re.escape(label)
                          + r'(?![一-龯々ァ-ヶーA-Za-z0-9_])', text))


def definition_ranges(text: str, labels: list[str]) -> list[dict]:
    """定義行と折返し条件を原文位置で返す。列一覧・検索語・途中切れは定義にしない。

    句点までを一体で扱い、次の項目・節を越えない。句点なしの場合は単一行だけを
    対象とする。文字位置は元のtextに対するもので、NFKC変換後の位置ではない。
    """
    results = []
    for label in labels:
        pattern = r'(?m)^[ \t]*(?:[-・]\s*)?[「『【]?' + re.escape(label) + r'[」』】]?\s*(?:[…⋯]+|[：:]|とは|は)\s*'
        for match in re.finditer(pattern, text):
            tail = text[match.end():]
            stop = re.search(r'\n\s*(?:ポイント\s*[０-９0-9]+|[●■◆]|(?:' + '|'.join(re.escape(v) for v in labels) + r')[…⋯：:])', tail)
            tail = tail[:stop.start()] if stop else tail
            end_sentence = tail.find('。')
            body = tail[:end_sentence+1] if end_sentence >= 0 else tail.split('\n', 1)[0]
            if len(body.strip()) < 4 or len(body) > 1200 or not re.search(r'表示|意味|示|対象|状態|場合|記録|です|指す|表す', body):
                continue
            end = match.end() + len(body.rstrip())
            results.append(dict(label=label, start=match.start(), end=end, text=text[match.start():end]))
    return sorted(results, key=lambda value: value['start'])


def definition_windows(text: str, labels: list[str]) -> list[tuple[int, int]]:
    """定義の直前にある対象項目の見出しも保持するための引用窓を返す。"""
    windows = []
    for item in definition_ranges(text, labels):
        start = item['start']
        before = text[:start].rstrip('\n').splitlines()
        if before and len(before[-1]) <= 100 and 'について' in before[-1] and any(label_mentioned(before[-1], v) for v in labels):
            start = text.rfind(before[-1], 0, start)
        windows.append((start, item['end']))
    return windows
