"""API の失敗のうち、画面が `error_code` で見分ける必要があるもの。"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException

# 文書の原本・処理後ファイルが保存先に無い（保存先の設定の変更・削除など。#1210）。
# 画面のプレビューは、この code のときだけ iframe の表示に戻さず理由と対処を出す。
DOCUMENT_FILE_MISSING_CODE = "RAG_DOCUMENT_FILE_MISSING"
# 文書は見られるが、指定の根拠の版が今は無い（再索引で chunk が作り直された。古い chunk_set・
# cursor を含む）。MCP の `rag_read_source` と同じ code。画面はエラーではなく案内を出す（#4092）。
SOURCE_STALE_CODE = "source_stale"


class CodedApiError(HTTPException):
    """応答の envelope に `error_code` を付ける失敗（`app.main` の共通の handler が返す）。"""

    code: str


class DocumentFileMissingError(CodedApiError):
    """文書のファイルが保存先に無い（404）。"""

    code = DOCUMENT_FILE_MISSING_CODE

    def __init__(self, message: str) -> None:
        super().__init__(status_code=404, detail=message)


class SourceVersionStaleError(CodedApiError):
    """見られる文書の、引用した版を表示できない（409）。"""

    code = SOURCE_STALE_CODE

    def __init__(self, message: str, *, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(status_code=409, detail=message, headers=dict(headers or {}) or None)
