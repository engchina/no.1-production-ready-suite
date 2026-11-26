"""Run の成果物のファイル（ツールが作ったバイナリ。#5837）。

業務 Agent のツールが作ったファイル（.xlsx / .docx / .pptx / .md）を、その実行（Run）の成果物として
残す。

- **持ち方**: Run の成果物（`Artifact`。kind は `FILE_ARTIFACT_KIND`）には、ファイル名・MIME・
  大きさ・sha256 だけを持つ（`ArtifactFile.metadata`）。中身（バイト列）は Run の JSON と分けて、
  保存先（`AGENT_RUNTIME_REPOSITORY_BACKEND`）に Run・成果物の ID で持つ（memory は repository の
  メモリ、Oracle は `AGENT_RUNTIME_ARTIFACT_FILES` の BLOB）。一覧・Run の snapshot・イベント・
  ログ・API の JSON・MCP の結果に中身（バイト列・base64）を載せない。
- **検査**: 形式は `FILE_ARTIFACT_EXTENSIONS` の 4 つだけ。大きさは設定
  `AGENT_ARTIFACT_FILE_MAX_BYTES` まで。中身は形式ごとに確かめる（OOXML の 3 つは zip の署名、
  Markdown は UTF-8 の文字。#5867）。ファイル名は安全な形に直し（`safe_file_name`）、拡張子は
  MIME から決める。合わないファイルは保存せず `ArtifactFileError`（成功にしない）。
- **読み出し**: ダウンロードの API だけが読む（Run の詳細と同じ認可）。保存した sha256 と合わない
  中身は返さない。
- **消え方**: Run の行を消すとき（`runtime_checkpoint_store.write_entities` の削除の印）に、同じ
  transaction でその Run のファイルも消す。全再作成はテーブルごと消す。
- **入口**: ツールの handler は `save_tool_file` を呼ぶ（参照だけを返す。失敗はツールのエラー）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

ARTIFACT_FILE_TABLE = "AGENT_RUNTIME_ARTIFACT_FILES"
# ファイルの成果物の種類（`Artifact.kind`）。
FILE_ARTIFACT_KIND = "file"
# Markdown のファイル（#5867。文字コードは UTF-8 だけ。ダウンロードの Content-Type にもなる）。
MARKDOWN_MIME_TYPE = "text/markdown; charset=utf-8"
# OOXML の形式（zip の入れ物。先頭の署名が違う中身は保存しない）。
_OOXML_EXTENSIONS: Mapping[str, str] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
# 保存できる形式（MIME → 拡張子）。足すときは画面の `FILE_ARTIFACT_KIND` の表示も確かめる。
FILE_ARTIFACT_EXTENSIONS: Mapping[str, str] = MappingProxyType(
    {**_OOXML_EXTENSIONS, MARKDOWN_MIME_TYPE: ".md"}
)
_ZIP_SIGNATURE = b"PK\x03\x04"
# ファイル名の長さの上限（拡張子を含む文字数）と、名前が空になったときの名前。
FILE_NAME_MAX_CHARS = 120
DEFAULT_FILE_STEM = "file"
# パス区切りと、OS がファイル名に使えない文字（1 文字ずつ `_` にする）。
_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')

FILE_TYPE_NOT_ALLOWED = "artifact.file_type_not_allowed"
FILE_TOO_LARGE = "artifact.file_too_large"
FILE_INVALID = "artifact.file_invalid"
FILE_RUN_CLOSED = "artifact.file_run_closed"
FILE_STORE_FAILED = "artifact.file_store_failed"


class ArtifactFileError(ValueError):
    """ファイルを成果物として保存できない（`code` はツールのエラーの code）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    """検査を通ったファイル（名前は安全な形。`data` は記録・表示に出さない）。"""

    file_name: str
    mime_type: str
    data: bytes = field(repr=False)
    sha256: str

    def metadata(self) -> dict[str, Any]:
        """成果物の `content`（中身は含めない）。"""
        return {
            "file_name": self.file_name,
            "mime_type": self.mime_type,
            "size_bytes": len(self.data),
            "sha256": self.sha256,
        }


def safe_file_name(name: str, mime_type: str) -> str:
    """ファイル名を安全な形に直す（拡張子は MIME から決める）。

    パス区切り・OS が使えない文字は `_` にし、制御文字・書字方向の制御などの見えない文字は除く。
    付いていた対象の形式の拡張子は外してから、MIME の拡張子を付ける。
    """
    extension = FILE_ARTIFACT_EXTENSIONS[mime_type]
    text = unicodedata.normalize("NFC", name)
    text = "".join(
        " " if char.isspace() else char
        for char in text
        if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
    )
    text = " ".join(_UNSAFE_NAME_CHARS.sub("_", text).split())
    lowered = text.lower()
    for known in FILE_ARTIFACT_EXTENSIONS.values():
        if lowered.endswith(known):
            text = text[: -len(known)]
            break
    stem = text.strip(" .")[: FILE_NAME_MAX_CHARS - len(extension)].rstrip(" .")
    return f"{stem or DEFAULT_FILE_STEM}{extension}"


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def checked_file(file_name: str, mime_type: str, data: bytes, *, max_bytes: int) -> ArtifactFile:
    """保存してよいファイルにする（形式・大きさ・名前。合わなければ `ArtifactFileError`）。"""
    mime = mime_type.strip().lower()
    if mime not in FILE_ARTIFACT_EXTENSIONS:
        raise ArtifactFileError(
            FILE_TYPE_NOT_ALLOWED,
            "この形式のファイルは保存できません（保存できるのは "
            f"{' / '.join(FILE_ARTIFACT_EXTENSIONS.values())}）。",
        )
    if len(data) > max_bytes:
        raise ArtifactFileError(
            FILE_TOO_LARGE,
            f"ファイルが大きすぎます（{len(data)} バイト。上限は {max_bytes} バイト）。"
            "内容を減らして作り直してください。",
        )
    if mime in _OOXML_EXTENSIONS and not data.startswith(_ZIP_SIGNATURE):
        raise ArtifactFileError(FILE_INVALID, "ファイルの中身が形式と合いません。")
    if mime == MARKDOWN_MIME_TYPE and not _is_utf8(data):
        raise ArtifactFileError(FILE_INVALID, "ファイルの中身が UTF-8 の文字ではありません。")
    return ArtifactFile(
        file_name=safe_file_name(file_name, mime),
        mime_type=mime,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def stored_file(content: Mapping[str, Any], data: bytes | None) -> ArtifactFile | None:
    """保存した成果物の `content` と中身から、返してよいファイル（合わなければ None）。

    形式が対象の 4 つでない・保存した sha256 と中身が合わないものは返さない。
    """
    mime, digest, name = content.get("mime_type"), content.get("sha256"), content.get("file_name")
    if data is None or not isinstance(mime, str) or mime not in FILE_ARTIFACT_EXTENSIONS:
        return None
    if not isinstance(digest, str) or hashlib.sha256(data).hexdigest() != digest:
        return None
    return ArtifactFile(
        file_name=safe_file_name(name if isinstance(name, str) else "", mime),
        mime_type=mime,
        data=data,
        sha256=digest,
    )


def attachment_disposition(file_name: str) -> str:
    """ダウンロードの `Content-Disposition`（ASCII の `filename` と RFC 5987 の `filename*`）。"""
    extension = file_name[file_name.rfind(".") :] if "." in file_name else ""
    fallback = file_name if file_name.isascii() else f"download{extension}"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name, safe='')}"


def save_tool_file(
    run_id: str | None, *, file_name: str, mime_type: str, data: bytes
) -> dict[str, Any]:
    """ツールの handler が、作ったファイルを Run の成果物として残す入口。

    成果物の参照（`artifact_id` とファイル名・MIME・大きさ・sha256）を返す。handler はこの参照を
    結果に入れて返し、中身を結果に入れない。保存できなければ `ExternalToolError`
    （`tool_registry.invoke` が失敗の結果にする。成功にしない）。
    """
    from app.features.agent.runtime import runtime_repository
    from app.features.agent.tools import ExternalToolError

    if not run_id:
        raise ExternalToolError(FILE_RUN_CLOSED, "実行の外ではファイルを保存できません。")
    try:
        artifact = runtime_repository.save_file_artifact(
            run_id, file_name=file_name, mime_type=mime_type, data=data
        )
    except ArtifactFileError as exc:
        raise ExternalToolError(exc.code, str(exc)) from exc
    return {"artifact_id": artifact.id, **artifact.content}


# ---- Oracle（`AGENT_RUNTIME_ARTIFACT_FILES`） ----------------------------------------------


def is_missing_table_error(exc: Exception) -> bool:
    text = str(exc)
    return "ORA-00942" in text or "table or view does not exist" in text


def insert_file(
    cursor: Any, checkpoint_key: str, run_id: str, artifact_id: str, file: ArtifactFile
) -> None:
    # 中身は 32,767 byte を超える。型を指定しないと bind が RAW になる。
    cursor.setinputsizes(content=import_module("oracledb").DB_TYPE_BLOB)
    cursor.execute(
        f"""
        INSERT INTO {ARTIFACT_FILE_TABLE} (
            checkpoint_key, run_id, artifact_id, size_bytes, sha256, content
        ) VALUES (
            :checkpoint_key, :run_id, :artifact_id, :size_bytes, :sha256, :content
        )
        """,  # nosec B608 - 固定の表名
        {
            "checkpoint_key": checkpoint_key,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "size_bytes": len(file.data),
            "sha256": file.sha256,
            "content": file.data,
        },
    )


def read_file(cursor: Any, checkpoint_key: str, run_id: str, artifact_id: str) -> bytes | None:
    """ファイルの中身（無ければ None）。BLOB は接続を閉じる前に読む。"""
    cursor.execute(
        f"SELECT content FROM {ARTIFACT_FILE_TABLE} "  # nosec B608 - 固定の表名
        "WHERE checkpoint_key = :checkpoint_key AND run_id = :run_id "
        "AND artifact_id = :artifact_id",
        {"checkpoint_key": checkpoint_key, "run_id": run_id, "artifact_id": artifact_id},
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    read = getattr(row[0], "read", None)
    return bytes(read() if callable(read) else row[0])


def delete_file(cursor: Any, checkpoint_key: str, run_id: str, artifact_id: str) -> None:
    cursor.execute(
        f"DELETE FROM {ARTIFACT_FILE_TABLE} "  # nosec B608 - 固定の表名
        "WHERE checkpoint_key = :checkpoint_key AND run_id = :run_id "
        "AND artifact_id = :artifact_id",
        {"checkpoint_key": checkpoint_key, "run_id": run_id, "artifact_id": artifact_id},
    )


def delete_run_files(cursor: Any, checkpoint_key: str, run_id: str) -> None:
    """消す Run のファイルを消す（Run の行の削除と同じ transaction で呼ぶ）。

    テーブルを作る前（migration 20261125_018 の適用の前）はファイルも無いので、何もしない。
    """
    try:
        cursor.execute(
            f"DELETE FROM {ARTIFACT_FILE_TABLE} "  # nosec B608 - 固定の表名
            "WHERE checkpoint_key = :checkpoint_key AND run_id = :run_id",
            {"checkpoint_key": checkpoint_key, "run_id": run_id},
        )
    except Exception as exc:
        if not is_missing_table_error(exc):
            raise


__all__ = [
    "ARTIFACT_FILE_TABLE",
    "FILE_ARTIFACT_EXTENSIONS",
    "FILE_ARTIFACT_KIND",
    "FILE_NAME_MAX_CHARS",
    "MARKDOWN_MIME_TYPE",
    "ArtifactFile",
    "ArtifactFileError",
    "attachment_disposition",
    "checked_file",
    "delete_file",
    "delete_run_files",
    "insert_file",
    "read_file",
    "safe_file_name",
    "save_tool_file",
    "stored_file",
]
