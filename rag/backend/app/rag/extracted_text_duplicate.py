"""解析後の本文で見る文書の重複（#5313）。

アップロードの時の重複の判定は原本のバイト列の sha256 で比べるため、書式だけが違う同じ内容の
文書（1 行の JSON と整形した JSON、同じ本文の PDF とテキストなど）は重複にならない。既定の
レシピ（レシピ 1）の抽出の後、索引用の本文の hash を文書に記録し、同じナレッジベースに先に
登録された正本の文書と同じなら、アップロードの時の重複と同じ扱い（``duplicate_of_document_id``・
``duplicate_content`` の SKIPPED）にする。形式ごとの規則は持たない。
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

# 取込 job の skip_reason。アップロードの時の重複と同じ値にする（画面・一括取込が同じに扱う）。
DUPLICATE_CONTENT_SKIP_REASON = "duplicate_content"

ExtractedTextGate = Callable[[str], Awaitable[None]]


class ExtractedTextDuplicateError(Exception):
    """解析後の本文が先に登録された文書と同じだったので、取込を重複として止めた。失敗ではない。"""

    def __init__(self, duplicate_of_document_id: str) -> None:
        super().__init__(duplicate_of_document_id)
        self.duplicate_of_document_id = duplicate_of_document_id


class ExtractedTextHashStore(Protocol):
    async def record_extracted_text_hash(
        self, document_id: str, extracted_text_sha256: str, *, judge_duplicate: bool
    ) -> str | None: ...


def extracted_text_sha256(text: str) -> str:
    """索引用の本文の hash。空白・改行・字下げの違いだけでは値を変えない。

    文字は NFC（同じ文字の合成と分解の違いだけをそろえる）にし、空白の文字をすべて除く。
    空白以外の文字が 1 つでも違えば別の値になる。
    """
    normalized = "".join(unicodedata.normalize("NFC", text).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extracted_text_duplicate_gate(
    store: ExtractedTextHashStore,
    *,
    document_id: str,
    recipe: Mapping[str, object] | None,
    duplicate_of_document_id: str | None,
) -> ExtractedTextGate | None:
    """抽出の後に呼ぶ確認を返す。確認しない取込なら None。

    - 既定のレシピ（レシピ 1）だけ。ほかのレシピは設定の試行で、文書の本文を表さない。
    - 既に重複の文書（アップロードの時の重複・重複を無視して取り込む操作）は確認しない。
    - 重複の判定は、初めて索引を作る取込（レシピに有効な版が無い）だけ。索引のある文書の
      再処理は hash の記録だけを新しくし、後から重複にしない（アップロードの時の判定と同じく、
      重複かは登録の時に 1 回決める）。
    """
    if recipe is None or duplicate_of_document_id is not None:
        return None
    if not _is_default_recipe(recipe):
        return None
    judge_duplicate = recipe.get("active_chunk_set_id") is None

    async def gate(text: str) -> None:
        canonical_id = await store.record_extracted_text_hash(
            document_id, extracted_text_sha256(text), judge_duplicate=judge_duplicate
        )
        if canonical_id is not None:
            raise ExtractedTextDuplicateError(canonical_id)

    return gate


def _is_default_recipe(recipe: Mapping[str, object]) -> bool:
    try:
        return int(str(recipe.get("slot_no"))) == 1
    except ValueError:
        return False
