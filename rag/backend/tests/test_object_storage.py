"""Object Storage adapter のローカル実装テスト。"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.object_storage import ObjectStorageClient
from app.config import Settings


async def test_local_put_get_roundtrip_and_sanitizes_key(tmp_path: Path) -> None:
    """local adapter は安全化したキーで保存・取得できる。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    uri = await client.put("uploaded/../invoice 001.txt", b"invoice body", "text/plain")

    assert uri == "local://uploaded/invoice_001.txt"
    assert await client.get(uri) == b"invoice body"
    assert not list((tmp_path / "objects" / "uploaded").glob("*.tmp"))


async def test_local_get_rejects_non_local_uri(tmp_path: Path) -> None:
    """local adapter は OCI URI を local key として扱わない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="local:// URI"):
        await client.get("oci://namespace/bucket/key.txt")


async def test_local_get_rejects_relative_path_segments(tmp_path: Path) -> None:
    """保存済み参照の取得時は相対パス要素を拒否する。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="相対パス要素"):
        await client.get("local://uploaded/../invoice.txt")


async def test_local_put_rejects_empty_key(tmp_path: Path) -> None:
    """空の Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="空"):
        await client.put("   ", b"body", "text/plain")


async def test_local_put_rejects_uri_key(tmp_path: Path) -> None:
    """local adapter の保存 key に URI は受け付けない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="URI"):
        await client.put("oci://namespace/bucket/key.txt", b"body", "text/plain")


async def test_local_put_rejects_too_deep_key(tmp_path: Path) -> None:
    """異常に深い Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )
    too_deep_key = "/".join(f"part-{index}" for index in range(17))

    with pytest.raises(ValueError, match="階層"):
        await client.put(too_deep_key, b"body", "text/plain")


async def test_local_put_rejects_too_long_key_part(tmp_path: Path) -> None:
    """単一要素が長すぎる Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="255"):
        await client.put(f"uploaded/{'a' * 256}.txt", b"body", "text/plain")


async def test_local_put_rejects_too_long_key(tmp_path: Path) -> None:
    """全体が長すぎる Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(ai_service_adapter="local", local_storage_dir=str(tmp_path))
    )
    too_long_key = "/".join(["a" * 70 for _ in range(15)])

    with pytest.raises(ValueError, match="長すぎ"):
        await client.put(too_long_key, b"body", "text/plain")


async def test_oci_put_uses_object_storage_sdk_and_returns_uri() -> None:
    """OCI adapter は Object Storage SDK へ安全化済み key と content-type を渡す。"""
    sdk = FakeObjectStorageSdkClient()
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    uri = await client.put("uploaded/invoice 001.txt", b"invoice body", "text/plain")

    assert uri == "oci://example-namespace/rag-originals/uploaded/invoice_001.txt"
    assert sdk.put_calls == 1
    assert sdk.last_put_request == {
        "namespace_name": "example-namespace",
        "bucket_name": "rag-originals",
        "object_name": "uploaded/invoice_001.txt",
        "put_object_body": b"invoice body",
        "kwargs": {"content_type": "text/plain"},
    }


async def test_oci_get_uses_object_storage_sdk_for_matching_uri() -> None:
    """OCI adapter は返却 URI から object key を復元して bytes を取得する。"""
    sdk = FakeObjectStorageSdkClient(
        get_response=SimpleNamespace(data=SimpleNamespace(content=b"invoice body"))
    )
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    body = await client.get("oci://example-namespace/rag-originals/uploaded/invoice.txt")

    assert body == b"invoice body"
    assert sdk.get_calls == 1
    assert sdk.last_get_request == {
        "namespace_name": "example-namespace",
        "bucket_name": "rag-originals",
        "object_name": "uploaded/invoice.txt",
    }


async def test_oci_get_rejects_foreign_namespace_or_bucket() -> None:
    """設定外 bucket の URI は誤取得を防ぐため拒否する。"""
    sdk = FakeObjectStorageSdkClient()
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="設定と一致しません"):
        await client.get("oci://other-namespace/rag-originals/uploaded/invoice.txt")

    assert sdk.get_calls == 0


async def test_oci_get_rejects_non_oci_uri() -> None:
    """OCI adapter の取得は oci:// URI または plain key に限定する。"""
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=FakeObjectStorageSdkClient(),
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="oci:// URI"):
        await client.get("local://uploaded/invoice.txt")


async def test_oci_adapter_requires_namespace_and_bucket() -> None:
    """OCI adapter は namespace/bucket の未設定を fail fast する。"""
    client = ObjectStorageClient(
        settings=Settings.model_construct(
            ai_service_adapter="oci",
            object_storage_namespace="",
            object_storage_bucket="",
        ),
        storage_client=FakeObjectStorageSdkClient(),
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="namespace / bucket"):
        await client.put("uploaded/invoice.txt", b"body", "text/plain")


class FakeObjectStorageSdkClient:
    """OCI Object Storage SDK client の最小 fake。"""

    def __init__(self, get_response: object | None = None) -> None:
        self._get_response = get_response or SimpleNamespace(data=SimpleNamespace(content=b""))
        self.put_calls = 0
        self.get_calls = 0
        self.last_put_request: dict[str, object] | None = None
        self.last_get_request: dict[str, object] | None = None

    def put_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
        put_object_body: bytes,
        **kwargs: object,
    ) -> object:
        self.put_calls += 1
        self.last_put_request = {
            "namespace_name": namespace_name,
            "bucket_name": bucket_name,
            "object_name": object_name,
            "put_object_body": put_object_body,
            "kwargs": kwargs,
        }
        return SimpleNamespace(data=None)

    def get_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
    ) -> object:
        self.get_calls += 1
        self.last_get_request = {
            "namespace_name": namespace_name,
            "bucket_name": bucket_name,
            "object_name": object_name,
        }
        return self._get_response


def _oci_settings() -> Settings:
    return Settings.model_construct(
        ai_service_adapter="oci",
        oci_config_file="~/.oci/config",
        oci_config_profile="DEFAULT",
        oci_region="ap-osaka-1",
        object_storage_namespace="example-namespace",
        object_storage_bucket="rag-originals",
    )


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
