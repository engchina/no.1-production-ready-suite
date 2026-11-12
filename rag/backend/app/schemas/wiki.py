"""KB ごとの Wiki 構築の指定と、原文保存版に結び付く構築状態。"""

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class KnowledgeBaseWikiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class KnowledgeBaseWikiData(BaseModel):
    enabled: bool
    counts: dict[str, int] = Field(default_factory=dict)
    cache_policy: str = "retain_on_disable"
