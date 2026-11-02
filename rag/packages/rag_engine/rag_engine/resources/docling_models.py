"""Docling のモデルを配布元のハッシュで検証し、原子的に修復する。"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelArtifact:
    name: str
    url: str
    digest: str
    hash_kind: str = "sha256"


def _registered_models() -> dict[str, list[ModelArtifact]]:
    # URL と hash は lock 済み RapidOCR の registry を正本にし、別の一覧を持たない。
    from omegaconf import OmegaConf
    from rapidocr.inference_engine.base import InferSession

    result: dict[str, list[ModelArtifact]] = {}

    def visit(value: object) -> None:
        if not isinstance(value, dict):
            return
        url, digest = value.get("model_dir"), value.get("SHA256")
        if isinstance(url, str) and isinstance(digest, str):
            parsed = urlsplit(url)
            name = Path(parsed.path).name
            if parsed.scheme == "https" and len(digest) == 64:
                spec = ModelArtifact(name, url, digest.lower())
                if spec not in result.setdefault(name, []):
                    result[name].append(spec)
        for child in value.values():
            visit(child)

    visit(OmegaConf.to_container(InferSession.model_info, resolve=True))
    return result


def _digest(path: Path, hash_kind: str = "sha256") -> str:
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


def _replace_verified(path: Path, spec: ModelArtifact) -> None:
    for attempt in range(3):
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                with urlopen(spec.url, timeout=60) as response:  # noqa: S310 - 導入済み registry の HTTPS URL のみ
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if _digest(temporary, spec.hash_kind) != spec.digest:
                raise ValueError("ダウンロードした モデルの SHA-256 が一致しません。")
            os.replace(temporary, path)
            logger.info("モデルの検証と修復が完了しました: %s", path.name)
            return
        except (OSError, ValueError, IncompleteRead) as exc:
            if attempt == 2:
                raise RuntimeError(f"モデルの修復に失敗しました: {path.name}。モデル取得先への接続と保存先を確認してください。") from exc
            delay = 2**attempt
            time.sleep(delay + random.uniform(0, delay))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _verify_files(entries: list[tuple[Path, list[ModelArtifact]]], lock_path: Path, *, repair: bool) -> list[str]:
    repaired: list[str] = []
    for path, specs in entries:
        if not specs or any(_digest(path, spec.hash_kind) == spec.digest for spec in specs):
            continue
        if not repair:
            raise RuntimeError(f"モデルのハッシュが一致しません: {path.name}。モデル取得処理を再実行してください。")
        if len(specs) != 1:
            raise RuntimeError(f"モデルの取得先を一意に決定できません: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # 同時修復を直列化し、検証済みのファイルだけを公開する。
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if _digest(path, specs[0].hash_kind) == specs[0].digest:
                continue
            logger.warning("モデルの破損または欠落を検出しました: %s", path.name)
            _replace_verified(path, specs[0])
            repaired.append(specs[0].name)
    logger.info("モデルのハッシュ検証が完了しました: %s files", len(entries))
    return repaired


def verify_rapidocr_cache(model_dir: Path, *, repair: bool = True) -> list[str]:
    """登録モデルの全形式を検証する。独自モデルや hash の無い補助ファイルは変更しない。"""
    files = sorted(path for path in model_dir.glob("*") if path.is_file() and not path.name.startswith("."))
    if not files:
        return []
    registry = _registered_models()
    entries = [(path, registry[path.name]) for path in files if path.name in registry]
    for path in files:
        if path.name not in registry:
            logger.warning("公式 hash が無いため検証対象外の補助／独自ファイル: %s", path.name)
    return _verify_files(entries, model_dir / ".integrity.lock", repair=repair)


def _hf_url(repo: str, filename: str, revision: str) -> str:
    from huggingface_hub import hf_hub_url

    return str(hf_hub_url(repo, filename, revision=revision))


def _hf_entries(repo_dir: Path) -> list[tuple[Path, list[ModelArtifact]]]:
    metadata_root = repo_dir / ".cache/huggingface/download"
    entries: list[tuple[Path, list[ModelArtifact]]] = []
    known: set[Path] = set()
    for metadata in sorted(metadata_root.rglob("*.metadata")):
        relative = metadata.relative_to(metadata_root).as_posix().removesuffix(".metadata")
        lines = metadata.read_text(encoding="utf-8").splitlines()
        if len(lines) < 2:
            raise RuntimeError(f"モデルの配布元 metadata が不足しています: {repo_dir.name}/{relative}")
        revision, digest = lines[:2]
        if len(revision) not in (40, 64) or len(digest) not in (40, 64) or any(c not in "0123456789abcdef" for c in revision + digest):
            raise RuntimeError(f"モデルの配布元 metadata が不正です: {repo_dir.name}/{relative}")
        path = repo_dir / relative
        spec = ModelArtifact(
            relative, _hf_url(repo_dir.name.replace("--", "/"), relative, revision), digest, "sha256" if len(digest) == 64 else "git-sha1"
        )
        entries.append((path, [spec]))
        known.add(path)
    for path in repo_dir.rglob("*"):
        relative_path = path.relative_to(repo_dir)
        if path.is_file() and ".cache" not in relative_path.parts and path.name != ".integrity.lock" and path not in known:
            raise RuntimeError(
                f"モデルの公式 hash が記録されていません: {repo_dir.name}/{relative_path}。モデル取得処理を再実行してください。"
            )
    return entries


def verify_docling_cache(model_root: Path, *, repair: bool = True, include_ocr: bool = True) -> list[str]:
    """OCR・layout・table など、取得済みのモデルを同じ版の配布元 hash と照合する。"""
    repaired = verify_rapidocr_cache(model_root / "RapidOcr", repair=repair) if include_ocr else []
    if not model_root.exists():
        raise RuntimeError("モデルの保存先がありません。モデル取得処理を実行してください。")
    for repo in sorted(model_root.iterdir()):
        if not repo.is_dir() or repo.name == "RapidOcr" or repo.name.startswith("."):
            continue
        entries = _hf_entries(repo)
        repaired.extend(_verify_files(entries, repo / ".integrity.lock", repair=repair))
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="モデルを変更せず検証だけ行う")
    mode.add_argument("--repair", action="store_true", help="既存 モデルだけを検証・修復する")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from docling.datamodel.settings import settings

    model_root = settings.artifacts_path or settings.cache_dir / "models"
    cache = Path(model_root) / "RapidOcr"
    if args.check or args.repair:
        verify_docling_cache(Path(model_root), repair=not args.check)
    else:
        verify_rapidocr_cache(cache)
        subprocess.run([str(Path(sys.executable).with_name("docling-tools")), "models", "download"], check=True)
        verify_docling_cache(Path(model_root))


if __name__ == "__main__":
    main()
