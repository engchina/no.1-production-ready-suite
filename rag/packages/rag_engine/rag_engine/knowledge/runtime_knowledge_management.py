"""用語・同義語の管理の検証、競合検出、バックアップ付き保存を扱う。"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rag_engine.knowledge.runtime_knowledge import (
    MAX_ALIASES_PER_TERM,
    MAX_TERMS,
    MAX_TEXT_CHARS,
    RUNTIME_KNOWLEDGE_ACTIVE_STATUSES,
    runtime_knowledge_path,
)


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """編集開始時の設定パス・内容・リビジョンを保持する。内容は保存時にコピーする。"""

    path: Path
    payload: dict[str, Any]
    revision: str


def _revision(raw: bytes | None) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else "missing"


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _validate_payload(payload: Any) -> None:
    """暗黙の行破棄を避けるため、管理可能な標準形式だけを書込対象にする。"""
    if not isinstance(payload, dict) or payload.get("schema_version", 1) != 1:
        raise ValueError("管理画面は schema_version: 1 の JSON オブジェクトに対応しています。既存ファイルは変更していません。")
    if any(key in payload for key in ("glossary", "glossaries")):
        raise ValueError("旧形式の設定です。terms の標準形式へ移行してから再読込してください。回答生成の旧形式読込は継続できます。")
    rows = payload.get("terms", [])
    if not isinstance(rows, list) or len(rows) > MAX_TERMS:
        raise ValueError(f"terms は {MAX_TERMS} 件以内の配列で指定してください。")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("term"), str) or not _key(row["term"]):
            raise ValueError("terms の各行には term が必要です。標準形式を確認してください。")
        key = _key(row["term"])
        if key in seen:
            raise ValueError("terms の term が重複しています。既存ファイルを確認してください。")
        seen.add(key)
        legacy_fields = (
            "synonyms",
            "abbreviations",
            "abbrev",
            "alias",
            "definition",
            "body",
            "note",
            "document",
            "path",
            "lifecycle_status",
        )
        if any(field in row for field in legacy_fields):
            raise ValueError(
                "terms に旧形式の項目があります。標準フィールドへ移行してから再読込してください。既存ファイルは変更していません。"
            )
        if any(field in row and not isinstance(row[field], str) for field in ("term", "source", "status", "description")):
            raise ValueError("terms のテキスト項目は文字列で指定してください。")
        values = row.get("aliases", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("aliases は文字列の配列で指定してください。")


def load_knowledge_snapshot(output_dir: Path, configured_path: Path | None = None) -> KnowledgeSnapshot:
    """回答生成と同じパスから編集用コピーを読む。破損・非対応形式は ValueError、I/O は OSError。"""
    path = runtime_knowledge_path(output_dir, configured_path).resolve()
    raw = _read_bytes(path)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw is not None else {"schema_version": 1, "terms": []}
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("設定ファイルを読み込めません。JSON の文字コード・構文を確認してください。上書きは行いません。") from exc
    _validate_payload(payload)
    return KnowledgeSnapshot(path, payload, _revision(raw))


def _text(value: str, label: str, *, required: bool = False, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
    subject = label
    if required and not text:
        raise ValueError(f"{subject}を入力してください。")
    if len(text) > limit:
        raise ValueError(f"{subject}は {limit} 文字以内で入力してください（自動切り詰めはしません）。")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{subject}に制御文字は使用できません。")
    return text


def _labels(value: str, label: str, limit: int) -> list[str]:
    """UI の改行・カンマ区切りを正規化し、同一照合キーの重複を除く。切り詰めはしない。"""
    result, seen = [], set()
    for part in re.split(r"[\n,、，]", str(value or "")):
        text = _text(part, label, limit=160)
        if text and _key(text) not in seen:
            result.append(text)
            seen.add(_key(text))
    if len(result) > limit:
        raise ValueError(f"{label}は {limit} 件以内で入力してください。")
    return result


def edit_knowledge(
    snapshot: KnowledgeSnapshot,
    selected: str | None,
    *,
    name: str = "",
    labels: str = "",
    content: str = "",
    source: str = "",
    enabled: bool = True,
    delete: bool = False,
    confirmed: bool = False,
) -> tuple[KnowledgeSnapshot, Path | None]:
    """選択した用語を更新・削除し、未選択なら追加する。未知のメタデータと他行を保持する。

    Linux のファイルロックと SHA-256 により管理画面間の上書きを拒否する。
    既存内容を同じ親ディレクトリの runtime_knowledge_backups に保存してから原子的に置換する。
    入力・競合は ValueError、書込失敗は OSError。削除は confirmed=True が必須。
    外部エディタはロックに協調しないため、管理画面との同時編集を避けること。
    """
    payload = copy.deepcopy(snapshot.payload)
    rows = payload.setdefault("terms", [])
    index = next((i for i, row in enumerate(rows) if row["term"] == selected), None)
    if selected and index is None:
        raise ValueError("選択項目がありません。一覧を再読込してください。")
    if delete:
        if index is None or not confirmed:
            raise ValueError("削除対象を選択し、削除確認にチェックしてください。")
        rows.pop(index)
    else:
        row = copy.deepcopy(rows[index]) if index is not None else {}
        name = _text(name, "用語", required=True, limit=160)
        if any(_key(other["term"]) == _key(name) for i, other in enumerate(rows) if i != index):
            raise ValueError("同じ用語が登録済みです。編集対象を選択してください。")
        row["term"] = name
        row["source"] = _text(source, "出典")
        row["aliases"] = _labels(labels, "別名", MAX_ALIASES_PER_TERM)
        row["description"] = _text(content, "説明")
        # 既存の承認・再確認・下書き状態は、利用可否を変更した場合だけ置き換える。
        if index is None or bool(enabled) != (row.get("status", "") in RUNTIME_KNOWLEDGE_ACTIVE_STATUSES):
            row["status"] = "approved" if enabled else "deprecated"
        row["updated_at"] = datetime.now(UTC).isoformat()
        if index is None:
            rows.append(row)
        else:
            rows[index] = row
    _validate_payload(payload)
    path = snapshot.path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = _read_bytes(path)
        if _revision(old) != snapshot.revision:
            raise ValueError("他の操作で設定が更新されています。一覧を再読込してから編集してください。未保存の入力は保持しています。")
        backup = None
        if old is not None:
            directory = path.parent / "runtime_knowledge_backups"
            directory.mkdir(parents=True, exist_ok=True)
            backup = directory / f"{path.stem}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{uuid4().hex}.json"
            with backup.open("xb") as stream:
                os.chmod(backup, 0o600)
                stream.write(old)
                stream.flush()
                os.fsync(stream.fileno())
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
                temp_path = Path(stream.name)
                if path.exists():
                    os.chmod(temp_path, path.stat().st_mode & 0o777)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return KnowledgeSnapshot(path, payload, _revision(raw)), backup
