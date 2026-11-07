"""API の失敗のうち、画面が `error_code` で見分ける必要があるもの。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import HTTPException
from pr_system_settings.model import EnterpriseAiModelNotRegisteredError

from app.rag.answer_failure import DOCUMENT_REPROCESS_REQUIRED_CODE

# 文書の原本・処理後ファイルが保存先に無い（保存先の設定の変更・削除など。#1210）。
# 画面のプレビューは、この code のときだけ iframe の表示に戻さず理由と対処を出す。
DOCUMENT_FILE_MISSING_CODE = "RAG_DOCUMENT_FILE_MISSING"
# 文書は見られるが、指定の根拠の版が今は無い（再索引で chunk が作り直された。古い chunk_set・
# cursor を含む）。MCP の `rag_read_source` と同じ code。画面はエラーではなく案内を出す（#4092）。
SOURCE_STALE_CODE = "source_stale"
# DB の読み取りの失敗（#4217）。画面は「0 件」ではなく失敗として出し、code で原因の文を分ける。
# 値は 3 製品で共通（`@production-ready/ui` の `dataLoadFailureKind`）。
DATABASE_READ_TIMEOUT_CODE = "database_read_timeout"
DATABASE_UNAVAILABLE_CODE = "database_unavailable"
DATABASE_READ_FAILED_CODE = "database_read_failed"
# 同じ読み取りをすぐ繰り返しても混雑を増やすだけなので、次に試すまでの目安を返す（秒）。
DATABASE_READ_RETRY_AFTER_SECONDS = 5


class CodedApiError(HTTPException):
    """応答の envelope に `error_code` を付ける失敗（`app.main` の共通の handler が返す）。"""

    code: str


class DocumentRecipeConfigInvalidError(CodedApiError):
    """保存済みの処理レシピ設定が現在の入力契約に合わない（422）。"""

    code = "DOCUMENT_RECIPE_CONFIG_INVALID"

    def __init__(self, fields: Sequence[str] = ()) -> None:
        suffix = f"（項目: {', '.join(fields)}）" if fields else ""
        super().__init__(
            status_code=422,
            detail=(
                "保存済みの処理レシピ設定が現在の形式に合いません。"
                "処理レシピの設定を現在の選択肢で保存し直してください。" + suffix
            ),
        )


class AnswerModelNotRegisteredError(CodedApiError):
    """要求で明示した回答のモデルが登録されていない（422。#4582）。

    既定のモデルへ暗黙に切り替えない（#3240）。code と文は共通のモデル設定の失敗と同じ。
    """

    code = EnterpriseAiModelNotRegisteredError.code

    def __init__(self, model_id: str) -> None:
        super().__init__(status_code=422, detail=str(EnterpriseAiModelNotRegisteredError(model_id)))


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


class DocumentReprocessRequiredError(CodedApiError):
    """保存済みの chunk が旧い形式で、文書の再解析・再分割が要る（409。#4550）。

    ``message`` は対象の文書の表示名と対処を含む利用者向けの文。
    """

    code = DOCUMENT_REPROCESS_REQUIRED_CODE

    def __init__(self, message: str) -> None:
        super().__init__(status_code=409, detail=message)


class DatabaseReadTimeoutError(CodedApiError):
    """読み取りが時間の上限に達した（503）。DB は混んでいるだけかもしれない。"""

    code = DATABASE_READ_TIMEOUT_CODE

    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail="データの読み取りがタイムアウトしました。データは失われていません。",
            headers={"Retry-After": str(DATABASE_READ_RETRY_AFTER_SECONDS)},
        )


class DatabaseUnavailableError(CodedApiError):
    """DB に接続できない（503）。"""

    code = DATABASE_UNAVAILABLE_CODE

    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail="データベースに接続できませんでした。データは失われていません。",
            headers={"Retry-After": str(DATABASE_READ_RETRY_AFTER_SECONDS)},
        )


class DatabaseReadFailedError(CodedApiError):
    """接続・時間の上限以外の理由で読み取りが失敗した（500）。"""

    code = DATABASE_READ_FAILED_CODE

    def __init__(self) -> None:
        super().__init__(
            status_code=500,
            detail="データを読み取れませんでした。データは失われていません。",
        )
