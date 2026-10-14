"""一覧に出す利用者の表示名（実行履歴の利用者。#1890。Agent の `user_names` と同じ規則）。

画面の利用者は表示名で出し、UUID・ログイン ID は出さない（#1696）。構成のシステム管理者
（認証テーブルに無い固定の UUID）は、共通認証の `display_identities` が
表示名「システム管理者」で補う。
引けない利用者（削除済みなど）は省き、画面は「（削除済みのユーザー）」と出す。
"""

from __future__ import annotations

import logging

from app.security.dependencies import local_debug_principal
from app.security.domain import LOCAL_DEBUG_USER_UUID
from app.security.service import get_security_service

logger = logging.getLogger(__name__)


def user_display_names(user_uuids: list[str]) -> dict[str, str]:
    """利用者の UUID → 表示名（ローカル利用者・構成のシステム管理者を含む）。引けない人は省く。"""
    names: dict[str, str] = {}
    if LOCAL_DEBUG_USER_UUID in user_uuids:
        names[LOCAL_DEBUG_USER_UUID] = local_debug_principal().display_name
    remaining = [uuid for uuid in user_uuids if uuid and uuid not in names]
    if not remaining:
        return names
    try:
        identities = get_security_service().display_identities(remaining)
    except Exception:  # noqa: BLE001 - 名前は表示の補助。引けなくても一覧は返す
        logger.warning("rag_user_names_unavailable", exc_info=True)
        return names
    for uuid, identity in identities.items():
        names[uuid] = identity.display_name or identity.login_user_id
    return names
