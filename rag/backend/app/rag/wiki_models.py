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
    section_id: str
    section_path: list[str]
    title: str
    text: str
    source_hash: str


class WikiDependency(WikiModel):
    unit_id: str
    relation: Literal[
        "prerequisite", "continuation", "warning", "exception", "reference", "expected_result"
    ]


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


class WikiFilePlan(WikiModel):
    document_ids: list[str]
    search_expressions: list[str] = Field(max_length=8)
    unresolved: list[str] = Field(max_length=8)


class WikiReadPlan(WikiModel):
    unit_ids: list[str]
    section_ids: list[str]
    unresolved: list[str] = Field(max_length=8)


def scoped_plan[T: WikiModel](
    base: type[T], fields: dict[str, tuple[Sequence[str], int]]
) -> type[T]:
    definitions: dict[str, Any] = {}
    for name, (values, limit) in fields.items():
        allowed: Any = Literal.__getitem__(tuple(sorted(set(values)))) if values else str
        definitions[name] = (list[allowed], Field(max_length=limit if values else 0))
    return create_model(base.__name__ + "Scoped", __base__=base, **definitions)


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


def dependency_units(
    seeds: Sequence[str], pages: dict[str, WikiPage], allowed: set[str]
) -> tuple[list[str], list[str]]:
    """型で検証済みの依存だけを巡回防止付きで辿り、未取得を別に返す。"""
    queue, visited, blocked = list(dict.fromkeys(seeds)), set(), set()
    ordered: list[str] = []
    while queue:
        uid = queue.pop(0)
        if uid in visited:
            continue
        visited.add(uid)
        if uid not in allowed or uid not in pages:
            blocked.add(uid)
            continue
        ordered.append(uid)
        queue.extend(item.unit_id for item in pages[uid].navigation.dependencies)
    return ordered, sorted(blocked)


def enabled_scopes(memberships: Sequence[str], preferences: dict[str, bool]) -> set[str]:
    return {kb for kb in memberships if preferences.get(kb) is True}


def wiki_available(
    *,
    profile_enabled: bool,
    requested_scope: Sequence[str],
    memberships: Sequence[str],
    preferences: dict[str, bool],
    source_current: bool,
) -> bool:
    return bool(
        profile_enabled
        and source_current
        and set(requested_scope) & enabled_scopes(memberships, preferences)
    )
