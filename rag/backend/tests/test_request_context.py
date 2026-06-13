"""監査用リクエストコンテキストのテスト。"""

from app.config import Settings
from app.rag.request_context import (
    AuditRequestContext,
    audit_request_context_from_headers,
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)


def test_audit_request_context_hashes_tenant_and_user_headers() -> None:
    """tenant/user header は raw 値ではなく hash として保持する。"""
    context = audit_request_context_from_headers(
        {
            "x-tenant-id": "tenant-a",
            "x-user-id": "user@example.com",
        },
        request_id="request-1",
        settings=Settings(audit_context_hash_salt="salt-1"),
    )

    assert context.request_id == "request-1"
    assert context.tenant_id_hash
    assert context.user_id_hash
    assert len(context.tenant_id_hash) == 64
    assert len(context.user_id_hash) == 64
    assert "tenant-a" not in repr(context)
    assert "user@example.com" not in repr(context)


def test_audit_context_hash_salt_changes_identifier_hashes() -> None:
    """salt を変えると同じ id でも hash が変わる。"""
    first = audit_request_context_from_headers(
        {"x-tenant-id": "tenant-a"},
        request_id="request-1",
        settings=Settings(audit_context_hash_salt="salt-1"),
    )
    second = audit_request_context_from_headers(
        {"x-tenant-id": "tenant-a"},
        request_id="request-1",
        settings=Settings(audit_context_hash_salt="salt-2"),
    )

    assert first.tenant_id_hash != second.tenant_id_hash


def test_audit_context_ignores_invalid_or_oversized_header_values() -> None:
    """制御文字や過大な header は監査 context へ入れない。"""
    context = audit_request_context_from_headers(
        {
            "x-tenant-id": "tenant\nbad",
            "x-user-id": "u" * 257,
        },
        request_id="request-1",
        settings=Settings(),
    )

    assert context.tenant_id_hash is None
    assert context.user_id_hash is None


def test_audit_request_context_is_scoped_to_current_context() -> None:
    """contextvars の設定・復元ができる。"""
    assert current_audit_request_context() == AuditRequestContext()

    token = set_audit_request_context(
        AuditRequestContext(
            request_id="request-1",
            tenant_id_hash="a" * 64,
            user_id_hash="b" * 64,
        )
    )
    try:
        context = current_audit_request_context()
        assert context.request_id == "request-1"
        assert context.tenant_id_hash == "a" * 64
        assert context.user_id_hash == "b" * 64
    finally:
        reset_audit_request_context(token)

    assert current_audit_request_context() == AuditRequestContext()
