"""項目名・ボタン名・shortcutを別々の証拠として照合する。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

_KEY = r'(?<![A-Za-z0-9])F(?:1[0-2]|[1-9])(?![0-9])'
_LABEL = r'[一-龯々ァ-ヶー]{1,24}'
_PAIRS = re.compile(
    rf'[「【(]?(?P<key>{_KEY})[)\]]?\s*[=:＝：]?\s*[「【（(]?\s*(?P<label>{_LABEL})[」】）)]?'
    rf'|[「【]?(?P<reverse>{_LABEL})[」】]?\s*[（(\[](?P<reverse_key>{_KEY})[）)\]]')


def _native(spans: Sequence[dict[str, Any]]) -> str:
    return '\n'.join(re.split(r'回答用本文:|視覚種別:|画面/メニュー:|OCR抽出テキスト:',
                             unicodedata.normalize('NFKC', s.get('text', '')), maxsplit=1)[0]
        for s in spans if s.get('origin') != 'image_extraction')


def _pair(match: re.Match) -> tuple[str, str]:
    return (match.group('key') or match.group('reverse_key'),
            (match.group('label') or match.group('reverse')).removesuffix('ボタン'))


def interaction_binding_error(answer: str, spans: Sequence[dict[str, Any]], *, screen_texts: Sequence[str] = (),
                              body_texts: Sequence[str] = ()) -> str:
    """未裏付けshortcutと、明示根拠のない引用符付き項目の同一視を検出する。

    一般的な意味同一性は独立監査に残す。検索別名や同じページ内の共存は証明にしない。
    screen_texts は同じ機能の画面キャプチャの説明（Vision）。画面に表示されるキー番号とボタン名の対応
    （「実行(F5)」）は本文に書かれないため、対応の照合にだけ使う。ボタン名の存在や別名の同一視には使わない (#637)。
    body_texts は同じ機能の本文（生成説明を除く）。手順が隣の span に分かれても、案内したボタン名の存在は
    機能の本文で確認できる (#658, #676)。キー番号との対応や別名の同一視には使わない。
    """
    value, native = unicodedata.normalize('NFKC', answer), _native(spans)
    screen = '\n'.join(unicodedata.normalize('NFKC', text) for text in screen_texts)
    # 「実行または保存」の代替候補にも各ボタン名の根拠が必要。
    # 登録という結果の記載を、保存ボタンの存在証明にはしない。
    label_text = '\n'.join(unicodedata.normalize('NFKC', line)
                           for text in (*(s.get('text', '') for s in spans), *body_texts) for line in text.splitlines()
                           if not re.match(r'\s*(?:検索語|主題):', line))
    for match in re.finditer(r'(?:「[^」]{1,24}」(?:\s*(?:または|又は|か|もしくは|や)\s*)?)+ボタン', value):
        labels = re.findall(r'「([^」]+)」', match.group())
        if any(label not in label_text for label in labels):
            return '案内したボタン名が引用本文にない'
    pairs = {_pair(m) for m in _PAIRS.finditer(native + '\n' + screen) if _pair(m)[1] not in {'キー', ''}}
    for match in _PAIRS.finditer(value):
        if _pair(match)[1] not in {'キー', ''} and _pair(match) not in pairs:
            return 'キー番号とボタン名の対応が本文で未確認'
    # 無効ボタンはラベルを持たないまま画面に写る（`(F3)`）。対応が無くても根拠にそのキーがあれば、
    # 回答がボタン列を引き写しただけなので未確認にしない。根拠のどこにも無いキーは従来どおり拒む (#776)。
    keys_in_evidence = set(re.findall(_KEY, native + '\n' + screen))
    for key in re.findall(_KEY, value):
        if not any(p[0] == key for p in pairs) and key not in keys_in_evidence:
            return 'キー番号の操作が本文で未確認'
    aliases = re.findall(r'「([^」]{1,30})」\s*\(\s*「?([^」)]{1,30})」?\s*\)', value)
    aliases += re.findall(r'(' + _LABEL + r')\((' + _LABEL + r')\)(?:ボタン|欄|項目)', value)
    for a, b in aliases:
        if a != b and a in native and b in native:
            if not any(a in line and b in line and re.search(r'同じ|同一|別名|とも呼|を指す', line)
                       and not re.search(r'異なる|別の|ではない', line)
                       for line in re.split(r'[。\n]', native)):
                return '別項目を同義のラベルとする原文対応が未確認'
    return ''
