"""profile 単位のアクセス制御ヘルパ(nl2sql router / ontology router 共有)。

`Principal.allowed_profile_ids` に基づく行レベルアクセス判定を 1 箇所に集約する。
認証無効(APP_AUTH_ENABLED=false)のとき principal は存在せず、テナント概念が
無いため全 actor 制約を外す(管理者相当)。認証有効時は authorize_api_request が
principal を必ず設定する。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.security.domain import Principal
from app.security.permissions import PROFILE_MANAGE_PERMISSION


def principal_from_request(request: Request) -> Principal | None:
    principal = getattr(request.state, "principal", None)
    return principal if isinstance(principal, Principal) else None


def profile_access_denied() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail="この業務プロファイルを利用する権限がありません。",
    )


def assert_profile_access(
    request: Request,
    profile_id: str | None,
    *,
    default_profile: bool = False,
) -> None:
    """profile_id が principal の許可リストに含まれることを検証する。

    system admin、認証無効、業務プロファイル管理権限は無条件で許可。許可外は 403。
    """

    principal = principal_from_request(request)
    if principal is None or principal.has_permission(PROFILE_MANAGE_PERMISSION):
        return
    resolved = (profile_id or ("default" if default_profile else "")).strip()
    if resolved and resolved in principal.allowed_profile_ids:
        return
    raise profile_access_denied()
