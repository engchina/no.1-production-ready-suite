"""集計・一覧に出す利用者の表示名（#772 / #774）。"""

from __future__ import annotations

import logging

from app.security.dependencies import local_debug_principal
from app.security.domain import LOCAL_DEBUG_USER_UUID
from app.security.service import get_security_service

logger = logging.getLogger(__name__)


def user_display_names(user_uuids: list[str]) -> dict[str, str]:
    """共通認証の利用者の表示名（local のローカル利用者を含む）。引けない人は省く。"""
    names: dict[str, str] = {}
    if LOCAL_DEBUG_USER_UUID in user_uuids:
        names[LOCAL_DEBUG_USER_UUID] = local_debug_principal().display_name
    remaining = [uuid for uuid in user_uuids if uuid not in names]
    if not remaining:
        return names
    try:
        identities = get_security_service().store.get_user_identities(remaining)
    except Exception:  # noqa: BLE001 - 名前は表示の補助。引けなくても集計は返す
        logger.warning("agent_user_names_unavailable", exc_info=True)
        return names
    for uuid, identity in identities.items():
        names[uuid] = identity.display_name or identity.login_user_id
    return names
