"""`backend/.env` の排他付き部分更新。

NL2SQL の app/env_file.py と settings router から移設した（#97）。
"""

from __future__ import annotations

import fcntl
import re
import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

DEFAULT_ENV_FILE_MODE = 0o600
ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[Path, threading.RLock] = {}


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _process_lock_for(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(path, threading.RLock())


@contextmanager
def locked_env_file(path: Path) -> Iterator[Path]:
    """同一 `.env` の read-modify-replace 全体を process/thread 間で直列化する。"""
    env_path = _canonical_path(path)
    process_lock = _process_lock_for(env_path)
    with process_lock:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = env_path.with_name(f".{env_path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            with suppress(OSError):
                lock_path.chmod(DEFAULT_ENV_FILE_MODE)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield env_path
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def replace_env_file(
    path: Path,
    content: str,
    *,
    default_mode: int = DEFAULT_ENV_FILE_MODE,
) -> None:
    """同一ディレクトリの一時ファイルから `.env` を atomic replace する（既存の権限を保つ）。"""
    env_path = _canonical_path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(env_path.stat().st_mode) if env_path.exists() else default_mode
    tmp_path = env_path.with_name(f".{env_path.name}.tmp-{uuid4().hex}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.chmod(mode)
        tmp_path.replace(env_path)
        env_path.chmod(mode)
    finally:
        with suppress(OSError):
            tmp_path.unlink()


def env_assignment_key(line: str) -> str | None:
    """通常の .env 代入行から key を取り出す。コメント行は対象外。"""
    if line.lstrip().startswith("#"):
        return None
    match = ENV_ASSIGNMENT_RE.match(line)
    return match.group(1) if match else None


def format_env_value(value: str) -> str:
    """python-dotenv と shell の両方で読みやすい .env value へ整形する。"""
    normalized = value.strip()
    if not normalized:
        return ""
    if re.search(r"[\s#\"']", normalized):
        return '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return normalized


def write_env_values(
    path: Path,
    values: Mapping[str, str | None],
    *,
    section_comment: str,
) -> None:
    """既存 .env のコメントや無関係な値を保ったまま、指定 key だけ更新する。

    value が None の key は削除する。新しい key は `section_comment` の下に追記する。
    書込みに失敗した場合は OSError をそのまま送出する（呼出側で API エラーへ変換する）。
    """
    with locked_env_file(path) as env_path:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        next_lines: list[str] = []
        written: set[str] = set()
        for line in lines:
            key = env_assignment_key(line)
            if key is None or key not in values:
                next_lines.append(line)
                continue
            if key in written:
                continue
            value = values[key]
            written.add(key)
            if value is None:
                continue
            next_lines.append(f"{key}={format_env_value(value)}")

        missing = [key for key, value in values.items() if key not in written and value is not None]
        if missing:
            if next_lines and next_lines[-1].strip():
                next_lines.append("")
            next_lines.append(section_comment)
            for key in missing:
                value = values[key]
                if value is not None:
                    next_lines.append(f"{key}={format_env_value(value)}")

        content = "\n".join(next_lines).rstrip() + "\n"
        replace_env_file(env_path, content)
