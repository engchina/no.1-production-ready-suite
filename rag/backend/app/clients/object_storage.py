"""OCI Object Storage クライアント。原本ファイルの保管。"""

import asyncio
import importlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.clients.oci_auth import load_oci_config_without_prompt
from app.config import Settings, get_settings

MAX_OBJECT_KEY_LENGTH = 1024
MAX_OBJECT_KEY_DEPTH = 16
MAX_OBJECT_KEY_PART_LENGTH = 255
type SdkCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


class ObjectStorageSdkClientProtocol(Protocol):
    """OCI Object Storage SDK client の最小インターフェース。"""

    def put_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        **kwargs: object,
    ) -> Any:
        """OCI Object Storage put_object を呼び出す。"""

    def get_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
    ) -> Any:
        """OCI Object Storage get_object を呼び出す。"""

    def delete_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
    ) -> Any:
        """OCI Object Storage delete_object を呼び出す。"""


class ObjectStorageClient:
    """OCI Object Storage への保存・取得。"""

    def __init__(
        self,
        settings: Settings | None = None,
        storage_client: ObjectStorageSdkClientProtocol | None = None,
        sdk_call_runner: SdkCallRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._storage_client = storage_client
        self._sdk_call_runner = sdk_call_runner or _run_sdk_call_in_thread

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """オブジェクトを保存し、参照パスを返す。"""
        safe_key = _local_storage_key(key)
        if self._settings.upload_storage_backend == "oci":
            return await self._put_to_oci(safe_key, data, content_type)
        return self._put_to_local(safe_key, data)

    async def get(self, key: str) -> bytes:
        """オブジェクトを取得する。"""
        if key.strip().startswith("oci://"):
            if self._has_oci_location() or self._settings.upload_storage_backend == "oci":
                return await self._get_from_oci(key)
            return self._get_from_local(key)
        if key.strip().startswith("local://"):
            return self._get_from_local(key)
        if self._settings.upload_storage_backend == "oci":
            return await self._get_from_oci(key)
        return self._get_from_local(key)

    async def delete(self, key: str) -> bool:
        """オブジェクトを削除する。存在した場合は True を返す。"""
        if key.strip().startswith("oci://"):
            if self._has_oci_location() or self._settings.upload_storage_backend == "oci":
                return await self._delete_from_oci(key)
            return self._delete_from_local(key)
        if key.strip().startswith("local://"):
            return self._delete_from_local(key)
        if self._settings.upload_storage_backend == "oci":
            return await self._delete_from_oci(key)
        return self._delete_from_local(key)

    def _put_to_local(self, safe_key: str, data: bytes) -> str:
        """ローカル保存先へ保存する。"""
        root = Path(self._settings.local_storage_dir).expanduser().resolve()
        path = (root / "objects" / safe_key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("保存先キーが不正です。")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, data)
        return f"local://{safe_key}"

    def _get_from_local(self, key: str) -> bytes:
        """ローカル保存先から取得する。"""
        safe_key = _local_object_key(key)
        root = Path(self._settings.local_storage_dir).expanduser().resolve()
        path = (root / "objects" / safe_key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("取得キーが不正です。")
        return path.read_bytes()

    def _delete_from_local(self, key: str) -> bool:
        """ローカル保存先から削除する。"""
        safe_key = _local_object_key(key)
        root = Path(self._settings.local_storage_dir).expanduser().resolve()
        path = (root / "objects" / safe_key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("削除キーが不正です。")
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    async def _put_to_oci(self, key: str, data: bytes, content_type: str) -> str:
        """OCI Object Storage へ保存する。"""
        namespace, bucket = self._require_oci_location()
        await self._sdk_call_runner(
            lambda: self._client().put_object(
                namespace,
                bucket,
                key,
                data,
                content_type=content_type,
            )
        )
        return _oci_storage_uri(namespace, bucket, key)

    async def _get_from_oci(self, key: str) -> bytes:
        """OCI Object Storage から取得する。"""
        namespace, bucket = self._require_oci_location()
        object_key = _oci_object_key(key, namespace=namespace, bucket=bucket)
        response = await self._sdk_call_runner(
            lambda: self._client().get_object(namespace, bucket, object_key)
        )
        return _response_body_to_bytes(response)

    async def _delete_from_oci(self, key: str) -> bool:
        """OCI Object Storage から削除する。"""
        namespace, bucket = self._require_oci_location()
        object_key = _oci_object_key(key, namespace=namespace, bucket=bucket)
        await self._sdk_call_runner(
            lambda: self._client().delete_object(namespace, bucket, object_key)
        )
        return True

    def _client(self) -> ObjectStorageSdkClientProtocol:
        """OCI Object Storage client を遅延初期化する。"""
        if self._storage_client is not None:
            return self._storage_client

        oci_config = importlib.import_module("oci.config")
        object_storage = importlib.import_module("oci.object_storage")
        object_storage_region = self._settings.object_storage_region.strip()
        region = object_storage_region or self._settings.oci_region.strip() or None
        config = load_oci_config_without_prompt(
            oci_config,
            self._settings.oci_config_file,
            self._settings.oci_config_profile,
            region=region,
        )
        self._storage_client = object_storage.ObjectStorageClient(config)
        return self._storage_client

    def _require_oci_location(self) -> tuple[str, str]:
        """OCI Object Storage の namespace/bucket 設定を検証する。"""
        namespace = self._settings.object_storage_namespace.strip()
        bucket = self._settings.object_storage_bucket.strip()
        if not namespace or not bucket:
            raise ValueError("OCI Object Storage の namespace / bucket が未設定です。")
        return namespace, bucket

    def _has_oci_location(self) -> bool:
        """既存 OCI URI を取得できる最小設定があるか確認する。"""
        return bool(
            self._settings.object_storage_namespace.strip()
            and self._settings.object_storage_bucket.strip()
        )


def _safe_key(key: str, *, reject_relative_segments: bool = False) -> str:
    """Object Storage キーとして扱える文字だけに正規化する。"""
    normalized = key.strip().replace("\\", "/")
    if reject_relative_segments and any(part in (".", "..") for part in normalized.split("/")):
        raise ValueError("Object Storage キーに相対パス要素は指定できません。")
    normalized = re.sub(r"[^A-Za-z0-9._/\-]", "_", normalized)
    parts = [part for part in normalized.split("/") if part not in ("", ".", "..")]
    if not parts:
        raise ValueError("Object Storage キーが空です。")
    if len(parts) > MAX_OBJECT_KEY_DEPTH:
        raise ValueError("Object Storage キーの階層が深すぎます。")
    if any(len(part) > MAX_OBJECT_KEY_PART_LENGTH for part in parts):
        raise ValueError("Object Storage キーの各要素は 255 文字以下にしてください。")
    safe_key = "/".join(parts)
    if len(safe_key) > MAX_OBJECT_KEY_LENGTH:
        raise ValueError("Object Storage キーが長すぎます。")
    return safe_key


def _local_object_key(key: str) -> str:
    """local upload storage backend で扱える参照だけをキー化する。"""
    if "://" in key and not key.startswith("local://"):
        raise ValueError("ローカルモードでは local:// URI のみ取得できます。")
    return _safe_key(key.removeprefix("local://"), reject_relative_segments=True)


def _local_storage_key(key: str) -> str:
    """local upload storage backend の保存キーとして扱える値だけをキー化する。"""
    if "://" in key:
        raise ValueError("保存先キーに URI は指定できません。")
    return _safe_key(key)


def _oci_object_key(reference: str, *, namespace: str, bucket: str) -> str:
    """OCI Object Storage で取得対象にできる object key を取り出す。"""
    normalized = reference.strip()
    if normalized.startswith("oci://"):
        parts = normalized.removeprefix("oci://").split("/", 2)
        if len(parts) != 3 or not all(parts):
            raise ValueError("OCI Object Storage URI が不正です。")
        actual_namespace, actual_bucket, object_key = parts
        if actual_namespace != namespace or actual_bucket != bucket:
            raise ValueError("OCI Object Storage URI の namespace / bucket が設定と一致しません。")
        return _safe_key(object_key, reject_relative_segments=True)
    if "://" in normalized:
        raise ValueError("OCI Object Storage では oci:// URI のみ取得できます。")
    return _safe_key(normalized, reject_relative_segments=True)


def _oci_storage_uri(namespace: str, bucket: str, key: str) -> str:
    """保存済み object の参照 URI を生成する。"""
    return f"oci://{namespace}/{bucket}/{key}"


def _atomic_write(path: Path, data: bytes) -> None:
    """同一ディレクトリ内の一時ファイル経由で原子的に書き込む。"""
    temporary_path = path.with_name(f".tmp-{uuid4().hex}")
    try:
        temporary_path.write_bytes(data)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


async def _run_sdk_call_in_thread(operation: Callable[[], Any]) -> Any:
    """同期 OCI SDK 呼び出しを event loop 外で実行する。"""
    return await asyncio.to_thread(operation)


def _response_body_to_bytes(response: Any) -> bytes:
    """OCI SDK response から object body bytes を取り出す。"""
    data = getattr(response, "data", None)
    if isinstance(data, bytes):
        return data
    content = getattr(data, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    read = getattr(data, "read", None)
    if callable(read):
        body = read()
        if isinstance(body, bytes):
            return body
        if isinstance(body, str):
            return body.encode("utf-8")
    raise ValueError("OCI Object Storage response に bytes body がありません。")
