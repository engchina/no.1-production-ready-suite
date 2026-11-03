"""UTF-8 ではないテキストの文字コードの判定。"""

from __future__ import annotations

import re

from charset_normalizer import from_bytes
from charset_normalizer.models import CharsetMatch

_JAPANESE_ENCODINGS = ["cp932", "euc_jis_2004"]
# 日本語の文は 0.06 以下が多い。中国語・韓国語を日本語の符号化で読むと多くは 0.1 以上になる。
_JAPANESE_MAX_CHAOS = 0.06
# 他の言語を日本語の符号化で読んでも、ひらがな・カタカナはほとんど現れない。
_JAPANESE_MIN_KANA_RATIO = 0.05
_KANA = re.compile("[\u3041-\u3096\u30a1-\u30fa]")
_NON_ASCII = re.compile("[^\x00-\x7f]")


def _reads_as_japanese(match: CharsetMatch) -> bool:
    if match.chaos <= _JAPANESE_MAX_CHAOS:
        return True
    text = str(match)
    kana = len(_KANA.findall(text))
    return kana > 0 and kana / len(_NON_ASCII.findall(text)) >= _JAPANESE_MIN_KANA_RATIO


def detect_text_match(data: bytes) -> CharsetMatch | None:
    """システムの主要言語は日本語なので、日本語として読めるならそれを採る。

    検出器の 1 位だけを使うと、僅差で韓国語や UTF-16 に読むことがある（#4205）。
    記号の多い日本語は検出器が候補を返さないので、そのときも日本語の符号化で読む。
    """
    japanese = from_bytes(data, cp_isolation=_JAPANESE_ENCODINGS, threshold=1.0).best()
    if japanese is not None and _reads_as_japanese(japanese):
        return japanese
    return from_bytes(data).best() or japanese
