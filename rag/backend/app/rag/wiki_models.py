"""原文と Wiki の案内を別の型で保持する。原文の意味を語の一覧で判定しない。"""

from __future__ import annotations

import hashlib
import json
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


class WikiContext(WikiModel):
    """生成された検索用の語境。引用可能な原文の型にはしない。"""

    unit_id: str
    context: str = Field(max_length=420)
    possible_questions: list[Annotated[str, Field(max_length=120)]] = Field(max_length=2)
    dependencies: list[WikiDependency] = Field(max_length=16)
    unresolved: list[Annotated[str, Field(max_length=180)]] = Field(max_length=4)


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


class WikiLinkChoice(WikiModel):
    unit_id: str
    relation: WikiRelation
    reason: str = Field(max_length=160)


class WikiLinkJudgment(WikiModel):
    unit_id: str
    links: list[WikiLinkChoice]


class WikiLinkJudgments(WikiModel):
    items: list[WikiLinkJudgment]


def link_schema(
    sources: Sequence[str], candidates: Sequence[str], *, max_links: int
) -> type[WikiLinkJudgments]:
    """起点ごとの関連の出力 schema（#5492）。終点は候補の単位だけ・起点は全件を一意に返す。"""
    if not sources or not candidates or set(sources) & set(candidates):
        raise ValueError("Wiki の関連の起点と候補が不正です。")
    allowed: Any = Literal.__getitem__(tuple(sorted(set(candidates))))
    requested: Any = Literal.__getitem__(tuple(sorted(set(sources))))
    choice = create_model("ScopedWikiLinkChoice", __base__=WikiLinkChoice, unit_id=(allowed, ...))
    item = create_model(
        "ScopedWikiLinkJudgment",
        __base__=WikiLinkJudgment,
        unit_id=(requested, ...),
        links=(list[choice], Field(max_length=max_links)),
    )
    expected = set(sources)

    def complete(value: Any) -> Any:
        returned = [entry.unit_id for entry in value.items]
        if len(returned) != len(set(returned)) or set(returned) != expected:
            raise ValueError("Wiki の関連の出力に起点の重複または欠落があります。")
        for entry in value.items:
            targets = [link.unit_id for link in entry.links]
            if len(targets) != len(set(targets)):
                raise ValueError("Wiki の関連の出力で同じ候補が重複しています。")
        return value

    validators: dict[str, Any] = {"complete": model_validator(mode="after")(complete)}
    return create_model(
        "ScopedWikiLinkJudgments",
        __base__=WikiLinkJudgments,
        __validators__=validators,
        items=(list[item], Field(min_length=len(sources), max_length=len(sources))),
    )


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


class WikiFilePlan(WikiModel):
    """読むファイルの選択。ID は要求内の短いハンドル。"""

    document_ids: list[str]
    search_expressions: list[Annotated[str, Field(max_length=200)]] = Field(max_length=8)


class WikiReadPlan(WikiModel):
    """読む原文単位と章節の選択。ID は要求内の短いハンドル。"""

    unit_ids: list[str]
    section_ids: list[str]


def scoped_plan[T: WikiModel](
    base: type[T], fields: dict[str, tuple[Sequence[str], int]]
) -> type[T]:
    """選べる ID を候補のハンドルだけ・件数を上限までに絞った schema。

    候補が無い欄は空だけを許す。
    """
    definitions: dict[str, Any] = {}
    for name, (values, limit) in fields.items():
        allowed: Any = Literal.__getitem__(tuple(sorted(set(values)))) if values else str
        definitions[name] = (list[allowed], Field(max_length=limit if values else 0))
    return create_model(base.__name__ + "Scoped", __base__=base, **definitions)
