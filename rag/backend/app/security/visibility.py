"""「AI 活用」の機能のデータの見え方（#1889 の規則）。

system_admin（構成管理者・local の利用者を含む）は全員のデータを見られ、それ以外の利用者は自分の
データだけを見る。#1889 で `pr_system_settings` に 3 製品共通の判定が入ったら、この関数をそれに
置き換える（呼び出し側は `can_view_all_users` だけを使う）。
"""

from __future__ import annotations

from typing import Protocol


class _Principal(Protocol):
    @property
    def is_system_admin(self) -> bool: ...


def can_view_all_users(principal: _Principal) -> bool:
    """全員の分のデータ（実行履歴など）を見られるか。system_admin だけ True。"""
    return bool(principal.is_system_admin)
