"""PDF の text layer の配置から、分数と上付き・下付きの構造を本文に戻す（#4140・#4154）。

解析エンジンは字を読み順に並べるので、上下に積んだ分子と分母は同じ行の字の並びになり、どちらが分子か
読み取れなくなる。ここでは字の位置と PDF に描かれた線だけを見て、次の場合に構造を文字列にする。

- 分数: 短い横線の真上と真下に、線の幅に収まり中央にそろった字がある（``a/(b-c)``）。線の高さの隣に演算子が
  ある線は式の中の線と言えるので、線の長さ・字との隙間の上限を広げ、折り返した分子・分母も含める。
- 式の項: 線のある分数と演算子でつながる、線の高さを挟んだ複数行の字は折り返した 1 つの項にする（``P Q``）。
  左右で対になる曲線の括弧も本文に戻す（``_expression_elements``）。
- 根号: 線の左端に斜めの線がつながるなら分数の線ではなく、線の下の字を ``√(...)`` にする。
- 線の無い分数: 同じページに線のある分数が無く、上下の字の並びの真ん中の高さに同じ式の行があり、演算子で
  隣り合い、字の配置で 2 行の式・行列・左そろえの並び・折り返しの行ではないと言えるものだけ
  （``_stacked_fractions``）。
- 上付き・下付き（分数を含む塊と、解析エンジンが数式と判定した塊）: 字が周りより小さく、基準線より上か下に
  ある（``x^2``・``a_1``）。
- 上付き（それ以外の本文）: 脚注の番号と区別できるものだけ、元の本文のその箇所だけを直す
  （``_body_script_edits``）。下付きは直さない。

位置だけで決まる処理で、モデルを呼ばない。条件に当たらない積み重ねと text layer の無いスキャンは推測せず、
元の本文のままにする。
"""

from __future__ import annotations

import re
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
# 線の高さの隣に演算子がある線の条件。この距離（字の大きさに対する割合）の中で一番近い字が演算子なら、式の中の線。
_AXIS_OPERATOR_REACH_RATIO = 1.5
_AXIS_OPERATOR_HEIGHT_RATIO = 0.4
# 式の中の線で広げる上限（字との隙間と、線が分子・分母の広い方より長い分の線の幅に対する割合）。
_MAX_RULE_GAP_RATIO_IN_EXPRESSION = 1.5
_MAX_RULE_EXTRA_RATIO_IN_EXPRESSION = 0.6
# 式の中の線で、分子・分母の折り返しとみる行の間隔（字の大きさに対する割合）。
_WRAPPED_LINE_PITCH_RATIO = 1.5
# 式の項をたどる隣の距離（字の大きさに対する割合）。
_TERM_REACH_RATIO = 2.5
# 折り返した項の行間の下限（上付き・下付きを除く）と、行の真ん中と線の高さの差の許容（字の大きさに対する割合）。
_TERM_MIN_PITCH_RATIO = 0.8
_TERM_AXIS_RATIO = 0.5
# 曲線の括弧の両端の高さの差の許容（字の大きさに対する割合）。
_BRACKET_PAIR_RATIO = 1.0
# 分数を隣の演算子の高さにそろえる差の上限（字の大きさに対する割合）。
_OPERATOR_ALIGN_RATIO = 1.0
# 根号の斜めの線と上の線の左端のずれの許容（pt）。
_RADICAL_JOINT_POINTS = 2.0
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
_ADDED_NOTATION = frozenset("()/^_{}√")

# 線の無い分数の判定（字の大きさに対する割合）。上下の中心の距離・同じ基準線の差・字の並びの中の隙間。
# 距離の下限は、上下の字の間に線を引く余地が無い行間（折り返しの行）を除く値。
_STACK_MIN_SEPARATION_RATIO = 1.3
_STACK_MAX_SEPARATION_RATIO = 2.4
_STACK_SAME_BASELINE_RATIO = 0.1
_STACK_RUN_GAP_RATIO = 0.6
# 上下の字の並びの真ん中の高さと、同じ行の字の中心の差の許容。
_STACK_AXIS_RATIO = 0.2
# 上下の字の大きさの差の許容（割合）と、同じ行の字がこの距離の中にあれば隣とみる距離。
_STACK_SIZE_RATIO = 0.1
_STACK_NEIGHBOR_RATIO = 1.0
# 上下の並びの中央のずれの許容。幅の差で「左そろえではない」と言うには、幅の差の 1/4 より小さいずれを求める。
_STACK_CENTERED_RATIO = 0.25
_STACK_WIDTH_DIFFERENCE_RATIO = 0.5
# 上下の外にこの距離（中心の差）で別の字があれば、3 行以上の並びか上付きを含むとみて戻さない。
_STACK_CLEARANCE_RATIO = 1.25
_STACK_OPERATORS = frozenset("=＝+＋-−－–×✕*＊∗·÷<>＜＞≤≥≦≧≠≈≒±∓")
_STACK_CLOSING_BRACKETS = frozenset(")）]］}｝|｜")
# 括弧の形の線（字の大きさに対する幅の上限と、上下の並びの高さに対する割合）。
_BRACKET_MAX_WIDTH_RATIO = 1.0
_BRACKET_MIN_HEIGHT_RATIO = 0.8

# 本文の上付きの判定。語の短い記号と数字・括弧の後だけにする（語の後の脚注の番号を除く）。
_SCRIPT_ADJACENT_RATIO = 0.3
_EXPONENT_MAX_LETTERS = 2
_EXPONENT_MAX_ALNUM = 3
_EXPONENT_PATTERN = re.compile(r"[+\-−－]?[0-9a-zA-Z]{1,4}")
# 脚注の本文の行頭の印（``1 本文``・``(1) 本文``・``注1 本文``・``a) 本文``）。
_FOOTNOTE_NUMBER_LINE = re.compile(r"\s*\S{0,2}?[\(（]?([0-9]{1,3})[\)）.．:：]?(?:\s|$)")
_FOOTNOTE_LETTER_LINE = re.compile(r"\s*[\(（]?([a-z])[\)）.．:：](?:\s|$)")


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
class PdfMathPage:
    """1 ページの字と線（すべてページの座標の PDF point）。

    ``other_paths`` は細い線でない path の外接矩形（left, bottom, right, top。曲線で描いた括弧など）。
    ``images`` は画像の外接矩形。
    """

    glyphs: list[PdfGlyph]
    horizontal_rules: list[PdfRule]
    vertical_rules: list[PdfRule]
    other_paths: list[tuple[float, float, float, float]]
    images: list[tuple[float, float, float, float]]


@dataclass(frozen=True)
class MathBlock:
    """解析エンジンの 1 つの本文の塊（``box`` は PDF point の left, bottom, right, top）。

    ``expression_only`` の塊（見出しと判定された塊）の字は、線のある式の一部と確かめられたときだけ使う。
    """

    key: str
    box: tuple[float, float, float, float]
    text: str
    is_formula: bool = False
    expression_only: bool = False


@dataclass(frozen=True)
class RestoredBlock:
    """構造を戻した本文。``merged_keys`` の塊の字を含むので、その塊はまとめて 1 つにする。"""

    key: str
    text: str
    merged_keys: tuple[str, ...]
    fraction_count: int
    script_count: int
    stacked_fraction_count: int = 0
    line_count: int = 1


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
    # 複数の字をまとめた 1 つの項（分数・折り返した項・括弧）。
    is_composite: bool = False
    is_stacked: bool = False
    space_before: bool = False
    expression_only: bool = False

    @property
    def center(self) -> float:
        return self.baseline + _CENTER_RATIO * self.size

    @property
    def middle(self) -> float:
        return (self.left + self.right) / 2


def has_glyph_in_box(box: tuple[float, float, float, float], glyphs: Sequence[PdfGlyph]) -> bool:
    """領域の中に text layer の字があるか（無ければスキャンの領域）。"""
    left, bottom, right, top = box
    return any(
        left <= (glyph.left + glyph.right) / 2 <= right and bottom <= glyph.baseline + _CENTER_RATIO * glyph.size <= top
        for glyph in glyphs
        if glyph.text.strip()
    )


def restore_math_text(
    blocks: Sequence[MathBlock],
    glyphs: Sequence[PdfGlyph],
    horizontal_rules: Sequence[PdfRule],
    vertical_rules: Sequence[PdfRule] = (),
    other_paths: Sequence[tuple[float, float, float, float]] = (),
) -> list[RestoredBlock]:
    """1 ページの本文の塊のうち、構造が分かったものだけを戻して返す（無ければ空）。"""
    tokens = _block_tokens(blocks, glyphs)
    if not tokens:
        return []
    rules = _fraction_rules(horizontal_rules, vertical_rules)
    heights = {block.key: block.box[3] - block.box[1] for block in blocks}
    groups: list[set[str]] = []
    has_ruled_fraction = False
    for rule in sorted(rules, key=lambda item: item.end - item.start):
        if _is_radical_bar(rule, other_paths):
            tokens = _with_radical(rule, rules, tokens)
            continue
        parts = _fraction_parts(rule, rules, tokens)
        if parts is None:
            continue
        has_ruled_fraction = True
        numerator, denominator = parts
        composite = _fraction_token(rule, numerator, denominator)
        members = {id(token) for token in (*numerator, *denominator)}
        tokens = [token for token in tokens if id(token) not in members]
        groups.append(set(composite.keys) | _main_line_keys(rule, composite, _plain_tokens(tokens), heights))
        _align_to_operator(composite, tokens)
        tokens.append(composite)
    for fraction in [token for token in tokens if token.fractions]:
        tokens, expression_keys = _absorb_expression(fraction, tokens, other_paths, heights)
        groups.append(set(fraction.keys) | expression_keys)
    # 線を描く文書の線の無い積み重ねは、分数でなく項目名の折り返しとみる（字の配置では区別できない）。
    if not has_ruled_fraction:
        for stack in _stacked_fractions(_plain_tokens(tokens), horizontal_rules, vertical_rules, other_paths):
            axis_rule = PdfRule(stack.axis, stack.left, stack.right)
            composite = _fraction_token(axis_rule, stack.upper, stack.lower, is_stacked=True)
            members = {id(token) for token in (*stack.upper, *stack.lower)}
            tokens = [token for token in tokens if id(token) not in members]
            groups.append(set(composite.keys) | _main_line_keys(axis_rule, composite, _plain_tokens(tokens), heights))
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
            stacked = sum(token.is_stacked for token in members_of_group)
            line_count = len(_lines(members_of_group))
            restored.append(RestoredBlock(keys[0], text, tuple(keys), fractions, scripts, stacked, line_count))
    footnote_markers = _footnote_markers([block for block in blocks if not block.expression_only], _plain_tokens(tokens))
    for block in blocks:
        if block.key in grouped or block.expression_only:
            continue
        members_of_block = [token for token in tokens if block.key in token.keys]
        if block.is_formula:
            text, _, scripts = _compose(members_of_block)
            if scripts and _agrees_with_original(block.text, text):
                restored.append(RestoredBlock(block.key, text, (block.key,), 0, scripts))
            continue
        edited = _body_script_edits(block.text, members_of_block, footnote_markers)
        if edited is not None:
            restored.append(RestoredBlock(block.key, edited[0], (block.key,), 0, edited[1]))
    return sorted(restored, key=lambda item: order[item.key])


def _plain_tokens(tokens: Sequence[_Token]) -> list[_Token]:
    return [token for token in tokens if not token.expression_only]


def _block_tokens(blocks: Sequence[MathBlock], glyphs: Sequence[PdfGlyph]) -> list[_Token]:
    tokens: list[_Token] = []
    # 本文の塊と重なる字は、見出しの塊でなく本文の塊のものにする。
    ordered = sorted(blocks, key=lambda item: item.expression_only)
    for glyph in glyphs:
        if not glyph.text.strip() or glyph.size <= 0:
            continue
        middle = (glyph.left + glyph.right) / 2
        center = glyph.baseline + _CENTER_RATIO * glyph.size
        block = next(
            (item for item in ordered if item.box[0] - 1 <= middle <= item.box[2] + 1 and item.box[1] - 1 <= center <= item.box[3] + 1),
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
                expression_only=block.expression_only,
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


def _is_radical_bar(rule: PdfRule, other_paths: Sequence[tuple[float, float, float, float]]) -> bool:
    """線の左端に、上がってきた斜めの線がつながるなら根号の上の線（分数の線ではない）。"""
    return any(
        abs(top - rule.position) <= _RADICAL_JOINT_POINTS
        and abs(right - rule.start) <= _RADICAL_JOINT_POINTS
        and top - bottom > right - left
        for left, bottom, right, top in other_paths
    )


def _with_radical(rule: PdfRule, rules: Sequence[PdfRule], tokens: Sequence[_Token]) -> list[_Token]:
    """根号の線の下の字を ``√(...)`` の 1 つの項にする。"""
    max_gap = _MAX_RULE_GAP_RATIO_IN_EXPRESSION
    inside = [
        token
        for token in _plain_tokens(tokens)
        if rule.start - _RULE_END_TOLERANCE_POINTS <= token.middle <= rule.end + _RULE_END_TOLERANCE_POINTS
        and _MIN_RULE_GAP_RATIO * token.size <= rule.position - token.high <= max_gap * token.size
    ]
    if not inside:
        return list(tokens)
    radicand = _with_wrapped_lines(inside, rule, rules, _plain_tokens(tokens), upward=False)
    text, fractions, scripts = _compose(radicand)
    members = {id(token) for token in radicand}
    return [
        *(token for token in tokens if id(token) not in members),
        _Token(
            text=f"√({text})",
            left=min(token.left for token in radicand),
            right=max(token.right for token in radicand),
            baseline=radicand[0].baseline,
            size=max(token.size for token in radicand),
            low=min(token.low for token in radicand),
            high=max(token.high for token in radicand),
            keys=frozenset(key for token in radicand for key in token.keys),
            fractions=fractions,
            scripts=scripts,
            is_composite=True,
        ),
    ]


def _fraction_parts(rule: PdfRule, rules: Sequence[PdfRule], tokens: Sequence[_Token]) -> tuple[list[_Token], list[_Token]] | None:
    in_expression = _is_beside_operator(rule, tokens)
    candidates = tokens if in_expression else _plain_tokens(tokens)
    left = rule.start - _RULE_END_TOLERANCE_POINTS
    right = rule.end + _RULE_END_TOLERANCE_POINTS
    inside = [token for token in candidates if left <= token.middle <= right]
    if any(abs(token.center - rule.position) <= 0.25 * token.size for token in inside):
        return None  # 字を貫く線（取り消し線）か、線の上に本文の行がある。
    max_gap = _MAX_RULE_GAP_RATIO_IN_EXPRESSION if in_expression else _MAX_RULE_GAP_RATIO
    numerator = [token for token in inside if _MIN_RULE_GAP_RATIO * token.size <= token.low - rule.position <= max_gap * token.size]
    denominator = [token for token in inside if _MIN_RULE_GAP_RATIO * token.size <= rule.position - token.high <= max_gap * token.size]
    if not numerator or not denominator:
        return None
    if in_expression:
        numerator = _with_wrapped_lines(numerator, rule, rules, candidates, upward=True)
        denominator = _with_wrapped_lines(denominator, rule, rules, candidates, upward=False)
    if not _fits_rule(numerator, denominator, rule, in_expression=in_expression):
        return None
    if _has_outside_neighbor(numerator, rule, rules, candidates) or _has_outside_neighbor(denominator, rule, rules, candidates):
        return None
    return numerator, denominator


def _is_beside_operator(rule: PdfRule, tokens: Sequence[_Token]) -> bool:
    """線の高さで左右に一番近い字のどちらかが演算子なら、線は式の中にある（区切りの線・下線ではない）。"""
    before: list[_Token] = []
    after: list[_Token] = []
    for token in tokens:
        if abs(token.center - rule.position) > _AXIS_OPERATOR_HEIGHT_RATIO * token.size:
            continue
        if 0 <= rule.start - token.right <= _AXIS_OPERATOR_REACH_RATIO * token.size:
            before.append(token)
        elif 0 <= token.left - rule.end <= _AXIS_OPERATOR_REACH_RATIO * token.size:
            after.append(token)
    nearest = [
        *([max(before, key=lambda token: token.right)] if before else []),
        *([min(after, key=lambda token: token.left)] if after else []),
    ]
    return any(token.text in _STACK_OPERATORS for token in nearest)


def _with_wrapped_lines(
    part: Sequence[_Token], rule: PdfRule, rules: Sequence[PdfRule], tokens: Sequence[_Token], *, upward: bool
) -> list[_Token]:
    """式の中の分子・分母に、線の幅の中で続く折り返しの行を足す。

    線の外へ続く行（本文）と、別の線の向こうの行では止める。
    """
    direction = 1.0 if upward else -1.0
    members = list(part)
    while True:
        edge = max(token.baseline * direction for token in members)
        size = max(token.size for token in members)
        taken = {id(token) for token in members}
        beyond = [
            token
            for token in tokens
            if id(token) not in taken
            and not token.is_composite
            and rule.start - _RULE_END_TOLERANCE_POINTS <= token.middle <= rule.end + _RULE_END_TOLERANCE_POINTS
            and 0 < token.baseline * direction - edge <= _WRAPPED_LINE_PITCH_RATIO * size
        ]
        if not beyond:
            return members
        nearest = min(token.baseline * direction for token in beyond)
        line = [token for token in beyond if abs(token.baseline * direction - nearest) <= 0.15 * size]
        crosses_rule = any(
            other is not rule and edge < other.position * direction < nearest and other.start < rule.end and other.end > rule.start
            for other in rules
        )
        if crosses_rule or _has_outside_neighbor(line, rule, rules, tokens):
            return members
        members.extend(line)


def _fits_rule(numerator: Sequence[_Token], denominator: Sequence[_Token], rule: PdfRule, *, in_expression: bool) -> bool:
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
    allowed = _MAX_RULE_EXTRA_SIZES * size
    if in_expression:
        allowed = max(allowed, _MAX_RULE_EXTRA_RATIO_IN_EXPRESSION * width)
    return width - widest <= allowed


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


def _fraction_token(rule: PdfRule, numerator: Sequence[_Token], denominator: Sequence[_Token], *, is_stacked: bool = False) -> _Token:
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
        is_composite=True,
        is_stacked=is_stacked,
    )


def _align_to_operator(fraction: _Token, tokens: Sequence[_Token]) -> None:
    """線が式の行の高さからずれる分数（分母の根号で線が上がるなど）を、隣の演算子の高さにそろえる。"""
    reach = _AXIS_OPERATOR_REACH_RATIO * fraction.size
    neighbors = [
        token
        for token in tokens
        if token.text in _STACK_OPERATORS
        and abs(token.center - fraction.center) <= _OPERATOR_ALIGN_RATIO * fraction.size
        and (0 <= fraction.left - token.right <= reach or 0 <= token.left - fraction.right <= reach)
    ]
    if neighbors:
        nearest = min(neighbors, key=lambda token: abs(token.center - fraction.center))
        fraction.baseline = nearest.center - _CENTER_RATIO * fraction.size


def _operand(text: str) -> str:
    """分子・分母を、1 語か全体が括弧で閉じているときを除いて括弧で囲む。"""
    return text if text.isalnum() or _is_enclosed(text.removeprefix("√")) else f"({text})"


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


@dataclass
class _Element:
    """線のある分数と同じ式の左右に並ぶ 1 つのまとまり。

    ``atom`` は線の高さの字の並び（演算子・分数・1 行の項）、``term`` は線の高さを挟む複数行の字の並び、
    ``bracket`` は曲線の括弧、``other`` は式の一部と言えない字の並び。
    """

    kind: str
    left: float
    right: float
    tokens: list[_Token]
    path: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def _absorb_expression(
    fraction: _Token,
    tokens: Sequence[_Token],
    other_paths: Sequence[tuple[float, float, float, float]],
    heights: dict[str, float],
) -> tuple[list[_Token], set[str]]:
    """線のある分数と演算子でつながる項を 1 つの式にまとめる（字の並びと、まとめる塊の key を返す）。

    折り返した項は 1 つの項にし、左右で対になる曲線の括弧は本文に戻す。
    """
    elements = _expression_elements(fraction, tokens, other_paths)
    accepted = [
        *_walk_expression(fraction, elements, heights, rightward=False),
        *_walk_expression(fraction, elements, heights, rightward=True),
    ]
    replaced = {id(token) for element in accepted if element.kind == "term" for token in element.tokens}
    result = [token for token in tokens if id(token) not in replaced]
    keys: set[str] = set()
    for element in accepted:
        keys.update(key for token in element.tokens for key in token.keys)
        if element.kind == "term":
            result.append(_term_token(element.tokens, fraction.center))
    result.extend(_bracket_tokens(fraction, accepted, tokens))
    return result, keys


def _expression_elements(
    fraction: _Token, tokens: Sequence[_Token], other_paths: Sequence[tuple[float, float, float, float]]
) -> list[_Element]:
    axis, size = fraction.center, fraction.size
    low, high = fraction.low - size, fraction.high + size
    # 字を同じ行の並びにし、横に重なる並びを 1 つのまとまりにする。
    runs = [run for run in _baseline_runs(tokens) if low <= run[0].center <= high and not _overlaps(run, fraction)]
    clusters: list[list[list[_Token]]] = []
    for run in sorted(runs, key=lambda item: item[0].left):
        if clusters and run[0].left < max(line[-1].right for line in clusters[-1]):
            clusters[-1].append(run)
        else:
            clusters.append([run])
    elements = [_cluster_element(cluster, axis, size) for cluster in clusters]
    elements.extend(
        _Element("atom", token.left, token.right, [token])
        for token in tokens
        if token.is_composite and token is not fraction and abs(token.center - axis) <= _AXIS_OPERATOR_HEIGHT_RATIO * size
    )
    min_height = _BRACKET_MIN_HEIGHT_RATIO * (fraction.high - fraction.low)
    elements.extend(
        _Element("bracket", path[0], path[2], [], path)
        for path in other_paths
        if path[2] - path[0] <= _BRACKET_MAX_WIDTH_RATIO * size and path[3] - path[1] >= min_height and path[1] <= axis <= path[3]
    )
    return elements


def _overlaps(run: Sequence[_Token], fraction: _Token) -> bool:
    return run[0].left < fraction.right - _RULE_END_TOLERANCE_POINTS and run[-1].right > fraction.left + _RULE_END_TOLERANCE_POINTS


def _cluster_element(cluster: Sequence[Sequence[_Token]], axis: float, size: float) -> _Element:
    lines = sorted(cluster, key=lambda line: -line[0].center)
    tokens = sorted((token for line in lines for token in line), key=lambda token: token.left)
    left, right = min(line[0].left for line in lines), max(line[-1].right for line in lines)
    if len(lines) == 1:
        on_axis = abs(lines[0][0].center - axis) <= _AXIS_OPERATOR_HEIGHT_RATIO * size
        return _Element("atom" if on_axis else "other", left, right, tokens)
    pitches = [upper[0].center - lower[0].center for upper, lower in zip(lines, lines[1:], strict=False)]
    middle = (lines[0][0].center + lines[-1][0].center) / 2
    is_term = (
        all(_TERM_MIN_PITCH_RATIO * size <= pitch <= _STACK_MAX_SEPARATION_RATIO * size for pitch in pitches)
        and abs(middle - axis) <= _TERM_AXIS_RATIO * size
        and all(abs(line[0].size - size) <= _STACK_SIZE_RATIO * size for line in lines)
    )
    return _Element("term" if is_term else "other", left, right, tokens)


def _walk_expression(fraction: _Token, elements: Sequence[_Element], heights: dict[str, float], *, rightward: bool) -> list[_Element]:
    """分数から外へ、演算子でつながるまとまりをたどる。つながらないものの手前で止める。"""
    direction = 1.0 if rightward else -1.0
    reach = _TERM_REACH_RATIO * fraction.size
    max_height = _MAX_MERGED_BLOCK_HEIGHT_RATIO * (fraction.high - fraction.low)
    ordered = sorted(elements, key=lambda item: item.left if rightward else -item.right)
    edge = fraction.right if rightward else fraction.left
    after_operator = False
    accepted: list[_Element] = []
    for element in ordered:
        near, far = (element.left, element.right) if rightward else (element.right, element.left)
        gap = (near - edge) * direction
        if gap < -_RULE_END_TOLERANCE_POINTS:
            continue
        if gap > reach or element.kind == "other":
            break
        if element.kind == "bracket":
            accepted.append(element)
            edge = far
            continue
        if any(heights[key] > max_height for token in element.tokens for key in token.keys - fraction.keys):
            break
        if element.kind == "term":
            if not after_operator:
                break
            after_operator = False
        else:
            ordered_tokens = sorted(element.tokens, key=lambda token: token.left * direction)
            if not after_operator and ordered_tokens[0].text not in _STACK_OPERATORS:
                break
            after_operator = ordered_tokens[-1].text in _STACK_OPERATORS
        accepted.append(element)
        edge = far
    return accepted


def _term_token(tokens: Sequence[_Token], axis: float) -> _Token:
    """折り返した項を、線の高さの 1 つの項にする（行の間は空白。解析エンジンの折り返しの本文と同じ）。"""
    size = max(token.size for token in tokens)
    texts: list[str] = []
    scripts = 0
    for line in _lines(tokens):
        text, line_scripts = _line_text(sorted(line, key=lambda item: item.left))
        texts.append(text)
        scripts += line_scripts
    return _Token(
        text=" ".join(texts),
        left=min(token.left for token in tokens),
        right=max(token.right for token in tokens),
        baseline=axis - _CENTER_RATIO * size,
        size=size,
        low=min(token.low for token in tokens),
        high=max(token.high for token in tokens),
        keys=frozenset(key for token in tokens for key in token.keys),
        scripts=scripts,
        is_composite=True,
    )


def _bracket_tokens(fraction: _Token, accepted: Sequence[_Element], tokens: Sequence[_Token]) -> list[_Token]:
    """分数を囲む左右の曲線が対になるなら、丸括弧の字にする。形は読まない（どの括弧でも項の結びつきは同じ）。"""
    brackets = [element for element in accepted if element.kind == "bracket"]
    lefts = [element for element in brackets if element.right <= fraction.left + _RULE_END_TOLERANCE_POINTS]
    rights = [element for element in brackets if element.left >= fraction.right - _RULE_END_TOLERANCE_POINTS]
    if not lefts or not rights:
        return []
    opening = lefts[0]
    closing = rights[0]
    tolerance = _BRACKET_PAIR_RATIO * fraction.size
    if abs(opening.path[1] - closing.path[1]) > tolerance or abs(opening.path[3] - closing.path[3]) > tolerance:
        return []
    # 同じ括弧を別の分数でも足さない。
    if any(token.is_composite and token.text in {"(", ")"} and token.left == opening.left for token in tokens):
        return []
    return [_bracket_token("(", opening, fraction), _bracket_token(")", closing, fraction)]


def _bracket_token(text: str, element: _Element, fraction: _Token) -> _Token:
    return _Token(
        text=text,
        left=element.left,
        right=element.right,
        baseline=fraction.baseline,
        size=fraction.size,
        low=fraction.low,
        high=fraction.high,
        keys=fraction.keys,
        is_composite=True,
    )


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


def _lines(tokens: Sequence[_Token]) -> list[list[_Token]]:
    """字の並びを行に分ける（上の行から）。行の中の順は整えない。"""
    lines: list[list[_Token]] = []
    for token in sorted(tokens, key=lambda item: -item.center):
        for line in lines:
            reference = max(line, key=lambda item: item.size)
            if abs(token.center - reference.center) <= _SAME_LINE_RATIO * max(token.size, reference.size):
                line.append(token)
                break
        else:
            lines.append([token])
    return lines


def _compose(tokens: Sequence[_Token]) -> tuple[str, int, int]:
    """字の並びを行に分け、左から順の本文にする（本文・分数の数・上付き下付きの数）。"""
    lines = _lines(tokens)
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
    plain = [token for token in line if not token.is_composite] or list(line)
    size = Counter(round(token.size, 1) for token in plain).most_common(1)[0][0]
    largest = max(token.size for token in plain)
    # 上付きの字が多い行でも、基準は本文の大きさにする。
    if size < _SCRIPT_MAX_SIZE_RATIO * largest:
        size = round(largest, 1)
    baselines = Counter(round(token.baseline, 1) for token in plain if round(token.size, 1) == size)
    return size, baselines.most_common(1)[0][0]


def _script_level(token: _Token, base_size: float, base_line: float) -> int:
    if token.is_composite or token.size > _SCRIPT_MAX_SIZE_RATIO * base_size:
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


@dataclass(frozen=True)
class _Stack:
    """線の無い分数の候補（上下の字の並びと、同じ行で左右に隣る字）。"""

    upper: list[_Token]
    lower: list[_Token]
    axis: float
    left: float
    right: float
    size: float
    left_neighbor: _Token | None
    right_neighbor: _Token | None
    proves_centering: bool


def _stacked_fractions(
    tokens: Sequence[_Token],
    horizontal_rules: Sequence[PdfRule],
    vertical_rules: Sequence[PdfRule],
    other_paths: Sequence[tuple[float, float, float, float]],
) -> list[_Stack]:
    """線の無い分数のうち、字の配置だけで分数と言えるもの。

    次のすべてに当たるときだけ分数とする。当たらなければ 2 行の式・行列・縦の並びと区別できないので戻さない。

    1. 上下の字の並びの真ん中の高さに同じ行の字があり、隣の字が演算子（式の中の項で、行の途中にある）。
       2 行の式は同じ行の字が片方の並びの高さにあるので当たらない。
    2. 上下の並びの近くに別の字・線・括弧の形の線が無い（3 行以上の並び・行列・上付きを含むものを除く）。
    3. 中央にそろっていると言える: 幅が違うのに中央がそろう、または左右に同じ行の字が続く（分数の連なりを含む）。
       幅が同じで右に続きが無い並びは左そろえの 2 行と区別できない。
    """
    runs = _baseline_runs(tokens)
    candidates = [
        stack
        for upper in runs
        for lower in runs
        if upper is not lower
        and (stack := _stack_candidate(upper, lower, tokens, horizontal_rules, vertical_rules, other_paths)) is not None
    ]
    return [stack for stack in candidates if _proves_fraction(stack, candidates)]


def _baseline_runs(tokens: Sequence[_Token]) -> list[list[_Token]]:
    """同じ基準線・同じ大きさで続く字の並び（分数に戻した字を除く）。"""
    runs: list[list[_Token]] = []
    for token in sorted((item for item in tokens if not item.is_composite), key=lambda item: item.left):
        for run in runs:
            last = run[-1]
            if (
                abs(token.baseline - last.baseline) <= _STACK_SAME_BASELINE_RATIO * last.size
                and abs(token.size - last.size) <= _STACK_SIZE_RATIO * last.size
                and token.left - last.right <= _STACK_RUN_GAP_RATIO * last.size
            ):
                run.append(token)
                break
        else:
            runs.append([token])
    return runs


def _stack_candidate(
    upper: Sequence[_Token],
    lower: Sequence[_Token],
    tokens: Sequence[_Token],
    horizontal_rules: Sequence[PdfRule],
    vertical_rules: Sequence[PdfRule],
    other_paths: Sequence[tuple[float, float, float, float]],
) -> _Stack | None:
    size = upper[0].size
    if abs(lower[0].size - size) > _STACK_SIZE_RATIO * size:
        return None
    separation = upper[0].center - lower[0].center
    if not _STACK_MIN_SEPARATION_RATIO * size <= separation <= _STACK_MAX_SEPARATION_RATIO * size:
        return None
    upper_left, upper_right = upper[0].left, upper[-1].right
    lower_left, lower_right = lower[0].left, lower[-1].right
    upper_width, lower_width = upper_right - upper_left, lower_right - lower_left
    offset = abs((upper_left + upper_right) / 2 - (lower_left + lower_right) / 2)
    if offset > _STACK_CENTERED_RATIO * size:
        return None
    left, right = min(upper_left, lower_left), max(upper_right, lower_right)
    axis = (upper[0].center + lower[0].center) / 2
    members = {id(token) for token in (*upper, *lower)}
    if _has_stack_obstacle(
        (left, lower[0].center, right, upper[0].center), size, tokens, members, horizontal_rules, vertical_rules, other_paths
    ):
        return None
    left_neighbor, right_neighbor = _axis_neighbors(axis, left, right, size, tokens, members)
    left_is_operator = left_neighbor is not None and left_neighbor.text in _STACK_OPERATORS
    right_is_operator = right_neighbor is not None and right_neighbor.text in _STACK_OPERATORS
    if not (left_is_operator or right_is_operator):
        return None
    if right_neighbor is not None and right_neighbor.text in _STACK_CLOSING_BRACKETS:
        return None
    width_difference = abs(upper_width - lower_width)
    proves_centering = width_difference >= _STACK_WIDTH_DIFFERENCE_RATIO * size and offset < _STACK_CENTERED_RATIO * width_difference
    return _Stack(list(upper), list(lower), axis, left, right, size, left_neighbor, right_neighbor, proves_centering)


def _has_stack_obstacle(
    box: tuple[float, float, float, float],
    size: float,
    tokens: Sequence[_Token],
    members: set[int],
    horizontal_rules: Sequence[PdfRule],
    vertical_rules: Sequence[PdfRule],
    other_paths: Sequence[tuple[float, float, float, float]],
) -> bool:
    left, lower_center, right, upper_center = box
    clearance = _STACK_CLEARANCE_RATIO * size
    for token in tokens:
        if id(token) in members or not left <= token.middle <= right:
            continue
        if lower_center - clearance <= token.center <= upper_center + clearance:
            return True
    # 表の罫線など、上下の並びの間を通る線。
    if any(lower_center <= rule.position <= upper_center and rule.start < right and rule.end > left for rule in horizontal_rules):
        return True
    # 縦の線・曲線の括弧（上下の並びと同じくらい高く、左右のすぐ外にある細いもの）。
    reach = _STACK_NEIGHBOR_RATIO * size
    height = upper_center - lower_center
    edges = [(rule.position, rule.start, rule.position, rule.end) for rule in vertical_rules]
    edges.extend((path[0], path[1], path[2], path[3]) for path in other_paths if path[2] - path[0] <= _BRACKET_MAX_WIDTH_RATIO * size)
    return any(
        top - bottom >= _BRACKET_MIN_HEIGHT_RATIO * height
        and min(top, upper_center) - max(bottom, lower_center) >= _BRACKET_MIN_HEIGHT_RATIO * height
        and (0 <= left - edge_right <= reach or 0 <= edge_left - right <= reach or (edge_left < right and edge_right > left))
        for edge_left, bottom, edge_right, top in edges
    )


def _axis_neighbors(
    axis: float, left: float, right: float, size: float, tokens: Sequence[_Token], members: set[int]
) -> tuple[_Token | None, _Token | None]:
    """上下の並びの真ん中の高さで、左右に一番近い字。"""
    reach = _STACK_NEIGHBOR_RATIO * size
    on_axis = [
        token for token in tokens if id(token) not in members and abs(token.center - axis) <= _STACK_AXIS_RATIO * max(token.size, size)
    ]
    before = [token for token in on_axis if 0 <= left - token.right <= reach]
    after = [token for token in on_axis if 0 <= token.left - right <= reach]
    return (
        max(before, key=lambda token: token.right, default=None),
        min(after, key=lambda token: token.left, default=None),
    )


def _proves_fraction(stack: _Stack, candidates: Sequence[_Stack]) -> bool:
    if stack.proves_centering or (stack.left_neighbor is not None and stack.right_neighbor is not None):
        return True
    return any(_joined_by_operator(stack, other) for other in candidates if other is not stack)


def _joined_by_operator(stack: _Stack, other: _Stack) -> bool:
    """同じ行で演算子 1 字を挟んで別の候補と隣り合う（``p/q + r/s`` の最後の項）。"""
    if abs(stack.axis - other.axis) > _STACK_AXIS_RATIO * stack.size:
        return False
    reach = _STACK_NEIGHBOR_RATIO * stack.size
    before, after = stack.left_neighbor, stack.right_neighbor
    return (before is not None and before.text in _STACK_OPERATORS and 0 <= before.left - other.right <= reach) or (
        after is not None and after.text in _STACK_OPERATORS and 0 <= other.left - after.right <= reach
    )


def _footnote_markers(blocks: Sequence[MathBlock], tokens: Sequence[_Token]) -> set[str]:
    """このページで脚注の本文の行頭にある印。同じ印の上付きは脚注の番号とみる。"""
    markers: set[str] = set()
    for block in blocks:
        normalized = unicodedata.normalize("NFKC", block.text)
        match = _FOOTNOTE_NUMBER_LINE.match(normalized) or _FOOTNOTE_LETTER_LINE.match(normalized)
        if match is not None:
            markers.add(match.group(1))
    for line in _lines([token for token in tokens if not token.is_composite]):
        ordered = sorted(line, key=lambda item: item.left)
        base_size, base_line = _line_reference(ordered)
        for token in ordered:
            if _script_level(token, base_size, base_line) != 1:
                break
            markers.add(unicodedata.normalize("NFKC", token.text))
    return markers


def _body_script_edits(text: str, tokens: Sequence[_Token], footnote_markers: set[str]) -> tuple[str, int] | None:
    """本文の中の上付きを、元の本文のその箇所だけ直す（直すものが無ければ None）。

    段落の改行・空白は解析エンジンの本文のまま保つ。字の配置から求めた箇所の数と本文の中の同じ字の並びの数が
    違うとき（同じ字の並びが普通の大きさでもある）は、どちらか決められないので直さない。
    """
    found = Counter[tuple[str, str]]()
    for line in _lines([token for token in tokens if not token.is_composite]):
        found.update(_line_body_scripts(sorted(line, key=lambda item: item.left), footnote_markers))
    if not found:
        return None
    spans: list[tuple[int, int, str]] = []
    for (base, script), count in found.items():
        matches = list(re.finditer(rf"{re.escape(base)}\s*{re.escape(script)}", text))
        if len(matches) != count:
            continue
        body = script if len(script) == 1 else f"{{{script}}}"
        spans.extend((match.start(), match.end(), f"{base}^{body}") for match in matches)
    if not spans:
        return None
    spans.sort()
    if any(previous[1] > current[0] for previous, current in zip(spans, spans[1:], strict=False)):
        return None
    pieces: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        pieces.extend((text[cursor:start], replacement))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), len(spans)


def _line_body_scripts(line: Sequence[_Token], footnote_markers: set[str]) -> list[tuple[str, str]]:
    """1 行の中の、脚注の番号と区別できる上付き（基の字・上付きの字）。"""
    base_size, base_line = _line_reference(line)
    levels = [_script_level(token, base_size, base_line) for token in line]
    found: list[tuple[str, str]] = []
    index = 0
    while index < len(line):
        level = levels[index]
        if not level:
            index += 1
            continue
        end = index
        while end + 1 < len(line) and levels[end + 1] == level:
            end += 1
        script = "".join(token.text for token in line[index : end + 1])
        base = _script_base(line, levels, index)
        if level > 0 and base and _is_body_exponent(base, script, footnote_markers):
            found.append((base, script))
        index = end + 1
    return found


def _script_base(line: Sequence[_Token], levels: Sequence[int], index: int) -> str:
    """上付き・下付きの直前に隙間なく続く基の字（英数字か、閉じた括弧の中が英数字と演算子だけの項）。"""
    chars: list[str] = []
    depth = 0
    for cursor in range(index - 1, -1, -1):
        token, follower = line[cursor], line[cursor + 1]
        if levels[cursor] or follower.space_before or follower.left - token.right > _SCRIPT_ADJACENT_RATIO * token.size:
            break
        char = unicodedata.normalize("NFKC", token.text)
        if char == ")":
            depth += 1
        elif char == "(":
            if not depth:
                break
            depth -= 1
        elif depth:
            if not (char.isascii() or _is_base_char(char) or char in _STACK_OPERATORS):
                return ""
        elif not _is_base_char(char):
            break
        chars.append(token.text)
        if not depth and char == "(":
            break
    return "" if depth else "".join(reversed(chars))


def _is_base_char(char: str) -> bool:
    return len(char) == 1 and char.isalnum() and (char.isascii() or unicodedata.name(char, "").startswith("GREEK"))


def _is_body_exponent(base: str, script: str, footnote_markers: set[str]) -> bool:
    normalized_script = unicodedata.normalize("NFKC", script)
    if normalized_script == "1" or normalized_script in footnote_markers:
        return False
    if _EXPONENT_PATTERN.fullmatch(normalized_script) is None:
        return False
    if base.endswith((")", "）")):
        return True
    normalized_base = unicodedata.normalize("NFKC", base)
    return len(normalized_base) <= (_EXPONENT_MAX_LETTERS if normalized_base.isalpha() else _EXPONENT_MAX_ALNUM)
