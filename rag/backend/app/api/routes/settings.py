"""設定 API。"""

import configparser
import importlib
import io
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.clients.oracle import close_oracle_pool, test_oracle_connection
from app.config import (
    EnterpriseAiConfiguredModel,
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
    get_settings,
)
from app.readiness import READINESS_OK, oracle_readiness_check, upload_storage_readiness_checks
from app.schemas.common import ApiResponse
from app.schemas.settings import (
    DatabaseConnectionTestResult,
    DatabaseConnectionTestStatus,
    DatabaseSettingsData,
    DatabaseSettingsUpdate,
    EnterpriseAiModelEntrySettings,
    EnterpriseAiModelSettings,
    GenerativeAiModelSettings,
    ModelSettingsCheckStatus,
    ModelSettingsData,
    ModelSettingsPayload,
    OciConfigField,
    OciConfigReadData,
    OciConfigReadRequest,
    OciObjectStorageNamespaceData,
    OciObjectStorageNamespaceRequest,
    OciPrivateKeyUploadData,
    OciSettingsData,
    UploadStorageSettingsData,
    UploadStorageSettingsUpdate,
)

router = APIRouter()
OCI_CONFIG_MAX_BYTES = 64 * 1024
OCI_PRIVATE_KEY_FILE = "~/.oci/oci_api_key.pem"
OCI_PRIVATE_KEY_MAX_BYTES = 64 * 1024
ORACLE_WALLET_MAX_BYTES = 20 * 1024 * 1024
ORACLE_WALLET_MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
ORACLE_WALLET_REQUIRED_FILES = frozenset({"tnsnames.ora", "sqlnet.ora"})
ORACLE_WALLET_AUTH_FILES = frozenset({"cwallet.sso", "ewallet.p12"})
OCI_CONFIG_KEYS: tuple[OciConfigField, ...] = (
    "user",
    "fingerprint",
    "tenancy",
    "region",
    "key_file",
)


@router.get("/model", response_model=ApiResponse[ModelSettingsData])
async def get_model_settings() -> ApiResponse[ModelSettingsData]:
    """現在のモデル設定を返す。"""
    settings = get_settings()
    payload = _payload_from_settings(settings)
    return ApiResponse(data=_model_settings_data(payload))


@router.patch("/model", response_model=ApiResponse[ModelSettingsData])
async def update_model_settings(
    request: ModelSettingsPayload,
) -> ApiResponse[ModelSettingsData]:
    """モデル設定をランタイム設定へ反映する。

    永続的な正本は `.env` / OCI Vault とし、この API は稼働中プロセスの設定を更新する。
    """
    settings = get_settings()
    _apply_model_settings(settings, request)
    payload = _payload_from_settings(settings)
    return ApiResponse(data=_model_settings_data(payload))


@router.post("/model/check", response_model=ApiResponse[ModelSettingsData])
async def check_model_settings(
    request: ModelSettingsPayload,
) -> ApiResponse[ModelSettingsData]:
    """保存前のモデル設定を検証する。外部 AI API への推論呼び出しは行わない。"""
    return ApiResponse(data=_model_settings_data(request))


@router.get("/database", response_model=ApiResponse[DatabaseSettingsData])
async def get_database_settings() -> ApiResponse[DatabaseSettingsData]:
    """現在の Oracle 26ai 接続設定を返す。secret は返さない。"""
    return ApiResponse(data=_database_settings_data(get_settings()))


@router.patch("/database", response_model=ApiResponse[DatabaseSettingsData])
async def update_database_settings(
    payload: DatabaseSettingsUpdate,
) -> ApiResponse[DatabaseSettingsData]:
    """Oracle 26ai 接続設定を現在プロセスへ反映する。

    永続化は .env / OCI Vault 等の外部設定層で行う前提。
    """
    settings = get_settings()
    candidate = _database_settings_candidate(settings, payload)
    _apply_database_settings(settings, candidate)
    close_oracle_pool()
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/wallet", response_model=ApiResponse[DatabaseSettingsData])
async def upload_database_wallet(
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[DatabaseSettingsData]:
    """Oracle Wallet ZIP を固定の TNS_ADMIN ディレクトリへ展開する。"""
    settings = get_settings()
    data = await _read_upload_file(file, ORACLE_WALLET_MAX_BYTES)
    wallet_dir = _install_database_wallet(settings, data, file.filename)
    settings.oracle_wallet_dir = str(wallet_dir)
    close_oracle_pool()
    return ApiResponse(data=_database_settings_data(settings))


@router.post("/database/test", response_model=ApiResponse[DatabaseConnectionTestResult])
async def test_database_settings(
    payload: DatabaseSettingsUpdate | None = None,
) -> ApiResponse[DatabaseConnectionTestResult]:
    """Oracle 26ai 接続設定を検証する。

    local adapter では実 DB 接続を行わず、OCI adapter で使う設定値の充足だけ確認する。
    """
    base = get_settings()
    candidate = _database_settings_candidate(base, payload) if payload is not None else base
    readiness = oracle_readiness_check(candidate)

    if candidate.ai_service_adapter == "local":
        status: DatabaseConnectionTestStatus = "skipped" if readiness == READINESS_OK else "failed"
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status=status,
                readiness=readiness,
                message=(
                    "local adapter のため実 DB 接続は行いません。"
                    if readiness == READINESS_OK
                    else "Oracle 26ai 接続に必要な設定が不足しています。"
                ),
            )
        )

    if readiness != READINESS_OK:
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message="Oracle 26ai 接続に必要な設定が不足しています。",
            )
        )

    try:
        await test_oracle_connection(candidate)
    except Exception as exc:
        return ApiResponse(
            data=DatabaseConnectionTestResult(
                status="failed",
                readiness=readiness,
                message="Oracle 26ai へ接続できませんでした。設定を確認してください。",
                error_type=type(exc).__name__,
            )
        )

    return ApiResponse(
        data=DatabaseConnectionTestResult(
            status="success",
            readiness=readiness,
            message="Oracle 26ai への接続に成功しました。",
        )
    )


@router.get("/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def get_upload_storage_settings() -> ApiResponse[UploadStorageSettingsData]:
    """現在のアップロード原本保存先設定を返す。"""
    return ApiResponse(data=_upload_storage_settings_data(get_settings()))


@router.patch("/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
async def update_upload_storage_settings(
    payload: UploadStorageSettingsUpdate,
) -> ApiResponse[UploadStorageSettingsData]:
    """アップロード原本保存先を現在プロセスへ反映する。

    永続化は .env / OCI Vault 等の外部設定層で行う前提。
    """
    settings = get_settings()
    candidate = _upload_storage_settings_candidate(settings, payload)
    _validate_upload_storage_settings_candidate(candidate)
    _apply_upload_storage_settings(settings, candidate)
    return ApiResponse(data=_upload_storage_settings_data(settings))


@router.get("/oci", response_model=ApiResponse[OciSettingsData])
async def get_oci_settings() -> ApiResponse[OciSettingsData]:
    """OCI 認証設定画面の初期表示に使う runtime 設定を返す。"""
    return ApiResponse(data=_oci_settings_data(get_settings()))


@router.post("/oci/config/read", response_model=ApiResponse[OciConfigReadData])
async def read_oci_config(
    payload: OciConfigReadRequest,
) -> ApiResponse[OciConfigReadData]:
    """バックエンドから読める OCI config file の指定 profile を読み取る。"""
    content = _read_oci_config_text(payload.config_file)
    return ApiResponse(data=_parse_oci_config(content, payload.profile))


@router.post(
    "/oci/object-storage/namespace",
    response_model=ApiResponse[OciObjectStorageNamespaceData],
)
async def read_oci_object_storage_namespace(
    payload: OciObjectStorageNamespaceRequest,
) -> ApiResponse[OciObjectStorageNamespaceData]:
    """OCI Object Storage API から tenancy namespace を取得する。"""
    namespace = _read_object_storage_namespace(payload)
    return ApiResponse(data=OciObjectStorageNamespaceData(namespace=namespace))


@router.post("/oci/key-file", response_model=ApiResponse[OciPrivateKeyUploadData])
async def upload_oci_private_key(
    file: Annotated[UploadFile, File(...)],
) -> ApiResponse[OciPrivateKeyUploadData]:
    """OCI API 秘密鍵を固定 path へ上書き保存する。"""
    data = await _read_upload_file(
        file,
        OCI_PRIVATE_KEY_MAX_BYTES,
        "秘密鍵 PEM ファイルのサイズが上限を超えています。",
    )
    _install_oci_private_key(data, file.filename)
    return ApiResponse(data=OciPrivateKeyUploadData(key_file=OCI_PRIVATE_KEY_FILE, saved=True))


def _payload_from_settings(settings: Settings) -> ModelSettingsPayload:
    """Settings から UI 用 payload を組み立てる。"""
    api_path = settings.oci_enterprise_ai_llm_path or settings.oci_enterprise_ai_vlm_path
    return ModelSettingsPayload(
        enterprise_ai=EnterpriseAiModelSettings(
            endpoint=settings.oci_enterprise_ai_endpoint,
            project_ocid=settings.oci_enterprise_ai_project_ocid,
            api_key="",
            has_api_key=bool(settings.oci_enterprise_ai_api_key.strip()),
            clear_api_key=False,
            models=[
                EnterpriseAiModelEntrySettings(
                    model_id=model.model_id,
                    display_name=model.display_name,
                    vision_enabled=model.vision_enabled,
                )
                for model in enterprise_ai_model_catalog(settings)
            ],
            default_model_id=enterprise_ai_default_model_id(settings),
            api_path=api_path or "/responses",
            text_payload_template=settings.oci_enterprise_ai_llm_payload_template,
            vision_payload_template=settings.oci_enterprise_ai_vlm_payload_template,
            text_response_path=settings.oci_enterprise_ai_llm_response_path,
            vision_response_path=settings.oci_enterprise_ai_vlm_response_path,
            timeout_seconds=settings.oci_enterprise_ai_timeout_seconds,
            max_retries=settings.oci_enterprise_ai_max_retries,
        ),
        generative_ai=GenerativeAiModelSettings(
            embedding_model=settings.oci_genai_embedding_model,
            embedding_dim=settings.oci_genai_embedding_dim,
            rerank_model=settings.oci_genai_rerank_model,
        ),
    )


def _apply_model_settings(settings: Settings, request: ModelSettingsPayload) -> None:
    """API payload を Settings シングルトンへ反映する。"""
    enterprise_ai = request.enterprise_ai
    generative_ai = request.generative_ai

    settings.oci_enterprise_ai_endpoint = enterprise_ai.endpoint
    settings.oci_enterprise_ai_project_ocid = enterprise_ai.project_ocid
    settings.oci_enterprise_ai_api_key = _secret_value(
        current=settings.oci_enterprise_ai_api_key,
        update=enterprise_ai.api_key,
        clear=enterprise_ai.clear_api_key,
    )
    settings.oci_enterprise_ai_models = [
        EnterpriseAiConfiguredModel(
            model_id=model.model_id,
            display_name=model.display_name,
            vision_enabled=model.vision_enabled,
        )
        for model in enterprise_ai.models
        if model.model_id
    ]
    settings.oci_enterprise_ai_default_model = enterprise_ai.default_model_id
    default_model = enterprise_ai.default_model_id
    vision_model = next(
        (
            model.model_id
            for model in enterprise_ai.models
            if model.model_id == default_model and model.vision_enabled
        ),
        "",
    ) or next(
        (
            model.model_id
            for model in enterprise_ai.models
            if model.model_id and model.vision_enabled
        ),
        default_model,
    )
    settings.oci_enterprise_ai_llm_model = default_model
    settings.oci_enterprise_ai_vlm_model = vision_model
    settings.oci_enterprise_ai_llm_path = enterprise_ai.api_path
    settings.oci_enterprise_ai_vlm_path = enterprise_ai.api_path
    settings.oci_enterprise_ai_llm_payload_template = enterprise_ai.text_payload_template
    settings.oci_enterprise_ai_vlm_payload_template = enterprise_ai.vision_payload_template
    settings.oci_enterprise_ai_llm_response_path = enterprise_ai.text_response_path
    settings.oci_enterprise_ai_vlm_response_path = enterprise_ai.vision_response_path
    settings.oci_enterprise_ai_timeout_seconds = enterprise_ai.timeout_seconds
    settings.oci_enterprise_ai_max_retries = enterprise_ai.max_retries

    settings.oci_genai_embedding_model = generative_ai.embedding_model
    settings.oci_genai_embedding_dim = generative_ai.embedding_dim
    settings.oci_genai_rerank_model = generative_ai.rerank_model


def _database_settings_data(settings: Settings) -> DatabaseSettingsData:
    """Settings から表示用データを作る。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    wallet_path = Path(wallet_dir).expanduser() if wallet_dir else None
    wallet_uploaded = wallet_path is not None and wallet_path.is_dir()
    available_services = (
        _extract_wallet_services(wallet_path) if wallet_path is not None and wallet_uploaded else []
    )

    return DatabaseSettingsData(
        adapter=settings.ai_service_adapter,
        user=settings.oracle_user,
        dsn=settings.oracle_dsn,
        wallet_dir=wallet_dir,
        wallet_uploaded=wallet_uploaded,
        available_services=available_services,
        has_password=bool(settings.oracle_password.strip()),
        has_wallet_password=bool(settings.oracle_wallet_password.strip()),
        readiness=oracle_readiness_check(settings),
        embedding_dimension=settings.oci_genai_embedding_dim,
        vector_column=f"VECTOR({settings.oci_genai_embedding_dim}, FLOAT32)",
        config_source="runtime",
    )


def _database_settings_candidate(
    base: Settings,
    payload: DatabaseSettingsUpdate,
) -> Settings:
    """更新 payload を適用した一時 Settings を作る。"""
    updates = {
        "oracle_user": payload.user,
        "oracle_dsn": payload.dsn,
        "oracle_wallet_dir": base.resolved_oracle_wallet_dir,
        "oracle_password": _secret_value(
            current=base.oracle_password,
            update=payload.password,
            clear=False,
        ),
        "oracle_wallet_password": _secret_value(
            current=base.oracle_wallet_password,
            update=payload.wallet_password,
            clear=False,
        ),
    }
    return base.model_copy(update=updates)


def _apply_database_settings(target: Settings, source: Settings) -> None:
    """Oracle 関連設定だけ現在プロセスへ反映する。"""
    target.oracle_user = source.oracle_user
    target.oracle_password = source.oracle_password
    target.oracle_dsn = source.oracle_dsn
    target.oracle_wallet_dir = source.oracle_wallet_dir
    target.oracle_wallet_password = source.oracle_wallet_password


async def _read_upload_file(
    file: UploadFile,
    max_bytes: int,
    too_large_detail: str = "Wallet ZIP のサイズが上限を超えています。",
) -> bytes:
    """アップロードファイルを上限付きで読み込む。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=too_large_detail)
        chunks.append(chunk)
    return b"".join(chunks)


def _install_oci_private_key(data: bytes, file_name: str | None) -> Path:
    """OCI API 秘密鍵 PEM を固定 path へ上書き保存する。"""
    safe_name = PurePosixPath((file_name or "oci_api_key.pem").replace("\\", "/")).name
    if Path(safe_name).suffix.lower() not in {".pem", ".key"}:
        raise HTTPException(
            status_code=415,
            detail="秘密鍵は .pem または .key ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の秘密鍵ファイルはアップロードできません。")
    _validate_private_key_pem(data)

    target = Path(OCI_PRIVATE_KEY_FILE).expanduser()
    tmp_path = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        tmp_path.write_bytes(data)
        tmp_path.chmod(0o600)
        tmp_path.replace(target)
        target.chmod(0o600)
    except OSError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail="秘密鍵ファイルをバックエンドの固定 path へ保存できませんでした。",
        ) from exc
    return target


def _validate_private_key_pem(data: bytes) -> None:
    """秘密鍵らしい PEM テキストだけを受け付ける。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="秘密鍵ファイルは UTF-8 の PEM テキストとして読み取れる必要があります。",
        ) from exc
    if "\x00" in text or "-----BEGIN " not in text or "PRIVATE KEY-----" not in text:
        raise HTTPException(
            status_code=400,
            detail="秘密鍵 PEM ファイルの形式を確認してください。",
        )


def _install_database_wallet(settings: Settings, data: bytes, file_name: str | None) -> Path:
    """Wallet ZIP を ORACLE_CLIENT_LIB_DIR/network/admin へ展開する。"""
    safe_name = _safe_wallet_filename(file_name)
    if not safe_name.lower().endswith(".zip"):
        raise HTTPException(
            status_code=415,
            detail="Oracle Wallet は ZIP ファイルを選択してください。",
        )
    if not data:
        raise HTTPException(status_code=400, detail="空の Wallet ZIP はアップロードできません。")

    target = _wallet_storage_root(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = target.parent / f".{target.name}.tmp-{uuid4().hex}"
    try:
        wallet_dir = _extract_wallet_zip(data, tmp_dir)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(wallet_dir), str(target))
        return target
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Wallet ZIP をバックエンドの保存先へ展開できませんでした。",
        ) from exc
    finally:
        _remove_tmp_wallet_dir(tmp_dir)


def _extract_wallet_zip(data: bytes, target_dir: Path) -> Path:
    """ZIP を検証しながら展開し、config_dir として使うディレクトリを返す。"""
    extracted_files: list[Path] = []
    total_uncompressed = 0
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members:
                raise HTTPException(
                    status_code=400,
                    detail="Wallet ZIP にファイルが含まれていません。",
                )
            for member in members:
                total_uncompressed += member.file_size
                if total_uncompressed > ORACLE_WALLET_MAX_EXTRACTED_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Wallet ZIP の展開後サイズが上限を超えています。",
                    )
                destination = _wallet_member_destination(target_dir, member.filename)
                if _zip_member_is_symlink(member.external_attr):
                    raise HTTPException(
                        status_code=400,
                        detail="Wallet ZIP にシンボリックリンクは含められません。",
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted_files.append(destination)
    except BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP の形式を確認してください。",
        ) from exc

    wallet_dir = _find_wallet_config_dir(extracted_files)
    if wallet_dir is None:
        required = ", ".join(sorted(ORACLE_WALLET_REQUIRED_FILES))
        auth = " または ".join(sorted(ORACLE_WALLET_AUTH_FILES))
        raise HTTPException(
            status_code=400,
            detail=f"Wallet ZIP に {required} と {auth} が含まれているか確認してください。",
        )
    return wallet_dir


def _wallet_member_destination(root: Path, member_name: str) -> Path:
    """Zip Slip を防ぎながら member の展開先を決める。"""
    path = PurePosixPath(member_name.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP に安全でないファイルパスが含まれています。",
        )
    destination = (root.joinpath(*path.parts)).resolve()
    resolved_root = root.resolve()
    if resolved_root != destination and resolved_root not in destination.parents:
        raise HTTPException(
            status_code=400,
            detail="Wallet ZIP に安全でないファイルパスが含まれています。",
        )
    return destination


def _find_wallet_config_dir(extracted_files: list[Path]) -> Path | None:
    """tnsnames.ora/sqlnet.ora と認証ファイルが揃うディレクトリを探す。"""
    candidates = {path.parent for path in extracted_files}
    for candidate in sorted(candidates, key=lambda path: len(path.parts)):
        names = {path.name.lower() for path in extracted_files if path.parent == candidate}
        if ORACLE_WALLET_REQUIRED_FILES.issubset(names) and names & ORACLE_WALLET_AUTH_FILES:
            return candidate
    return None


def _extract_wallet_services(wallet_dir: Path) -> list[str]:
    """tnsnames.ora からトップレベルの TNS alias を抽出する。"""
    tnsnames = wallet_dir / "tnsnames.ora"
    if not tnsnames.is_file():
        return []

    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    reserved_names = {
        "ADDRESS",
        "ADDRESS_LIST",
        "CONNECT_DATA",
        "DESCRIPTION",
        "DESCRIPTION_LIST",
        "HOST",
        "PORT",
        "PROTOCOL",
        "SECURITY",
        "SERVICE_NAME",
        "SSL_SERVER_CERT_DN",
    }
    services: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)\s*=", content):
        service = match.group(1)
        normalized = service.upper()
        if normalized in reserved_names or normalized in seen:
            continue
        seen.add(normalized)
        services.append(service)
    return services


def _zip_member_is_symlink(external_attr: int) -> bool:
    """ZIP metadata 上の symlink を拒否する。"""
    mode = external_attr >> 16
    return bool(mode and stat.S_ISLNK(mode))


def _wallet_storage_root(settings: Settings) -> Path:
    """アップロード Wallet の固定保存先。"""
    return Path(settings.resolved_oracle_wallet_dir).expanduser().resolve()


def _safe_wallet_filename(file_name: str | None) -> str:
    """表示名由来の ZIP ファイル名を basename に丸める。"""
    name = PurePosixPath((file_name or "wallet.zip").replace("\\", "/")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name).strip(" .")
    return name[:255] if name else "wallet.zip"


def _remove_tmp_wallet_dir(path: Path) -> None:
    """失敗時に今回作成した一時展開先だけを片付ける。"""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _upload_storage_settings_data(settings: Settings) -> UploadStorageSettingsData:
    """Settings からアップロード保存先の表示用データを作る。"""
    return UploadStorageSettingsData(
        backend=settings.upload_storage_backend,
        ai_service_adapter=settings.ai_service_adapter,
        local_storage_dir=settings.local_storage_dir,
        object_storage_region=settings.object_storage_region,
        object_storage_namespace=settings.object_storage_namespace,
        object_storage_bucket=settings.object_storage_bucket,
        readiness=_upload_storage_readiness(settings),
        max_upload_bytes=settings.max_upload_bytes,
        config_source="runtime",
    )


def _upload_storage_settings_candidate(
    base: Settings,
    payload: UploadStorageSettingsUpdate,
) -> Settings:
    """更新 payload を適用した一時 Settings を作る。"""
    updates = {
        "upload_storage_backend": payload.backend,
        "local_storage_dir": payload.local_storage_dir,
        "object_storage_namespace": (
            payload.object_storage_namespace
            if payload.object_storage_namespace is not None
            else base.object_storage_namespace
        ),
        "object_storage_bucket": payload.object_storage_bucket,
    }
    return base.model_copy(update=updates)


def _validate_upload_storage_settings_candidate(settings: Settings) -> None:
    """OCI 保存時は OCI 認証設定側の namespace が解決済みであることを確認する。"""
    if settings.upload_storage_backend != "oci":
        return
    if not settings.object_storage_namespace.strip():
        raise HTTPException(
            status_code=422,
            detail="OCI 認証設定で Object Storage ネームスペースを設定してください。",
        )


def _apply_upload_storage_settings(target: Settings, source: Settings) -> None:
    """アップロード保存先関連設定だけ現在プロセスへ反映する。"""
    target.upload_storage_backend = source.upload_storage_backend
    target.local_storage_dir = source.local_storage_dir
    target.object_storage_namespace = source.object_storage_namespace
    target.object_storage_bucket = source.object_storage_bucket


def _upload_storage_readiness(settings: Settings) -> str:
    """アップロード保存先の readiness status を返す。"""
    checks = upload_storage_readiness_checks(settings)
    return next(iter(checks.values()), "missing")


def _oci_settings_data(settings: Settings) -> OciSettingsData:
    """Settings と OCI config から OCI 設定画面の初期表示データを作る。"""
    parsed = _read_runtime_oci_config(settings)
    config_path = Path(settings.oci_config_file).expanduser()
    key_path = Path(OCI_PRIVATE_KEY_FILE).expanduser()

    return OciSettingsData(
        config_file=settings.oci_config_file,
        profile=settings.oci_config_profile,
        user=parsed.user if parsed is not None else "",
        fingerprint=parsed.fingerprint if parsed is not None else "",
        tenancy=parsed.tenancy if parsed is not None else "",
        region=settings.oci_region.strip() or (parsed.region if parsed is not None else ""),
        key_file=OCI_PRIVATE_KEY_FILE,
        key_file_exists=key_path.is_file(),
        config_file_exists=config_path.is_file(),
        config_source="runtime",
    )


def _read_runtime_oci_config(settings: Settings) -> OciConfigReadData | None:
    """runtime の OCI config を表示用に読む。読めない場合は画面表示を継続する。"""
    try:
        content = _read_oci_config_text(settings.oci_config_file)
        return _parse_oci_config(content, settings.oci_config_profile)
    except HTTPException:
        return None


def _read_object_storage_namespace(payload: OciObjectStorageNamespaceRequest) -> str:
    """OCI SDK で Object Storage namespace を取得する。"""
    try:
        oci_config = importlib.import_module("oci.config")
        object_storage = importlib.import_module("oci.object_storage")
        config = oci_config.from_file(
            str(Path(payload.config_file).expanduser()),
            payload.profile,
        )
        config["region"] = payload.region
        response = object_storage.ObjectStorageClient(config).get_namespace()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "OCI Object Storage namespace を取得できませんでした。"
                "OCI config / profile / region を確認してください。"
            ),
        ) from exc

    namespace = getattr(response, "data", "")
    if not isinstance(namespace, str):
        namespace = str(namespace) if namespace is not None else ""
    namespace = namespace.strip()
    if not namespace:
        raise HTTPException(
            status_code=502,
            detail="OCI Object Storage namespace が空で返されました。",
        )
    return namespace


def _secret_value(*, current: str, update: str | None, clear: bool) -> str:
    """secret の保持・更新・削除を判定する。"""
    if clear:
        return ""
    if update is not None and update != "":
        return update
    return current


def _read_oci_config_text(config_file: str) -> str:
    """OCI config file を安全な上限付きで読み込む。"""
    path = Path(config_file).expanduser()
    try:
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=(
                    "OCI config ファイルを読み取れません。"
                    "バックエンドから参照できる path を指定してください。"
                ),
            )
        if path.stat().st_size > OCI_CONFIG_MAX_BYTES:
            raise HTTPException(status_code=413, detail="OCI config ファイルが大きすぎます。")
        return path.read_text(encoding="utf-8")
    except HTTPException:
        raise
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルは UTF-8 テキストとして読み取れる必要があります。",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "OCI config ファイルを読み取れません。"
                "バックエンドから参照できる path を指定してください。"
            ),
        ) from exc


def _parse_oci_config(content: str, profile: str) -> OciConfigReadData:
    """OCI config の profile から UI に反映する値だけを抽出する。"""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise HTTPException(
            status_code=400,
            detail="OCI config ファイルの形式を確認してください。",
        ) from exc

    selected_profile = profile.strip() or "DEFAULT"
    if selected_profile.upper() == "DEFAULT":
        entries = parser.defaults()
    elif parser.has_section(selected_profile):
        entries = parser[selected_profile]
    else:
        raise HTTPException(
            status_code=404,
            detail="指定した OCI config profile が見つかりません。",
        )

    values = {key: str(entries.get(key, "")).strip() for key in OCI_CONFIG_KEYS}
    applied_fields = [key for key in OCI_CONFIG_KEYS if values[key]]
    if not applied_fields:
        raise HTTPException(
            status_code=422,
            detail="指定した profile から OCI config 項目を読み取れませんでした。",
        )

    return OciConfigReadData(
        profile=selected_profile,
        user=values["user"],
        fingerprint=values["fingerprint"],
        tenancy=values["tenancy"],
        region=values["region"],
        key_file=values["key_file"],
        applied_fields=applied_fields,
    )


def _model_settings_data(payload: ModelSettingsPayload) -> ModelSettingsData:
    """payload と静的チェック結果を API data へ変換する。"""
    return ModelSettingsData(
        settings=_public_model_settings_payload(payload),
        checks={
            "enterprise_ai": _enterprise_ai_status(payload.enterprise_ai),
            "generative_ai": _generative_ai_status(payload.generative_ai),
            "embedding_dim": _embedding_dim_status(payload.generative_ai),
        },
        source="runtime",
    )


def _public_model_settings_payload(payload: ModelSettingsPayload) -> ModelSettingsPayload:
    """secret を除いたモデル設定 payload を返す。"""
    enterprise_ai = payload.enterprise_ai.model_copy(
        update={
            "api_key": "",
            "has_api_key": (
                not payload.enterprise_ai.clear_api_key
                and (
                    payload.enterprise_ai.has_api_key or _is_present(payload.enterprise_ai.api_key)
                )
            ),
            "clear_api_key": False,
        }
    )
    return ModelSettingsPayload(
        enterprise_ai=enterprise_ai,
        generative_ai=payload.generative_ai,
    )


def _enterprise_ai_status(
    settings: EnterpriseAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Enterprise AI の必須設定が揃っているか確認する。"""
    required = (settings.endpoint, settings.project_ocid, settings.api_path)
    if not all(_is_present(value) for value in required):
        return "missing"
    if not _secret_is_available(settings):
        return "missing"
    model_ids = [model.model_id for model in settings.models if _is_present(model.model_id)]
    if len(model_ids) != len(settings.models):
        return "missing"
    if not model_ids or not _is_present(settings.default_model_id):
        return "missing"
    if settings.default_model_id not in model_ids:
        return "invalid"
    if not any(model.vision_enabled for model in settings.models if _is_present(model.model_id)):
        return "missing"
    return "ok"


def _generative_ai_status(
    settings: GenerativeAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Generative AI の必須設定が揃っているか確認する。"""
    if _embedding_dim_status(settings) == "invalid":
        return "invalid"
    required = (settings.embedding_model, settings.rerank_model)
    return "ok" if all(_is_present(value) for value in required) else "missing"


def _embedding_dim_status(
    settings: GenerativeAiModelSettings,
) -> ModelSettingsCheckStatus:
    """Oracle 26ai VECTOR 列と embedding 次元の互換性を確認する。"""
    return "ok" if settings.embedding_dim == 1536 else "invalid"


def _is_present(value: str) -> bool:
    """空白のみの値を未設定として扱う。"""
    return bool(value.strip())


def _secret_is_available(settings: EnterpriseAiModelSettings) -> bool:
    """新規入力または保存済み Enterprise AI API key があるか確認する。"""
    if settings.clear_api_key:
        return False
    return _is_present(settings.api_key) or settings.has_api_key
