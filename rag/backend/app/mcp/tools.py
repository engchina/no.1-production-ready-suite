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
  条件。#1219）。`include_image=true` で図の根拠の元の画像の領域を、上限つきの PNG で MCP の
  content の image として返す（読むたびに同じ見え方の条件を確かめ直す。#1282）

RAG のチャットは画面の機能で、MCP では提供しない（#787）。MCP で提供するのは検索と根拠の
読み取りだけにする。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

from fastapi import HTTPException, Request
from pr_backend_core.api import OffsetParams
from pr_backend_core.api.validation import validation_tool_errors
from pr_backend_core.mcp import (
    TOOL_ARGUMENTS_INVALID_CODE,
    McpServer,
    McpTool,
    McpToolError,
    McpToolResult,
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
from app.rag.cross_references import (
    REFERENCE_FROM_KEY,
    REFERENCE_LABEL_KEY,
    reference_targets,
    split_section_path,
)
from app.rag.document_crop import (
    CropTooLargeError,
    DocumentSourceNotFoundError,
    crop_png_bounded,
    load_parsed_source,
)
from app.rag.rate_limit import enforce_rate_limit
from app.rag.support_guide_runtime import GuideMatch, clarification_questions, rank_guides
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.schemas.search_answer_profile import SearchAnswerProfileStatus
from app.security.permissions import MENU_SEARCH, ROUTE_PERMISSIONS

MCP_SERVER_NAME = "production-ready-rag"
# ツールの出力の版（出力の形を変えたら上げる。handoff §10。#1276）。
MCP_OUTPUT_SCHEMA_VERSION = 3
# 根拠の抜粋の長さ（続きは rag_read_source で読む）。
EVIDENCE_EXCERPT_MAX_CHARS = 1000
EVIDENCE_LIMIT_DEFAULT = 12
EVIDENCE_LIMIT_MAX = 50
# rag_read_source が 1 回で返す本文・親の本文の上限。
READ_SOURCE_MAX_CHARS_DEFAULT = 8000
READ_SOURCE_MAX_CHARS_LIMIT = 20000
SOURCE_NOT_FOUND_CODE = "source_not_found"
SOURCE_STALE_CODE = "source_stale"
# 図の元の画像（rag_read_source の include_image。#1282）。長い辺は VLM が縮めずに読める大きさ、
# バイト数は 1 回の応答に収まる大きさにする。画像は structuredContent に入れず、MCP の content の
# image で返す（呼び出し側のモデルの文脈を base64 で膨らませない）。
IMAGE_MAX_EDGE_PX = 1568
IMAGE_MAX_BYTES = 1_500_000
IMAGE_MIME_TYPE: Literal["image/png"] = "image/png"
IMAGE_NOT_AVAILABLE_CODE = "image_not_available"
IMAGE_TOO_LARGE_CODE = "image_too_large"
IMAGE_SOURCE_MISSING_CODE = "image_source_missing"
# 根拠の種類（#1282）。figure_description=図を VLM が読んだ説明（元の図で確かめる）/
# ocr=図の中・周りの文字（解析の OCR・キャプション）。
EvidenceType = Literal["text", "table", "figure_description", "ocr"]
GUIDES_UNAVAILABLE_MESSAGE = "業務ガイドを読み込めませんでした。時間をおいて再度お試しください。"

SEARCH_ANSWER_PROFILE_READ_PERMISSIONS = ROUTE_PERMISSIONS[("GET", "/search-answer-profiles")]
SEARCH_PERMISSIONS = frozenset({MENU_SEARCH})

INSTRUCTIONS = (
    "Production Ready RAG の検索・回答のツールです。"
    "まず rag_list_search_answer_profiles で使"
    "える検索・回答プロファイルを確認し、その id を rag_search に"
    "渡してください。回答の根拠は evidence にあり、used_in_answer が回答に使った根拠です。"
    "根拠の excerpt が切り詰められている（truncated）ときや、前後の文脈が要るときは、"
    "rag_read_source に document_id と chunk_id を渡して本文と親の本文を読んでください。"
    "evidence_type が figure_description の根拠は図を AI が読んだ説明です。元の図で確かめるときは"
    " rag_read_source に include_image=true を渡すと、図の領域の画像を返します。"
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
    include_image: bool = Field(
        default=False,
        description=(
            "true のとき、図の根拠（image_ref がある根拠）の元の画像の領域を返す。画像は MCP の"
            f" content の image（PNG。長い辺 {IMAGE_MAX_EDGE_PX} px 以下・{IMAGE_MAX_BYTES} バイト"
            "以下）で返し、structuredContent の image には大きさだけを入れる。図でない・場所が"
            "分からない根拠は image_not_available のエラー。"
        ),
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


class EvidenceReference(BaseModel):
    """根拠の本文が参照する節・文書（「第3章を参照」など。取込時に抜き出す。#1280）。"""

    label: str = Field(description="本文の参照の表記（例: 第3章、別紙1、「権限」）。")
    document_title: str | None = Field(
        default=None, description="他の文書への参照の文書名（同じ文書なら null）。"
    )
    section_path: list[str] = Field(
        default_factory=list,
        description="同じ文書の参照先の節の見出しの列（解決できたときだけ）。",
    )
    resolved: bool = Field(
        description=(
            "参照先の節を取込時に決められたか（他の文書への参照は回答のときに検索範囲から"
            "決めるので false）。"
        )
    )


class EvidenceImageRef(BaseModel):
    """図の根拠の元の画像の領域（#1282）。

    場所を示すだけで、読む権限は持たない。画像は rag_read_source に document_id・chunk_id と
    include_image=true を渡して読む（読むたびに今の権限・版で確かめ、領域はサーバーが保存した
    場所から決める）。文書の版（chunk_set）が変わると source_stale になる。
    """

    document_id: str
    chunk_id: str
    chunk_set_id: str | None = Field(default=None, description="領域を記録した処理の版。")
    page: int = Field(description="図のある頁（PDF の物理頁。画像ファイルは 1）。")
    page_label: str | None = Field(
        default=None, description="印刷の頁番号（物理頁と違うときだけ。#1244）。"
    )
    bbox: list[float] = Field(description="頁の中の図の領域 [x0, y0, x1, y1]。")
    bbox_unit: Literal["absolute", "ratio", "percent"] = Field(
        description="bbox の単位（absolute=頁の座標 / ratio=0〜1 / percent=0〜100）。"
    )


class EvidenceImage(BaseModel):
    """rag_read_source が content の image で返した図の画像の大きさ（#1282）。"""

    mime_type: Literal["image/png"] = IMAGE_MIME_TYPE
    width: int = Field(description="画像の幅（px）。")
    height: int = Field(description="画像の高さ（px）。")
    byte_size: int = Field(description="画像のバイト数。")
    sha256: str = Field(description="画像の sha256（content の image と同じか確かめる）。")
    content_index: int = Field(
        default=1, description="MCP の content の何番目（0 始まり）に画像があるか。"
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
    evidence_type: EvidenceType = Field(
        default="text",
        description=(
            "根拠の種類（text=本文 / table=表 / figure_description=図を AI（VLM）が読んだ説明。"
            "原文ではないので元の図で確かめる / ocr=図の中・周りの文字（OCR・キャプション））。"
        ),
    )
    image_ref: EvidenceImageRef | None = Field(
        default=None,
        description=(
            "図の元の画像の領域（rag_read_source の include_image で読む）。無ければ null。"
        ),
    )
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
    references: list[EvidenceReference] = Field(
        default_factory=list, description="この根拠の本文が参照する節・文書（#1280）。"
    )
    reference_from: str | None = Field(
        default=None,
        description=(
            "別の根拠の本文の参照（「第3章を参照」など）を辿って足した根拠なら、その根拠の"
            " evidence_id（#1280）。"
        ),
    )
    reference_label: str | None = Field(
        default=None, description="reference_from の根拠の本文の参照の表記。"
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
    state: Literal["known", "unknown", "conflicting"] = Field(
        default="unknown",
        description=(
            "条件の状態（known=分かっている / unknown=分からない / conflicting=質問に複数の値が"
            "出ていて決められない。値は candidates）。"
        ),
    )
    value: str | None = None
    source: str | None = Field(
        default=None, description="user=利用者が答えた / question=質問の文から読んだ。"
    )
    handling: str | None = Field(
        default=None, description="不明のときの扱い（ask=確かめる / branch=分岐 / handoff=人へ）。"
    )
    candidates: list[str] = Field(
        default_factory=list, description="state=conflicting のとき、質問に出た値。"
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
    unknown_conditions: list[GuideCondition] = Field(
        default_factory=list, description="確かめる・分岐する・引き継ぐ条件（不明・矛盾）。"
    )
    applicability: dict[str, Literal["matched", "unverified"]] = Field(
        default_factory=dict,
        description=(
            "値のある適用範囲の項目（business_domains / object_types / versions）ごとの状態。"
            "matched=質問・絞り込みの手がかりと合った / unverified=手がかりが無く確かめていない。"
            "空の項目は制限なし（出さない）。手がかりと合わないガイドは返さない。"
        ),
    )


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
        description=(
            "質問に当たり、適用範囲の手がかりと合う公開の業務ガイド（照合の点の高い順）。"
            "無ければ空。"
        )
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
    evidence_type: EvidenceType = "text"
    image_ref: EvidenceImageRef | None = None
    image: EvidenceImage | None = Field(
        default=None,
        description=(
            "include_image=true で返した図の画像の大きさ（画像は content の image）。"
            "include_image=false なら null。"
        ),
    )
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
    references: list[EvidenceReference] = Field(
        default_factory=list,
        description="本文が参照する節・文書（#1280。続きは rag_search で参照先を検索する）。",
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


def _references(metadata: dict[str, Any]) -> list[EvidenceReference]:
    """chunk の metadata に保存した交差参照（#1280）を根拠の参照にする。"""
    return [
        EvidenceReference(
            label=target.label,
            document_title=target.document_title,
            section_path=list(split_section_path(target.section_path)),
            resolved=target.resolved,
        )
        for target in reference_targets(metadata)
    ]


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


def _evidence_type(metadata: dict[str, Any]) -> EvidenceType:
    """根拠の種類（#1282）。

    図の本文の出どころが分からない古い記録は、VLM の説明として扱う（原文として扱わず、元の図で
    確かめる側に倒す）。
    """
    kind = _metadata_str(metadata, "content_kind")
    if kind == "table":
        return "table"
    if kind == "figure":
        return "ocr" if metadata.get("figure_text_source") == "ocr" else "figure_description"
    return "text"


BboxUnit = Literal["absolute", "ratio", "percent"]


@dataclass(frozen=True, slots=True)
class _ImageRegion:
    page: int
    bbox: tuple[float, float, float, float]
    unit: BboxUnit
    page_size: tuple[float, float]


def _positive(value: object) -> float | None:
    number = _float_or_none(value)
    return number if number is not None and number > 0 else None


def _engine_image_bbox(metadata: dict[str, Any], page: int) -> list[float] | None:
    """親子階層（small-to-big）の chunk の image_evidence から、その頁の図の bbox を取る。"""
    raw = metadata.get("engine_metadata_json")
    try:
        engine = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    images = engine.get("image_evidence") if isinstance(engine, dict) else None
    for image in images if isinstance(images, list) else []:
        if not isinstance(image, dict) or _metadata_int(image, "page") != page:
            continue
        if (bbox := _bbox(image.get("bbox"))) is not None:
            return bbox
    return None


def _bbox_unit_value(value: str | None) -> BboxUnit | None:
    if value in (None, "absolute"):
        return "absolute"
    if value in {"ratio", "normalized"}:
        return "ratio"
    if value == "percent":
        return "percent"
    return None


def _image_region(metadata: dict[str, Any]) -> _ImageRegion | None:
    """図の根拠の元の画像の領域を、保存した chunk の metadata から決める（#1282）。

    利用者が渡した座標は使わない。頁・領域・頁の大きさのどれかが分からない、頁が回転している、
    領域が頁からはみ出す根拠は、画像を返さない（None）。
    """
    if _metadata_str(metadata, "content_kind") != "figure":
        return None
    page = _metadata_int(metadata, "page_start") or _metadata_int(metadata, "page_number")
    if not page or page < 1:
        return None
    rotation = _metadata_int(metadata, "page_rotation")
    if rotation is not None and rotation % 360 != 0:
        return None
    unit: BboxUnit | None
    engine_bbox = _engine_image_bbox(metadata, page)
    if engine_bbox is not None:
        bbox, unit = engine_bbox, "absolute"
    else:
        stored = _bbox(metadata.get("bbox"))
        if stored is None:
            return None
        bbox, unit = stored, _bbox_unit_value(_metadata_str(metadata, "bbox_unit"))
        if _metadata_str(metadata, "bbox_coordinate_mode") == "xywh":
            bbox = [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]]
    if unit == "absolute":
        width = _positive(metadata.get("page_width"))
        height = _positive(metadata.get("page_height"))
        if width is None or height is None:
            return None
        page_size = (width, height)
    elif unit == "ratio":
        page_size = (1.0, 1.0)
    elif unit == "percent":
        page_size = (100.0, 100.0)
    else:
        return None
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        return None
    if x1 > page_size[0] * 1.001 or y1 > page_size[1] * 1.001:
        return None
    return _ImageRegion(page=page, bbox=(x0, y0, x1, y1), unit=unit, page_size=page_size)


def _image_ref(
    chunk: RetrievedChunk, metadata: dict[str, Any], region: _ImageRegion | None
) -> EvidenceImageRef | None:
    if region is None:
        return None
    return EvidenceImageRef(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        chunk_set_id=_metadata_str(metadata, "chunk_set_id"),
        page=region.page,
        page_label=_metadata_str(metadata, "page_label_start"),
        bbox=list(region.bbox),
        bbox_unit=region.unit,
    )


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
        evidence_type=_evidence_type(metadata),
        image_ref=_image_ref(chunk, metadata, _image_region(metadata)),
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
        references=_references(metadata),
        reference_from=_metadata_str(metadata, REFERENCE_FROM_KEY),
        reference_label=_metadata_str(metadata, REFERENCE_LABEL_KEY),
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
        "applicability": (
            dict(value["applicability"]) if isinstance(value.get("applicability"), dict) else {}
        ),
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


async def _read_image(
    oracle: OracleClient,
    chunk: RetrievedChunk,
    metadata: dict[str, Any],
    region: _ImageRegion | None,
) -> tuple[EvidenceImage, dict[str, Any]]:
    """図の領域を、解析に使ったファイル（処理レシピの artifact）から上限つきで切り出す（#1282）。"""
    if region is None:
        raise McpToolError(
            IMAGE_NOT_AVAILABLE_CODE,
            "この根拠には元の図の画像がありません（図ではないか、図の場所が分かりません）。",
        )
    try:
        source = await load_parsed_source(
            oracle, chunk.document_id, _metadata_str(metadata, "recipe_id")
        )
    except (DocumentSourceNotFoundError, ValueError) as exc:
        raise McpToolError(
            IMAGE_SOURCE_MISSING_CODE,
            "図を切り出す元のファイルが見つかりません。文書を再処理してください。",
            status=404,
        ) from exc
    try:
        crop = await asyncio.to_thread(
            crop_png_bounded,
            source,
            region.page,
            region.bbox,
            region.page_size,
            max_edge=IMAGE_MAX_EDGE_PX,
            max_bytes=IMAGE_MAX_BYTES,
        )
    except CropTooLargeError as exc:
        raise McpToolError(
            IMAGE_TOO_LARGE_CODE,
            "図の画像が大きすぎて返せません。頁と領域（image_ref）で元の文書を確かめてください。",
            status=413,
        ) from exc
    except ValueError as exc:
        raise McpToolError(
            IMAGE_NOT_AVAILABLE_CODE,
            "元の図を切り出せませんでした（ファイルの形式か図の場所が対応していません）。",
        ) from exc
    image = EvidenceImage(
        width=crop.width,
        height=crop.height,
        byte_size=len(crop.png),
        sha256=hashlib.sha256(crop.png).hexdigest(),
    )
    block = {
        "type": "image",
        "data": base64.b64encode(crop.png).decode("ascii"),
        "mimeType": IMAGE_MIME_TYPE,
    }
    return image, block


async def read_source(arguments: ReadSourceInput) -> ReadSourceOutput | McpToolResult:
    """検索と同じ見え方の条件で根拠の本文を読む（見えない・古い版は区別できるエラー）。

    図の画像（include_image）も、読むたびに同じ条件で chunk を読み直してから切り出す。
    """
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
    region = _image_region(metadata)
    image: EvidenceImage | None = None
    blocks: tuple[dict[str, Any], ...] = ()
    if arguments.include_image:
        image, block = await _read_image(oracle, chunk, metadata, region)
        blocks = (block,)
    output = ReadSourceOutput(
        evidence_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        file_name=chunk.file_name,
        chunk_set_id=_metadata_str(metadata, "chunk_set_id"),
        recipe_id=_metadata_str(metadata, "recipe_id"),
        content_kind=_metadata_str(metadata, "content_kind"),
        evidence_type=_evidence_type(metadata),
        image_ref=_image_ref(chunk, metadata, region),
        image=image,
        locator=_locator(metadata),
        text=text,
        offset=arguments.offset,
        text_length=len(chunk.text),
        truncated=more,
        next_offset=next_offset,
        parent_text=parent_text[: arguments.max_chars] if parent_text else None,
        parent_truncated=bool(parent_text) and len(parent_text or "") > arguments.max_chars,
        superseded=metadata.get("document_superseded") is True,
        references=_references(metadata),
    )
    return McpToolResult(output=output, content=blocks) if blocks else output


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
        # rag_search と同じく、アーカイブ済みのプロファイルは使わない（409。#1278）。
        search_route.ensure_search_answer_profile_not_archived(
            view, arguments.search_answer_profile_id
        )
        guides, failed = await search_route.load_published_guides(oracle, view.id)
        if failed:
            # 読めなかったことを「当たるガイドが無い」（0 件）と区別する（#1278）。
            raise HTTPException(status_code=503, detail=GUIDES_UNAVAILABLE_MESSAGE)
        context = await search_route.support_guide_context(
            oracle, guides, arguments.query, search_route.profile_scope_filters(view)
        )
        return LookupGuidesOutput(
            guides=[
                _lookup_item(match)
                for match in rank_guides(
                    guides,
                    arguments.query,
                    arguments.conditions,
                    limit=arguments.limit,
                    context=context,
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

    async def read(arguments: ReadSourceInput) -> ReadSourceOutput | McpToolResult:
        if arguments.include_image:
            # 図の切り出しは元のファイルを読んで描くので、検索と同じ上限で守る（#1282）。
            enforce_rate_limit("search", http_request)
        return await read_source(arguments)

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
                    "公開する前の最終の検証に使います（モデルを 1 回呼びます）。見出し・出典の行・"
                    "利用者への質問の段落は主張ではないので監査せず、claims に含めません。"
                    "requests（要求の充足）・guide（業務ガイド）を渡すと、要求の漏れ・手順の順序と"
                    "分岐・影響範囲も"
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
                    "検索と同じ利用範囲の根拠だけを読めます。include_image=true で、図の根拠の"
                    "元の画像の領域を画像（content の image）で返します。"
                ),
                input_model=ReadSourceInput,
                handler=read,
                output_model=ReadSourceOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
        ],
    )
