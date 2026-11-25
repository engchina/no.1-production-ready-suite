"""権限カタログ（PERMISSION_CATALOG）と左のナビの一致（#580）。

権限管理の機能の一覧は、左のナビを正本にしてグループ・並び順・名前をそろえる（#567）。
frontend はナビに合わせて並べ替えるが、API（`GET /api/security/permissions`）の
group / label と説明（description）は backend のカタログのままなので、backend の
カタログもナビと同じにする。

frontend の nav-config.ts（セクションと項目の並び）・i18n.ts / rag-i18n.ts
（サイドナビの表示名）・permissions.ts（MENU_PERMISSIONS）を読み、メニュー権限の
group・label・並び順がサイドナビと一致することを確かめる。

承認の画面（`menu.approvals`）はナビに出さない（#5834。URL で開く）。カタログには残し、
グループはナビの「AI 活用」と同じ名前にする（権限管理の画面で同じグループに出る）。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.security.permissions import MENU_APPROVALS, PERMISSION_CATALOG, PermissionDefinition

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# ナビに出さない画面のメニュー権限（#5834）。frontend は lib/route-permissions.ts の
# HIDDEN_ROUTE_PERMISSIONS で route を守る。
HIDDEN_MENU_CODES = frozenset({MENU_APPROVALS})


def _nav_catalog() -> list[PermissionDefinition]:
    """ナビに出さない画面のメニュー権限を除いたカタログ（並びはそのまま）。"""
    return [item for item in PERMISSION_CATALOG if item.code not in HIDDEN_MENU_CODES]


# platform の共有パッケージ（system-settings の paths.ts）が決める共通の項目の
# （サイドナビの表示名の i18n key, MENU_PERMISSIONS の key）。
# nav-config は `.map(...)` で使う。
SHARED_NAV_ITEMS: dict[str, tuple[tuple[str, str], ...]] = {
    "USER_ROLE_NAV_ITEMS": (
        ("nav.securityUsers", "securityUsers"),
        ("nav.securityRoles", "securityRoles"),
    ),
    "SYSTEM_SETTINGS_NAV_ITEMS": (
        ("nav.settingsOci.sidebar", "settingsOci"),
        ("nav.settingsUploadStorage", "settingsUploadStorage"),
        ("nav.settingsModel.sidebar", "settingsModel"),
        ("nav.settingsDatabase.sidebar", "settingsDatabase"),
        ("nav.settingsAppearance", "settingsAppearance"),
    ),
}

_NAV_TOKEN = re.compile(
    r'\btitleKey: "(?P<title>[^"]+)"'
    r'|\blabelKey: "(?P<label>[^"]+)"'
    r'|\bsidebarLabelKey: "(?P<sidebar>[^"]+)"'
    r"|\bpermission: MENU_PERMISSIONS\.(?P<permission>\w+)"
    r"|\b(?P<shared>USER_ROLE_NAV_ITEMS|SYSTEM_SETTINGS_NAV_ITEMS)\.map\("
)
_I18N_ENTRY = re.compile(r'^\s*"(?P<key>[^"]+)": "(?P<value>[^"]*)",?$', re.MULTILINE)
_MENU_CODE = re.compile(r'^\s*(?P<key>\w+): "(?P<code>menu\.[a-z0-9_]+)",$', re.MULTILINE)


def _read(relative: str) -> str:
    return (FRONTEND_SRC / relative).read_text(encoding="utf-8")


def _entries(relative: str) -> dict[str, str]:
    return {match["key"]: match["value"] for match in _I18N_ENTRY.finditer(_read(relative))}


def _i18n() -> dict[str, str]:
    """frontend の lib/i18n.ts の `ja` と同じ順に重ねる（rag-i18n.ts → Agent の文言）。"""
    return {**_entries("lib/rag-i18n.ts"), **_entries("lib/i18n.ts")}


def _sidebar_nav() -> list[tuple[str, list[tuple[str, str]]]]:
    """サイドナビの（見出し, [(表示名, メニュー権限の code)]）を nav-config の順に返す。

    表示名は共通の Sidebar と同じ規則（`sidebarLabelKey` があればそれ、なければ `labelKey`）。
    """
    i18n = _i18n()
    codes = {
        match["key"]: match["code"] for match in _MENU_CODE.finditer(_read("lib/permissions.ts"))
    }
    sections: list[tuple[str, list[list[str]]]] = []
    for match in _NAV_TOKEN.finditer(_read("components/layout/nav-config.ts")):
        if match["title"]:
            sections.append((match["title"], []))
        elif match["label"]:
            sections[-1][1].append([match["label"], ""])
        elif match["sidebar"]:
            sections[-1][1][-1][0] = match["sidebar"]
        elif match["permission"]:
            sections[-1][1][-1][1] = match["permission"]
        else:
            sections[-1][1].extend([list(item) for item in SHARED_NAV_ITEMS[match["shared"]]])
    return [
        (i18n[title], [(i18n[label], codes[permission]) for label, permission in items])
        for title, items in sections
    ]


def test_menu_permissions_follow_sidebar_nav() -> None:
    """メニュー権限は、サイドナビと同じグループ・並び順・名前で先頭に並ぶ。"""
    nav = [(title, label, code) for title, items in _sidebar_nav() for label, code in items]
    assert len(nav) > 15
    menus = _nav_catalog()[: len(nav)]
    assert [(item.group, item.label, item.code) for item in menus] == nav


def test_permissions_outside_nav_follow_menus() -> None:
    """ナビに無い権限（capability）はメニュー権限の後ろに置く。"""
    nav_codes = {code for _, items in _sidebar_nav() for _, code in items}
    rest = _nav_catalog()[len(nav_codes) :]
    assert rest
    assert all(not item.code.startswith("menu.") for item in rest)
    assert not nav_codes & HIDDEN_MENU_CODES
    assert {
        item.code for item in PERMISSION_CATALOG if item.code.startswith("menu.")
    } == nav_codes | HIDDEN_MENU_CODES


def test_hidden_menu_permissions_stay_in_nav_group() -> None:
    """ナビに出さない画面のメニュー権限は、カタログに残り、ナビにあるグループの名前を使う。"""
    nav_groups = {title for title, _ in _sidebar_nav()}
    hidden = [item for item in PERMISSION_CATALOG if item.code in HIDDEN_MENU_CODES]
    assert {item.code for item in hidden} == HIDDEN_MENU_CODES
    assert all(item.group in nav_groups for item in hidden)


def test_menu_descriptions_use_nav_label() -> None:
    """メニュー権限の説明は、ナビの名前と同じ用語（「名前」の画面）で書く。"""
    for item in PERMISSION_CATALOG:
        if item.code.startswith("menu."):
            assert item.description.startswith(f"「{item.label}」の画面を表示し"), item.code
