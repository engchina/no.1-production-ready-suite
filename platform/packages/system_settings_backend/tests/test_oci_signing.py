"""OCI のリクエスト署名の httpx の auth（#5932）。

鍵はテストの中で生成し、署名は公開鍵で検証する（OCI には接続しない）。
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from pr_system_settings.oci_signing import (
    OciRequestSigningNotConfiguredError,
    oci_request_signing_auth,
    oci_request_signing_configured,
)

# 合成の OCID（実在しない。形だけ OCI SDK の検証に通す）。
USER_OCID = "ocid1.user.oc1.." + "a" * 24
TENANCY_OCID = "ocid1.tenancy.oc1.." + "b" * 24
URL = "https://gateway.example.test/agentendpoint/agent-a/a2a?mode=test"


def _write_oci_config(
    directory: Path, *, encrypted: bool = False
) -> tuple[Path, rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    encryption: serialization.KeySerializationEncryption = serialization.NoEncryption()
    if encrypted:
        encryption = serialization.BestAvailableEncryption(hashlib.sha256(b"synthetic").digest())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    )
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    digest = hashlib.md5(der, usedforsecurity=False).hexdigest()
    fingerprint = ":".join(digest[index : index + 2] for index in range(0, 32, 2))
    key_file = directory / "api_key.pem"
    key_file.write_bytes(pem)
    config_file = directory / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                f"user={USER_OCID}",
                f"fingerprint={fingerprint}",
                f"tenancy={TENANCY_OCID}",
                "region=ap-osaka-1",
                f"key_file={key_file}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_file, key, fingerprint


def _signed_request(config_file: Path, method: str, **kwargs: object) -> httpx.Request:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    auth = oci_request_signing_auth(str(config_file), "DEFAULT")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        client.request(method, URL, auth=auth, **kwargs)  # type: ignore[arg-type]
    [request] = seen
    return request


def _signature_fields(request: httpx.Request) -> dict[str, str]:
    authorization = request.headers["authorization"]
    assert authorization.startswith("Signature ")
    return dict(re.findall(r'(\w+)="([^"]*)"', authorization))


def _verify(request: httpx.Request, key: rsa.RSAPrivateKey) -> list[str]:
    """署名の対象の文字列を組み立て直し、公開鍵で検証する。署名したヘッダーの名前を返す。"""
    fields = _signature_fields(request)
    signed = fields["headers"].split(" ")
    lines = []
    for name in signed:
        if name == "(request-target)":
            path = request.url.raw_path.decode("ascii")
            lines.append(f"(request-target): {request.method.lower()} {path}")
        else:
            lines.append(f"{name}: {request.headers[name]}")
    key.public_key().verify(
        base64.b64decode(fields["signature"]),
        "\n".join(lines).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return signed


def test_post_is_signed_with_the_body_headers(tmp_path: Path) -> None:
    config_file, key, fingerprint = _write_oci_config(tmp_path)

    request = _signed_request(config_file, "POST", json={"jsonrpc": "2.0", "method": "x"})

    fields = _signature_fields(request)
    assert fields["keyId"] == f"{TENANCY_OCID}/{USER_OCID}/{fingerprint}"
    assert fields["algorithm"] == "rsa-sha256"
    signed = _verify(request, key)
    assert signed == [
        "date",
        "(request-target)",
        "host",
        "content-length",
        "content-type",
        "x-content-sha256",
    ]
    # 本文の hash は送った本文そのものの SHA-256（改変を検出できる）。
    assert request.headers["x-content-sha256"] == base64.b64encode(
        hashlib.sha256(request.content).digest()
    ).decode("ascii")
    assert request.headers["content-length"] == str(len(request.content))


def test_get_is_signed_without_the_body_headers(tmp_path: Path) -> None:
    config_file, key, _fingerprint = _write_oci_config(tmp_path)

    request = _signed_request(config_file, "GET")

    assert _verify(request, key) == ["date", "(request-target)", "host"]
    assert "x-content-sha256" not in request.headers


def test_missing_config_is_reported_without_details(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-config"

    with pytest.raises(OciRequestSigningNotConfiguredError) as raised:
        oci_request_signing_auth(str(missing), "DEFAULT")

    # 文に設定のパス・profile を出さない（種類だけを残す）。
    assert str(missing) not in str(raised.value)
    assert raised.value.reason
    assert oci_request_signing_configured(str(missing), "DEFAULT") is False


def test_encrypted_key_without_pass_phrase_does_not_prompt(tmp_path: Path) -> None:
    config_file, _key, _fingerprint = _write_oci_config(tmp_path, encrypted=True)

    with pytest.raises(OciRequestSigningNotConfiguredError) as raised:
        oci_request_signing_auth(str(config_file), "DEFAULT")

    assert "pass_phrase" in str(raised.value)


def test_configured_when_the_profile_loads(tmp_path: Path) -> None:
    config_file, _key, _fingerprint = _write_oci_config(tmp_path)

    assert oci_request_signing_configured(str(config_file), "DEFAULT") is True
    assert oci_request_signing_configured(str(config_file), "OTHER") is False
