"""「AI 活用」の下の機能のデータの見え方（3 製品共通。#1889）。

規則: **system_admin は全員のデータ、それ以外の利用者は自分のデータだけ**を、一覧・詳細・操作で
扱う（RAG のチャット・RAG 検索・実行履歴、NL2SQL のチャット・SQL 生成・SQL 直接実行・SQL から質問・
実行履歴、Agent のチャット・承認・実行履歴）。

- system_admin は SYSTEM_ADMIN のロールを持つ利用者（構成管理者 `system_admin` と、local の
  ローカル利用者を含む）。製品の管理権限（`agent.admin` など）やメニュー権限は、全員を見る
  理由にしない。
- 判定は backend で行い、一覧は SQL / store の条件で絞る（画面で隠すだけにしない）。他人のデータの
  詳細・操作は 404 にして存在を漏らさない（製品の API の契約。`OWNER_NOT_FOUND_STATUS`）。
- system_admin の一覧には「利用者」の列（共通の表示名。`AuthService.display_identities`。
  #1696）を出す。
- 「AI 活用」の外（改善・運用の監査・利用状況・フィードバック管理など）はこの規則の対象外で、
  それぞれの権限のまま。
"""

from __future__ import annotations

from typing import Protocol

# 他人のデータの詳細・操作に返す status（存在を漏らさない）。
OWNER_NOT_FOUND_STATUS = 404


class _AdminFlag(Protocol):
    @property
    def is_system_admin(self) -> bool: ...


class _UserPrincipal(_AdminFlag, Protocol):
    @property
    def user_uuid(self) -> str: ...


def can_view_all_users_data(principal: _AdminFlag | None) -> bool:
    """「AI 活用」の下の機能で、全員のデータを見られるか（system_admin だけ）。"""
    return bool(principal is not None and principal.is_system_admin)


def owner_filter_user_uuid(principal: _UserPrincipal) -> str | None:
    """一覧を絞る持ち主の UUID。全員を見られる利用者は None（絞らない）。"""
    return None if can_view_all_users_data(principal) else principal.user_uuid


def can_access_user_data(principal: _UserPrincipal | None, owner_user_uuid: str | None) -> bool:
    """`owner_user_uuid` の持ち主のデータの詳細を見て操作してよいか。

    持ち主の分からないデータ（記録の無い古い行など）は system_admin だけが扱える。
    """
    if principal is None:
        return False
    if can_view_all_users_data(principal):
        return True
    owner = (owner_user_uuid or "").strip()
    return bool(owner) and owner == principal.user_uuid
