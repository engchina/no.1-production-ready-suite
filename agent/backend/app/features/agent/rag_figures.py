"""Run の RAG の図の根拠を画面で開く、短命の URL（#1311）。

Agent の画面（ブラウザ）は RAG の Cookie を持たないため、図の根拠を開くときは Agent の backend が
RAG の MCP の ``rag_read_source``（``include_image_url``）を呼び、RAG が作る短命・対象（利用者・
chunk・版）に縛った署名つきの URL を受け取る。画面はその URL で図を開く（RAG が読むたびに今の
権限・版を確かめ直す）。

- 呼ぶのは組み込みの接続 ``rag``（サービストークン）だけ。``sub`` は **画面を見ている利用者**
  （Run の利用者ではない。Run を見られる管理者が、Run の利用者の権限を借りて図を開けないように
  する）。サービストークンに ``purpose=figure_url`` を入れ、RAG は Run の中でモデルが呼んだときには
  URL を作らない（URL を Run の記録・モデルの文脈に残さない）。
- 画面の操作で、Run のツール呼び出しではないので ``tool_registry.invoke`` を通さない（承認・
  ガードレール・成果物の対象ではない。URL は保存しない）。
- 開けるのは、その Run の RAG の根拠（``rag_evidence`` の成果物）に ``image_ref`` がある図だけ。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field

from app.features.agent.config import runtime_config_store
from app.features.agent.runtime import Artifact, RunState
from app.features.agent.tools import (
    MCP_TOOL_SEPARATOR,
    ExternalToolError,
    McpConnectionClient,
    ToolInvocationContext,
)
from app.settings import get_settings

logger = logging.getLogger(__name__)

RAG_CONNECTION_ID = "rag"
RAG_EVIDENCE_KIND = "rag_evidence"
READ_SOURCE_TOOL = "rag_read_source"
# RAG が URL を作ってよい呼び出し（画面の操作）の印（RAG の `FIGURE_URL_PURPOSE` と同じ値）。
FIGURE_URL_PURPOSE = "figure_url"
# RAG の図の読み取りの path（`GET /api/figures/{token}`）。公開の起点に付け替えるときに確かめる。
FIGURE_PATH_PREFIX = "/api/figures/"

NOT_IN_RUN_MESSAGE = "この実行の根拠に、その図はありません。"
NOT_SUPPORTED_MESSAGE = "この接続の根拠の図は開けません。"
STALE_MESSAGE = "資料が更新されたため、この図は開けません。もう一度質問してください。"
NOT_FOUND_MESSAGE = "図が見つからないか、この図を見る権限がありません。"
NOT_AVAILABLE_MESSAGE = "この根拠には開ける図がありません。"
UNAVAILABLE_MESSAGE = "RAG の設定が足りないため、図を開けません。管理者に連絡してください。"
FAILED_MESSAGE = "RAG から図を開く URL を取得できませんでした。時間をおいて再度お試しください。"

# RAG の MCP のエラー（`rag_read_source`）→ 画面へ返す status と文言。
_TOOL_ERRORS: dict[str, tuple[int, str]] = {
    "source_stale": (409, STALE_MESSAGE),
    "source_not_found": (404, NOT_FOUND_MESSAGE),
    "image_not_available": (404, NOT_AVAILABLE_MESSAGE),
    "image_url_unavailable": (503, UNAVAILABLE_MESSAGE),
    "MCP_TOOL_FORBIDDEN": (403, NOT_FOUND_MESSAGE),
}


class RunFigureUrlData(BaseModel):
    """図を開く短命の URL（画面は保存しない。期限が切れたら取り直す）。"""

    url: str = Field(description="図の画像の URL（RAG。期限つき）。")
    expires_at: str = Field(description="URL の期限（ISO 8601、UTC）。")


class RunFigureUrlError(Exception):
    """画面へ返す失敗（status と日本語の文言）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _is_rag_evidence(artifact: Artifact) -> bool:
    # 成果物の名前は `<接続>__rag_search:<step>`。組み込みの接続 `rag` の根拠だけを対象にする。
    return artifact.kind == RAG_EVIDENCE_KIND and artifact.name.startswith(
        f"{RAG_CONNECTION_ID}{MCP_TOOL_SEPARATOR}"
    )


def run_has_figure_evidence(artifacts: list[Artifact], *, document_id: str, chunk_id: str) -> bool:
    """Run の RAG の根拠に、その図（``image_ref`` がある根拠）があるか。"""
    for artifact in artifacts:
        if not _is_rag_evidence(artifact):
            continue
        evidence = artifact.content.get("evidence")
        for item in evidence if isinstance(evidence, list) else []:
            if (
                isinstance(item, dict)
                and item.get("document_id") == document_id
                and item.get("chunk_id") == chunk_id
                and isinstance(item.get("image_ref"), dict)
            ):
                return True
    return False


def _browser_url(image_url: dict[str, Any]) -> str:
    """画面が開く URL。公開の起点（AGENT_EXTERNAL_RAG_PUBLIC_URL）があれば path をそこに付ける。"""
    path = image_url.get("path")
    url = image_url.get("url")
    public_base = (get_settings().agent_external_rag_public_url or "").strip()
    if public_base and isinstance(path, str) and path.startswith(FIGURE_PATH_PREFIX):
        candidate = f"{public_base.rstrip('/')}{path}"
    elif isinstance(url, str):
        candidate = url
    else:
        raise RunFigureUrlError(502, FAILED_MESSAGE)
    parts = urlsplit(candidate)
    if _is_same_origin_prefix(public_base) and candidate.startswith(public_base.rstrip("/") + "/"):
        # 1 台の Compute（#1316）: 起点は同じ origin の path（例 `/rag`）。ブラウザが Agent の
        # 画面と同じ origin で解決するので host を持たない。起点の後ろは図の path だけを許す。
        figure_path = candidate[len(public_base.rstrip("/")) :]
        if (
            not figure_path.startswith(FIGURE_PATH_PREFIX)
            or parts.query
            or parts.fragment
            or parts.scheme
            or parts.netloc
            or "/../" in figure_path
        ):
            raise RunFigureUrlError(502, FAILED_MESSAGE)
        return candidate
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise RunFigureUrlError(502, FAILED_MESSAGE)
    if not parts.path.startswith(FIGURE_PATH_PREFIX) or parts.query or parts.fragment:
        raise RunFigureUrlError(502, FAILED_MESSAGE)
    return candidate


def _is_same_origin_prefix(public_base: str) -> bool:
    """公開の起点が同じ origin の path（`/rag` など。`//host` ではない）か。"""
    return public_base.startswith("/") and not public_base.startswith("//")


def issue_run_figure_url(
    run: RunState,
    artifacts: list[Artifact],
    *,
    document_id: str,
    chunk_id: str,
    viewer_user_uuid: str,
) -> RunFigureUrlData:
    """画面を見ている利用者として RAG に図の URL を作らせる（同期。thread で呼ぶ）。"""
    if not run_has_figure_evidence(artifacts, document_id=document_id, chunk_id=chunk_id):
        raise RunFigureUrlError(404, NOT_IN_RUN_MESSAGE)
    try:
        config = runtime_config_store.get_mcp(RAG_CONNECTION_ID)
    except KeyError:
        raise RunFigureUrlError(409, NOT_SUPPORTED_MESSAGE) from None
    if config.effective_auth_mode() != "service_token":
        raise RunFigureUrlError(409, NOT_SUPPORTED_MESSAGE)
    trace_id = f"figure_{uuid4().hex}"
    client = McpConnectionClient(
        config,
        context=ToolInvocationContext(
            trace_id=trace_id,
            agent_id=run.agent_id,
            run_id=run.id,
            user_uuid=viewer_user_uuid,
        ),
        extra_claims={"purpose": FIGURE_URL_PURPOSE},
    )
    try:
        output = client.call_tool(
            READ_SOURCE_TOOL,
            {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "include_image_url": True,
                "max_chars": 1,
            },
            idempotent=True,
            trace_id=trace_id,
        )
    except ExternalToolError as exc:
        error_code = exc.details.get("error_code")
        status, message = _TOOL_ERRORS.get(str(error_code), (502, FAILED_MESSAGE))
        logger.warning(
            "agent_run_figure_url_failed",
            extra={"run_id": run.id, "error_code": error_code or exc.code},
        )
        raise RunFigureUrlError(status, message) from None
    image_url = output.get("image_url")
    if not isinstance(image_url, dict):
        raise RunFigureUrlError(502, FAILED_MESSAGE)
    expires_at = image_url.get("expires_at")
    # URL（トークン）はログに残さない。
    logger.info("agent_run_figure_url_issued", extra={"run_id": run.id})
    return RunFigureUrlData(
        url=_browser_url(image_url),
        expires_at=expires_at if isinstance(expires_at, str) else "",
    )
