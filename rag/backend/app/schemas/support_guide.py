"""業務ガイド（SupportGuide。#1237、handoff §6.2）の内容と API の型。

業務ガイドは、答えそのものではなく「どの条件で・どの資料を・どの順で確かめれば答えに届くか」を示す
宣言的な内容。検索・回答プロファイルの知識として持ち、下書きを検証して公開した版だけを回答に使う。
分岐は宣言的な条件（equals / in / unknown）に限り、式や script は評価しない。実在の顧客の内容は
配備先で登録し、リポジトリには架空の例だけを置く。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORT_GUIDE_SCHEMA_VERSION: Literal[1] = 1
# 使ってよい道具（handoff §10。手順の allowed_tools はこの中から選ぶ）。
SUPPORT_GUIDE_TOOLS: tuple[str, ...] = (
    "rag_search",
    "rag_read_source",
    "rag_retrieve_evidence",
    "nl2sql_query",
)
_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
_SHORT = 200
_TEXT = 2000

SupportGuideStatus = Literal["active", "archived"]
ConditionType = Literal["enum", "text", "boolean"]
ConditionSource = Literal["user", "document", "tool"]
UnknownHandling = Literal["ask", "branch", "handoff"]
ImpactScope = Literal["individual", "group", "all"]
BranchOperator = Literal["equals", "in", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class SupportGuideGoal(_Strict):
    expected_result: str = Field(min_length=1, max_length=_TEXT, description="期待する結果。")
    intent_examples: list[str] = Field(
        default_factory=list, max_length=20, description="この業務ガイドに当たる質問の例。"
    )
    match_terms: list[str] = Field(
        default_factory=list, max_length=30, description="照合の手がかりの語。"
    )

    _clean = field_validator("intent_examples", "match_terms")(_clean_list)


class SupportGuideApplicability(_Strict):
    """適用範囲。空の項目は「このプロファイルの中」を意味し、全範囲を意味しない。"""

    business_domains: list[str] = Field(default_factory=list, max_length=20)
    object_types: list[str] = Field(default_factory=list, max_length=20)
    versions: list[str] = Field(
        default_factory=list, max_length=20, description="資料・システムの版の条件。"
    )
    effective_from: date | None = None
    effective_to: date | None = None

    _clean = field_validator("business_domains", "object_types", "versions")(_clean_list)

    @model_validator(mode="after")
    def _valid_period(self) -> SupportGuideApplicability:
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("有効期間の終わりは始まりより後の日付にしてください。")
        return self


class SupportGuideCondition(_Strict):
    id: str = Field(pattern=_ID_PATTERN)
    label: str = Field(min_length=1, max_length=_SHORT)
    type: ConditionType = "enum"
    allowed_values: list[str] = Field(default_factory=list, max_length=20)
    required: bool = True
    source: ConditionSource = Field(default="user", description="値の出所（利用者・資料・道具）。")
    unknown_handling: UnknownHandling = Field(
        default="ask", description="分からないとき: ask=確かめる / branch=分岐 / handoff=人へ。"
    )
    question: str = Field(default="", max_length=_SHORT, description="利用者に確かめる問い。")
    value_aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "選択肢の言い換え（選択肢 → 質問に出る語）。例: 個別 → 検証用アカウント。質問にこの語が"
            "出れば、その選択肢が分かっているとみなし、聞き直さない。"
        ),
    )

    _clean = field_validator("allowed_values")(_clean_list)

    @field_validator("value_aliases")
    @classmethod
    def _clean_aliases(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        cleaned = {
            key.strip(): _clean_list(words)
            for key, words in value.items()
            if key.strip() and _clean_list(words)
        }
        if len(cleaned) > 20 or any(len(words) > 20 for words in cleaned.values()):
            raise ValueError("言い換えは選択肢ごとに 20 語まで、20 個の選択肢までです。")
        return cleaned

    @model_validator(mode="after")
    def _valid_values(self) -> SupportGuideCondition:
        if self.type == "enum" and len(self.allowed_values) < 2:
            raise ValueError(f"条件「{self.label}」は選択肢を 2 つ以上入れてください。")
        if self.type != "enum" and self.allowed_values:
            raise ValueError(
                f"条件「{self.label}」の選択肢は、型が選択（enum）のときだけ使えます。"
            )
        if self.unknown_handling == "ask" and self.source == "user" and not self.question:
            raise ValueError(f"条件「{self.label}」に確かめる問いを入れてください。")
        unknown = [key for key in self.value_aliases if key not in self.allowed_values]
        if unknown:
            raise ValueError(
                f"条件「{self.label}」の言い換え「{'、'.join(unknown)}」は選択肢にありません。"
            )
        return self


class SupportGuideBranchWhen(_Strict):
    condition_id: str = Field(pattern=_ID_PATTERN)
    operator: BranchOperator = "equals"
    values: list[str] = Field(default_factory=list, max_length=20)

    _clean = field_validator("values")(_clean_list)

    @model_validator(mode="after")
    def _valid_operator(self) -> SupportGuideBranchWhen:
        if self.operator == "unknown" and self.values:
            raise ValueError("unknown の分岐には値を入れません。")
        if self.operator == "equals" and len(self.values) != 1:
            raise ValueError("equals の分岐の値は 1 つです。")
        if self.operator == "in" and not self.values:
            raise ValueError("in の分岐には値を 1 つ以上入れてください。")
        return self


class SupportGuideBranch(_Strict):
    id: str = Field(pattern=_ID_PATTERN)
    when: SupportGuideBranchWhen
    goto_step: str = Field(pattern=_ID_PATTERN)
    note: str = Field(default="", max_length=_SHORT)


class SupportGuideStep(_Strict):
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=_SHORT)
    purpose: str = Field(default="", max_length=_TEXT)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    retrieval_hints: list[str] = Field(default_factory=list, max_length=10)
    evidence_requirements: list[str] = Field(
        default_factory=list, max_length=10, description="この手順で示すべき根拠。"
    )
    allowed_tools: list[str] = Field(default_factory=list, max_length=len(SUPPORT_GUIDE_TOOLS))
    done_when: str = Field(default="", max_length=_TEXT, description="この手順の終わりの条件。")

    _clean = field_validator(
        "depends_on", "retrieval_hints", "evidence_requirements", "allowed_tools"
    )(_clean_list)


class SupportGuideReference(_Strict):
    document_id: str = Field(min_length=1, max_length=64)
    title: str = Field(
        default="", max_length=_SHORT, description="表示名（参照の正本は document_id）。"
    )
    section_path: list[str] = Field(default_factory=list, max_length=10)
    version: str = Field(default="", max_length=64)


class SupportGuideCompletion(_Strict):
    id: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=_TEXT)
    check_method: str = Field(default="", max_length=_TEXT)


class SupportGuideImpact(_Strict):
    scope: ImpactScope = "individual"
    approval_required: bool = False
    approval_note: str = Field(default="", max_length=_TEXT)


class SupportGuideHandoff(_Strict):
    conditions: list[str] = Field(
        default_factory=list, max_length=20, description="人へ引き継ぐ条件。"
    )
    contact: str = Field(default="", max_length=_SHORT, description="引き継ぎ先（窓口の名前）。")

    _clean = field_validator("conditions")(_clean_list)


class SupportGuideContent(_Strict):
    """業務ガイドの内容（下書き・公開の版で同じ形）。"""

    schema_version: Literal[1] = SUPPORT_GUIDE_SCHEMA_VERSION
    title: str = Field(min_length=1, max_length=_SHORT)
    description: str = Field(default="", max_length=_TEXT)
    goal: SupportGuideGoal
    applicability: SupportGuideApplicability = Field(default_factory=SupportGuideApplicability)
    conditions: list[SupportGuideCondition] = Field(default_factory=list, max_length=30)
    steps: list[SupportGuideStep] = Field(default_factory=list, max_length=50)
    branches: list[SupportGuideBranch] = Field(default_factory=list, max_length=50)
    references: list[SupportGuideReference] = Field(default_factory=list, max_length=30)
    completion: list[SupportGuideCompletion] = Field(default_factory=list, max_length=20)
    impact: SupportGuideImpact = Field(default_factory=SupportGuideImpact)
    handoff: SupportGuideHandoff = Field(default_factory=SupportGuideHandoff)


class SupportGuideIssue(BaseModel):
    """検証で見つけた問題。severity=error は公開できない。"""

    severity: Literal["error", "warning"]
    code: str
    path: str = Field(description="問題のある項目（例: steps[1].depends_on）。")
    message: str


class SupportGuideRevisionSummary(BaseModel):
    revision: int
    title: str
    content_sha256: str
    published_at: datetime
    published_by: str | None = None
    rollback_from: int | None = None


class SupportGuideRevision(SupportGuideRevisionSummary):
    content: SupportGuideContent


class SupportGuideSummary(BaseModel):
    guide_id: str
    search_answer_profile_id: str
    status: SupportGuideStatus
    title: str
    draft_revision: int
    published_revision: int | None = None
    # 下書きが公開の版と違うか（公開していなければ True）。
    has_unpublished_changes: bool
    updated_at: datetime
    updated_by: str | None = None


class SupportGuideDetail(SupportGuideSummary):
    draft: SupportGuideContent
    published: SupportGuideContent | None = None
    revisions: list[SupportGuideRevisionSummary] = Field(default_factory=list)
    issues: list[SupportGuideIssue] = Field(default_factory=list)


class SupportGuideCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: SupportGuideContent


class SupportGuideDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: SupportGuideContent
    base_revision: int = Field(ge=1, description="読み込んだときの draft_revision。")


class SupportGuidePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: int = Field(ge=1, description="公開する下書きの draft_revision。")


class SupportGuideRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1, description="戻す公開の版。新しい版として公開する。")


class SupportGuideValidationData(BaseModel):
    valid: bool
    issues: list[SupportGuideIssue]


class SupportGuideImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guides: list[dict[str, object]] = Field(min_length=1, max_length=100)


class SupportGuideImportItem(BaseModel):
    index: int
    title: str | None = None
    valid: bool
    issues: list[SupportGuideIssue]


class SupportGuideImportPreviewData(BaseModel):
    items: list[SupportGuideImportItem]
    importable_count: int


class SupportGuideImportData(BaseModel):
    created: list[SupportGuideSummary]


class SupportGuideExportData(BaseModel):
    schema_version: Literal[1] = SUPPORT_GUIDE_SCHEMA_VERSION
    guides: list[SupportGuideContent]
