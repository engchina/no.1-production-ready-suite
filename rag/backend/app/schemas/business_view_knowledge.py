"""業務ビュー単位の知識(ドメインキーワード等)の API schema。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import JsonValue


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


class RuntimeKnowledgeData(BaseModel):
    """業務ビューの用語・ルール(rag_poc runtime knowledge の標準形式)。"""

    business_view_id: str
    terms: list[dict[str, JsonValue]] = Field(default_factory=list)
    rules: list[dict[str, JsonValue]] = Field(default_factory=list)


class RuntimeKnowledgeEditRequest(BaseModel):
    """1 行の追加・更新・削除。selected 未指定は追加。"""

    kind: Literal["terms", "rules"]
    selected: str | None = Field(default=None, max_length=160)
    name: str = Field(default="", max_length=160, description="用語、またはルール ID")
    title: str = Field(default="", max_length=160, description="ルール名(rules のみ)")
    labels: str = Field(
        default="", max_length=8000, description="別名 / 照合キーワード(改行・読点区切り)"
    )
    content: str = Field(default="", max_length=8000, description="説明 / ルール内容")
    source: str = Field(default="", max_length=800)
    enabled: bool = True
    delete: bool = False


class RuntimeKnowledgePreviewRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class RuntimeKnowledgePreviewData(BaseModel):
    """照合テスト: 一致した用語・ルールと拡張後の検索文。"""

    expanded_question: str
    matched_terms: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)


class QuerySuggestion(BaseModel):
    """よく聞かれる質問の候補(rag_poc の QueryHistorySuggestion)。"""

    question: str
    count: int


class QuerySuggestionsData(BaseModel):
    business_view_id: str
    enabled: bool
    suggestions: list[QuerySuggestion] = Field(default_factory=list)
