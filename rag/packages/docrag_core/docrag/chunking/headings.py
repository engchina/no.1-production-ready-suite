"""見出しの判定・昇格・節パス（section_path）・目次・ランニングヘッダの扱い。"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Sequence

from docrag.chunking.constants import (
    DocumentChunk,
    PROMOTED_TEXT_HEADING_SOURCE,
    ROUTE_CAPTION_HEADING_SOURCE,
    RUNNING_HEAD_HEADING_SOURCE,
    SECTION_CATEGORIES,
    SECTION_HEADER_HEADING_SOURCE,
    TITLE_HEADING_SOURCE,
    _boilerplate_text_key,
    _int_value,
)
from docrag.chunking.records import _record_text

# 文末の直後（閉じ括弧の前は除く）と、空白が続く英文のピリオドの直後を分割候補にします。
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[。！？!?\n])(?![」』）)\]】])|(?<=\.)(?=\s)")


_KANJI_NUMBER = "0-9一二三四五六七八九十百"
_HEADING_LEVEL_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(rf"第[{_KANJI_NUMBER}]+[章編部]"), 1),
    # 「Ⅰ 月次定例処理」「Ⅴ．定例処理」のローマ数字の章。NFKC で Ⅴ は V になる。英字 1 文字の章記号（「A 【画面説明】」）
    # と紛れないよう、ローマ数字として成立する並びに限る (#730)。I・V・X は単独でもローマ数字として成立するため、
    # 1 文字だけで空白の直後に囲み記号が続く「I 【画面説明】」「V 【操作説明】」は英字系列（下の level 6）へ倒す。
    # 2 文字以上（「Ⅲ 【補足】」）や句点区切り（「Ⅴ．定例処理」）はローマ数字のまま (#747)。
    (re.compile(r"(?:X{0,3}(?:IX|IV|V?I{0,3}))(?<=[IVX])(?:\s*[.．、]|(?<=[IVX]{2})\s|\s(?![【\[〔]))"), 1),
    (re.compile(rf"第[{_KANJI_NUMBER}]+節"), 2),
    (re.compile(rf"第[{_KANJI_NUMBER}]+[項条]"), 3),
    # 「B-(1) 新規登録」形式の機能見出し。章を表す英字だけの見出しは推定しません。
    (re.compile(r"[A-Z]?[-—―ー]\([0-9]+\)"), 2),
    # 「（Ａ）システム設定」「（ B ）利用状況」のような英字 1 文字の括弧番号は番号付き機能見出しの上の章。
    (re.compile(r"\(\s*[A-Z]\s*\)"), 4),
    # 「（１）」「（ 12 ）」（Docling は括弧内に空白を入れることがある）。
    (re.compile(r"\(?\s*[0-9]{1,2}\s*\)"), 5),
    # 操作説明書の「A 【画面説明】」「B 【操作説明】」「B-1.」。番号付き機能見出しの直下に並ぶ系列。
    (re.compile(r"[A-Z]\s*(?:[【\[〔]|[-—―ー.．]\s*[0-9])"), 6),
)
# 柱の初出（章名）。番号見出しより上位で、path の根になる。
_CHAPTER_LEVEL = 0
_LETTERED_LEVEL = 6
_CIRCLED_LEVEL = 8
# 番号付き機能見出し（(n) 以上）までを「機能ユニット」とみなし、親チャンクはこの単位をまたがない。
_UNIT_MAX_LEVEL = 5
# 系列の先頭（「A 【画面説明】」）。直近の非英字見出しの直下に新しい系列を始める。
_SERIES_START_PATTERN = re.compile(r"A\s*[【\[〔]")
_SECTION_PATH_LIMIT = 8
# 「1.」「1.2」「1.2.3 概要」。区切りのない「3つの方法」や「2024年度」は番号として扱いません。
_DOTTED_HEADING_PATTERN = re.compile(r"([0-9]{1,2}(?:\.[0-9]{1,2})*)(?:[.、]|\s|$)")


# 画面経路の形をした 1 行（[ 画面：A⇒B ]、〔A⇒B⇒C〕）。Docling は図に近いこの行を図の caption として返すことがある。
_ROUTE_CAPTION_PATTERN = re.compile(r"^[\[［〔【]\s*[^\n]{1,80}[⇒→＞>][^\n]{1,80}[\]］〕】]$")


# 柱（毎ページ上下端に繰り返す章名）とみなすページ上端・下端の帯。bbox の上端がページ高さに対して占める割合。
_RUNNING_HEADER_TOP = 0.07
_RUNNING_FOOTER_TOP = 0.90
# 柱と同族とみなす高さの許容差（確認済みの柱の高さの中央値に対する比）。
_RUNNING_HEIGHT_TOLERANCE = 0.25
# 文として終わる「見出し」（Docling が Section-header に分類した手順行「２ . 顧客情報を入力します。」）。
_SENTENCE_HEADING_PATTERN = re.compile(r"(?:。|ます|ません|ください|下さい|です)[。）)]?$")
# 本文へ戻す手順行のうち、丸数字・番号で始まるものは List-item として手順の並びに載せる。
_STEP_MARKER_PATTERN = re.compile(r"^\s*(?:[①-⑳]|[0-9０-９]{1,2}\s*[.)．、）])")
# 番号・記号だけの「見出し」（手順番号「５ .」が本文から切り離されたもの）。本文へ戻す。
_MARKER_ONLY_PATTERN = re.compile(r"^[\s0-9０-９.．、()（）①-⑳]+$")


def _running_heading_keys(records: Sequence[dict[str, Any]]) -> set[str]:
    """ページ上端・下端の帯に 2 ページ以上同じ文字列で出る Section-header（と同族の見出し）を柱として返す。

    Docling は「１データ連携」「Ａシステム設定－(１)利用者設定」のような柱を Section-header に分類する。
    そのままだと毎ページ section_path が章へ戻され、前の機能の見出しが残る。文字列は
    `_boilerplate_text_key` で比べ、ページ番号だけの差は同一視する。
    """
    pages_by_key: dict[str, set[int]] = {}
    heights_by_key: dict[str, list[float]] = {}
    for record in records:
        if str(record.get("category") or "") not in SECTION_CATEGORIES or not _page_band(record):
            continue
        page = _int_value(record.get("page"))
        text = _record_text(record)
        if page is None or not text:
            continue
        key = _boilerplate_text_key(text)
        pages_by_key.setdefault(key, set()).add(page)
        heights_by_key.setdefault(key, []).append(_record_height(record))
    running = {key for key, pages in pages_by_key.items() if len(pages) >= 2}
    if not running:
        return running
    # 柱が確認できた文書では、同じ帯に同じ高さで 1 ページだけ出る見出し（1 ページしかない章の柱
    # 「Ａマスタ管理－ ( ２ ) 基本設定」）も柱とみなす。文字列が違うだけで章の切り替わりを
    # 通常の見出しにすると、前の章の柱が path の根に残る。
    reference = sorted(h for key in running for h in heights_by_key[key])
    median = reference[len(reference) // 2]
    for key, heights in heights_by_key.items():
        if key not in running and median > 0 and all(abs(h - median) / median <= _RUNNING_HEIGHT_TOLERANCE for h in heights):
            running.add(key)
    return running


def _record_height(record: dict[str, Any]) -> float:
    bbox = record.get("bbox")
    try:
        return float(bbox[3]) - float(bbox[1]) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _page_band(record: dict[str, Any]) -> bool:
    """record の上端がページ上端・下端の帯にあるか。bbox や page_height がなければ False。"""
    bbox = record.get("bbox")
    height = _int_value(record.get("page_height"))
    if str(record.get("coord_system") or "") != "image_top_left" or not isinstance(bbox, (list, tuple)) or len(bbox) < 4 or not height:
        return False
    try:
        top = float(bbox[1]) / float(height)
    except (TypeError, ValueError):
        return False
    return top < _RUNNING_HEADER_TOP or top > _RUNNING_FOOTER_TOP


def _promote_headings(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Docling の category だけでは決まらない見出し／非見出しを正規化する。

    - 柱（`_running_heading_keys`）は初出だけ章の見出し（`raw.heading_level=0`）として残し、2 回目以降は
      `Page-header` に付け替えて既存のページ定型除外に載せる。
    - 文として終わる Section-header（手順行）は本文へ戻す。番号・丸数字で始まれば `List-item`、それ以外は `Text`。
      見出しのままだと「２ . 顧客情報を入力します。」が番号見出しと判定され、section_path 全体を置き換える。
    - 同じ文書で見出しとして出現した文字列の再出現（目次では Section-header、本文では List-item と
      分類された「２．顧客への区分の登録」）は見出しに寄せる。見出しにならないと前の節の section_path が続き、
      別機能の手順が同じ機能として扱われる。
    - 画面経路の形をした caption も見出しに寄せる。図の外の行だが、図の caption として返ると section_path に
      載らず、経路が Vision 説明にしか残らない。
    入力は変更せず、category を差し替えた写しを返す。
    """
    running = _running_heading_keys(records)
    seen_running: set[str] = set()

    def demoted_category(record: dict[str, Any], text: str) -> str | None:
        if str(record.get("category") or "") not in SECTION_CATEGORIES:
            return None
        key = _boilerplate_text_key(text)
        if key in running:
            if key in seen_running:
                # 上端・下端のどちらでも同じ category にし、繰り返し回数を合算して定型除外に載せる。
                return "Page-header"
            # 初出は章の見出し（level 0）として残す。「８データ連携－〈データ連携１〉」の 1 ページ目の
            # メニュー表や、章ごとに文字列が変わる柱の切り替わりを path の根にする。
            seen_running.add(key)
            return ""
        if _SENTENCE_HEADING_PATTERN.search(text):
            return "List-item" if _STEP_MARKER_PATTERN.match(text) else "Text"
        if _MARKER_ONLY_PATTERN.match(text):
            return "Text"
        return None

    ordered = sorted(records, key=lambda item: (int(item.get("page") or 0), int(item.get("seq_no") or 0)))
    demotions = {id(r): demoted_category(r, _record_text(r).strip()) for r in ordered}
    toc_ids = _toc_entry_ids(ordered)
    # 再出現の照合は空白と全角 / 半角を無視した鍵で行う（「２．機関の廃止登録」と「2 ．機関の廃止登録」）。
    # 目次・フロー一覧の番号行も鍵に含め、本文側の出現（List-item と分類されたもの）を見出しに寄せる (#714)。
    known = {
        _heading_key(_record_text(r))
        for r in records
        if (str(r.get("category") or "") in SECTION_CATEGORIES and not demotions.get(id(r))) or id(r) in toc_ids
    } - {""}
    promoted: list[dict[str, Any]] = []
    for record in records:
        category = str(record.get("category") or "")
        text = _record_text(record).strip()
        if demotions.get(id(record)):
            promoted.append({**record, "category": demotions[id(record)]})
            continue
        if demotions.get(id(record)) == "":
            raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
            promoted.append({**record, "raw": {**raw, "heading_level": _CHAPTER_LEVEL}})
            continue
        if id(record) in toc_ids:
            # 目次・フロー一覧の番号行そのものは見出しにしない。見出しになると以降の本文の節がその配下に入る。
            promoted.append({**record, "category": "List-item"} if category in SECTION_CATEGORIES else record)
            continue
        recurring = category in {"Text", "List-item"} and len(text) <= 40 and _heading_key(text) in known
        route_caption = category == "Caption" and bool(_ROUTE_CAPTION_PATTERN.match(text))
        if recurring:
            # 本文側の再出現には印を付ける。現在の節名の再掲（柱に近い行）なら節を変えないため (#764)。
            raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
            promoted.append({**record, "category": "Section-header", "raw": {**raw, "recurring_heading": True}})
            continue
        if route_caption:
            raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
            promoted.append({**record, "category": "Section-header", "raw": {**raw, "heading_source": ROUTE_CAPTION_HEADING_SOURCE}})
            continue
        promoted.append(record)
    return promoted


# 目次・フロー一覧の番号行（「１．営業所登録情報変更」）。短い番号付きの行で、文として終わらない。
_TOC_ENTRY_PATTERN = re.compile(r"^\s*[0-9０-９]{1,2}\s*[.．]\s*\S")
_TOC_MIN_ENTRIES = 3
_TOC_MIN_REAPPEARING = 2


def _heading_key(text: str) -> str:
    """見出しの再出現を照合する鍵。空白を除き全角 / 半角を同一視する。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def _toc_entry_ids(ordered: Sequence[dict[str, Any]]) -> set[int]:
    """目次・フロー一覧とみなすページの番号行の id を返す (#714)。

    同じページに番号付きの短い行が 3 つ以上並び、そのうち 2 つ以上が後のページで見出し（Section-header）
    として再出現するページを目次とみなす。表紙のフロー一覧「１．営業所登録情報変更 ※当資料２ページを参照」が
    再出現の規則で見出しに昇格し、本文の節がその配下に入るのを防ぐ。番号の形だけで判定し、語は見ない。
    """
    by_page: dict[int, list[dict[str, Any]]] = {}
    for record in ordered:
        by_page.setdefault(int(record.get("page") or 0), []).append(record)
    later_headings: dict[str, int] = {}
    for record in ordered:
        if str(record.get("category") or "") in SECTION_CATEGORIES:
            key = _heading_key(_record_text(record))
            later_headings.setdefault(key, int(record.get("page") or 0))
    ids: set[int] = set()
    for page, page_records in by_page.items():
        entries = [
            r for r in page_records
            if str(r.get("category") or "") in {"List-item", "Text", *SECTION_CATEGORIES}
            and len(_record_text(r).strip()) <= 40
            and _TOC_ENTRY_PATTERN.match(_record_text(r).strip())
            and not _SENTENCE_HEADING_PATTERN.search(_record_text(r).strip())
        ]
        if len(entries) < _TOC_MIN_ENTRIES:
            continue
        reappearing = sum(1 for r in entries if later_headings.get(_heading_key(_record_text(r)), page) > page)
        if reappearing >= _TOC_MIN_REAPPEARING:
            ids.update(id(r) for r in entries)
    return ids


def _heading_level(text: str) -> int | None:
    """見出しの番号表記から階層の深さを推定します。推定できない場合は None を返します。

    解析結果の見出しには階層情報がないため、番号の形式だけを根拠にします。①-⑳ は NFKC で
    数字へ変わるため、正規化前の文字で判定します。
    """
    stripped = str(text or "").strip()
    if re.match(r"[①-⑳]", stripped):
        return _CIRCLED_LEVEL
    normalized = unicodedata.normalize("NFKC", stripped)
    for pattern, level in _HEADING_LEVEL_PATTERNS:
        if pattern.match(normalized):
            return level
    dotted = _DOTTED_HEADING_PATTERN.match(normalized)
    return dotted.group(1).count(".") + 1 if dotted else None


def _heading_form(text: str) -> str:
    """階層を推定できない見出しの「形」（囲み記号の種類か無印）。同じ形の直近の見出しを兄弟とみなす。"""
    stripped = str(text or "").strip()
    return stripped[0] if stripped and stripped[0] in "【〔[［「『" else ""


def _heading_source(record: dict[str, Any]) -> str:
    """見出し record の出所。section_path_sources に残し、下流が見出しの信頼度を扱えるようにする (#814)。"""
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    if _int_value(raw.get("heading_level")) == _CHAPTER_LEVEL:
        return RUNNING_HEAD_HEADING_SOURCE
    if raw.get("recurring_heading"):
        return PROMOTED_TEXT_HEADING_SOURCE
    if raw.get("heading_source") == ROUTE_CAPTION_HEADING_SOURCE:
        return ROUTE_CAPTION_HEADING_SOURCE
    if str(record.get("category") or "") == "Title":
        return TITLE_HEADING_SOURCE
    return SECTION_HEADER_HEADING_SOURCE


def _updated_section_path(
    path: Sequence[str], levels: Sequence[int | None], depths: Sequence[int], text: str, level: int | None = None,
    *, sources: Sequence[str] = (), source: str = "",
) -> tuple[list[str], list[int | None], list[int], list[str]]:
    """見出し text を受けて section_path と、その推定レベル列・実効深さ列・出所列を更新する。

    実効深さ（depth）は path 上での入れ子の深さで、レベルを推定できた見出しはレベル自体、推定できない
    見出しは直上の見出しの深さ + 1 か、兄弟とみなした見出しと同じ深さになる。新しい見出しは自分以上の
    深さの見出しを末尾から外してから載る（stack）。

    - 英字系列の先頭（「A 【画面説明】」）は直近の非英字見出しの直下に新しい系列を始める。
      「D 【遷移画面】 > 〔納品書〕 > A 【画面説明】」のように、階層不明の見出しの下で系列が再開する
      操作説明書の形を保つため。系列の続き（B、C）は直近の英字見出しと同じ深さに並ぶ。
    - 同じ見出しの再出現（帳票の 2 ページ目など）は、その見出しへ戻る。
    - 階層不明の見出しは、同じ形（【…】、〔…〕、無印）の直近の階層不明見出しを兄弟として置き換える
      （「【修正方法１】」の次の「【修正方法２】」）。無印は番号見出しをまたいで遡らず、先頭の見出し
      （文書の表題）は兄弟の候補にしない。同じ形がなければ直上の見出しの配下に入る。
    """
    level = _heading_level(text) if level is None else level
    normalized = unicodedata.normalize("NFKC", text.strip())
    cut = len(path)
    depth: int | None = None
    if level == _LETTERED_LEVEL and _SERIES_START_PATTERN.match(normalized):
        cut = next((index + 1 for index in range(len(path) - 1, -1, -1) if levels[index] != _LETTERED_LEVEL), 0)
        depth = depths[cut - 1] + 1 if cut else 0
    elif level == _LETTERED_LEVEL:
        depth = next((depths[index] for index in range(len(path) - 1, -1, -1) if levels[index] == _LETTERED_LEVEL), None)
    elif level is not None:
        depth = level
    elif text in path:
        cut = list(path).index(text)
        depth = depths[cut]
    else:
        form = _heading_form(text)
        for index in range(len(path) - 1, 0, -1):
            if levels[index] is not None:
                if not form:
                    break
                continue
            if _heading_form(path[index]) == form:
                depth = depths[index]
                break
    if depth is None:
        depth = depths[cut - 1] + 1 if cut else 0
    else:
        while cut and depths[cut - 1] >= depth:
            cut -= 1
    padded_sources = list(sources) + [SECTION_HEADER_HEADING_SOURCE] * (len(path) - len(sources))
    return (
        (list(path[:cut]) + [text])[-_SECTION_PATH_LIMIT:],
        (list(levels[:cut]) + [level])[-_SECTION_PATH_LIMIT:],
        (list(depths[:cut]) + [depth])[-_SECTION_PATH_LIMIT:],
        (padded_sources[:cut] + [source or SECTION_HEADER_HEADING_SOURCE])[-_SECTION_PATH_LIMIT:],
    )


def _section_unit(path: Sequence[str]) -> tuple[str, ...]:
    """section_path のうち番号付き機能見出しまで（機能ユニット）。番号見出しがなければ空。"""
    return _section_unit_cached(tuple(str(item) for item in path))


@lru_cache(maxsize=4096)
def _section_unit_cached(path: tuple[str, ...]) -> tuple[str, ...]:
    # 親の区切り判定が child ごとに同じ path で呼ぶため、path のタプルを key に結果を共有する (#867)。
    for index in range(len(path) - 1, -1, -1):
        level = _heading_level(path[index])
        if level is not None and level <= _UNIT_MAX_LEVEL:
            return tuple(path[: index + 1])
    return ()


def _merge_section_paths(children: Sequence[DocumentChunk]) -> list[str]:
    merged: list[str] = []
    for child in children:
        for item in child.metadata.get("section_path") or []:
            if item and item not in merged:
                merged.append(item)
    return merged[-_SECTION_PATH_LIMIT:]


def _merge_section_path_sources(children: Sequence[DocumentChunk]) -> list[str]:
    """親の section_path（_merge_section_paths）と同じ順で、各見出しの出所を子から引き継ぐ (#814)。"""
    merged = _merge_section_paths(children)
    sources: dict[str, str] = {}
    for child in children:
        path = child.metadata.get("section_path") or []
        child_sources = child.metadata.get("section_path_sources") or []
        for item, source in zip(path, child_sources):
            sources.setdefault(item, source)
    return [sources.get(item, SECTION_HEADER_HEADING_SOURCE) for item in merged]


def _merge_logical_page_labels(children: Sequence[DocumentChunk]) -> dict[str, int]:
    labels: dict[str, int] = {}
    for child in children:
        for page, label in ((child.metadata.get("layout") or {}).get("logical_page_labels") or {}).items():
            labels[str(page)] = label
    return labels
