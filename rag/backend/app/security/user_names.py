"""会話の利用者の表示名（system_admin の会話の一覧の「利用者」。#1889・#1696）。

RAG の会話は利用者を `user_id_hash`（salt + 利用者 ID の SHA-256）でしか持たないので、共通認証の
利用者の UUID を同じ hash にして表示名を引き当てる（利用者の数は少ない）。構成のシステム管理者と
local の利用者も含める。引けない利用者（削除済みなど）は省き、画面は「（削除済みのユーザー）」と
出す。
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from pr_system_settings.auth.domain import (
    CONFIGURED_SYSTEM_ADMIN_USER_UUID,
    LOCAL_DEBUG_USER_UUID,
)

from app.rag.request_context import user_id_hash_for

from .service import get_security_service

logger = logging.getLogger(__name__)

# local で `X-User-ID` が無いときの会話の持ち主（`dependencies.LOCAL_DEFAULT_USER_ID`）。
_LOCAL_USER_IDS = ("local-user", LOCAL_DEBUG_USER_UUID)
_LOCAL_USER_NAME = "ローカル利用者"
_CONFIGURED_ADMIN_NAME = "システム管理者"


def display_names_by_user_hash(user_id_hashes: Collection[str]) -> dict[str, str]:
    """`user_id_hash` → 表示名。引けない hash は省く。"""
    wanted = {value for value in user_id_hashes if value}
    if not wanted:
        return {}
    candidates: dict[str, str] = {user_id: _LOCAL_USER_NAME for user_id in _LOCAL_USER_IDS}
    candidates[CONFIGURED_SYSTEM_ADMIN_USER_UUID] = _CONFIGURED_ADMIN_NAME
    try:
        for user in get_security_service().list_users():
            candidates[user.user_uuid] = user.display_name or user.login_user_id
    except Exception:  # noqa: BLE001 - 名前は表示の補助。引けなくても一覧は返す
        logger.warning("rag_conversation_owner_names_unavailable", exc_info=True)
    names: dict[str, str] = {}
    for user_id, name in candidates.items():
        hashed = user_id_hash_for(user_id)
        if hashed is not None and hashed in wanted:
            names[hashed] = name
    return names
