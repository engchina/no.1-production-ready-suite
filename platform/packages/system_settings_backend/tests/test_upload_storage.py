from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pr_system_settings import upload_storage
from pr_system_settings.env_file import write_env_values
from pr_system_settings.upload_storage import build_upload_storage_router


class FakeSettings(BaseModel):
    """製品の Settings と同じ属性だけを持つテスト用 model。"""

    upload_storage_backend: str = "local"
    local_storage_dir: str = "/u01/data/production-ready-test"
    object_storage_region: str = ""
    object_storage_namespace: str = ""
    object_storage_bucket: str = ""
    oci_region: str = ""
    max_upload_bytes: int = 200 * 1024 * 1024


def make_client(settings: FakeSettings, env_file: Path, *, deny_write: bool = False) -> TestClient:
    def forbid() -> None:
        raise HTTPException(status_code=403, detail="forbidden")

    app = FastAPI()
    app.include_router(
        build_upload_storage_router(
            get_settings=lambda: settings,
            env_file=lambda: env_file,
            write_dependencies=[Depends(forbid)] if deny_write else [],
        ),
        prefix="/api/settings",
    )
    return TestClient(app)


def test_get_returns_runtime_values_and_falls_back_to_oci_region(tmp_path: Path) -> None:
    settings = FakeSettings(oci_region="ap-osaka-1")
    data = (
        make_client(settings, tmp_path / ".env").get("/api/settings/upload-storage").json()["data"]
    )
    assert data == {
        "backend": "local",
        "local_storage_dir": "/u01/data/production-ready-test",
        "object_storage_region": "ap-osaka-1",
        "object_storage_namespace": "",
        "object_storage_bucket": "",
        "readiness": "ok",
        "max_upload_bytes": 200 * 1024 * 1024,
        "config_source": "runtime",
    }


def test_patch_persists_only_upload_keys_and_mutates_runtime(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# 既存\nOCI_REGION=ap-osaka-1\nLOCAL_STORAGE_DIR=/old\n", encoding="utf-8")
    env_file.chmod(0o640)
    settings = FakeSettings(object_storage_namespace="ns-keep")

    response = make_client(settings, env_file).patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": " /u01/data/x ",
            "object_storage_region": "us-chicago-1",
            "object_storage_bucket": "originals",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["readiness"] == "ok"
    # namespace を省略したので既存値を保つ
    assert settings.object_storage_namespace == "ns-keep"
    assert settings.upload_storage_backend == "oci"
    assert settings.local_storage_dir == "/u01/data/x"
    content = env_file.read_text(encoding="utf-8")
    assert "# 既存\nOCI_REGION=ap-osaka-1\nLOCAL_STORAGE_DIR=/u01/data/x\n" in content
    assert "UPLOAD_STORAGE_BACKEND=oci" in content
    assert "OBJECT_STORAGE_REGION=us-chicago-1" in content
    assert "OBJECT_STORAGE_NAMESPACE=ns-keep" in content
    assert "OBJECT_STORAGE_BUCKET=originals" in content
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o640


def test_patch_rejects_incomplete_oci_settings_without_changes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    settings = FakeSettings()
    response = make_client(settings, env_file).patch(
        "/api/settings/upload-storage",
        json={"backend": "oci", "local_storage_dir": "/x", "object_storage_bucket": "b"},
    )
    assert response.status_code == 422
    assert "Object Storage リージョン" in response.json()["detail"]
    assert "Object Storage namespace" in response.json()["detail"]
    assert settings.upload_storage_backend == "local"
    assert not env_file.exists()


def test_patch_rejects_unsafe_object_storage_names(tmp_path: Path) -> None:
    response = make_client(FakeSettings(), tmp_path / ".env").patch(
        "/api/settings/upload-storage",
        json={"backend": "oci", "object_storage_bucket": "bad bucket;rm"},
    )
    assert response.status_code == 422


def test_patch_keeps_runtime_when_env_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(upload_storage, "write_env_values", fail)
    settings = FakeSettings()
    client = make_client(settings, tmp_path / ".env")

    response = client.patch(
        "/api/settings/upload-storage",
        json={"backend": "local", "local_storage_dir": "/u01/data/changed"},
    )

    assert response.status_code == 500
    assert settings.local_storage_dir == "/u01/data/production-ready-test"
    get_data = client.get("/api/settings/upload-storage").json()["data"]
    assert get_data["local_storage_dir"] == "/u01/data/production-ready-test"


def test_write_dependencies_apply_only_to_patch(tmp_path: Path) -> None:
    client = make_client(FakeSettings(), tmp_path / ".env", deny_write=True)
    assert client.get("/api/settings/upload-storage").status_code == 200
    assert (
        client.patch("/api/settings/upload-storage", json={"backend": "local"}).status_code == 403
    )


def test_write_env_values_removes_none_and_quotes_special_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\nB=2\nA=dup\n", encoding="utf-8")
    write_env_values(env_file, {"A": "a b", "B": None, "C": "c"}, section_comment="# 追加")
    assert env_file.read_text(encoding="utf-8") == 'A="a b"\n\n# 追加\nC=c\n'
