"""評価結果から作るテストセットの下書きと確認状態。"""

from typing import Literal, Self

from pr_backend_core.timezones import UtcDatetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evaluation import EVIDENCE_LABEL_MAX_CHARS, STANDARD_ANSWER_MAX_CHARS
from app.schemas.support_guide import PROCESS_IMPORT_TEXT_MAX_CHARS


class TestSetFields(BaseModel):
    """既存テンプレートの六列を編集する。コードによる正解の推測はしない。"""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(default="", max_length=2000)
    standard_answer: str = Field(default="", max_length=STANDARD_ANSWER_MAX_CHARS)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    required_answer_points: list[str] = Field(default_factory=list, max_length=30)
    response_decision: Literal["answer", "clarify", "hold"] = "answer"
    answer_process: str = Field(default="", max_length=PROCESS_IMPORT_TEXT_MAX_CHARS)

    @field_validator("evidence")
    @classmethod
    def bounded_lines(cls, values: list[str]) -> list[str]:
        if any(len(value) > EVIDENCE_LABEL_MAX_CHARS for value in values):
            raise ValueError("1 行は 1000 文字以内で入力してください。")
        return values


class TestSetEdit(TestSetFields):
    item_id: str = Field(min_length=1, max_length=64)
    confirmed: bool = False

    @model_validator(mode="after")
    def confirmation(self) -> Self:
        if self.confirmed:
            if not self.question.strip():
                raise ValueError("収録の確認には質問が必要です。")
            if self.response_decision == "answer" and not self.standard_answer.strip():
                raise ValueError("回答を期待する項目は標準回答を入力してから確認してください。")
        return self


class TestSetItem(TestSetEdit):
    source_kind: Literal["evaluation", "manual", "excel"] = "evaluation"
    source_job_id: str = ""
    source_case_id: str = ""
    source_pass: int = 0
    reference_answer: str = ""
    reference_evidence: list[dict[str, object]] = Field(default_factory=list)
    original: TestSetFields
    source_notice: str | None = None


class TestSetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    name: str
    revision: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    items: list[TestSetItem] = Field(max_length=200)

    @model_validator(mode="after")
    def storage_budget(self) -> Self:
        if len(self.model_dump_json().encode("utf-8")) > 20 * 1024 * 1024:
            raise ValueError("下書きが大きすぎます。項目を分けて保存してください。")
        return self


class TestSetSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[str] = Field(min_length=1, max_length=20)


class TestSetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["evaluation", "manual", "excel"] = "evaluation"
    job_ids: list[str] = Field(default_factory=list, max_length=20)
    item_ids: list[str] = Field(default_factory=list, max_length=200)
    items: list[TestSetFields] = Field(default_factory=list, max_length=200)
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("下書きの名前を入力してください。")
        return value.strip()


class TestSetSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    items: list[TestSetEdit] = Field(max_length=200)
    confirm_item_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        return TestSetCreateRequest.nonempty_name(value)


class TestSetSummary(BaseModel):
    draft_id: str
    name: str
    revision: int
    updated_at: UtcDatetime
    item_count: int
    confirmed_count: int


def draft_summary(draft: TestSetDraft) -> TestSetSummary:
    return TestSetSummary(
        draft_id=draft.draft_id,
        name=draft.name,
        revision=draft.revision,
        updated_at=draft.updated_at,
        item_count=len(draft.items),
        confirmed_count=sum(item.confirmed for item in draft.items),
    )


class TestSetPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    search_answer_profile_id: str = Field(min_length=1, max_length=128)
