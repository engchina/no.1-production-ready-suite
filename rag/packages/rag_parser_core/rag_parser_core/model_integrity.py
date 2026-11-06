"""parser のモデルのファイルを配布元のハッシュで検証し、原子的に取得し直す（標準ライブラリだけ）。

parser-docling（``rag_engine.resources.docling_models``）と parser-unstructured
（``rag_parser_core.unstructured_models``）が共有する。どのファイルを検証するか（配布元の URL と
ハッシュの一覧）は各 parser が決め、ここは検証・取得・公開の手順だけを持つ。
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import random
import shutil
import tempfile
import time
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from urllib.request import urlopen

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelArtifact:
    """配布元の 1 ファイル。digest は hash_kind（sha256 か git の blob の sha1）の 16 進。"""

    name: str
    url: str
    digest: str
    hash_kind: str = "sha256"


def file_digest(path: Path, hash_kind: str = "sha256") -> str:
    """ファイルのハッシュ。無ければ空文字。"""
    try:
        with path.open("rb") as stream:
            if hash_kind == "git-sha1":
                digest = hashlib.sha1(usedforsecurity=False)
                digest.update(f"blob {path.stat().st_size}\0".encode())
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                return digest.hexdigest()
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        return ""


def replace_verified(path: Path, spec: ModelArtifact) -> None:
    """取得した内容を同じディレクトリの一時ファイルで検証し、一致したときだけ置き換える。"""
    for attempt in range(3):
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False
            ) as output:
                temporary = Path(output.name)
                # 各 parser が配布元として固定した HTTPS の URL だけを渡す。
                with urlopen(spec.url, timeout=60) as response:  # noqa: S310
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if file_digest(temporary, spec.hash_kind) != spec.digest:
                raise ValueError("ダウンロードした モデルの SHA-256 が一致しません。")
            os.replace(temporary, path)
            logger.info("モデルの検証と修復が完了しました: %s", spec.name)
            return
        except (OSError, ValueError, IncompleteRead) as exc:
            if attempt == 2:
                raise RuntimeError(
                    f"モデルの修復に失敗しました: {spec.name}。"
                    "モデル取得先への接続と保存先を確認してください。"
                ) from exc
            delay = 2**attempt
            time.sleep(delay + random.uniform(0, delay))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def verify_files(
    entries: list[tuple[Path, list[ModelArtifact]]], lock_path: Path, *, repair: bool
) -> list[str]:
    """各ファイルを配布元のハッシュと照合する。

    repair なら欠落・破損を取得し直し、直した名前を返す。
    """
    repaired: list[str] = []
    for path, specs in entries:
        if not specs or any(file_digest(path, spec.hash_kind) == spec.digest for spec in specs):
            continue
        if not repair:
            raise RuntimeError(
                f"モデルのハッシュが一致しません: {specs[0].name}。"
                "モデル取得処理を再実行してください。"
            )
        if len(specs) != 1:
            raise RuntimeError(f"モデルの取得先を一意に決定できません: {specs[0].name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # 同時修復を直列化し、検証済みのファイルだけを公開する。
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if file_digest(path, specs[0].hash_kind) == specs[0].digest:
                continue
            logger.warning("モデルの破損または欠落を検出しました: %s", specs[0].name)
            replace_verified(path, specs[0])
            repaired.append(specs[0].name)
    logger.info("モデルのハッシュ検証が完了しました: %s files", len(entries))
    return repaired
