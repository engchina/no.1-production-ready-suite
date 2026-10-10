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
- `rag_outline` / `rag_read_document`（`menu.search`）: 文書の節の構成と、頁・節・定位子・
  続きの位置から本文を順に読む（検索と同じ見え方の条件。#1332）

RAG のチャットは画面の機能で、MCP では提供しない（#787）。MCP で提供するのは検索と根拠の
読み取りだけにする。

サービストークンにデータの範囲の claim（`profile_ids`。業務 Agent の定義。#1379）があれば、
すべてのツールを「利用者の権限 ∩ 範囲」で判定する（`app.mcp.profile_scope`）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args

from fastapi import HTTPException, Request
from pr_backend_core.api import InvalidCursorError, OffsetParams, decode_cursor, encode_cursor
from pr_backend_core.api.validation import validation_tool_errors
from pr_backend_core.mcp import (
    TOOL_ARGUMENTS_INVALID_CODE,
    McpServer,
    McpTool,
    McpToolError,
    McpToolResult,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from rag_engine.retrieval.entity_expansion import entity_expansion_relevant

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as search_answer_profiles_route
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.mcp.profile_scope import (
    ProfileScope,
    check_profile,
    check_search,
    within_profile_scope,
)
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
    section_path_within,
    split_section_path,
)
from app.rag.document_crop import (
    BoundedCrop,
    CropTooLargeError,
    DocumentSourceNotFoundError,
    crop_png_bounded,
    load_parsed_source,
)
from app.rag.element_locator import (
    LOCATOR_MAX_LENGTH,
    chunk_element_locator,
    parse_element_locator,
)
from app.rag.figure_url import (
    FIGURE_URL_TTL_SECONDS,
    FigureUrlUnavailableError,
    issue_figure_token,
)
from app.rag.rate_limit import enforce_rate_limit
from app.rag.support_guide_runtime import (
    GuideMatch,
    clarification_questions,
    impact_applies,
    rank_guides,
)
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.schemas.search_answer_profile import SearchAnswerProfileStatus
from app.security.permissions import MENU_SEARCH, ROUTE_PERMISSIONS

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "production-ready-rag"
# ツールの出力の版（出力の形を変えたら上げる。handoff §10。#1276）。
# 6: 根拠の場所に要素の定位子（locator.element_locator）を足した（#1330）。
# 7: 文書を順に読むツール（rag_outline / rag_read_document）を足した（#1332）。
MCP_OUTPUT_SCHEMA_VERSION = 7
# 根拠の抜粋の長さ（続きは rag_read_source で読む）。
EVIDENCE_EXCERPT_MAX_CHARS = 1000
EVIDENCE_LIMIT_DEFAULT = 12
EVIDENCE_LIMIT_MAX = 50
# rag_retrieve_evidence（回答を作らない根拠の収集）の既定の件数。検索する件数（SearchRequest の
# top_k の既定 20）と同じにし、検索で当たった chunk を既定で全部返す（#1365）。rag_search は回答に
# 使った根拠が先に並ぶので 12 のまま。
RETRIEVE_EVIDENCE_LIMIT_DEFAULT = 20
# rag_read_source が 1 回で返す本文・親の本文の上限。
READ_SOURCE_MAX_CHARS_DEFAULT = 8000
READ_SOURCE_MAX_CHARS_LIMIT = 20000
# rag_read_document が 1 回で読む chunk の数の単位と、rag_outline が返す節の上限（#1332）。
READ_DOCUMENT_BATCH = 50
OUTLINE_MAX_SECTIONS = 300
CURSOR_INVALID_CODE = "cursor_invalid"
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
# 図をブラウザで開く短命の URL（rag_read_source の include_image_url。#1311）。
IMAGE_URL_NOT_ALLOWED_CODE = "image_url_not_allowed"
IMAGE_URL_UNAVAILABLE_CODE = "image_url_unavailable"
# URL を作ってよい呼び出し（Agent の画面の操作）のサービストークンの claim `purpose` の値。
FIGURE_URL_PURPOSE = "figure_url"
# 図の画像の読み取りの path（`app.api.routes.figures`。/api の下）。
FIGURE_URL_PATH_PREFIX = "/api/figures"
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
        description=(
            "検索・回答プロファイルの id。参照するナレッジベースと検索・回答設定を使う。"
            "呼び出し元（業務 Agent）にデータの範囲があるときは必須。"
        ),
    )
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        max_length=200,
        description=(
            "検索するナレッジベースの id（省略時は検索・回答プロファイルの参照先）。"
            "呼び出し元にデータの範囲があるときは、検索・回答プロファイルの参照先の中だけ。"
        ),
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
        description=(
            "返す根拠の最大件数。回答に使った根拠、検索で当たった根拠（関連度の順）、前後の文脈の"
            "順に並べてから切る。"
        ),
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


class RetrieveEvidenceInput(SearchInput):
    """根拠の収集の条件（rag_search と同じ。返す根拠の件数の既定だけが違う）。"""

    evidence_limit: int = Field(
        default=RETRIEVE_EVIDENCE_LIMIT_DEFAULT,
        ge=1,
        le=EVIDENCE_LIMIT_MAX,
        description=(
            "返す根拠の最大件数。検索で当たった根拠（関連度の順）、前後の文脈の順に並べてから切る。"
            "省略時は検索する件数（top_k。省略時 20）と同じで、検索で当たった根拠を全部返す。"
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
    """根拠の本文を読む条件（evidence の document_id と、chunk_id か要素の定位子）。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128, description="文書の id。")
    chunk_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="根拠の chunk の id。locator とどちらか一方を渡す。",
    )
    locator: str | None = Field(
        default=None,
        min_length=1,
        max_length=LOCATOR_MAX_LENGTH,
        description=(
            "根拠の要素の定位子（evidence の locator.element_locator。#1330）。文書分割の設定を"
            "変えて chunk を作り直しても、同じ解析の結果の同じ要素を含む今の chunk を返す。"
            "解析をやり直した後の古い定位子は source_stale のエラー。"
            "chunk_id とどちらか一方を渡す。"
        ),
    )
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
    include_image_url: bool = Field(
        default=False,
        description=(
            "true のとき、図の根拠の元の画像をブラウザで開く短命の URL（image_url）を返す"
            f"（{FIGURE_URL_TTL_SECONDS} 秒で切れ、読むたびに今の権限・版を確かめる）。Agent の"
            "画面の操作からだけ使える（それ以外は image_url_not_allowed のエラー）。"
        ),
    )

    @model_validator(mode="after")
    def _one_reference(self) -> ReadSourceInput:
        if (self.chunk_id is None) == (self.locator is None):
            raise ValueError("chunk_id と locator のどちらか一方だけを渡してください。")
        if self.locator is not None:
            parsed = parse_element_locator(self.locator)
            if parsed is None:
                raise ValueError(
                    "locator の形が違います（doc:{document_id}/ext:{…}/page:{頁}/el:{要素}）。"
                )
            if parsed.document_id != self.document_id:
                raise ValueError("locator の文書が document_id と違います。")
        return self


class OutlineInput(BaseModel):
    """文書の節の構成を読む条件（#1332）。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128, description="文書の id。")


class ReadDocumentInput(BaseModel):
    """文書の本文を順に読む条件（#1332）。読み始めの位置はどれか 1 つ（無ければ文書の先頭）。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, max_length=128, description="文書の id。")
    cursor: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description=(
            "続きの位置（前の rag_read_document の next_cursor、"
            "または rag_outline の節の cursor）。"
        ),
    )
    locator: str | None = Field(
        default=None,
        min_length=1,
        max_length=LOCATOR_MAX_LENGTH,
        description=(
            "根拠の要素の定位子（evidence の locator.element_locator）。その要素の chunk から読む。"
        ),
    )
    page: int | None = Field(default=None, ge=1, description="この頁（PDF の物理頁）から読む。")
    section: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="この節（見出しの列を「 > 」でつないだもの。前方一致）から読む。",
    )
    max_chars: int = Field(
        default=READ_SOURCE_MAX_CHARS_DEFAULT,
        ge=1,
        le=READ_SOURCE_MAX_CHARS_LIMIT,
        description="返す本文の最大文字数（頁の区切りの行を含む）。",
    )

    @model_validator(mode="after")
    def _one_start(self) -> ReadDocumentInput:
        starts = [self.cursor, self.locator, self.page, self.section]
        if sum(value is not None for value in starts) > 1:
            raise ValueError("cursor・locator・page・section はどれか 1 つだけを渡してください。")
        if self.locator is not None:
            parsed = parse_element_locator(self.locator)
            if parsed is None or parsed.document_id != self.document_id:
                raise ValueError("locator の形が違うか、文書が document_id と違います。")
        return self


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


class OutlineSection(BaseModel):
    """文書の 1 つの節（続く chunk のまとまり）。"""

    section_path: list[str] = Field(default_factory=list, description="節の見出しの列。")
    page_start: int | None = Field(default=None, description="節の開始の頁。")
    page_end: int | None = Field(default=None, description="節の終了の頁。")
    chunk_count: int = Field(description="節の chunk の数。")
    chars: int = Field(description="節の本文の文字数。")
    cursor: str = Field(description="この節から読む rag_read_document の cursor。")


class OutlineOutput(VersionedOutput):
    document_id: str
    file_name: str | None = None
    chunk_set_id: str = Field(
        description="読んだ chunk_set（続きの cursor はこの chunk_set に縛る）。"
    )
    chunk_count: int
    page_start: int | None = None
    page_end: int | None = None
    sections: list[OutlineSection]
    sections_omitted: int = Field(default=0, description="上限を超えて省いた節の数。")
    superseded: bool = Field(default=False, description="新しい版に置き換えた文書（旧版）か。")


class ReadDocumentChunk(BaseModel):
    """返した本文に含まれる chunk（根拠として引用するときの場所）。"""

    chunk_id: str
    element_locator: str | None = None
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    start: int = Field(description="返した本文の中の開始の位置（文字数）。")
    end: int = Field(description="返した本文の中の終了の位置（文字数）。")


class ReadDocumentOutput(VersionedOutput):
    document_id: str
    file_name: str | None = None
    chunk_set_id: str
    text: str = Field(description="本文。頁が変わる所に「--- p.N ---」の行を入れる。")
    chunks: list[ReadDocumentChunk]
    next_cursor: str | None = Field(
        default=None, description="続きを読む cursor（文書の最後まで読んだら null）。"
    )
    superseded: bool = Field(default=False, description="新しい版に置き換えた文書（旧版）か。")


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
    element_locator: str | None = Field(
        default=None,
        description=(
            "根拠の先頭の要素の定位子（#1330。doc:{document_id}/ext:{解析の結果}/page:{頁}/el:{要素}）。"
            "文書分割を作り直しても変わらず、rag_read_source の locator で読み直せる。"
            "要素を持たない分割の根拠は null。"
        ),
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


class EvidenceImageUrl(BaseModel):
    """図の根拠の元の画像をブラウザで開く短命の URL（rag_read_source の include_image_url。#1311）。

    URL は利用者・根拠・版に縛った署名つきのトークンを path に持ち、期限（5 分）で切れる。読むたびに
    今の権限・版を確かめ直す（版が変われば 409、見えなければ 404）。
    """

    url: str = Field(description="図の画像の URL（MCP の呼び出しを受けた RAG の起点から作る）。")
    path: str = Field(description="url の path（RAG の公開の起点に付け替えるときに使う）。")
    expires_at: str = Field(description="期限（ISO 8601、UTC）。")
    expires_in_seconds: int = Field(description="発行からの有効な秒数。")


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
        default=None,
        description=(
            "根拠の役割（retrieved_anchor / entity_expansion / parent_context など）。"
            "entity_expansion は、質問・検索の上位の根拠の実体（システム・部署の略号など）から"
            "実体の表で 1 段だけたどって足した根拠（台帳の行・略号の表など。#1362）。"
        ),
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
    evidence: list[RagEvidence] = Field(
        description=(
            "回答に使った根拠（used_in_answer。回答に使った順）を先に、検索で当たった根拠"
            "（role=retrieved_anchor と、実体からたどった role=entity_expansion。関連度の順）、"
            "前後の文脈（neighbor_context など）の順。実体からたどった根拠（前後の文脈の役割に"
            "なったものも含む）と、その同じ親の前後の文脈は、evidence_limit の 3 割まで上限の内に"
            "入れる。"
        )
    )
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
    impact_steps: list[str] = Field(
        default_factory=list,
        description="影響範囲と承認が係る手順の id（空なら業務ガイドのすべての場合に係る）。",
    )
    impact_applies: bool = Field(
        default=True,
        description=(
            "分かっている条件の場合に影響範囲・承認が係るか（false なら、外れた分岐の手順だけに"
            "係るので、回答に影響範囲・承認を書かない。#1320）。"
        ),
    )
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
        description=(
            "検索で当たった根拠（role=retrieved_anchor。関連度＝rerank の順）と、実体からたどった"
            "根拠（role=entity_expansion。候補の中の位置の順）を先に、前後の文脈"
            "（neighbor_context など）をその後ろに並べる。実体からたどった根拠（前後の文脈の"
            "役割になったものも含む）と、その同じ親の前後の文脈は、evidence_limit の 3 割まで"
            "上限の内に入れる。回答は作らないので used_in_answer は false。"
        )
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
    image_url: EvidenceImageUrl | None = Field(
        default=None,
        description="include_image_url=true で返した、図をブラウザで開く短命の URL（#1311）。",
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
        element_locator=str(element) if (element := chunk_element_locator(metadata)) else None,
    )


async def _with_extraction_recipe_ids(
    oracle: OracleClient | None, chunks: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """根拠の chunk の metadata に解析の結果の ID を入れる（要素の定位子を作るため。#1330）。

    chunk の行には解析の結果の ID が無く、chunk_set の列にある。読めなかったときは定位子を
    付けずに続ける（検索の結果は返す）。
    """
    chunk_set_ids = [
        value for chunk in chunks if (value := _metadata_str(chunk.metadata, "chunk_set_id"))
    ]
    if not chunk_set_ids:
        return chunks
    try:
        client = oracle if oracle is not None else OracleClient()
        extraction_ids = await client.chunk_set_extraction_recipe_ids(chunk_set_ids)
    except Exception:  # noqa: BLE001 - 定位子は補助の情報。検索の結果を止めない
        logger.warning("rag_mcp_extraction_recipe_lookup_failed", exc_info=True)
        return chunks
    enriched: list[RetrievedChunk] = []
    for chunk in chunks:
        extraction_id = extraction_ids.get(_metadata_str(chunk.metadata, "chunk_set_id") or "")
        if extraction_id is None:
            enriched.append(chunk)
            continue
        metadata = {
            "document_id": chunk.document_id,
            **chunk.metadata,
            "extraction_recipe_id": extraction_id,
        }
        enriched.append(chunk.model_copy(update={"metadata": metadata}))
    return enriched


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


# 検索で当たった chunk（関連度の順位を持つ）の役割。前後の文脈（neighbor_context など）と分ける。
RETRIEVED_ANCHOR_ROLE = "retrieved_anchor"
# 実体の 1 段の拡張で足した chunk の役割（#1362）。検索で当たった chunk と同じく順位を持つ。
ENTITY_EXPANSION_ROLE = "entity_expansion"
_RANKED_ROLES = frozenset({RETRIEVED_ANCHOR_ROLE, ENTITY_EXPANSION_ROLE})


# evidence_limit のうち、実体の 1 段の拡張で足した根拠に確保する割合（#1362）。
ENTITY_EXPANSION_LIMIT_SHARE = 0.3
# evidence_limit のうち、検索で当たった上位の chunk の前後の文脈・章の参照の先に確保する割合
# （#1390）。
LINKED_CONTEXT_LIMIT_SHARE = 0.3
# 前後の文脈・章の参照の先をたどる、検索で当たった上位の chunk の数（回答の検索で参照をたどる起点
# と同じ 5 件。``answer_engine._REFERENCE_SOURCE_ANCHORS``）。
LINKED_CONTEXT_ANCHORS = 5


def _is_entity_expansion(chunk: RetrievedChunk) -> bool:
    """実体の 1 段の拡張で足した根拠か（役割が前後の文脈になったものも含む。#1362）。"""
    metadata = chunk.metadata
    return _metadata_str(metadata, "evidence_role") == ENTITY_EXPANSION_ROLE or isinstance(
        metadata.get("entity_expansion"), dict
    )


def _rerank_score(chunk: RetrievedChunk) -> float | None:
    if chunk.rerank_score is not None:
        return chunk.rerank_score
    return _float_or_none(chunk.metadata.get("rerank_score"))


def _is_reserved_entity_expansion(chunk: RetrievedChunk, *, reranked: bool) -> bool:
    """evidence_limit の内に枠を確保する拡張の根拠か（#1362・#1390）。

    回答の文脈の枠（rag_engine の ``is_reserved_entity_expansion``）と同じ規則で、rerank の関連度
    が起点の種類（質問の実体 / 上位の chunk の実体）の下限以上のもの。質問と関係の薄い属性の
    chunk（別のシステムの台帳の行・質問と別の章）で枠を使わない。関連度の無い拡張の根拠は、
    rerank を実行した検索（``reranked``）では起点にならず前後の文脈に入ったもの（同じ親の確保
    する根拠があればその文脈として確保する）で、rerank を実行しなかった検索では確保する（#1362
    と同じ）。
    """
    if not _is_entity_expansion(chunk):
        return False
    score = _rerank_score(chunk)
    if score is None:
        return not reranked
    return entity_expansion_relevant(score, chunk.metadata.get("entity_expansion"))


def _evidence_group(chunk: RetrievedChunk) -> tuple[str, str] | None:
    """根拠の親のかたまり（文書と chunk_group_id）。無ければ None。"""
    group = _metadata_str(chunk.metadata, "chunk_group_id")
    return (chunk.document_id, group) if group else None


def _same_version(left: RetrievedChunk, right: RetrievedChunk) -> bool:
    """同じ文書の同じ版（chunk_set）の chunk か（版の分からない chunk は文書だけで比べる）。"""
    if left.document_id != right.document_id:
        return False
    left_set = _metadata_str(left.metadata, "chunk_set_id")
    right_set = _metadata_str(right.metadata, "chunk_set_id")
    return left_set is None or right_set is None or left_set == right_set


def _same_document_targets(chunk: RetrievedChunk) -> list[str]:
    """chunk が本文で参照する、同じ文書の節（見出しの列。#1280・#1382）。"""
    return [
        target.section_path
        for target in reference_targets(chunk.metadata)
        if target.document_title is None and target.section_path
    ]


def _references_between(hit: RetrievedChunk, context: RetrievedChunk) -> bool:
    """当たった chunk と前後の文脈が章の参照でつながるか（#1390）。

    当たった chunk が参照する節の chunk（「第 2 章の共通の保守枠で保守します」→ 第 2 章）、
    当たった chunk の節を参照する chunk（「連絡の手段は第 3 章を参照」→ 当たった第 3 章）、
    回答の検索が当たった chunk の参照先として足した chunk（``reference_from_chunk_id``。他の
    文書の参照も含む）。
    """
    if _metadata_str(context.metadata, REFERENCE_FROM_KEY) == hit.chunk_id:
        return True
    if not _same_version(hit, context):
        return False
    context_path = context.metadata.get("section_path")
    hit_path = hit.metadata.get("section_path")
    outgoing = any(section_path_within(context_path, path) for path in _same_document_targets(hit))
    return outgoing or any(
        section_path_within(hit_path, path) for path in _same_document_targets(context)
    )


def _adjacent(hit: RetrievedChunk, context: RetrievedChunk) -> bool:
    """同じ文書の同じ版で、当たった chunk のすぐ前・すぐ後の chunk か（chunk の番号の差が 1）。"""
    if not _same_version(hit, context):
        return False
    left = _metadata_int(hit.metadata, "chunk_index")
    right = _metadata_int(context.metadata, "chunk_index")
    return left is not None and right is not None and abs(left - right) == 1


def _linked_contexts(ordered: Sequence[RetrievedChunk], tiers: Sequence[int]) -> list[int]:
    """検索で当たった上位の chunk の章の参照の先と前後の文脈（``ordered`` の位置。#1390）。

    起点は当たった chunk（回答に使った根拠と、関連度の順位を持つ根拠。拡張の根拠は除く）の上位
    ``LINKED_CONTEXT_ANCHORS`` 件。確保する順は、先に章の参照でつながる chunk（起点の順）、次に
    すぐ前・すぐ後の chunk（起点の順）。対象は前後の文脈（3 の段）の根拠だけ。
    """
    hits = [
        chunk
        for chunk, tier in zip(ordered, tiers, strict=True)
        if tier < 2 and not _is_entity_expansion(chunk)
    ][:LINKED_CONTEXT_ANCHORS]
    contexts = [index for index, tier in enumerate(tiers) if tier == 2]
    linked: list[int] = []
    for related in (_references_between, _adjacent):
        for hit in hits:
            linked.extend(
                index for index in contexts if index not in linked and related(hit, ordered[index])
            )
    return linked


def mcp_evidence_order(
    citations: Sequence[RetrievedChunk], limit: int | None = None
) -> list[RetrievedChunk]:
    """MCP の根拠の並び（evidence_limit で切る前。#1348）。

    検索の結果（citations）は、文書ごとのかたまりの中を文書の順（前後の文脈を含む）に並べている
    （画面の検索結果・回答の文脈の並び）。そのまま上限で切ると、上位の文書の前置き・前の章で枠が
    埋まり、検索で当たった chunk が落ちる。MCP では次の順にしてから切る。

    1. 回答に使った根拠（used_in_answer）。回答に使った順（citations の順）を保つ。
    2. 検索で当たった chunk（role=retrieved_anchor と、役割の無い根拠）と、実体からたどった chunk
       （role=entity_expansion。#1362）。関連度の順位（rerank の後の順。実体の拡張は候補の中の位置。
       ``evidence_retrieval_rank``）の順で、順位が無ければ citations の順。
    3. 前後の文脈（neighbor_context / same_page_context / parent_context など）。citations の順。

    ``limit``（evidence_limit）を渡すと、上限の内に次の枠をこの順に確保する（すでに上限の内の 2 の
    根拠は動かさない。確保した根拠は 2 の後・3 の前に置き、上限の内に収まらないときは上限の末尾に
    置く）。

    - 検索で当たった上位の chunk の章の参照の先・すぐ前とすぐ後の chunk（3 の段の根拠）を、上限の
      ``LINKED_CONTEXT_LIMIT_SHARE`` の割合（最低 1 件）まで（#1390。``_linked_contexts``）。
    - 実体の 1 段の拡張で足した根拠（台帳の行・略号の表。親の文脈の役割になったものも含む）のうち、
      rerank の関連度が下限以上のもの（``_is_reserved_entity_expansion``）と、その根拠と同じ親の
      かたまり（文書と ``chunk_group_id``）の前後の文脈（略号の表の続きなど）を、上限の
      ``ENTITY_EXPANSION_LIMIT_SHARE`` の割合（最低 1 件）まで（#1362）。関連度の低い拡張の根拠は
      確保せず、2 の関連度の順位のまま。
    """

    def key(item: tuple[int, RetrievedChunk]) -> tuple[int, int, int]:
        position, chunk = item
        metadata = chunk.metadata
        if metadata.get("evidence_model_used") is True:
            return (0, 0, position)
        role = _metadata_str(metadata, "evidence_role")
        if role is not None and role not in _RANKED_ROLES:
            return (2, 0, position)
        rank = _metadata_int(metadata, "evidence_retrieval_rank")
        return (1, rank if rank is not None and rank > 0 else sys.maxsize, position)

    keyed = sorted(enumerate(citations), key=key)
    ordered = [chunk for _, chunk in keyed]
    if limit is None or limit <= 0 or len(ordered) <= limit:
        return ordered
    tiers = [key((0, chunk))[0] for chunk in ordered]
    # rerank を実行した検索か（関連度を持つ根拠がある）。
    reranked = any(_rerank_score(chunk) is not None for chunk in ordered)
    expansions = [
        index
        for index, chunk in enumerate(ordered)
        if _is_reserved_entity_expansion(chunk, reranked=reranked)
    ]
    # 拡張の根拠と同じ親のかたまりの前後の文脈（略号の表の続きなど）も、拡張の根拠の後に確保する。
    groups = {_evidence_group(ordered[index]) for index in expansions} - {None}
    expansion_set = set(expansions)
    contexts = [
        index
        for index, chunk in enumerate(ordered)
        if index not in expansion_set and tiers[index] == 2 and _evidence_group(chunk) in groups
    ]
    # 確保する根拠（上限の内の 2 の根拠はその位置のまま、それ以外は 2 の後へ移す）。
    fixed: set[int] = set()
    moved: list[int] = []
    for reserved, share in (
        (_linked_contexts(ordered, tiers), LINKED_CONTEXT_LIMIT_SHARE),
        ([*expansions, *contexts], ENTITY_EXPANSION_LIMIT_SHARE),
    ):
        candidates = [index for index in reserved if index not in fixed and index not in moved]
        quota = min(len(candidates), max(1, math.ceil(limit * share)))
        for index in candidates[:quota]:
            if index < limit and tiers[index] < 2:
                fixed.add(index)
            else:
                moved.append(index)
    if not any(index >= limit for index in moved):
        return ordered
    # 上限の内: 確保しない根拠を上限の残りの数だけ元の順で残し、2 の段（と上限の内の確保した 2 の
    # 根拠）、確保して移す根拠、残した 3 の段の順に並べる。残りは元の順で後ろへ。
    selected = fixed | set(moved)
    others = [index for index in range(len(ordered)) if index not in selected]
    kept = set(others[: max(0, limit - len(selected))])
    head = [
        index
        for index in range(len(ordered))
        if (index in fixed or index in kept) and tiers[index] < 2
    ]
    tail = [index for index in others if index in kept and tiers[index] == 2]
    after = [index for index in others if index not in kept]
    return [ordered[index] for index in (*head, *moved, *tail, *after)]


def retrieve_evidence_limit(arguments: RetrieveEvidenceInput) -> int:
    """rag_retrieve_evidence が返す根拠の件数（#1365）。

    evidence_limit を省略して top_k を既定より大きくしたときは top_k まで返す（検索で当たった
    chunk を全部返す。上限は EVIDENCE_LIMIT_MAX）。渡した evidence_limit はそのまま使う。
    """
    if "evidence_limit" in arguments.model_fields_set or arguments.top_k is None:
        return arguments.evidence_limit
    return min(max(arguments.evidence_limit, arguments.top_k), EVIDENCE_LIMIT_MAX)


def _answer_fields(result: SearchResponse, evidence_limit: int) -> dict[str, Any]:
    # 回答に使った根拠を先に、検索で当たった chunk、前後の文脈の順にする（#1348）。
    ordered = mcp_evidence_order(result.citations, evidence_limit)
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


async def crop_figure(
    oracle: OracleClient,
    chunk: RetrievedChunk,
    metadata: dict[str, Any],
    region: _ImageRegion | None,
) -> BoundedCrop:
    """図の領域を、解析に使ったファイル（処理レシピの artifact）から上限つきで切り出す（#1282）。

    MCP の content の画像（include_image）と、署名つきの URL の読み取り（#1311）で共用する。
    """
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
        return await asyncio.to_thread(
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


async def _read_image(
    oracle: OracleClient,
    chunk: RetrievedChunk,
    metadata: dict[str, Any],
    region: _ImageRegion | None,
) -> tuple[EvidenceImage, dict[str, Any]]:
    """図の領域を切り出し、大きさと MCP の content の image のブロックを返す（#1282）。"""
    crop = await crop_figure(oracle, chunk, metadata, region)
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


async def readable_chunk(oracle: OracleClient, document_id: str, chunk_id: str) -> RetrievedChunk:
    """検索と同じ見え方の条件で chunk を読み直す（見えない・古い版は区別できるエラー）。

    rag_read_source と、図の署名つきの URL の読み取り（#1311）で同じ判定を使う。
    """
    chunk = await oracle.retrievable_chunk(document_id, chunk_id)
    if chunk is not None:
        return chunk
    if await oracle.accessible_chunk_exists(document_id, chunk_id):
        raise McpToolError(
            SOURCE_STALE_CODE,
            "この根拠は文書の古い版のものです。rag_search で検索し直してください。",
        )
    raise McpToolError(
        SOURCE_NOT_FOUND_CODE,
        "根拠が見つかりません（削除されたか、利用できる範囲の外です）。",
    )


async def readable_element_chunk(
    oracle: OracleClient, document_id: str, locator: str
) -> RetrievedChunk:
    """要素の定位子が指す要素を含む今の chunk を、検索と同じ見え方の条件で読む（#1330）。"""
    parsed = parse_element_locator(locator)
    if parsed is None or parsed.document_id != document_id:
        raise McpToolError(SOURCE_NOT_FOUND_CODE, "根拠の定位子の形が違います。")
    chunk = await oracle.retrievable_element_chunk(
        document_id, parsed.extraction_recipe_id, parsed.element_id
    )
    if chunk is not None:
        metadata = {
            "document_id": chunk.document_id,
            **chunk.metadata,
            "extraction_recipe_id": parsed.extraction_recipe_id,
        }
        return chunk.model_copy(update={"metadata": metadata})
    if await oracle.accessible_document_exists(document_id):
        raise McpToolError(
            SOURCE_STALE_CODE,
            "この根拠は文書の古い解析の結果のものです。rag_search で検索し直してください。",
        )
    raise McpToolError(
        SOURCE_NOT_FOUND_CODE,
        "根拠が見つかりません（削除されたか、利用できる範囲の外です）。",
    )


def _section_parts(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(" > ") if part.strip()]
    return []


def _read_cursor(chunk_set_id: str, chunk_index: int, offset: int = 0) -> str:
    return encode_cursor({"cs": chunk_set_id, "i": chunk_index, "o": offset})


async def _first_chunk_or_missing(
    oracle: OracleClient, document_id: str, *, stale: bool
) -> McpToolError:
    """読める chunk が無いときのエラー（見える文書なら古い位置、見えなければ無い）。"""
    if stale and await oracle.accessible_document_exists(document_id):
        return McpToolError(
            SOURCE_STALE_CODE,
            "この位置は文書の古い版のものです。rag_outline か rag_search で読み直してください。",
        )
    return McpToolError(
        SOURCE_NOT_FOUND_CODE,
        "文書が見つかりません（削除されたか、利用できる範囲の外か、まだ検索の対象になっていません）。",
    )


async def outline_document(arguments: OutlineInput) -> OutlineOutput:
    """文書の節の構成（節・頁・chunk の数と、その節から読む cursor）を返す（#1332）。"""
    oracle = OracleClient()
    chunk_set_id, rows = await oracle.document_reading_index(arguments.document_id)
    if chunk_set_id is None or not rows:
        raise await _first_chunk_or_missing(oracle, arguments.document_id, stale=False)
    head = await oracle.readable_document_chunks(
        arguments.document_id,
        chunk_set_id=chunk_set_id,
        from_index=int(str(rows[0]["chunk_index"])),
        limit=1,
    )
    sections: list[OutlineSection] = []
    for row in rows:
        path = _section_parts(row.get("section_path"))
        raw_start, raw_end = row.get("page_start"), row.get("page_end")
        page_start = raw_start if isinstance(raw_start, int) else None
        page_end = raw_end if isinstance(raw_end, int) else page_start
        chars = int(str(row.get("chars") or 0))
        if sections and sections[-1].section_path == path:
            current = sections[-1]
            current.chunk_count += 1
            current.chars += chars
            if page_end is not None:
                current.page_end = max(current.page_end or page_end, page_end)
            if current.page_start is None:
                current.page_start = page_start
            continue
        sections.append(
            OutlineSection(
                section_path=path,
                page_start=page_start,
                page_end=page_end,
                chunk_count=1,
                chars=chars,
                cursor=_read_cursor(chunk_set_id, int(str(row["chunk_index"]))),
            )
        )
    pages = [value for row in rows for value in (row.get("page_start"), row.get("page_end"))]
    numbers = [value for value in pages if isinstance(value, int)]
    return OutlineOutput(
        document_id=arguments.document_id,
        file_name=head[0].file_name if head else None,
        chunk_set_id=chunk_set_id,
        chunk_count=len(rows),
        page_start=min(numbers) if numbers else None,
        page_end=max(numbers) if numbers else None,
        sections=sections[:OUTLINE_MAX_SECTIONS],
        sections_omitted=max(0, len(sections) - OUTLINE_MAX_SECTIONS),
        superseded=bool(head) and head[0].metadata.get("document_superseded") is True,
    )


async def _read_start(
    oracle: OracleClient, arguments: ReadDocumentInput
) -> tuple[str, int, int, bool]:
    """読み始めの (chunk_set_id, chunk_index, 文字の位置, 古い位置を指せるか)。"""
    if arguments.cursor is not None:
        try:
            payload = decode_cursor(arguments.cursor, required=("cs", "i", "o"))
        except InvalidCursorError as exc:
            raise McpToolError(CURSOR_INVALID_CODE, "cursor の形が違います。") from exc
        assert payload is not None
        return str(payload["cs"]), int(payload["i"]), max(0, int(payload["o"])), True
    if arguments.locator is not None:
        chunk = await readable_element_chunk(oracle, arguments.document_id, arguments.locator)
        chunk_set_id = _metadata_str(dict(chunk.metadata), "chunk_set_id") or ""
        index = _metadata_int(dict(chunk.metadata), "chunk_index") or 0
        return chunk_set_id, index, 0, True
    index_chunk_set_id, rows = await oracle.document_reading_index(arguments.document_id)
    if index_chunk_set_id is None or not rows:
        raise await _first_chunk_or_missing(oracle, arguments.document_id, stale=False)
    target = rows[0]
    if arguments.page is not None:
        page = arguments.page
        matched = [
            row
            for row in rows
            if isinstance(row.get("page_end") or row.get("page_start"), int)
            and int(str(row.get("page_end") or row.get("page_start"))) >= page
        ]
        if not matched:
            raise McpToolError(SOURCE_NOT_FOUND_CODE, f"文書に {page} 頁はありません。")
        target = matched[0]
    elif arguments.section is not None:
        wanted = _section_parts(arguments.section)
        matched = [
            row for row in rows if _section_parts(row.get("section_path"))[: len(wanted)] == wanted
        ]
        if not matched:
            raise McpToolError(SOURCE_NOT_FOUND_CODE, "その節は文書にありません。")
        target = matched[0]
    return index_chunk_set_id, int(str(target["chunk_index"])), 0, False


async def read_document(arguments: ReadDocumentInput) -> ReadDocumentOutput:
    """文書の本文を、読み始めの位置から chunk の順に max_chars まで返す（#1332）。

    頁が変わる所に「--- p.N ---」の行を入れ、返した本文に含まれる chunk（chunk_id・定位子・位置）を
    添える。続きは next_cursor で読む（cursor は chunk_set に縛り、作り直された後は source_stale）。
    """
    oracle = OracleClient()
    chunk_set_id, index, offset, from_position = await _read_start(oracle, arguments)
    budget = arguments.max_chars
    parts: list[str] = []
    length = 0
    chunks: list[ReadDocumentChunk] = []
    last_page: int | None = None
    next_cursor: str | None = None
    file_name: str | None = None
    superseded = False
    first = True
    while True:
        batch = await oracle.readable_document_chunks(
            arguments.document_id,
            chunk_set_id=chunk_set_id,
            from_index=index,
            limit=READ_DOCUMENT_BATCH,
        )
        if not batch:
            if first:
                raise await _first_chunk_or_missing(
                    oracle, arguments.document_id, stale=from_position
                )
            break
        batch = await _with_extraction_recipe_ids(oracle, batch)
        stop = False
        for chunk in batch:
            metadata = dict(chunk.metadata)
            chunk_index = _metadata_int(metadata, "chunk_index") or index
            if first:
                file_name = chunk.file_name
                superseded = metadata.get("document_superseded") is True
            page_start = _metadata_int(metadata, "page_start") or _metadata_int(
                metadata, "page_number"
            )
            marker = f"--- p.{page_start} ---\n" if page_start and page_start != last_page else ""
            body = chunk.text[offset:] if first else chunk.text
            separator = "\n\n" if parts else ""
            room = budget - length - len(separator) - len(marker)
            if room <= 0 or (len(body) > room and chunks):
                # 入りきらない chunk は次の回の先頭にする（最初の chunk だけは途中で切る）。
                next_cursor = _read_cursor(chunk_set_id, chunk_index, 0)
                stop = True
                break
            taken = body[:room]
            start = length + len(separator) + len(marker)
            parts.append(separator + marker + taken)
            length = start + len(taken)
            locator = _locator(metadata)
            chunks.append(
                ReadDocumentChunk(
                    chunk_id=chunk.chunk_id,
                    element_locator=locator.element_locator,
                    section_path=locator.section_path,
                    page_start=locator.page_start,
                    page_end=locator.page_end,
                    start=start,
                    end=length,
                )
            )
            if page_start:
                last_page = _metadata_int(metadata, "page_end") or page_start
            used = (offset if first else 0) + len(taken)
            first = False
            offset = 0
            if used < len(chunk.text):
                next_cursor = _read_cursor(chunk_set_id, chunk_index, used)
                stop = True
                break
            index = chunk_index + 1
        if stop or len(batch) < READ_DOCUMENT_BATCH:
            if not stop:
                next_cursor = None
            break
    if next_cursor is None and chunks:
        # 最後の batch がちょうど上限の件数だったとき、続きがあるかを確かめる。
        more = await oracle.readable_document_chunks(
            arguments.document_id, chunk_set_id=chunk_set_id, from_index=index, limit=1
        )
        if more:
            next_cursor = _read_cursor(chunk_set_id, index, 0)
    return ReadDocumentOutput(
        document_id=arguments.document_id,
        file_name=file_name,
        chunk_set_id=chunk_set_id,
        text="".join(parts),
        chunks=chunks,
        next_cursor=next_cursor,
        superseded=superseded,
    )


def figure_region(metadata: dict[str, Any]) -> _ImageRegion | None:
    """図の根拠の領域（保存した chunk の metadata から決める。署名つきの URL の読み取りで使う）。"""
    return _image_region(metadata)


@dataclass(frozen=True, slots=True)
class FigureUrlRequest:
    """図の署名つきの URL を作る文脈（MCP の呼び出しの利用者と、URL の起点。#1311）。"""

    subject: str
    base_url: str


def _figure_url(
    issuer: FigureUrlRequest | None,
    chunk: RetrievedChunk,
    metadata: dict[str, Any],
    region: _ImageRegion | None,
) -> EvidenceImageUrl:
    if region is None:
        raise McpToolError(
            IMAGE_NOT_AVAILABLE_CODE,
            "この根拠には元の図の画像がありません（図ではないか、図の場所が分かりません）。",
        )
    if issuer is None:
        raise McpToolError(
            IMAGE_URL_NOT_ALLOWED_CODE,
            "図を開く URL は、Agent の画面の操作からだけ作れます。",
            status=403,
        )
    try:
        token, expires_at = issue_figure_token(
            get_settings().app_service_token_secret,
            subject=issuer.subject,
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            chunk_set_id=_metadata_str(metadata, "chunk_set_id"),
        )
    except FigureUrlUnavailableError as exc:
        raise McpToolError(IMAGE_URL_UNAVAILABLE_CODE, str(exc), status=503) from exc
    path = f"{FIGURE_URL_PATH_PREFIX}/{token}"
    return EvidenceImageUrl(
        url=f"{issuer.base_url.rstrip('/')}{path}",
        path=path,
        expires_at=datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
        expires_in_seconds=FIGURE_URL_TTL_SECONDS,
    )


async def read_source(
    arguments: ReadSourceInput, *, figure_url_request: FigureUrlRequest | None = None
) -> ReadSourceOutput | McpToolResult:
    """検索と同じ見え方の条件で根拠の本文を読む（見えない・古い版は区別できるエラー）。

    図の画像（include_image）も、読むたびに同じ条件で chunk を読み直してから切り出す。
    図を開く URL（include_image_url。#1311）は、同じ条件で見える図の根拠にだけ作る。
    """
    oracle = OracleClient()
    if arguments.locator is not None:
        chunk = await readable_element_chunk(oracle, arguments.document_id, arguments.locator)
    else:
        chunk = await readable_chunk(oracle, arguments.document_id, arguments.chunk_id or "")
        chunk = (await _with_extraction_recipe_ids(oracle, [chunk]))[0]
    metadata = dict(chunk.metadata)
    text, more, next_offset = _text_window(chunk.text, arguments.offset, arguments.max_chars)
    parent = metadata.get("parent_text")
    parent_text = parent if isinstance(parent, str) and parent.strip() else None
    region = _image_region(metadata)
    image: EvidenceImage | None = None
    image_url: EvidenceImageUrl | None = None
    blocks: tuple[dict[str, Any], ...] = ()
    if arguments.include_image_url:
        image_url = _figure_url(figure_url_request, chunk, metadata, region)
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
        image_url=image_url,
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
        impact_steps=list(content.impact.steps),
        impact_applies=impact_applies(match),
        handoff_contact=content.handoff.contact,
    )


def _figure_url_request(http_request: Request) -> FigureUrlRequest | None:
    """図を開く URL（#1311）を作ってよい呼び出しなら、その利用者と URL の起点を返す。

    URL は Agent の画面の操作（Agent の backend が閲覧者を ``sub`` にし、``purpose`` に
    ``figure_url`` を入れたサービストークンで呼ぶ）でだけ作る。Run の中でモデルが呼んだときは
    作らない（URL を Run の記録・モデルの文脈に残さない）。local（認証なし）は確かめない。
    """
    principal = getattr(http_request.state, "principal", None)
    subject = getattr(principal, "user_uuid", None)
    if not isinstance(subject, str) or not subject:
        return None
    if not get_settings().local_debug_enabled:
        claims = getattr(http_request.state, "service_token_claims", None)
        if not isinstance(claims, dict) or claims.get("purpose") != FIGURE_URL_PURPOSE:
            return None
    return FigureUrlRequest(subject=subject, base_url=str(http_request.base_url))


def _check_search_scope(scope: ProfileScope | None, arguments: SearchInput) -> None:
    check_search(scope, arguments.search_answer_profile_id, arguments.knowledge_base_ids)


def _check_guides_scope(scope: ProfileScope | None, arguments: LookupGuidesInput) -> None:
    check_profile(scope, arguments.search_answer_profile_id)


def _check_validate_scope(scope: ProfileScope | None, arguments: ValidateAnswerInput) -> None:
    if arguments.guide is not None:
        check_profile(scope, arguments.guide.search_answer_profile_id)


def build_rag_mcp_server(http_request: Request) -> McpServer:
    """1 リクエスト分の MCP サーバー（rate limit に呼び出し元の request を使う）。

    handler はデータの範囲（サービストークンの claim。#1379）の判定と、範囲に絞った監査 context の
    中で実行する（`within_profile_scope`。claim が無ければ今までどおり）。
    """

    def scoped[A, R](
        handler: Callable[[A], Awaitable[R]],
        check: Callable[[ProfileScope | None, A], None] | None = None,
    ) -> Callable[[A], Awaitable[R]]:
        return within_profile_scope(http_request, handler, check)

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
        citations = await _with_extraction_recipe_ids(None, list(result.citations))
        result = result.model_copy(update={"citations": citations})
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

    async def retrieve_evidence(arguments: RetrieveEvidenceInput) -> RetrieveEvidenceOutput:
        # 検索の画面と同じく質問の理解・拡張・検索・rerank まで行い、回答（CRAG・生成）は作らない。
        request = _search_request(arguments).model_copy(update={"generate_answer": False})
        enforce_rate_limit("search", http_request)
        result = await search_route._run_search_with_timeout(request)
        limit = retrieve_evidence_limit(arguments)
        # 検索で当たった chunk を関連度の順に先に、前後の文脈を後ろにしてから切る（#1348）。
        ordered = mcp_evidence_order(result.citations, limit)
        citations = await _with_extraction_recipe_ids(None, ordered[:limit])
        return RetrieveEvidenceOutput(
            trace_id=result.trace_id,
            guardrail_warnings=list(result.guardrail_warnings),
            evidence=[_evidence(chunk) for chunk in citations],
            evidence_omitted=max(0, len(result.citations) - limit),
        )

    async def read(arguments: ReadSourceInput) -> ReadSourceOutput | McpToolResult:
        if arguments.include_image:
            # 図の切り出しは元のファイルを読んで描くので、検索と同じ上限で守る（#1282）。
            enforce_rate_limit("search", http_request)
        return await read_source(
            arguments,
            figure_url_request=(
                _figure_url_request(http_request) if arguments.include_image_url else None
            ),
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
                handler=scoped(list_search_answer_profiles),
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
                handler=scoped(search, _check_search_scope),
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
                handler=scoped(lookup_guides, _check_guides_scope),
                output_model=LookupGuidesOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_retrieve_evidence",
                description=(
                    "回答を作らずに、検索・回答プロファイルのナレッジベースから根拠（evidence。"
                    "場所・版付き）だけを返します。rag_search より速く、根拠を集める段で使います。"
                ),
                input_model=RetrieveEvidenceInput,
                handler=scoped(retrieve_evidence, _check_search_scope),
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
                handler=scoped(validate, _check_validate_scope),
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
                handler=scoped(read),
                output_model=ReadSourceOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_outline",
                description=(
                    "文書の節の構成（節の見出し・頁・chunk の数と、その節から読む cursor）を"
                    "返します。"
                    "長い文書のどこを読むかを決めるときに使います。"
                ),
                input_model=OutlineInput,
                handler=scoped(outline_document),
                output_model=OutlineOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
            McpTool(
                name="rag_read_document",
                description=(
                    "文書の本文を、頁・節・根拠の定位子・続きの cursor の位置から順に読みます"
                    "（頁の区切り付き。続きは next_cursor）。検索で当たらなかった前後の章や、"
                    "「第 3 章を参照」の先を確かめるときに使います。"
                ),
                input_model=ReadDocumentInput,
                handler=scoped(read_document),
                output_model=ReadDocumentOutput,
                permissions=(SEARCH_PERMISSIONS,),
            ),
        ],
    )
