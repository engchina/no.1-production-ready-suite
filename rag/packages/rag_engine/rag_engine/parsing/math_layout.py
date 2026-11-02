"""PDF の text layer の配置から、分数と上付き・下付きの構造を本文に戻す（#4140）。

解析エンジンは字を読み順に並べるので、上下に積んだ分子と分母は同じ行の字の並びになり、どちらが分子か
読み取れなくなる。ここでは字の位置と PDF に描かれた横線だけを見て、次の場合に構造を文字列にする。

- 分数: 短い横線の真上と真下に、線の幅に収まり中央にそろった字がある（``a/(b-c)``）。
- 上付き・下付き: 字が周りより小さく、基準線より上か下にある（``x^2``・``a_1``）。分数を含むか、解析
  エンジンが数式と判定した塊だけで行う（本文の脚注の番号を指数にしないため）。

位置だけで決まる処理で、モデルを呼ばない。線の無い積み重ね（2 行の式・行列と区別できない）と text layer
の無いスキャンは推測せず、元の本文のままにする。
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

# 字の縦の広がりを、基準線と字の大きさから見積もる割合（PDF のフォントの実寸に依らない近似）。
_DESCENT_RATIO = 0.2
_ASCENT_RATIO = 0.85
_CENTER_RATIO = 0.35
# 分子・分母と線の隙間の範囲（字の大きさに対する割合）。
_MIN_RULE_GAP_RATIO = -0.3
_MAX_RULE_GAP_RATIO = 1.0
# 線の両端の許容（pt）と、分子・分母の中央のずれの許容（線の幅に対する割合）。
_RULE_END_TOLERANCE_POINTS = 1.0
_CENTERED_RATIO = 0.2
# 線が分子・分母の広い方よりこの字数を超えて長いものは、分数の線としない（区切りの線など）。
_MAX_RULE_EXTRA_SIZES = 4.0
# 表の罫線と判定する、線の端と別の線の距離（pt）。
_RULE_JOINT_POINTS = 1.5
# 同じ基準線の字が線の外のこの距離（字の大きさに対する割合）にあれば、下線とみて分数にしない。
_UNDERLINE_NEIGHBOR_RATIO = 0.8
# 同じ行とする中心の差・text layer に空白が無くても空白とみる隙間（字の大きさに対する割合）。
_SAME_LINE_RATIO = 0.5
_SPACE_GAP_RATIO = 0.5
# 上付き・下付きとする字の大きさの比と、基準線からのずれ（基準の字の大きさに対する割合）。
_SCRIPT_MAX_SIZE_RATIO = 0.85
_SUPERSCRIPT_MIN_SHIFT_RATIO = 0.15
_SUBSCRIPT_MIN_SHIFT_RATIO = 0.08
# 分数の前後の字をつなげて 1 つの塊にする距離（字の大きさに対する割合）。
_MAIN_LINE_REACH_RATIO = 1.5
# 隣の塊をまとめる上限（分数の高さに対する塊の高さの割合）。別の段の段落などをまとめない。
_MAX_MERGED_BLOCK_HEIGHT_RATIO = 1.5
# 戻した本文が元の本文の字とこの割合以上一致しなければ、text layer を信じない。
_MIN_TEXT_AGREEMENT = 0.85
_ADDED_NOTATION = frozenset("()/^_{}")


@dataclass(frozen=True)
class PdfGlyph:
    """text layer の 1 字（PDF point。左下原点）。"""

    text: str
    left: float
    right: float
    baseline: float
    size: float
    space_before: bool = False


@dataclass(frozen=True)
class PdfRule:
    """PDF に描かれた細い線（水平は ``position`` が y、``start``・``end`` が x。垂直は逆）。"""

    position: float
    start: float
    end: float


@dataclass(frozen=True)
class MathBlock:
    """解析エンジンの 1 つの本文の塊（``box`` は PDF point の left, bottom, right, top）。"""

    key: str
    box: tuple[float, float, float, float]
    text: str
    is_formula: bool = False


@dataclass(frozen=True)
class RestoredBlock:
    """構造を戻した本文。``merged_keys`` の塊の字を含むので、その塊はまとめて 1 つにする。"""

    key: str
    text: str
    merged_keys: tuple[str, ...]
    fraction_count: int
    script_count: int


@dataclass
class _Token:
    text: str
    left: float
    right: float
    baseline: float
    size: float
    low: float
    high: float
    keys: frozenset[str]
    fractions: int = 0
    scripts: int = 0
    is_fraction: bool = False
    space_before: bool = False

    @property
    def center(self) -> float:
        return self.baseline + _CENTER_RATIO * self.size

    @property
    def middle(self) -> float:
        return (self.left + self.right) / 2


def restore_math_text(
    blocks: Sequence[MathBlock],
    glyphs: Sequence[PdfGlyph],
    horizontal_rules: Sequence[PdfRule],
    vertical_rules: Sequence[PdfRule] = (),
) -> list[RestoredBlock]:
    """1 ページの本文の塊のうち、構造が分かったものだけを戻して返す（無ければ空）。"""
    tokens = _block_tokens(blocks, glyphs)
    if not tokens:
        return []
    rules = _fraction_rules(horizontal_rules, vertical_rules)
    heights = {block.key: block.box[3] - block.box[1] for block in blocks}
    groups: list[set[str]] = []
    for rule in sorted(rules, key=lambda item: item.end - item.start):
        parts = _fraction_parts(rule, rules, tokens)
        if parts is None:
            continue
        numerator, denominator = parts
        composite = _fraction_token(rule, numerator, denominator)
        members = {id(token) for token in (*numerator, *denominator)}
        tokens = [token for token in tokens if id(token) not in members]
        groups.append(set(composite.keys) | _main_line_keys(rule, composite, tokens, heights))
        tokens.append(composite)

    order = {block.key: index for index, block in enumerate(blocks)}
    merged = _merge_groups(groups)
    grouped = {key for group in merged for key in group}
    by_key = {block.key: block for block in blocks}
    restored: list[RestoredBlock] = []
    for group in merged:
        keys = sorted(group, key=order.__getitem__)
        members_of_group = [token for token in tokens if token.keys & group]
        text, fractions, scripts = _compose(members_of_group)
        original = " ".join(by_key[key].text for key in keys)
        if fractions and _agrees_with_original(original, text):
            restored.append(RestoredBlock(keys[0], text, tuple(keys), fractions, scripts))
    for block in blocks:
        if block.key in grouped or not block.is_formula:
            continue
        members_of_block = [token for token in tokens if block.key in token.keys]
        text, _, scripts = _compose(members_of_block)
        if scripts and _agrees_with_original(block.text, text):
            restored.append(RestoredBlock(block.key, text, (block.key,), 0, scripts))
    return sorted(restored, key=lambda item: order[item.key])


def _block_tokens(blocks: Sequence[MathBlock], glyphs: Sequence[PdfGlyph]) -> list[_Token]:
    tokens: list[_Token] = []
    for glyph in glyphs:
        if not glyph.text.strip() or glyph.size <= 0:
            continue
        middle = (glyph.left + glyph.right) / 2
        center = glyph.baseline + _CENTER_RATIO * glyph.size
        block = next(
            (item for item in blocks if item.box[0] - 1 <= middle <= item.box[2] + 1 and item.box[1] - 1 <= center <= item.box[3] + 1),
            None,
        )
        if block is None:
            continue
        tokens.append(
            _Token(
                text=glyph.text,
                left=glyph.left,
                right=glyph.right,
                baseline=glyph.baseline,
                size=glyph.size,
                low=glyph.baseline - _DESCENT_RATIO * glyph.size,
                high=glyph.baseline + _ASCENT_RATIO * glyph.size,
                keys=frozenset({block.key}),
                space_before=glyph.space_before,
            )
        )
    return tokens


def _fraction_rules(horizontal_rules: Sequence[PdfRule], vertical_rules: Sequence[PdfRule]) -> list[PdfRule]:
    """表の罫線（別の線とつながる線）を除いた横線。塗りと線で重ねて描かれたものは 1 本にする。"""
    unique = {
        (round(rule.position, 1), round(rule.start, 1), round(rule.end, 1)): rule for rule in horizontal_rules if rule.end > rule.start
    }
    candidates = list(unique.values())
    return [rule for rule in candidates if not _joined_to_other_rule(rule, candidates, vertical_rules)]


def _joined_to_other_rule(rule: PdfRule, horizontal_rules: Sequence[PdfRule], vertical_rules: Sequence[PdfRule]) -> bool:
    for other in horizontal_rules:
        if other is rule or abs(other.position - rule.position) > _RULE_JOINT_POINTS:
            continue
        if abs(other.start - rule.end) <= _RULE_JOINT_POINTS or abs(rule.start - other.end) <= _RULE_JOINT_POINTS:
            return True
    return any(
        vertical.start - _RULE_JOINT_POINTS <= rule.position <= vertical.end + _RULE_JOINT_POINTS
        and (abs(vertical.position - rule.start) <= _RULE_JOINT_POINTS or abs(vertical.position - rule.end) <= _RULE_JOINT_POINTS)
        for vertical in vertical_rules
    )


def _fraction_parts(rule: PdfRule, rules: Sequence[PdfRule], tokens: Sequence[_Token]) -> tuple[list[_Token], list[_Token]] | None:
    left = rule.start - _RULE_END_TOLERANCE_POINTS
    right = rule.end + _RULE_END_TOLERANCE_POINTS
    inside = [token for token in tokens if left <= token.middle <= right]
    if any(abs(token.center - rule.position) <= 0.25 * token.size for token in inside):
        return None  # 字を貫く線（取り消し線）か、線の上に本文の行がある。
    numerator = [
        token for token in inside if _MIN_RULE_GAP_RATIO * token.size <= token.low - rule.position <= _MAX_RULE_GAP_RATIO * token.size
    ]
    denominator = [
        token for token in inside if _MIN_RULE_GAP_RATIO * token.size <= rule.position - token.high <= _MAX_RULE_GAP_RATIO * token.size
    ]
    if not numerator or not denominator:
        return None
    if not _fits_rule(numerator, denominator, rule):
        return None
    if _has_outside_neighbor(numerator, rule, rules, tokens) or _has_outside_neighbor(denominator, rule, rules, tokens):
        return None
    return numerator, denominator


def _fits_rule(numerator: Sequence[_Token], denominator: Sequence[_Token], rule: PdfRule) -> bool:
    width = rule.end - rule.start
    middle = (rule.start + rule.end) / 2
    widest = 0.0
    for part in (numerator, denominator):
        part_left = min(token.left for token in part)
        part_right = max(token.right for token in part)
        widest = max(widest, part_right - part_left)
        if abs((part_left + part_right) / 2 - middle) > _CENTERED_RATIO * width + (_RULE_END_TOLERANCE_POINTS):
            return False
    size = max(token.size for token in (*numerator, *denominator))
    return width - widest <= _MAX_RULE_EXTRA_SIZES * size


def _has_outside_neighbor(part: Sequence[_Token], rule: PdfRule, rules: Sequence[PdfRule], tokens: Sequence[_Token]) -> bool:
    """分子・分母と同じ基準線の字が線の外へ続くなら、下線の付いた行で分数ではない。"""
    baseline = part[0].baseline
    size = part[0].size
    members = {id(token) for token in part}
    for token in tokens:
        if id(token) in members or abs(token.baseline - baseline) > 0.15 * size:
            continue
        distance = max(rule.start - token.right, token.left - rule.end)
        if not 0 <= distance <= _UNDERLINE_NEIGHBOR_RATIO * size:
            continue
        if not any(
            other is not rule and other.start - _RULE_END_TOLERANCE_POINTS <= token.middle <= other.end + _RULE_END_TOLERANCE_POINTS
            for other in rules
        ):
            return True
    return False


def _fraction_token(rule: PdfRule, numerator: Sequence[_Token], denominator: Sequence[_Token]) -> _Token:
    numerator_text, numerator_fractions, numerator_scripts = _compose(numerator)
    denominator_text, denominator_fractions, denominator_scripts = _compose(denominator)
    members = (*numerator, *denominator)
    size = max(token.size for token in members)
    return _Token(
        text=f"{_operand(numerator_text)}/{_operand(denominator_text)}",
        left=min(rule.start, *(token.left for token in members)),
        right=max(rule.end, *(token.right for token in members)),
        baseline=rule.position - _CENTER_RATIO * size,
        size=size,
        low=min(token.low for token in members),
        high=max(token.high for token in members),
        keys=frozenset(key for token in members for key in token.keys),
        fractions=1 + numerator_fractions + denominator_fractions,
        scripts=numerator_scripts + denominator_scripts,
        is_fraction=True,
    )


def _operand(text: str) -> str:
    """分子・分母を、1 語か全体が括弧で閉じているときを除いて括弧で囲む。"""
    return text if text.isalnum() or _is_enclosed(text) else f"({text})"


def _is_enclosed(text: str) -> bool:
    pairs = {"(": ")", "（": "）"}
    closing = pairs.get(text[:1])
    if closing is None or not text.endswith(closing):
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == text[0]:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0 and index < len(text) - 1:
                return False
    return depth == 0


def _main_line_keys(rule: PdfRule, composite: _Token, tokens: Sequence[_Token], heights: dict[str, float]) -> set[str]:
    """分数と同じ行で隣にある字の塊（解析エンジンが式を分けたときに 1 つへ戻す）。

    分数より大きく高い塊（隣の段の段落など）はまとめない。
    """
    reach = _MAIN_LINE_REACH_RATIO * composite.size
    max_height = _MAX_MERGED_BLOCK_HEIGHT_RATIO * (composite.high - composite.low)
    keys: set[str] = set()
    for token in tokens:
        if abs(token.center - rule.position) > _SAME_LINE_RATIO * max(token.size, composite.size):
            continue
        if max(composite.left - token.right, token.left - composite.right) <= reach:
            keys.update(key for key in token.keys if heights[key] <= max_height)
    return keys


def _merge_groups(groups: Sequence[set[str]]) -> list[set[str]]:
    merged: list[set[str]] = []
    for group in groups:
        current = set(group)
        rest: list[set[str]] = []
        for other in merged:
            if other & current:
                current |= other
            else:
                rest.append(other)
        merged = [*rest, current]
    return merged


def _compose(tokens: Sequence[_Token]) -> tuple[str, int, int]:
    """字の並びを行に分け、左から順の本文にする（本文・分数の数・上付き下付きの数）。"""
    lines: list[list[_Token]] = []
    for token in sorted(tokens, key=lambda item: -item.center):
        for line in lines:
            reference = max(line, key=lambda item: item.size)
            if abs(token.center - reference.center) <= _SAME_LINE_RATIO * max(token.size, reference.size):
                line.append(token)
                break
        else:
            lines.append([token])
    fractions = sum(token.fractions for token in tokens)
    scripts = sum(token.scripts for token in tokens)
    texts: list[str] = []
    for line in lines:
        text, line_scripts = _line_text(sorted(line, key=lambda item: item.left))
        texts.append(text)
        scripts += line_scripts
    return " ".join(texts), fractions, scripts


def _line_text(line: Sequence[_Token]) -> tuple[str, int]:
    base_size, base_line = _line_reference(line)
    parts: list[str] = []
    scripts = 0
    previous: _Token | None = None
    previous_level = 0
    for token in line:
        level = _script_level(token, base_size, base_line)
        if level != previous_level:
            scripts += 1 if level else 0
            parts.append({-1: "\0_", 0: "\0", 1: "\0^"}[level])
        if previous is not None and not level:
            gap = token.left - previous.right
            if token.space_before or gap >= _SPACE_GAP_RATIO * min(token.size, previous.size):
                parts.append(" ")
        parts.append(token.text)
        previous, previous_level = token, level
    return _braced_scripts("".join(parts)), scripts


def _braced_scripts(text: str) -> str:
    """``\\0^ab\\0`` の印を ``^{ab}``（1 字なら ``^a``）にする。"""
    chunks = text.split("\0")
    rebuilt = [chunks[0]]
    for chunk in chunks[1:]:
        if chunk[:1] in {"^", "_"}:
            mark, body = chunk[0], chunk[1:]
            rebuilt.append(f"{mark}{body}" if len(body) == 1 else f"{mark}{{{body}}}")
        else:
            rebuilt.append(chunk)
    return "".join(rebuilt)


def _line_reference(line: Sequence[_Token]) -> tuple[float, float]:
    """行の基準（字数が最も多い大きさと、その大きさの字の基準線）。"""
    plain = [token for token in line if not token.is_fraction] or list(line)
    size = Counter(round(token.size, 1) for token in plain).most_common(1)[0][0]
    largest = max(token.size for token in plain)
    # 上付きの字が多い行でも、基準は本文の大きさにする。
    if size < _SCRIPT_MAX_SIZE_RATIO * largest:
        size = round(largest, 1)
    baselines = Counter(round(token.baseline, 1) for token in plain if round(token.size, 1) == size)
    return size, baselines.most_common(1)[0][0]


def _script_level(token: _Token, base_size: float, base_line: float) -> int:
    if token.is_fraction or token.size > _SCRIPT_MAX_SIZE_RATIO * base_size:
        return 0
    shift = token.baseline - base_line
    if shift >= _SUPERSCRIPT_MIN_SHIFT_RATIO * base_size:
        return 1
    if shift <= -_SUBSCRIPT_MIN_SHIFT_RATIO * base_size:
        return -1
    return 0


def _agrees_with_original(original: str, restored: str) -> bool:
    """戻した本文の字が元の本文と一致しないとき（text layer の符号化の崩れ）は使わない。"""

    def characters(text: str) -> Counter[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return Counter(char for char in normalized if not char.isspace() and char not in _ADDED_NOTATION)

    left, right = characters(original), characters(restored)
    if not left or not right:
        return False
    common = sum((left & right).values())
    return common / sum(left.values()) >= _MIN_TEXT_AGREEMENT and common / sum(right.values()) >= _MIN_TEXT_AGREEMENT
