"""業務ビュー単位の知識(ドメインキーワード等)の API schema。"""

from pydantic import BaseModel, Field


class DomainKeywordsData(BaseModel):
    """業務ビューのドメインキーワード。"""

    business_view_id: str
    keywords: list[str] = Field(default_factory=list)


class DomainKeywordsUpdate(BaseModel):
    """ドメインキーワードの全置換。空白・重複・長すぎる語は保存時に除く。"""

    keywords: list[str] = Field(default_factory=list, max_length=2000)


class DomainKeywordCandidateData(BaseModel):
    keyword: str
    score: float
    frequency: int
    chunk_count: int
    document_count: int


class DomainKeywordSuggestionData(BaseModel):
    """参照 KB のチャンクから抽出したキーワード候補(登録済みは除く)。"""

    candidates: list[DomainKeywordCandidateData] = Field(default_factory=list)
    processed_chunk_count: int = 0
