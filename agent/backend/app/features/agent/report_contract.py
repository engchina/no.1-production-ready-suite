"""公開した Agent の設定だけで決まる、参照付き回答の形式と予算。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.features.agent.report_fields import ConfirmationSection

# ユーザーの分量指定とは別の、入力と保存状態の技術上限。
MAX_REPORT_BLOCKS = 64
MAX_REPORT_TEXT_CHARS = 65_536
MAX_REPORT_PLAN_CHARS = 262_144


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AnswerBudget(_StrictModel):
    metric: Literal["expanded_markdown_codepoints"] = "expanded_markdown_codepoints"
    soft_target: int | None = Field(default=None, gt=0)
    hard_max: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _valid_budget(self) -> AnswerBudget:
        if self.soft_target is None and self.hard_max is None:
            raise ValueError("目標文字数か厳密な上限の少なくとも一方を指定してください。")
        if (
            self.soft_target is not None
            and self.hard_max is not None
            and self.soft_target > self.hard_max
        ):
            raise ValueError("目標文字数は厳密な上限以下にしてください。")
        return self


class ReportOutputContract(_StrictModel):
    mode: Literal["referenced_report"] = "referenced_report"
    budget: AnswerBudget | None = None


class NarrativeBlock(_StrictModel):
    kind: Literal["narrative"] = "narrative"
    text: str = Field(max_length=MAX_REPORT_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _table_block_only(cls, value: str) -> str:
        if "[[table:" in value:
            raise ValueError("表の参照はtableブロックに指定してください。")
        return value


class TableBlock(_StrictModel):
    kind: Literal["table"] = "table"
    table: str = Field(pattern=r"^T[1-9]\d*$", max_length=64)
    rows: str | None = Field(default=None, max_length=2048)
    cols: str | None = Field(default=None, max_length=2048)
    layout: Literal["horizontal", "vertical"] = "horizontal"

    @model_validator(mode="after")
    def _paired_selectors(self) -> TableBlock:
        if (self.rows is None) != (self.cols is None):
            raise ValueError("rowsとcolsは一緒に指定してください。")
        if self.rows is None and self.layout != "horizontal":
            raise ValueError("verticalには行列の指定が必要です。")
        return self

    def marker(self) -> str:
        if self.rows is None:
            return f"[[table:{self.table}]]"
        suffix = "" if self.layout == "horizontal" else ":layout=vertical"
        return f"[[table:{self.table}:rows={self.rows}:cols={self.cols}{suffix}]]"


ReportBlock = Annotated[
    NarrativeBlock | TableBlock | ConfirmationSection, Field(discriminator="kind")
]


class AnswerPlan(_StrictModel):
    blocks: tuple[ReportBlock, ...] = Field(min_length=1, max_length=MAX_REPORT_BLOCKS)

    @field_validator("blocks", mode="before")
    @classmethod
    def _json_array(cls, value: Any) -> Any:
        # JSON 配列だけを tuple にする。数値や任意 object の変換はしない。
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _bounded(self) -> AnswerPlan:
        if len(self.model_dump_json()) > MAX_REPORT_PLAN_CHARS:
            raise ValueError("回答案が保存できる大きさの上限を超えています。")
        return self


class FinalSelection(_StrictModel):
    preview_id: str = Field(pattern=r"^A[1-9]\d*$", max_length=64)
