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


class ApprovedFaqRecordData(BaseModel):
    """承認済み FAQ 1 件(rag_poc ApprovedFaqRecord の表示用射影)。"""

    id: str
    question: str
    answer: str
    alternate_questions: list[str] = Field(default_factory=list)
    status: str = ""


class ApprovedFaqListData(BaseModel):
    business_view_id: str
    records: list[ApprovedFaqRecordData] = Field(default_factory=list)


class ApprovedFaqAddRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=20000)


class ApprovedFaqDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class ApprovedFaqMutationData(ApprovedFaqListData):
    inserted_count: int = 0
    deleted_count: int = 0


class ApprovedFaqImportRowData(BaseModel):
    question: str
    answer: str
    row: int = 0


class ApprovedFaqImportPreviewData(BaseModel):
    """Excel 取込のプレビュー(先頭 10 件)と有効行数。"""

    total: int
    rows: list[ApprovedFaqImportRowData] = Field(default_factory=list)


class ApprovedFaqSuggestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class ApprovedFaqSuggestionData(BaseModel):
    """類似問候補。direct=true は FAQ 回答をそのまま使える一致度(0.92 以上)。"""

    id: str
    question: str
    matched_question: str
    answer: str
    score: float
    direct: bool


class ApprovedFaqSuggestionsData(BaseModel):
    suggestions: list[ApprovedFaqSuggestionData] = Field(default_factory=list)
