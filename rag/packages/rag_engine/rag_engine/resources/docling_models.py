"""Docling のモデルを配布元のハッシュで検証し、原子的に修復する。

取得と修復は配備（このモジュールの main。``rag_service_model_command``）だけが行う。解析の要求の時は
``check_docling_cache`` で検証だけを行い、取得・修復はしない（#4557）。
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

# 検証・原子的な取得の手順は parser-unstructured と共有する（rag_parser_core.model_integrity）。
from rag_parser_core.model_integrity import ModelArtifact, ModelsNotDeployedError
from rag_parser_core.model_integrity import verify_files as _verify_files

logger = logging.getLogger(__name__)

_REDEPLOY = "解析の要求の時にはモデルを取得しません。parser-docling を配備し直してください。"

# 解析の要求の時の検証の結果（キャッシュの場所と OCR の有無 → ファイルの一覧・大きさ・更新時刻と、失敗の文）。
_checked: dict[tuple[Path, bool], tuple[tuple[tuple[str, int, int], ...], str | None]] = {}
_checked_lock = threading.Lock()


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


def _cache_fingerprint(model_root: Path) -> tuple[tuple[str, int, int], ...]:
    entries = []
    for path in model_root.rglob("*"):
        if path.is_file():
            stat = path.stat()
            entries.append((path.relative_to(model_root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(entries))


def check_docling_cache(model_root: Path, *, include_ocr: bool = True) -> None:
    """解析の要求の時の検証。取得・修復はせず、無い・壊れているなら配備のやり直しを案内して失敗する。

    ハッシュの計算は重いので、ファイルの一覧・大きさ・更新時刻が前回の検証から変わっていなければ
    前回の結果を使う（converter の作成と /health が同じ結果を共有する）。
    """
    try:
        if not model_root.is_dir() or not any(model_root.iterdir()):
            raise ModelsNotDeployedError(f"Docling のモデルがありません。{_REDEPLOY}")
        fingerprint = _cache_fingerprint(model_root)
    except OSError as exc:
        raise ModelsNotDeployedError(f"Docling のモデルを読めません。{_REDEPLOY}") from exc
    key = (model_root.resolve(), include_ocr)
    with _checked_lock:
        cached = _checked.get(key)
        if cached is None or cached[0] != fingerprint:
            try:
                verify_docling_cache(model_root, repair=False, include_ocr=include_ocr)
                failure = None
            except RuntimeError as exc:
                failure = f"Docling のモデルを検証できません（{exc}）。{_REDEPLOY}"
            except OSError as exc:  # 文に path が入るため、利用者に見せる文には型名だけを書く。
                failure = f"Docling のモデルを読めません（{type(exc).__name__}）。{_REDEPLOY}"
            cached = _checked[key] = (fingerprint, failure)
    if cached[1] is not None:
        raise ModelsNotDeployedError(cached[1])


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
