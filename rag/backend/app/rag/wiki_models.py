"""原文と Wiki の案内を別の型で保持する。原文の意味を語の一覧で判定しない。"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

WIKI_BUILD_KEY = "wiki_build_enabled"
WIKI_SCHEMA_VERSION = "1"


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class WikiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiUnit(WikiModel):
    """保存された一つの原文単位。ID と本文はコードが原文から作る。"""

    unit_id: str
    document_id: str
    chunk_set_id: str
    source_chunk_ids: list[str] = Field(min_length=1)
    source_chunk_hashes: dict[str, str] = Field(default_factory=dict)
    section_id: str
    section_path: list[str]
    title: str
    text: str
    source_hash: str

    @model_validator(mode="after")
    def complete_original_bindings(self) -> Self:
        ids = set(self.source_chunk_ids)
        if len(ids) != len(self.source_chunk_ids):
            raise ValueError("Wiki の原文 ID が重複しています。")
        if self.source_chunk_hashes:
            if set(self.source_chunk_hashes) != ids:
                raise ValueError("Wiki の構成原文すべてのハッシュが必要です。")
        elif len(ids) > 1:
            raise ValueError("複数の原文には個別のハッシュが必要です。")
        return self


WikiRelation = Literal[
    "prerequisite", "continuation", "warning", "exception", "reference", "expected_result"
]
# 辿るときの優先の順（例外・前提を先、参照を後）。
WIKI_RELATION_PRIORITY: tuple[str, ...] = (
    "exception",
    "prerequisite",
    "warning",
    "continuation",
    "expected_result",
    "reference",
)


class WikiDependency(WikiModel):
    unit_id: str
    relation: WikiRelation


class WikiReference(WikiModel):
    """原文が名前を挙げて参照する別の文書（#5784）。参照先は読むときに今の KB の中で決める。"""

    document: str = Field(
        max_length=120,
        description="参照先の別の文書の名前。原文に書かれた表記のまま写す。",
    )
    section: str = Field(
        max_length=120,
        description="参照先の章・節・条・別紙の原文の表記。文書全体を指すときは空の文字列。",
    )
    relation: WikiRelation


WIKI_REFERENCE_LIMIT = 4


class WikiContext(WikiModel):
    """生成された検索用の語境。引用可能な原文の型にはしない。"""

    unit_id: str
    context: str = Field(max_length=420)
    possible_questions: list[Annotated[str, Field(max_length=120)]] = Field(max_length=2)
    dependencies: list[WikiDependency] = Field(max_length=16)
    unresolved: list[Annotated[str, Field(max_length=180)]] = Field(max_length=4)
    # 既存の保存済みの案内（#5784 より前）は持たないので既定は空。
    references: list[WikiReference] = Field(
        default_factory=list,
        max_length=WIKI_REFERENCE_LIMIT,
        description=(
            "この単位の原文が名前を挙げて参照する別の文書（と章節）。同じ文書の中の参照は "
            "dependencies に入れる。"
        ),
    )


class WikiContexts(WikiModel):
    items: list[WikiContext]


def context_schema(targets: Sequence[str], all_units: Sequence[str]) -> type[WikiContexts]:
    """実在する候補だけの出力 schema。全対象の一意な返却も保存前に検証する。"""
    if not targets or not set(targets) <= set(all_units):
        raise ValueError("Wiki の対象単位が原文にありません。")
    allowed: Any = Literal.__getitem__(tuple(sorted(set(all_units))))
    requested: Any = Literal.__getitem__(tuple(sorted(set(targets))))
    dependency = create_model(
        "ScopedWikiDependency", __base__=WikiDependency, unit_id=(allowed, ...)
    )
    item = create_model(
        "ScopedWikiContext",
        __base__=WikiContext,
        unit_id=(requested, ...),
        dependencies=(list[dependency], Field(max_length=16)),
        # 参照の欄が無い応答も案内として使う（参照が無いのと同じ）。
        references=(
            list[WikiReference],
            Field(
                default_factory=list,
                max_length=WIKI_REFERENCE_LIMIT,
                description=WikiContext.model_fields["references"].description,
            ),
        ),
    )
    expected = set(targets)

    def complete(value: Any) -> Any:
        returned = [entry.unit_id for entry in value.items]
        if len(returned) != len(set(returned)) or set(returned) != expected:
            raise ValueError("Wiki の出力に対象単位の重複または欠落があります。")
        return value

    validators: dict[str, Any] = {"complete": model_validator(mode="after")(complete)}
    return create_model(
        "ScopedWikiContexts",
        __base__=WikiContexts,
        __validators__=validators,
        items=(list[item], Field(min_length=len(targets), max_length=len(targets))),
    )


def _in_original(value: str, text: str) -> bool:
    """表記が原文にあるか（NFKC・空白の違いは無視する。形式の照合だけで意味は判定しない）。"""
    wanted = "".join(unicodedata.normalize("NFKC", value).split())
    return len(wanted) >= 2 and wanted in "".join(unicodedata.normalize("NFKC", text).split())


class WikiPage(WikiModel):
    """原文の参照と検索用の派生文を束ねる保存形式。"""

    schema_version: str = WIKI_SCHEMA_VERSION
    unit: WikiUnit
    navigation: WikiContext
    input_fingerprint: str

    @model_validator(mode="after")
    def matching_unit(self) -> Self:
        if self.unit.unit_id != self.navigation.unit_id:
            raise ValueError("Wiki の案内と原文の単位が一致しません。")
        # 参照は原文にある表記だけを残す（モデルが作った名前で結ばない。#5784）。
        kept = [
            reference
            for reference in self.navigation.references
            if _in_original(reference.document, self.unit.text)
            and (not reference.section.strip() or _in_original(reference.section, self.unit.text))
        ]
        if len(kept) != len(self.navigation.references):
            self.navigation = self.navigation.model_copy(update={"references": kept})
        return self

    def search_text(self, *, original_chars: int) -> str:
        return "\n".join(
            [
                self.unit.title,
                " > ".join(self.unit.section_path),
                self.navigation.context,
                *self.navigation.possible_questions,
                self.unit.text[:original_chars],
            ]
        )
