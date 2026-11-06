"""視覚説明の回答本文と検索本文を、同じ版の検証済み投影として保持する。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

VISION_TEXT_PROJECTION_VERSION = 1
VISION_TEXT_PROJECTION_KEY = "vision_text_projection"


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VisionTextProjection:
    answer_description: str
    index_text: str
    origin: Literal["image_generated_description"] = "image_generated_description"

    def dumps(self) -> str:
        return json.dumps(
            {
                "version": VISION_TEXT_PROJECTION_VERSION,
                "origin": self.origin,
                "answer_description": self.answer_description,
                "index_text": self.index_text,
                "answer_sha256": text_sha256(self.answer_description),
                "index_sha256": text_sha256(self.index_text),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def load_vision_text_projection(value: object) -> VisionTextProjection | None:
    """不明な旧本文・自己申告の由来・破損hashから投影を推測しない。"""
    if not isinstance(value, str):
        return None
    try:
        raw = json.loads(value)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(raw, dict)
        or type(raw.get("version")) is not int
        or raw.get("version") != VISION_TEXT_PROJECTION_VERSION
    ):
        return None
    if raw.get("origin") != "image_generated_description":
        return None
    answer, index = raw.get("answer_description"), raw.get("index_text")
    if not isinstance(answer, str) or not isinstance(index, str):
        return None
    if raw.get("answer_sha256") != text_sha256(answer) or raw.get("index_sha256") != text_sha256(
        index
    ):
        return None
    return VisionTextProjection(answer, index)


def has_answer_text_projection(metadata: object, body: str) -> bool:
    if not isinstance(metadata, dict):
        return False
    value = metadata.get("answer_text_projection")
    return (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value.get("version") == VISION_TEXT_PROJECTION_VERSION
        and value.get("body_sha256") == text_sha256(body)
    )


def answer_text_from_projection(
    metadata: object, body: str, index_text: str, *, parent: bool = False
) -> str:
    """未記録の旧契約と、存在するが壊れた新契約を区別する。"""
    if isinstance(metadata, dict) and "answer_text_projection" in metadata:
        if not has_answer_text_projection(metadata, body):
            raise ValueError("回答本文の投影の版またはhashが一致しません。再分割してください。")
        return body
    return body if parent else (index_text or body)


# 例外の文に名前を示す文書の上限（多いときは残りを件数で示す）。
UNPROJECTED_VISION_DOCUMENTS_SHOWN = 10


class UnprojectedVisionSourceError(ValueError):
    """回答本文を分離していない旧い画像説明の chunk。文書の再解析・再分割が要る。

    文は利用者に見せてよい（``safe_for_user``）。``documents`` は利用者が見られる文書の表示名で、
    chunk の本文は持たない。旧い chunk を推測で復元・除外しないため、呼び出し側は検索を止めて
    この文を返す。
    """

    safe_for_user = True

    def __init__(self, documents: Sequence[str] = ()) -> None:
        self.documents = tuple(dict.fromkeys(name for name in documents if name))
        super().__init__(_unprojected_vision_message(self.documents))


def _unprojected_vision_message(documents: Sequence[str]) -> str:
    if not documents:
        return "旧画像説明の回答本文が未分離です。再解析または再分割してください。"
    shown = documents[:UNPROJECTED_VISION_DOCUMENTS_SHOWN]
    rest = len(documents) - len(shown)
    names = "、".join(f"「{name}」" for name in shown) + (f"ほか {rest} 件" if rest else "")
    return (
        "画像説明が旧い形式のまま保存された文書があるため、検索・回答できません。"
        f"次の文書を再解析または再分割してください: {names}"
    )


def is_unprojected_vision_source(
    metadata: Mapping[str, object], refs: Sequence[Mapping[str, object]]
) -> bool:
    """producerが明示した旧VLM由来だけを旧形式とし、native OCRの語句やモデル名では判定しない。"""
    if any(
        isinstance(ref, Mapping) and ref.get("vision_text_projection_status") == "unrecorded"
        for ref in refs
    ):
        return True
    if "answer_text_projection" in metadata:
        return False
    return metadata.get("figure_text_source") == "vision"


def require_projected_vision_source(
    metadata: Mapping[str, object],
    refs: Sequence[Mapping[str, object]],
    *,
    document: str = "",
) -> None:
    """旧形式なら ``document``（文書の表示名）を示す ``UnprojectedVisionSourceError`` で止める。"""
    if is_unprojected_vision_source(metadata, refs):
        raise UnprojectedVisionSourceError((document,))
