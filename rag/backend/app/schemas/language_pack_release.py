"""公開した言語資源と、索引が使用した版の追跡契約。"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal

from pr_backend_core.timezones import UtcDatetime
from pydantic import BaseModel, ConfigDict, Field, computed_field


class LanguagePackReleaseSummary(BaseModel):
    """履歴のメタデータ。内容の検証・実行可否は表さない。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = Field(ge=1)
    hash: str
    ingest_hash: str
    published_at: UtcDatetime
    published_by: str | None = None
    previous_id: str | None = None
    restored_from: str | None = None
    change_note: str = ""


class LanguagePackRelease(LanguagePackReleaseSummary):
    effective: dict[str, Any]


class LanguagePackReleaseState(BaseModel):
    draft_revision: int = Field(ge=1)
    draft_effective: dict[str, Any]
    current: LanguagePackRelease


class LanguagePackDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    overrides: dict[str, dict[str, Any]]


class LanguagePackPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_current_id: str = Field(min_length=1, max_length=64)
    change_note: str = Field(default="", max_length=2000)


class LanguagePackRestoreRequest(LanguagePackPublishRequest):
    """以前の完全な版を新しい公開操作として有効にする。"""


class LanguagePackIndexRelation(StrEnum):
    CURRENT = "current"
    EQUIVALENT = "equivalent"
    DIFFERENT = "different"
    UNRECORDED = "unrecorded"


class LanguagePackDocumentStatus(StrEnum):
    """文書の言語資源の状態（文書インデックスの絞り込みの 6 つ。#4566）。

    有効なレシピごとの状態（job が版を固定して待機・実行中なら構築中、そうでなければ検索中の索引の
    関係）を、対応が要るもの > 構築中 > 問題の無いもの の順（値の並び）で 1 つにまとめる。
    判定は `language_pack_document_status`。旧版の文書はどれにも入れない（#4601）。
    """

    DIFFERENT = "different"
    UNRECORDED = "unrecorded"
    BUILDING = "building"
    EQUIVALENT = "equivalent"
    CURRENT = "current"
    NOT_BUILT = "not_built"


class LanguagePackFieldDifference(BaseModel):
    section: str
    key: str
    before: Any = None
    after: Any = None
    ingest: bool


class LanguagePackReleaseComparison(BaseModel):
    before_id: str
    after_id: str
    differences: list[LanguagePackFieldDifference]
    ingest_changed: bool
    # 依存を特定できない場合は工程を推定せず、確認が必要なことを返す。
    impact_requires_review: bool = False
    affected_stages: list[str] = Field(default_factory=list)


class LanguagePackReleaseList(BaseModel):
    releases: list[LanguagePackReleaseSummary]
    total: int = Field(ge=0)


class LanguagePackRecipeUse(BaseModel):
    recipe_id: str | None = None
    chunk_set_id: str | None = None
    release_id: str | None = None
    version: int | None = None
    ingest_hash: str | None = None
    relation: LanguagePackIndexRelation
    built_at: UtcDatetime | None = None
    job_id: str | None = None
    target_release_id: str | None = None
    # 待機中・実行中の job が固定した版の番号。版の行が無ければ None（版の数から推定しない）。
    target_version: int | None = None


# 新しい版に置き換えた文書（旧版）の状態。絞り込みは旧版を除く（#3709）ため、
# 6 つの状態に入れない（#4601）。
LANGUAGE_PACK_SUPERSEDED: Literal["superseded"] = "superseded"
LanguagePackDocumentUseStatus = LanguagePackDocumentStatus | Literal["superseded"]

_DOCUMENT_STATUS_PRIORITY = tuple(LanguagePackDocumentStatus)


def language_pack_recipe_status(recipe: LanguagePackRecipeUse) -> LanguagePackDocumentStatus:
    """レシピ 1 つの状態（`_language_pack_status_sql` のレシピごとの判定と同じ）。

    最新の待機中・実行中の job が版を固定していれば構築中、検索中の索引が無ければ未構築、
    使用版の id・取込設定の hash・版の行のどれかが無ければ判定できない、それ以外は索引の関係。
    """
    if recipe.target_release_id:
        return LanguagePackDocumentStatus.BUILDING
    if recipe.chunk_set_id is None:
        return LanguagePackDocumentStatus.NOT_BUILT
    recorded = bool(recipe.release_id) and bool(recipe.ingest_hash) and recipe.version is not None
    if not recorded or recipe.relation == LanguagePackIndexRelation.UNRECORDED:
        return LanguagePackDocumentStatus.UNRECORDED
    return LanguagePackDocumentStatus(recipe.relation.value)


def language_pack_document_status(
    recipes: Iterable[LanguagePackRecipeUse], *, superseded: bool
) -> LanguagePackDocumentUseStatus:
    """文書の状態。旧版は対象外、それ以外はレシピの状態の優先順で最初のもの（絞り込みと同じ）。"""
    if superseded:
        return LANGUAGE_PACK_SUPERSEDED
    present = {language_pack_recipe_status(recipe) for recipe in recipes}
    return next(
        (status for status in _DOCUMENT_STATUS_PRIORITY if status in present),
        LanguagePackDocumentStatus.NOT_BUILT,
    )


class LanguagePackDocumentUse(BaseModel):
    current_release_id: str
    current_version: int
    recipes: list[LanguagePackRecipeUse] = Field(default_factory=list)
    # 新しい版に置き換えた文書（旧版）か。言語資源の判定・絞り込みの対象外にする（#4601）。
    superseded: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> LanguagePackDocumentUseStatus:
        """一覧の列・文書の詳細のバッジの状態（絞り込みと同じ規則）。"""
        return language_pack_document_status(self.recipes, superseded=self.superseded)
