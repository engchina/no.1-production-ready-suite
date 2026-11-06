"""parser-unstructured が使うモデルを配備で取得・検証し、解析の要求の時には取得させない。

Unstructured はモデルが無いと、解析の要求の時に外部から取得する。

- spaCy のモデル（``unstructured.nlp.tokenize``。テキストの要素の分類）: venv の
  site-packages に入れる。parser-unstructured の ``uv.lock`` の SHA-256 で固定した wheel を、
  配備の ``uv sync --locked`` が入れる。ここでは版が unstructured の固定の値と同じことと、
  入ったファイルが wheel の RECORD のハッシュと一致することを確かめ、実行時の取得の関数を
  明確なエラーに置き換える。
- unstructured-inference の hi_res の layout（yolox）と表の構造（table transformer）:
  版を指定せずに HF Hub から取得する。ここで commit と各ファイルのハッシュを固定し、配備で
  HF のキャッシュへ取得して検証し、キャッシュの ``refs/main`` をその commit にする。
  実行時は unit の ``HF_HUB_OFFLINE=1`` でキャッシュだけを読む。

配備（``rag/scripts/rag-systemd.sh`` の ``rag_service_model_command``）が
``python -m rag_parser_core.unstructured_models`` を、サービスの実行ユーザーで実行する。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import importlib.metadata
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rag_parser_core.model_integrity import ModelArtifact, ModelsNotDeployedError, verify_files

logger = logging.getLogger(__name__)

SPACY_MODEL_DISTRIBUTION = "en_core_web_sm"
_TOKENIZE_MODULE = "unstructured.nlp.tokenize"
_FORBIDDEN_MARKER = "_rag_runtime_download_forbidden"


@dataclass(frozen=True)
class HubFile:
    """HF Hub の 1 ファイル。digest は Hub の etag（LFS は sha256、他は git の blob の sha1）。"""

    filename: str
    digest: str
    hash_kind: str


@dataclass(frozen=True)
class HubModel:
    repo: str
    revision: str
    files: tuple[HubFile, ...]


# unstructured-inference 1.6.13 が読む repo とファイル（yolox: models/yolox.py の
# MODEL_TYPES["yolox"]、表: models/tables.py の DEFAULT_MODEL）。版を上げて repo・ファイルが
# 変わったら、parser-unstructured のテスト（tests/test_runtime_models.py）が失敗する。
HUB_MODELS: tuple[HubModel, ...] = (
    HubModel(
        repo="unstructuredio/yolo_x_layout",
        revision="7680d6f857780bcf8d49916aa2e8881bd49dee3e",
        files=(
            HubFile(
                "yolox_l0.05.onnx",
                "134301ca94fb0df8027be9a6dad1908fe6218af8ffa4d34f0819c7c2226195f3",
                "sha256",
            ),
        ),
    ),
    HubModel(
        repo="microsoft/table-transformer-structure-recognition",
        revision="f4d4bdc85c3fe4b1fa49658882a5d38bbdd0f343",
        files=(
            HubFile("config.json", "b784cf6d3759e23c6af6d5c52a9db9e34a7b5aaa", "git-sha1"),
            HubFile(
                "model.safetensors",
                "f581da65a586591124cac9545b09b93e4e8bf28ce3a8357af6ac15ce94fce16b",
                "sha256",
            ),
            HubFile(
                "preprocessor_config.json", "3cf68cfe0f9b5ef223c0e71613930b758ddaae4d", "git-sha1"
            ),
        ),
    ),
)


# huggingface_hub は parser-unstructured の venv にだけある（rag_parser_core の依存にしない）。
def _hub_cache_dir() -> Path:
    # HF_HOME / HF_HUB_CACHE を huggingface_hub と同じ規則で読む。
    constants = importlib.import_module("huggingface_hub.constants")
    return Path(constants.HF_HUB_CACHE)


def _hub_url(repo: str, filename: str, revision: str) -> str:
    # HF_ENDPOINT を huggingface_hub と同じ規則で読む。
    huggingface_hub = importlib.import_module("huggingface_hub")
    return str(huggingface_hub.hf_hub_url(repo, filename, revision=revision))


def _repo_dir(cache_dir: Path, model: HubModel) -> Path:
    return cache_dir / f"models--{model.repo.replace('/', '--')}"


def _snapshot_path(cache_dir: Path, model: HubModel, file: HubFile) -> Path:
    return _repo_dir(cache_dir, model) / "snapshots" / model.revision / file.filename


def _blob_path(cache_dir: Path, model: HubModel, file: HubFile) -> Path:
    return _repo_dir(cache_dir, model) / "blobs" / file.digest


def _publish_snapshot(cache_dir: Path, model: HubModel) -> None:
    """検証した blob を snapshot から指し、refs/main を固定の commit にする（HF の構成）。"""
    for file in model.files:
        link = _snapshot_path(cache_dir, model, file)
        target = os.path.relpath(_blob_path(cache_dir, model, file), link.parent)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and os.readlink(link) == target:
            continue
        temporary = link.with_name(f".{link.name}.link")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)
    ref = _repo_dir(cache_dir, model) / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    if not ref.is_file() or ref.read_text(encoding="utf-8").strip() != model.revision:
        temporary_ref = ref.with_name(".main.tmp")
        temporary_ref.write_text(model.revision, encoding="utf-8")
        os.replace(temporary_ref, ref)


def _check_snapshot(cache_dir: Path, model: HubModel) -> None:
    ref = _repo_dir(cache_dir, model) / "refs" / "main"
    if not ref.is_file() or ref.read_text(encoding="utf-8").strip() != model.revision:
        raise RuntimeError(
            f"モデルの版が固定の版ではありません: {model.repo}。"
            "モデル取得処理を再実行してください。"
        )
    for file in model.files:
        link = _snapshot_path(cache_dir, model, file)
        if not link.is_file() or link.resolve() != _blob_path(cache_dir, model, file).resolve():
            raise RuntimeError(
                f"モデルのファイルがありません: {model.repo}/{file.filename}。"
                "モデル取得処理を再実行してください。"
            )


def verify_hub_models(cache_dir: Path, *, repair: bool = True) -> list[str]:
    """固定の版の HF のモデルを検証する。repair なら欠落・破損を取得し直し、直した名前を返す。"""
    repaired: list[str] = []
    for model in HUB_MODELS:
        entries = [
            (
                _blob_path(cache_dir, model, file),
                [
                    ModelArtifact(
                        f"{model.repo}/{file.filename}",
                        _hub_url(model.repo, file.filename, model.revision),
                        file.digest,
                        file.hash_kind,
                    )
                ],
            )
            for file in model.files
        ]
        lock_path = _repo_dir(cache_dir, model) / ".integrity.lock"
        if repair:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        repaired.extend(verify_files(entries, lock_path, repair=repair))
        if repair:
            _publish_snapshot(cache_dir, model)
        _check_snapshot(cache_dir, model)
    return repaired


def _record_digest(path: Path) -> str:
    with path.open("rb") as stream:
        raw = hashlib.file_digest(stream, "sha256").digest()
    return "sha256=" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def verify_spacy_model() -> None:
    """spaCy のモデルが unstructured の固定の版で、wheel の RECORD と一致するか確かめる。"""
    tokenize = importlib.import_module(_TOKENIZE_MODULE)
    expected = str(tokenize._SPACY_MODEL_VERSION)
    try:
        distribution = importlib.metadata.distribution(SPACY_MODEL_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            f"spaCy のモデル（{SPACY_MODEL_DISTRIBUTION} {expected}）が venv にありません。"
            "parser-unstructured の venv を uv sync --locked で作り直してください。"
        ) from exc
    if distribution.version != expected:
        raise RuntimeError(
            f"spaCy のモデルの版が unstructured の固定の版と違います: {distribution.version}"
            f"（期待: {expected}）。parser-unstructured の uv.lock を直してください。"
        )
    for file in distribution.files or ():
        if file.hash is None:
            continue
        path = Path(str(file.locate()))
        if _record_digest(path) != f"{file.hash.mode}={file.hash.value}":
            raise RuntimeError(
                f"spaCy のモデルのファイルが wheel と一致しません: {file}。"
                "parser-unstructured の venv を uv sync --locked で作り直してください。"
            )
    logger.info("spaCy のモデルの検証が完了しました: %s %s", SPACY_MODEL_DISTRIBUTION, expected)


def _spacy_runtime_download_forbidden() -> None:
    raise ModelsNotDeployedError(
        f"spaCy のモデル（{SPACY_MODEL_DISTRIBUTION}）が venv に無いため解析できません。"
        "解析の要求の時にはモデルを取得しません。parser-unstructured を配備し直してください。"
    )


def forbid_runtime_downloads() -> None:
    """解析の要求の時の spaCy のモデルの取得を、明確なエラーに置き換える。

    HF のモデルの取得は unit の ``HF_HUB_OFFLINE=1`` が禁じる。
    """
    tokenize = importlib.import_module(_TOKENIZE_MODULE)
    current: Callable[[], None] = tokenize._install_spacy_model
    if getattr(current, _FORBIDDEN_MARKER, False):
        return
    setattr(_spacy_runtime_download_forbidden, _FORBIDDEN_MARKER, True)
    # unstructured の _load_spacy_model が module の名前で呼ぶ関数を置き換える。
    setattr(tokenize, "_install_spacy_model", _spacy_runtime_download_forbidden)  # noqa: B010


def models_ready() -> bool:
    """/health 用: 配備で揃えたモデルがあるか（ハッシュは配備で検証する。ここは存在と版だけ）。"""
    try:
        tokenize = importlib.import_module(_TOKENIZE_MODULE)
        if importlib.metadata.version(SPACY_MODEL_DISTRIBUTION) != tokenize._SPACY_MODEL_VERSION:
            return False
        cache_dir = _hub_cache_dir()
        for model in HUB_MODELS:
            _check_snapshot(cache_dir, model)
    except (ImportError, importlib.metadata.PackageNotFoundError, OSError, RuntimeError):
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="取得せず検証だけ行う")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    verify_spacy_model()
    verify_hub_models(_hub_cache_dir(), repair=not args.check)


if __name__ == "__main__":
    main()
