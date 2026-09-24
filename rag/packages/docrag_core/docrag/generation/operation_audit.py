"""回答文と引用本文に対する決定的な判定。LLM を呼ばず、入力を変更しない。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from docrag.parsing.vision_prompt_rules import is_vision_enumeration_line


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
from docrag.retrieval.task_contract import GENERIC_TARGET_WORDS as _GENERIC_DELETION_TARGETS


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
