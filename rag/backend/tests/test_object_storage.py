"""Object Storage 保存先 client のテスト。"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.object_storage import ObjectStorageClient
from app.clients.oci_auth import OciPrivateKeyPassPhraseRequiredError
from app.config import Settings


async def test_local_put_get_roundtrip_and_sanitizes_key(tmp_path: Path) -> None:
    """local upload storage backend は安全化したキーで保存・取得できる。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    uri = await client.put("uploaded/../policy 001.txt", b"policy body", "text/plain")

    assert uri == "local://uploaded/policy_001.txt"
    assert await client.get(uri) == b"policy body"
    assert not list((tmp_path / "objects" / "uploaded").glob("*.tmp"))


async def test_local_get_rejects_unknown_uri(tmp_path: Path) -> None:
    """local 保存先は未知の URI scheme を local key として扱わない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="local:// URI"):
        await client.get("s3://bucket/key.txt")


async def test_local_delete_removes_object_and_is_idempotent(tmp_path: Path) -> None:
    """local upload storage backend は保存済み object を削除する。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )
    uri = await client.put("uploaded/policy.txt", b"policy body", "text/plain")

    assert await client.delete(uri) is True
    assert await client.delete(uri) is False
    with pytest.raises(FileNotFoundError):
        await client.get(uri)


async def test_local_get_rejects_relative_path_segments(tmp_path: Path) -> None:
    """保存済み参照の取得時は相対パス要素を拒否する。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="相対パス要素"):
        await client.get("local://uploaded/../policy.txt")


async def test_local_put_rejects_empty_key(tmp_path: Path) -> None:
    """空の Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="空"):
        await client.put("   ", b"body", "text/plain")


async def test_local_put_rejects_uri_key(tmp_path: Path) -> None:
    """local upload storage backend の保存 key に URI は受け付けない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="URI"):
        await client.put("oci://namespace/bucket/key.txt", b"body", "text/plain")


async def test_local_put_rejects_too_deep_key(tmp_path: Path) -> None:
    """異常に深い Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )
    too_deep_key = "/".join(f"part-{index}" for index in range(17))

    with pytest.raises(ValueError, match="階層"):
        await client.put(too_deep_key, b"body", "text/plain")


async def test_local_put_rejects_too_long_key_part(tmp_path: Path) -> None:
    """単一要素が長すぎる Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )

    with pytest.raises(ValueError, match="255"):
        await client.put(f"uploaded/{'a' * 256}.txt", b"body", "text/plain")


async def test_local_put_rejects_too_long_key(tmp_path: Path) -> None:
    """全体が長すぎる Object Storage key は保存しない。"""
    client = ObjectStorageClient(
        settings=Settings(upload_storage_backend="local", local_storage_dir=str(tmp_path))
    )
    too_long_key = "/".join(["a" * 70 for _ in range(15)])

    with pytest.raises(ValueError, match="長すぎ"):
        await client.put(too_long_key, b"body", "text/plain")


async def test_oci_put_uses_object_storage_sdk_and_returns_uri() -> None:
    """OCI backend は Object Storage SDK へ安全化済み key と content-type を渡す。"""
    sdk = FakeObjectStorageSdkClient()
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    uri = await client.put("uploaded/policy 001.txt", b"policy body", "text/plain")

    assert uri == "oci://example-namespace/rag-originals/uploaded/policy_001.txt"
    assert sdk.put_calls == 1
    assert sdk.last_put_request == {
        "namespace_name": "example-namespace",
        "bucket_name": "rag-originals",
        "object_name": "uploaded/policy_001.txt",
        "put_object_body": b"policy body",
        "kwargs": {"content_type": "text/plain"},
    }


async def test_upload_storage_backend_uses_oci_storage_client() -> None:
    """アップロード保存先を OCI にすると Object Storage SDK を使う。"""
    sdk = FakeObjectStorageSdkClient()
    settings = _oci_settings()
    client = ObjectStorageClient(
        settings=settings,
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    uri = await client.put("uploaded/policy.txt", b"policy body", "text/plain")

    assert uri == "oci://example-namespace/rag-originals/uploaded/policy.txt"
    assert sdk.put_calls == 1


async def test_oci_get_uses_object_storage_sdk_for_matching_uri() -> None:
    """OCI backend は返却 URI から object key を復元して bytes を取得する。"""
    sdk = FakeObjectStorageSdkClient(
        get_response=SimpleNamespace(data=SimpleNamespace(content=b"policy body"))
    )
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    body = await client.get("oci://example-namespace/rag-originals/uploaded/policy.txt")

    assert body == b"policy body"
    assert sdk.get_calls == 1
    assert sdk.last_get_request == {
        "namespace_name": "example-namespace",
        "bucket_name": "rag-originals",
        "object_name": "uploaded/policy.txt",
    }


async def test_oci_delete_uses_object_storage_sdk_for_matching_uri() -> None:
    """OCI backend は返却 URI から object key を復元して削除する。"""
    sdk = FakeObjectStorageSdkClient()
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    deleted = await client.delete("oci://example-namespace/rag-originals/uploaded/policy.txt")

    assert deleted is True
    assert sdk.delete_calls == 1
    assert sdk.last_delete_request == {
        "namespace_name": "example-namespace",
        "bucket_name": "rag-originals",
        "object_name": "uploaded/policy.txt",
    }


async def test_get_oci_uri_uses_oci_even_when_upload_storage_is_local() -> None:
    """保存先を local に切り替えた後も、既存 OCI URI は取得できる。"""
    sdk = FakeObjectStorageSdkClient(
        get_response=SimpleNamespace(data=SimpleNamespace(content=b"policy body"))
    )
    client = ObjectStorageClient(
        settings=_oci_settings().model_copy(update={"upload_storage_backend": "local"}),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    body = await client.get("oci://example-namespace/rag-originals/uploaded/policy.txt")

    assert body == b"policy body"
    assert sdk.get_calls == 1


async def test_get_local_uri_uses_local_even_when_upload_storage_is_oci(tmp_path: Path) -> None:
    """保存先を OCI に切り替えた後も、既存 local URI は取得できる。"""
    settings = _oci_settings().model_copy(update={"local_storage_dir": str(tmp_path)})
    client = ObjectStorageClient(
        settings=settings,
        storage_client=FakeObjectStorageSdkClient(),
        sdk_call_runner=_run_inline,
    )
    local_uri = await ObjectStorageClient(
        settings=settings.model_copy(update={"upload_storage_backend": "local"})
    ).put("uploaded/policy.txt", b"policy body", "text/plain")

    body = await client.get(local_uri)

    assert body == b"policy body"


async def test_oci_get_rejects_foreign_namespace_or_bucket() -> None:
    """設定外 bucket の URI は誤取得を防ぐため拒否する。"""
    sdk = FakeObjectStorageSdkClient()
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=sdk,
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="設定と一致しません"):
        await client.get("oci://other-namespace/rag-originals/uploaded/policy.txt")

    assert sdk.get_calls == 0


async def test_oci_get_rejects_unknown_uri() -> None:
    """OCI 保存先は未知の URI scheme を OCI key として扱わない。"""
    client = ObjectStorageClient(
        settings=_oci_settings(),
        storage_client=FakeObjectStorageSdkClient(),
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="oci:// URI"):
        await client.get("s3://bucket/policy.txt")


async def test_oci_backend_requires_namespace_and_bucket() -> None:
    """OCI backend は namespace/bucket の未設定を fail fast する。"""
    client = ObjectStorageClient(
        settings=Settings.model_construct(
            upload_storage_backend="oci",
            object_storage_namespace="",
            object_storage_bucket="",
        ),
        storage_client=FakeObjectStorageSdkClient(),
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="namespace / bucket"):
        await client.put("uploaded/policy.txt", b"body", "text/plain")


def test_oci_client_prefers_object_storage_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Object Storage SDK client は専用リージョンを OCI 共通リージョンより優先する。"""
    captured_config: dict[str, object] = {}

    class CapturingObjectStorageSdkClient(FakeObjectStorageSdkClient):
        def __init__(self, config: dict[str, object]) -> None:
            super().__init__()
            captured_config.update(config)

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(
                from_file=lambda path, profile: {
                    "path": path,
                    "profile": profile,
                    "region": "ap-tokyo-1",
                }
            )
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=CapturingObjectStorageSdkClient)
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("app.clients.object_storage.importlib.import_module", fake_import_module)
    client = ObjectStorageClient(
        settings=_oci_settings().model_copy(
            update={
                "oci_region": "us-chicago-1",
                "object_storage_region": "ap-osaka-1",
            }
        )
    )

    assert isinstance(client._client(), CapturingObjectStorageSdkClient)
    assert captured_config["region"] == "ap-osaka-1"
    assert captured_config["profile"] == "DEFAULT"


def test_oci_client_refuses_encrypted_private_key_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """暗号化 OCI API key は Object Storage SDK client 作成前に止める。"""
    key_file = tmp_path / "encrypted.pem"
    key_file.write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    initialized = False

    class CapturingObjectStorageSdkClient(FakeObjectStorageSdkClient):
        def __init__(self, config: dict[str, object]) -> None:
            nonlocal initialized
            initialized = True
            super().__init__()

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(
                from_file=lambda path, profile: {"key_file": str(key_file), "region": "ap-tokyo-1"}
            )
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=CapturingObjectStorageSdkClient)
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("app.clients.object_storage.importlib.import_module", fake_import_module)
    client = ObjectStorageClient(
        settings=_oci_settings().model_copy(update={"oci_config_file": str(tmp_path / "config")})
    )

    with pytest.raises(OciPrivateKeyPassPhraseRequiredError, match="pass_phrase"):
        client._client()

    assert initialized is False


class FakeObjectStorageSdkClient:
    """OCI Object Storage SDK client の最小 fake。"""

    def __init__(self, get_response: object | None = None) -> None:
        self._get_response = get_response or SimpleNamespace(data=SimpleNamespace(content=b""))
        self.put_calls = 0
        self.get_calls = 0
        self.delete_calls = 0
        self.last_put_request: dict[str, object] | None = None
        self.last_get_request: dict[str, object] | None = None
        self.last_delete_request: dict[str, object] | None = None

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

    def delete_object(
        self,
        namespace_name: str,
        bucket_name: str,
        object_name: str,
    ) -> object:
        self.delete_calls += 1
        self.last_delete_request = {
            "namespace_name": namespace_name,
            "bucket_name": bucket_name,
            "object_name": object_name,
        }
        return SimpleNamespace(data=None)


def _oci_settings() -> Settings:
    return Settings.model_construct(
        upload_storage_backend="oci",
        oci_config_file="~/.oci/config",
        oci_config_profile="DEFAULT",
        oci_region="ap-osaka-1",
        object_storage_region="ap-osaka-1",
        object_storage_namespace="example-namespace",
        object_storage_bucket="rag-originals",
    )


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
