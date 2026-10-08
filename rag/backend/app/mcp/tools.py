"""RAG が Agent に公開する MCP ツール（#232）。

入口は `POST /api/mcp`（`app.api.routes.mcp`）。利用者はサービストークンの `sub`（Agent の Run の
利用者）で、権限・検索・回答プロファイル / ナレッ
ジベースの対象範囲は画面と同じ判定を使う。各ツールは

既存の route と同じ関数（rate limit・timeout・範囲の判定を含む）を呼ぶだけにする。

- `rag_list_search_answer_profiles`（検索・回答プロファイル一覧の route と同じ権限）:
  `search_answer_profiles.list_search_answer_profiles`（ACTIVE のみ）
- `rag_search`（`menu.search`）: `search._run_search_with_timeout`。根拠は `evidence`（場所・版・
  回答に使ったか・切り詰めの有無。#1219）で返す。
- `rag_read_source`（`menu.search`）: 根拠の本文の続きと親の本文を読む（検索と同じ見え方の
  条件。#1219）

RAG のチャットは画面の機能で、MCP では提供しない（#787）。MCP で提供するのは検索と根拠の
読み取りだけにする。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, get_args

from fastapi import HTTPException, Request
from pr_backend_core.api import OffsetParams
from pr_backend_core.api.validation import validation_tool_errors
from pr_backend_core.mcp import (
    TOOL_ARGUMENTS_INVALID_CODE,
    McpServer,
    McpTool,
    McpToolError,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.answer_validation import (
    EvidenceRef,
    GuideCheckRef,
    GuideProfileNotFoundError,
    validate_answer,
)
from app.rag.rate_limit import enforce_rate_limit
from app.rag.support_guide_runtime import GuideMatch, clarification_questions, rank_guides
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.schemas.search_answer_profile import SearchAnswerProfileStatus
from app.security.permissions import MENU_SEARCH, ROUTE_PERMISSIONS

MCP_SERVER_NAME = "production-ready-rag"
# ツールの出力の版（出力の形を変えたら上げる。handoff §10。#1276）。
MCP_OUTPUT_SCHEMA_VERSION = 2
# 根拠の抜粋の長さ（続きは rag_read_source で読む）。
EVIDENCE_EXCERPT_MAX_CHARS = 1000
EVIDENCE_LIMIT_DEFAULT = 12
EVIDENCE_LIMIT_MAX = 50
# rag_read_source が 1 回で返す本文・親の本文の上限。
READ_SOURCE_MAX_CHARS_DEFAULT = 8000
READ_SOURCE_MAX_CHARS_LIMIT = 20000
SOURCE_NOT_FOUND_CODE = "source_not_found"
SOURCE_STALE_CODE = "source_stale"

SEARCH_ANSWER_PROFILE_READ_PERMISSIONS = ROUTE_PERMISSIONS[("GET", "/search-answer-profiles")]
SEARCH_PERMISSIONS = frozenset({MENU_SEARCH})

INSTRUCTIONS = (
    "Production Ready RAG の検索・回答のツールです。"
    "まず rag_list_search_answer_profiles で使"
    "える検索・回答プロファイルを確認し、その id を rag_search に"
    "渡してください。回答の根拠は evidence にあり、used_in_answer が回答に使った根拠です。"
    "根拠の excerpt が切り詰められている（truncated）ときや、前後の文脈が要るときは、"
    "rag_read_source に document_id と chunk_id を渡して本文と親の本文を読んでください。"
)


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


# 前後の空白を除き、空文字は未指定として扱う文字列。
OptionalText = Annotated[str | None, AfterValidator(_strip_or_none)]


# ---- 入力 ----


class ListSearchAnswerProfilesInput(BaseModel):
    """検索・回答プロファイル一覧の条件。"""

    query: OptionalText = Field(
        default=None, max_length=200, description="名前・説明の部分一致（省略時は全件）。"
    )
    limit: int = Field(default=50, ge=1, le=200, description="返す最大件数。")


class SearchInput(BaseModel):
    """検索・回答の条件。旧 scope を含む未定義項目は拒否する。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=8000, description="質問文。")
    search_answer_profile_id: OptionalText = Field(
        default=None,
        max_length=128,
        description="検索・回答プロファイルの id。参照するナレッジベースと検索・回答設定を使う。",
    )
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description="検索するナレッジベースの id（省略時は検索・回答プロファイルの参照先）。",
    )
    top_k: int | None = Field(default=None, ge=1, le=100, description="検索する件数。")
    filters: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "検索フィルター（例: category_name）。新しい版に置き換えた文書（旧版）は既定で"
            "検索しない。旧版・変更点を尋ねるときは include_superseded に true を渡す。"
        ),
    )
    evidence_limit: int = Field(
        default=EVIDENCE_LIMIT_DEFAULT,
        ge=1,
        le=EVIDENCE_LIMIT_MAX,
        description="返す根拠の最大件数（回答に使った根拠を先に返す）。",
    )
    conditions: dict[str, str] = Field(
        default_factory=dict,
        max_length=30,
        description=(
            "業務ガイドの条件の値（条件の id → 値）。outcome=needs_clarification の"
            " clarifications に利用者が答えた値を入れて呼び直すと、分かっている条件は"
            "聞き直さない。"
        ),
    )


class LookupGuidesInput(BaseModel):
    """質問に当たる業務ガイドを引く条件。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=8000, description="質問文（利用者の目的）。")
    search_answer_profile_id: str = Field(
        ..., min_length=1, max_length=128, description="検索・回答プロファイルの id。"
    )
    conditions: dict[str, str] = Field(
        default_factory=dict, max_length=30, description="分かっている条件の値（条件の id → 値）。"
    )
    limit: int = Field(default=3, ge=1, le=10, description="返す業務ガイドの最大件数。")


class EvidenceRefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128)
    chunk_id: str = Field(..., min_length=1, max_length=512)


class ValidateRequestInput(BaseModel):
    """AnswerEnvelope の要求 1 件（rag_search の requests と同じ形）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, description="要求の ID（Q1 など）。")
    text: str = Field(default="", max_length=2000, description="要求の原文。")
    status: Literal["addressed", "partial", "missing", "unknown"] = Field(
        description=(
            "充足（addressed=答えた / partial=一部 / missing=答えていない / unknown=未確認）。"
        )
    )


class ValidateGuideInput(BaseModel):
    """回答が沿った業務ガイド（rag_search の guide・rag_lookup_guides の guides の 1 件）。"""

    model_config = ConfigDict(extra="forbid")

    search_answer_profile_id: str = Field(
        ..., min_length=1, max_length=128, description="業務ガイドの検索・回答プロファイルの id。"
    )
    guide_id: str = Field(..., min_length=1, max_length=64, description="業務ガイドの id。")
    revision: int = Field(..., ge=1, description="回答に使った公開の版。")
    conditions: dict[str, str] = Field(
        default_factory=dict,
        max_length=30,
        description=(
            "分かっている条件の値（条件の id → 値）。当たらない分岐の手順を見分けるのに使う。"
        ),
    )


class ValidateAnswerInput(BaseModel):
    """回答の最終の検証の条件（#1246）。

    requests・guide は渡したときだけ決定的に確かめる（#1276）。
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=8000, description="利用者の質問。")
    answer: str = Field(..., min_length=1, max_length=20000, description="検証する回答の本文。")
    evidence: list[EvidenceRefInput] = Field(
        ...,
        min_length=1,
        max_length=30,
        description=(
            "回答の根拠（rag_search / rag_retrieve_evidence が返した document_id と chunk_id）。"
            "本文はサーバーが今の権限と版で読み直す。"
        ),
    )
    requests: list[ValidateRequestInput] | None = Field(
        default=None,
        max_length=30,
        description=(
            "回答の要求ごとの充足（AnswerEnvelope の requests）。渡すと partial・missing の要求を"
            "確かめる（回答に不足として示していなければ error）。"
        ),
    )
    gaps: list[Annotated[str, Field(max_length=2000)]] = Field(
        default_factory=list,
        max_length=30,
        description="回答に示した資料から確かめられない点（AnswerEnvelope の gaps）。",
    )
    guide: ValidateGuideInput | None = Field(
        default=None,
        description=(
            "回答が沿った業務ガイド。渡すと公開の版を読み直し、手順の順序・依存・分岐と影響範囲・"
            "承認の記載を確かめる。"
        ),
    )


class ReadSourceInput(BaseModel):
    """根拠の本文を読む条件（rag_search の evidence の document_id と chunk_id）。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128, description="文書の id。")
    chunk_id: str = Field(..., min_length=1, max_length=512, description="根拠の chunk の id。")
    offset: int = Field(default=0, ge=0, description="本文の読み始めの位置（文字数）。")
    max_chars: int = Field(
        default=READ_SOURCE_MAX_CHARS_DEFAULT,
        ge=1,
        le=READ_SOURCE_MAX_CHARS_LIMIT,
        description="返す本文の最大文字数。",
    )


# ---- 出力 ----


class SearchAnswerProfileItem(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    knowledge_base_count: int


class VersionedOutput(BaseModel):
    """ツールの出力の共通部分（出力の形の版）。"""

    schema_version: int = Field(
        default=MCP_OUTPUT_SCHEMA_VERSION, description="出力の形の版（変わったら上がる）。"
    )


class ListSearchAnswerProfilesOutput(VersionedOutput):
    search_answer_profiles: list[SearchAnswerProfileItem]


class EvidenceLocator(BaseModel):
    """根拠の場所（種類によって空の項目がある）。"""

    section_path: list[str] = Field(default_factory=list, description="節の見出しの列。")
    page_start: int | None = Field(default=None, description="開始の頁（PDF の物理頁）。")
    page_end: int | None = Field(default=None, description="終了の頁（PDF の物理頁）。")
    sheet_name: str | None = Field(default=None, description="表計算のシート名（#1221）。")
    row_start: int | None = Field(default=None, description="シートの開始の行（1 始まり）。")
    row_end: int | None = Field(default=None, description="シートの終了の行。")
    cell_range: str | None = Field(default=None, description="セル範囲（例: A3:F7）。")
    page_label_start: str | None = Field(
        default=None,
        description="開始の頁の印刷の頁番号（PDF のページラベル。物理頁と違うときだけ。#1244）。",
    )
    page_label_end: str | None = Field(default=None, description="終了の頁の印刷の頁番号。")
    bbox: list[float] | None = Field(
        default=None, description="開始の頁の中の領域 [x0, y0, x1, y1]（分かるときだけ）。"
    )
    bbox_unit: str | None = Field(
        default=None, description="bbox の単位（absolute=頁の座標 / normalized=0〜1）。"
    )


class RagEvidence(BaseModel):
    """検索・回答の根拠 1 件。"""

    evidence_id: str = Field(description="根拠の id（chunk_id と同じ）。")
    document_id: str
    chunk_id: str
    file_name: str | None = None
    chunk_set_id: str | None = Field(default=None, description="根拠を作った処理の版。")
    recipe_id: str | None = Field(default=None, description="根拠を作った処理レシピ。")
    content_kind: str | None = Field(default=None, description="内容の種類（text / table など）。")
    locator: EvidenceLocator
    excerpt: str = Field(description=f"本文の先頭（最大 {EVIDENCE_EXCERPT_MAX_CHARS} 文字）。")
    truncated: bool = Field(description="excerpt が本文の一部だけか。")
    text_length: int = Field(description="本文の文字数。")
    used_in_answer: bool = Field(description="回答の生成に使った根拠か。")
    role: str | None = Field(
        default=None, description="根拠の役割（retrieved_anchor / parent_context など）。"
    )
    score: float | None = None
    rerank_score: float | None = None
    superseded: bool = Field(
        default=False,
        description=(
            "新しい版に置き換えた文書（旧版）の根拠か。旧版は既定では検索しない"
            "（filters の include_superseded=true で含める）。"
        ),
    )


AnswerOutcome = Literal[
    "answered",
    "conditional",
    "needs_clarification",
    "needs_environment_data",
    "needs_human",
    "insufficient_evidence",
]


class AnswerRequest(BaseModel):
    id: str = Field(description="質問の要求単位の ID（Q1 など）。")
    text: str = Field(description="要求の原文。")
    status: Literal["addressed", "partial", "missing", "unknown"] = Field(
        description=(
            "充足（addressed=答えた / partial=一部 / missing=答えていない / unknown=未確認）。"
        )
    )


class GuideClarification(BaseModel):
    condition_id: str = Field(description="条件の id（conditions に値を入れて呼び直す）。")
    label: str = Field(description="条件の名前。")
    question: str = Field(description="利用者に確かめる問い。")
    options: list[str] = Field(default_factory=list, description="選択肢（無ければ自由に答える）。")


class GuideCondition(BaseModel):
    id: str
    label: str
    value: str | None = None
    source: str | None = Field(
        default=None, description="user=利用者が答えた / question=質問の文から読んだ。"
    )
    handling: str | None = Field(
        default=None, description="不明のときの扱い（ask=確かめる / branch=分岐 / handoff=人へ）。"
    )


class GuideRef(BaseModel):
    guide_id: str
    revision: int = Field(description="使った公開の版。")
    title: str
    decision: Literal["answer", "branch", "clarify", "handoff"] = Field(
        description=(
            "answer=条件がそろった / branch=分岐で答えた / clarify=確かめる / handoff=人へ。"
        )
    )
    known_conditions: list[GuideCondition] = Field(default_factory=list)
    unknown_conditions: list[GuideCondition] = Field(default_factory=list)


class AnswerProvenance(BaseModel):
    """回答を作った設定の版（回答の記録の provenance。#1276）。"""

    search_answer_profile_id: str | None = None
    search_answer_profile_updated_at: str | None = Field(
        default=None, description="検索・回答プロファイルの更新時刻（版）。"
    )
    search_answer_profile_config_sha256: str | None = Field(
        default=None, description="検索・回答プロファイルの設定の sha256。"
    )
    prompt_version: str | None = Field(
        default=None, description="回答フローのプロンプトの版（内容の sha256 の先頭）。"
    )


class SearchOutput(VersionedOutput):
    answer: str
    trace_id: str
    guardrail_warnings: list[str]
    outcome: AnswerOutcome = Field(
        description=(
            "回答の対応。answered=資料で答えた / conditional=条件・不足を示して答えた / "
            "needs_environment_data=現場の値・記録の確認が要る / insufficient_evidence=資料から答え"
            "られない。needs_clarification / needs_human は利用者への確認・人への引き継ぎ。"
        )
    )
    requests: list[AnswerRequest] = Field(
        default_factory=list, description="質問の要求単位ごとの充足（背景の文は含めない）。"
    )
    conditions: list[str] = Field(
        default_factory=list,
        description="回答の説明が成り立つ条件（原文の語句。質問から確かめられないもの）。",
    )
    gaps: list[str] = Field(default_factory=list, description="資料から確かめられない点。")
    confirmations: list[str] = Field(
        default_factory=list, description="回答を確定するために確かめる現場のデータ・別の資料。"
    )
    clarifications: list[GuideClarification] = Field(
        default_factory=list,
        description=(
            "outcome=needs_clarification のとき、利用者に確かめる条件と問い（業務ガイド）。"
        ),
    )
    guide: GuideRef | None = Field(
        default=None, description="回答に使った業務ガイド（公開の版）。使わなければ null。"
    )
    insufficient_reason: str | None = Field(
        default=None, description="根拠が足りず答えきれなかった理由（無ければ null）。"
    )
    needs_human_review: bool = Field(default=False, description="人の確認が要る回答か。")
    evidence: list[RagEvidence]
    evidence_omitted: int = Field(description="evidence_limit を超えて返さなかった根拠の数。")
    provenance: AnswerProvenance | None = Field(
        default=None, description="回答を作った検索・回答プロファイルとプロンプトの版。"
    )


class GuideStepItem(BaseModel):
    id: str
    title: str
    depends_on: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class LookupGuideItem(GuideRef):
    expected_result: str
    score: int = Field(description="質問との照合の点（大きいほど合う）。")
    clarifications: list[GuideClarification] = Field(default_factory=list)
    steps: list[GuideStepItem] = Field(default_factory=list)
    impact_scope: Literal["individual", "group", "all"]
    approval_required: bool
    handoff_contact: str = ""


class LookupGuidesOutput(VersionedOutput):
    guides: list[LookupGuideItem] = Field(
        description="質問に当たる公開の業務ガイド（照合の点の高い順）。無ければ空。"
    )


class RetrieveEvidenceOutput(VersionedOutput):
    trace_id: str
    guardrail_warnings: list[str]
    evidence: list[RagEvidence] = Field(
        description="検索の順（rerank の順）の根拠。回答は作らないので used_in_answer は false。"
    )
    evidence_omitted: int = Field(description="evidence_limit を超えて返さなかった根拠の数。")


class ValidatedClaim(BaseModel):
    answer_quote: str = Field(description="回答の段落（原文）。")
    status: str = Field(
        description=(
            "supported=根拠で裏付けられる / unsupported=根拠で確かめられない / contradicted=根拠と"
            "矛盾 / data_confirmation=実データの確認を促すだけ / unassessed=監査されなかった"
        )
    )
    chunk_id: str | None = Field(default=None, description="裏付け・矛盾の根拠の chunk_id。")
    reason: str


class ValidationFinding(BaseModel):
    check: Literal["requests", "guide", "guide_steps", "impact"] = Field(
        description=(
            "検査（requests=要求の充足 / guide=業務ガイドの版 / guide_steps=手順 / "
            "impact=影響範囲）。"
        )
    )
    code: str = Field(
        description=(
            "request_missing / request_partial / request_unverified / guide_unavailable / "
            "guide_revision_stale / guide_steps_unmatched / step_order / step_wrong_branch / "
            "step_dependency_missing / step_following_missing / impact_scope_missing / "
            "approval_missing"
        )
    )
    severity: Literal["error", "warning"] = Field(
        description="error は valid にしない。warning は確かめたい点。"
    )
    message: str
    request_id: str | None = None
    step_id: str | None = None
    related_step_id: str | None = Field(
        default=None, description="関係する手順（前の手順・後の手順）。"
    )


class ValidateAnswerOutput(VersionedOutput):
    valid: bool = Field(
        description=(
            "矛盾・裏付けの無い主張・読めない根拠・error の finding が無く、裏付けのある主張が"
            " 1 つ以上あるか。"
        )
    )
    status: str = Field(description="completed / no_claims / no_evidence / input_too_large。")
    counts: dict[str, int] = Field(default_factory=dict, description="判定ごとの段落の数。")
    claims: list[ValidatedClaim] = Field(default_factory=list)
    missing_evidence: list[EvidenceRefInput] = Field(
        default_factory=list, description="見つからない（削除・権限の外）根拠。"
    )
    stale_evidence: list[EvidenceRefInput] = Field(
        default_factory=list, description="文書の古い版の根拠（検索し直す）。"
    )
    evidence_truncated: bool = Field(
        default=False, description="根拠が多く、後ろの根拠を監査に渡しきれなかったか。"
    )
    checks: list[Literal["requests", "guide", "guide_steps", "impact"]] = Field(
        default_factory=list,
        description="行った決定的な検査（requests・guide を渡したときだけ。モデルは呼ばない）。",
    )
    findings: list[ValidationFinding] = Field(
        default_factory=list, description="決定的な検査で見つけた点。"
    )
    guide_revision: int | None = Field(
        default=None, description="検査に使った業務ガイドの今の公開の版。"
    )


class ReadSourceOutput(VersionedOutput):
    evidence_id: str
    document_id: str
    chunk_id: str
    file_name: str | None = None
    chunk_set_id: str | None = None
    recipe_id: str | None = None
    content_kind: str | None = None
    locator: EvidenceLocator
    text: str = Field(description="offset から最大 max_chars 文字の本文。")
    offset: int
    text_length: int
    truncated: bool = Field(description="本文の続きがあるか。")
    next_offset: int | None = Field(default=None, description="続きを読むときの offset。")
    parent_text: str | None = Field(
        default=None, description="親の本文（前後の文脈。最大 max_chars 文字）。"
    )
    parent_truncated: bool = False
    superseded: bool = Field(
        default=False, description="新しい版に置き換えた文書（旧版）の根拠か。"
    )


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value.strip() or None if isinstance(value, str) else None


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _locator(metadata: dict[str, Any]) -> EvidenceLocator:
    section = metadata.get("section_path")
    if isinstance(section, list):
        path = [str(item).strip() for item in section if str(item).strip()]
    elif isinstance(section, str):
        path = [part.strip() for part in section.split(" > ") if part.strip()]
    else:
        path = []
    page_start = _metadata_int(metadata, "page_start") or _metadata_int(metadata, "page_number")
    page_end = _metadata_int(metadata, "page_end") or page_start
    sheet_name = _metadata_str(metadata, "sheet_name")
    return EvidenceLocator(
        section_path=path,
        page_start=page_start,
        page_end=page_end,
        sheet_name=sheet_name,
        row_start=_metadata_int(metadata, "row_start") if sheet_name else None,
        row_end=_metadata_int(metadata, "row_end") if sheet_name else None,
        cell_range=_metadata_str(metadata, "cell_range") if sheet_name else None,
        page_label_start=_metadata_str(metadata, "page_label_start") if page_start else None,
        page_label_end=_metadata_str(metadata, "page_label_end") if page_start else None,
        bbox=_bbox(metadata.get("bbox")),
        bbox_unit=_metadata_str(metadata, "bbox_unit") if _bbox(metadata.get("bbox")) else None,
    )


def _bbox(value: object) -> list[float] | None:
    """chunk の metadata の bbox（JSON の文字列か list）を 4 つの数にする。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, list) or len(value) != 4:
        return None
    if not all(isinstance(item, int | float) and not isinstance(item, bool) for item in value):
        return None
    return [float(item) for item in value]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _evidence(chunk: RetrievedChunk) -> RagEvidence:
    metadata = dict(chunk.metadata)
    return RagEvidence(
        evidence_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        chunk_set_id=_metadata_str(metadata, "chunk_set_id"),
        recipe_id=_metadata_str(metadata, "recipe_id"),
        content_kind=_metadata_str(metadata, "content_kind"),
        locator=_locator(metadata),
        excerpt=chunk.text[:EVIDENCE_EXCERPT_MAX_CHARS],
        truncated=len(chunk.text) > EVIDENCE_EXCERPT_MAX_CHARS,
        text_length=len(chunk.text),
        used_in_answer=metadata.get("evidence_model_used") is True,
        role=_metadata_str(metadata, "evidence_role"),
        score=chunk.score,
        rerank_score=chunk.rerank_score
        if chunk.rerank_score is not None
        else _float_or_none(metadata.get("rerank_score")),
        superseded=metadata.get("document_superseded") is True,
    )


def _answer_diagnostics(result: SearchResponse) -> dict[str, Any]:
    diagnostics = result.diagnostics
    answer = diagnostics.answer if diagnostics is not None else None
    return dict(answer) if isinstance(answer, dict) else {}


def _answer_fields(result: SearchResponse, evidence_limit: int) -> dict[str, Any]:
    # 回答に使った根拠を先にし、その中は検索の順（rerank の順）を保つ。
    ordered = sorted(
        result.citations,
        key=lambda chunk: chunk.metadata.get("evidence_model_used") is not True,
    )
    answer = _answer_diagnostics(result)
    reason = answer.get("insufficient_reason")
    raw_envelope = answer.get("envelope")
    envelope: dict[str, Any] = raw_envelope if isinstance(raw_envelope, dict) else {}
    outcome = envelope.get("outcome") or answer.get("outcome")
    if outcome not in get_args(AnswerOutcome):
        # 構造の無い回答（古い記録など）は、引用と人の確認の印から決める。
        if not result.citations:
            outcome = "insufficient_evidence"
        else:
            outcome = "conditional" if answer.get("needs_human_review") is True else "answered"
    return {
        "answer": result.answer,
        "trace_id": result.trace_id,
        "guardrail_warnings": list(result.guardrail_warnings),
        "outcome": outcome,
        "requests": [
            request
            for request in envelope.get("requests", [])
            if isinstance(request, dict)
            and request.get("status") in {"addressed", "partial", "missing", "unknown"}
        ],
        "conditions": _strings(envelope.get("conditions")),
        "gaps": _strings(envelope.get("gaps")),
        "confirmations": _strings(envelope.get("confirmations")),
        "clarifications": [
            item for item in envelope.get("clarifications", []) if isinstance(item, dict)
        ],
        "guide": _guide_ref(answer.get("guide")),
        "insufficient_reason": reason.strip() or None if isinstance(reason, str) else None,
        "needs_human_review": answer.get("needs_human_review") is True,
        "evidence": [_evidence(chunk) for chunk in ordered[:evidence_limit]],
        "evidence_omitted": max(0, len(ordered) - evidence_limit),
        "provenance": _provenance(answer.get("provenance")),
    }


def _provenance(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    profile = value.get("search_answer_profile")
    profile = profile if isinstance(profile, dict) else {}

    def text(item: object) -> str | None:
        return item if isinstance(item, str) and item else None

    return {
        "search_answer_profile_id": text(profile.get("id")),
        "search_answer_profile_updated_at": text(profile.get("updated_at")),
        "search_answer_profile_config_sha256": text(profile.get("config_sha256")),
        "prompt_version": text(value.get("prompt_version")),
    }


def _guide_conditions(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _guide_ref(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value.get("guide_id"):
        return None
    return {
        "guide_id": str(value["guide_id"]),
        "revision": int(value.get("revision") or 0),
        "title": str(value.get("title") or ""),
        "decision": value.get("decision") or "answer",
        "known_conditions": _guide_conditions(value.get("known_conditions")),
        "unknown_conditions": _guide_conditions(value.get("unknown_conditions")),
    }


def _strings(value: object) -> list[str]:
    return (
        [item for item in value if isinstance(item, str) and item]
        if isinstance(value, list)
        else []
    )


def _text_window(text: str, offset: int, max_chars: int) -> tuple[str, bool, int | None]:
    window = text[offset : offset + max_chars]
    end = offset + len(window)
    more = end < len(text)
    return window, more, (end if more else None)


async def read_source(arguments: ReadSourceInput) -> ReadSourceOutput:
    """検索と同じ見え方の条件で根拠の本文を読む（見えない・古い版は区別できるエラー）。"""
    oracle = OracleClient()
    chunk = await oracle.retrievable_chunk(arguments.document_id, arguments.chunk_id)
    if chunk is None:
        if await oracle.accessible_chunk_exists(arguments.document_id, arguments.chunk_id):
            raise McpToolError(
                SOURCE_STALE_CODE,
                "この根拠は文書の古い版のものです。rag_search で検索し直してください。",
            )
        raise McpToolError(
            SOURCE_NOT_FOUND_CODE,
            "根拠が見つかりません（削除されたか、利用できる範囲の外です）。",
        )
    metadata = dict(chunk.metadata)
    text, more, next_offset = _text_window(chunk.text, arguments.offset, arguments.max_chars)
    parent = metadata.get("parent_text")
    parent_text = parent if isinstance(parent, str) and parent.strip() else None
    return ReadSourceOutput(
        evidence_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        chunk_set_id=_metadata_str(metadata, "chunk_set_id"),
        recipe_id=_metadata_str(metadata, "recipe_id"),
        content_kind=_metadata_str(metadata, "content_kind"),
        locator=_locator(metadata),
        text=text,
        offset=arguments.offset,
        text_length=len(chunk.text),
        truncated=more,
        next_offset=next_offset,
        parent_text=parent_text[: arguments.max_chars] if parent_text else None,
        parent_truncated=bool(parent_text) and len(parent_text or "") > arguments.max_chars,
        superseded=metadata.get("document_superseded") is True,
    )


def _search_request(arguments: SearchInput) -> SearchRequest:
    payload: dict[str, Any] = {
        "query": arguments.query,
        "knowledge_base_ids": arguments.knowledge_base_ids,
        "filters": arguments.filters,
    }
    if arguments.search_answer_profile_id is not None:
        payload["search_answer_profile_id"] = arguments.search_answer_profile_id
    if arguments.top_k is not None:
        payload["top_k"] = arguments.top_k
    if arguments.conditions:
        payload["conditions"] = arguments.conditions
    try:
        return SearchRequest.model_validate(payload)
    except ValidationError as exc:
        errors = validation_tool_errors(exc.errors(include_url=False, include_input=False))
        raise McpToolError(
            TOOL_ARGUMENTS_INVALID_CODE,
            "検索条件が正しくありません。",
            details={"errors": errors},
        ) from exc


def _lookup_item(match: GuideMatch) -> LookupGuideItem:
    summary = match.summary()
    content = match.content
    return LookupGuideItem(
        **summary,
        expected_result=content.goal.expected_result,
        score=match.score,
        clarifications=[GuideClarification(**item) for item in clarification_questions(match)],
        steps=[
            GuideStepItem(
                id=step.id,
                title=step.title,
                depends_on=list(step.depends_on),
                allowed_tools=list(step.allowed_tools),
            )
            for step in content.steps
        ],
        impact_scope=content.impact.scope,
        approval_required=content.impact.approval_required,
        handoff_contact=content.handoff.contact,
    )


def build_rag_mcp_server(http_request: Request) -> McpServer:
    """1 リクエスト分の MCP サーバー（rate limit に呼び出し元の request を使う）。"""

    async def list_search_answer_profiles(
        arguments: ListSearchAnswerProfilesInput,
    ) -> ListSearchAnswerProfilesOutput:
        response = await search_answer_profiles_route.list_search_answer_profiles(
            OffsetParams(limit=arguments.limit, offset=0),
            status=SearchAnswerProfileStatus.ACTIVE,
            q=arguments.query,
        )
        if response.warning_messages:
            # DB 停止時の縮退（空一覧）は、Agent には「0 件」と区別できるエラーで返す。
            raise HTTPException(status_code=503, detail=response.warning_messages[0])
        page = response.data
        items = page.items if page is not None else []
        return ListSearchAnswerProfilesOutput(
            search_answer_profiles=[
                SearchAnswerProfileItem(
                    id=view.id,
                    name=view.name,
                    description=view.description,
                    status=view.status.value,
                    knowledge_base_count=view.knowledge_base_count,
                )
                for view in items
            ]
        )

    async def search(arguments: SearchInput) -> SearchOutput:
        request = _search_request(arguments)
        enforce_rate_limit("search", http_request)
        result = await search_route._run_search_with_timeout(request)
        return SearchOutput(**_answer_fields(result, arguments.evidence_limit))

    async def lookup_guides(arguments: LookupGuidesInput) -> LookupGuidesOutput:
        oracle = OracleClient()
        view = await oracle.get_search_answer_profile(arguments.search_answer_profile_id)
        if view is None:
            raise HTTPException(status_code=404, detail="検索・回答プロファイルが見つかりません。")
        guides = await search_route._published_guides(oracle, view.id)
        return LookupGuidesOutput(
            guides=[
                _lookup_item(match)
                for match in rank_guides(
                    guides, arguments.query, arguments.conditions, limit=arguments.limit
                )
            ]
        )

    async def retrieve_evidence(arguments: SearchInput) -> RetrieveEvidenceOutput:
        # 検索の画面と同じく質問の理解・拡張・検索・rerank まで行い、回答（CRAG・生成）は作らない。
        request = _search_request(arguments).model_copy(update={"generate_answer": False})
        enforce_rate_limit("search", http_request)
        result = await search_route._run_search_with_timeout(request)
        limit = arguments.evidence_limit
        return RetrieveEvidenceOutput(
            trace_id=result.trace_id,
            guardrail_warnings=list(result.guardrail_warnings),
            evidence=[_evidence(chunk) for chunk in result.citations[:limit]],
            evidence_omitted=max(0, len(result.citations) - limit),
        )

    async def validate(arguments: ValidateAnswerInput) -> ValidateAnswerOutput:
        enforce_rate_limit("search", http_request)
        guide = arguments.guide
        try:
            result = await validate_answer(
                arguments.query,
                arguments.answer,
                [EvidenceRef(item.document_id, item.chunk_id) for item in arguments.evidence],
                get_settings(),
                requests=(
                    [item.model_dump() for item in arguments.requests]
                    if arguments.requests is not None
                    else None
                ),
                gaps=arguments.gaps,
                guide=(
                    GuideCheckRef(
                        guide.search_answer_profile_id,
                        guide.guide_id,
                        guide.revision,
                        dict(guide.conditions),
                    )
                    if guide is not None
                    else None
                ),
            )
        except GuideProfileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="検索・回答プロファイルが見つかりません。"
            ) from exc
        return ValidateAnswerOutput(
            valid=result.valid,
            status=result.status,
            counts=result.counts,
            claims=[
                ValidatedClaim(
                    answer_quote=str(claim.get("answer_quote") or ""),
                    status=str(claim.get("status") or ""),
                    chunk_id=str(claim.get("source_id") or "") or None,
                    reason=str(claim.get("reason") or ""),
                )
                for claim in result.claim_checks
            ],
            missing_evidence=[
                EvidenceRefInput(document_id=ref.document_id, chunk_id=ref.chunk_id)
                for ref in result.missing_evidence
            ],
            stale_evidence=[
                EvidenceRefInput(document_id=ref.document_id, chunk_id=ref.chunk_id)
                for ref in result.stale_evidence
            ],
            evidence_truncated=result.evidence_truncated,
            checks=result.checks,
            findings=[ValidationFinding.model_validate(item.as_dict()) for item in result.findings],
            guide_revision=result.guide_revision,
        )

    return McpServer(
        name=MCP_SERVER_NAME,
        version=get_settings().app_version,
        instructions=INSTRUCTIONS,
        tools=[
            McpTool(
                name="rag_list_search_answer_profiles",
                description="利用できる検索・回答プロファイル（ACTIVE）の一覧を返します。",
                input_model=ListSearchAnswerProfilesInput,
                handler=list_search_answer_profiles,
                output_model=ListSearchAnswerProfilesOutput,
                permissions=(SEARCH_ANSWER_PROFILE_READ_PERMISSIONS,),
            ),
            McpTool(
                name="rag_search",
                description=(
                    "検索・回答プロファイルのナレッジベースを検索し、根拠（evidence。場所・版・"
                    "回答に使ったか付き）と回答を返します。"
                ),
                input_model=SearchInput,
                handler=search,
                output_model=SearchOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_lookup_guides",
                description=(
                    "質問に当たる公開の業務ガイド（目的・確かめる条件と状態・手順の順・影響範囲・"
                    "引き継ぎ先）を返します。回答は作りません。"
                ),
                input_model=LookupGuidesInput,
                handler=lookup_guides,
                output_model=LookupGuidesOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_retrieve_evidence",
                description=(
                    "回答を作らずに、検索・回答プロファイルのナレッジベースから根拠（evidence。"
                    "場所・版付き）だけを返します。rag_search より速く、根拠を集める段で使います。"
                ),
                input_model=SearchInput,
                handler=retrieve_evidence,
                output_model=RetrieveEvidenceOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_validate_answer",
                description=(
                    "回答の段落ごとの主張を、渡した根拠（今の権限と版で読み直す）で監査します。"
                    "公開する前の最終の検証に使います（モデルを 1 回呼びます）。requests（要求の"
                    "充足）・guide（業務ガイド）を渡すと、要求の漏れ・手順の順序と分岐・影響範囲も"
                    "決定的に確かめます。"
                ),
                input_model=ValidateAnswerInput,
                handler=validate,
                output_model=ValidateAnswerOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_read_source",
                description=(
                    "rag_search の根拠（document_id と chunk_id）の本文の続きと親の本文を読みます。"
                    "検索と同じ利用範囲の根拠だけを読めます。"
                ),
                input_model=ReadSourceInput,
                handler=read_source,
                output_model=ReadSourceOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
        ],
    )
