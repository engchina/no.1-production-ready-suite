"""左メニュー単位の permission catalog と API route manifest。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    code: str
    group: str
    label: str
    description: str
    implies: tuple[str, ...] = ()


def _permission(
    code: str,
    group: str,
    label: str,
    description: str,
    *,
    implies: tuple[str, ...] = (),
) -> PermissionDefinition:
    return PermissionDefinition(code, group, label, description, implies)


def _menu_permission(code: str, group: str, label: str) -> PermissionDefinition:
    return _permission(
        code,
        group,
        label,
        f"{label}を表示し、関連操作を利用できます。",
    )


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _menu_permission("menu.query", "AI 活用", "SQL 生成"),
    _menu_permission("menu.direct_sql", "AI 活用", "SELECT SQL を実行"),
    _menu_permission("menu.sql_to_question", "AI 活用", "SQL から質問を生成"),
    _menu_permission("menu.history", "AI 活用", "実行履歴"),
    _menu_permission("menu.admin_sql", "データ準備", "管理 SQL を実行"),
    _menu_permission("menu.table_management", "データ準備", "テーブルの管理"),
    _menu_permission("menu.view_management", "データ準備", "ビューの管理"),
    _menu_permission("menu.data_management", "データ準備", "データの管理"),
    _menu_permission("menu.comment_management", "データ準備", "コメント管理"),
    _menu_permission("menu.annotation_management", "データ準備", "アノテーション管理"),
    _menu_permission("menu.glossary_rules", "データ準備", "用語・同義語"),
    _menu_permission("menu.global_rules", "データ準備", "共通ルール"),
    _menu_permission("menu.sample_data", "データ準備", "検証用サンプルデータ"),
    _menu_permission("menu.profiles", "改善・運用", "業務プロファイル"),
    _menu_permission("menu.ontology_build", "改善・運用", "オントロジー構築"),
    _menu_permission("menu.feedback_management", "改善・運用", "フィードバック管理"),
    _menu_permission("menu.question_classifier_models", "改善・運用", "質問分類モデル管理"),
    _menu_permission("menu.evaluation", "改善・運用", "品質評価"),
    _menu_permission("menu.security_users", "セキュリティ管理", "ユーザー管理"),
    _menu_permission("menu.security_roles", "セキュリティ管理", "ロール・権限管理"),
    _menu_permission("menu.security_deepsec", "セキュリティ管理", "Deep Data Security"),
    _menu_permission("menu.settings_oci", "システム設定", "OCI 認証"),
    _menu_permission("menu.settings_upload_storage", "システム設定", "アップロード保存先"),
    _menu_permission("menu.settings_model", "システム設定", "モデル"),
    _menu_permission("menu.settings_database", "システム設定", "データベース"),
    _menu_permission("menu.settings_system_tables", "システム設定", "システムテーブル"),
    _menu_permission("menu.settings_appearance", "システム設定", "外観"),
)

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)
UNCLASSIFIED_PERMISSION = "__unclassified__"

AI_USE_MENUS = frozenset(
    {
        "menu.query",
        "menu.direct_sql",
        "menu.sql_to_question",
        "menu.history",
    }
)
DATA_PREP_MENUS = frozenset(
    {
        "menu.admin_sql",
        "menu.table_management",
        "menu.view_management",
        "menu.data_management",
        "menu.comment_management",
        "menu.annotation_management",
        "menu.glossary_rules",
        "menu.global_rules",
        "menu.sample_data",
    }
)
BUSINESS_MODEL_MENUS = frozenset(
    {
        "menu.profiles",
        "menu.ontology_build",
        "menu.glossary_rules",
        "menu.global_rules",
    }
)
IMPROVEMENT_MENUS = frozenset(
    {
        "menu.profiles",
        "menu.ontology_build",
        "menu.feedback_management",
        "menu.question_classifier_models",
        "menu.evaluation",
    }
)
SCHEMA_READ_MENUS = frozenset(
    {
        "menu.query",
        "menu.direct_sql",
        "menu.admin_sql",
        "menu.table_management",
        "menu.view_management",
        "menu.data_management",
        "menu.comment_management",
        "menu.annotation_management",
        "menu.profiles",
        "menu.ontology_build",
        "menu.glossary_rules",
        "menu.global_rules",
        "menu.settings_database",
        "menu.settings_system_tables",
    }
)

LEGACY_PERMISSION_ALIASES: dict[str, tuple[str, ...]] = {
    "dashboard.view": ("menu.settings_appearance",),
    "documents.view": (
        "menu.table_management",
        "menu.view_management",
        "menu.data_management",
        "menu.comment_management",
        "menu.annotation_management",
        "menu.sample_data",
    ),
    "documents.upload": ("menu.data_management", "menu.sample_data"),
    "documents.preview": (
        "menu.table_management",
        "menu.view_management",
        "menu.data_management",
    ),
    "documents.approve": ("menu.data_management",),
    "documents.ingest": ("menu.data_management",),
    "documents.delete": (
        "menu.table_management",
        "menu.view_management",
        "menu.data_management",
    ),
    "knowledge_bases.view": tuple(BUSINESS_MODEL_MENUS),
    "knowledge_bases.manage": tuple(BUSINESS_MODEL_MENUS),
    "business_views.view": ("menu.profiles",),
    "business_views.manage": ("menu.profiles",),
    "business_views.use": ("menu.query", "menu.profiles"),
    "search.view": tuple(AI_USE_MENUS),
    "search.execute": ("menu.query", "menu.direct_sql"),
    "search.export": ("menu.query", "menu.direct_sql", "menu.history"),
    "evaluation.view": (
        "menu.feedback_management",
        "menu.question_classifier_models",
        "menu.evaluation",
    ),
    "evaluation.run": ("menu.evaluation",),
    "evaluation.manage": (
        "menu.feedback_management",
        "menu.question_classifier_models",
        "menu.evaluation",
    ),
    "settings.oci.view": ("menu.settings_oci",),
    "settings.oci.manage": ("menu.settings_oci",),
    "settings.object_storage.view": ("menu.settings_upload_storage",),
    "settings.object_storage.manage": ("menu.settings_upload_storage",),
    "settings.models.view": ("menu.settings_model",),
    "settings.models.manage": ("menu.settings_model",),
    "settings.database.view": (
        "menu.settings_database",
        "menu.settings_system_tables",
    ),
    "settings.database.manage": ("menu.settings_database",),
    "settings.database.sql_execute": (
        "menu.admin_sql",
        "menu.settings_system_tables",
    ),
    "settings.appearance.view": ("menu.settings_appearance",),
    "security.users.view": ("menu.security_users",),
    "security.users.manage": ("menu.security_users",),
    "security.roles.view": ("menu.security_roles",),
    "security.roles.manage": ("menu.security_roles",),
    "security.deepsec.view": ("menu.security_deepsec",),
    "security.deepsec.apply": ("menu.security_deepsec",),
    "security.deepsec.verify": ("menu.security_deepsec",),
}

for _adapter in (
    "preprocess",
    "parser",
    "chunking",
    "vector_index",
    "retrieval",
    "grounding",
    "generation",
    "guardrail",
    "evaluation",
    "graph",
    "agentic",
):
    LEGACY_PERMISSION_ALIASES[f"pipeline.{_adapter}.view"] = ("menu.settings_model",)
    LEGACY_PERMISSION_ALIASES[f"pipeline.{_adapter}.manage"] = ("menu.settings_model",)


def normalize_permission_codes(codes: Iterable[str]) -> set[str]:
    """保存済み legacy action 権限を現在の menu 権限へ正規化する。"""

    normalized: set[str] = set()
    pending = [code.strip() for code in codes if code and code.strip()]
    while pending:
        code = pending.pop()
        if code in ALL_PERMISSION_CODES:
            normalized.add(code)
            continue
        pending.extend(LEGACY_PERMISSION_ALIASES.get(code, ()))
    return normalized


def unknown_permission_codes(codes: Iterable[str]) -> set[str]:
    """menu catalog と legacy alias のどちらにも存在しない code を返す。"""

    return {
        code.strip()
        for code in codes
        if code.strip()
        and code.strip() not in ALL_PERMISSION_CODES
        and code.strip() not in LEGACY_PERMISSION_ALIASES
    }


def expand_permissions(codes: set[str]) -> set[str]:
    """旧 action permission を含む入力を menu permission set へ閉包する。"""

    return normalize_permission_codes(codes)


PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/ready/database", "/auth/login"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})


def _allowed(*codes: str) -> frozenset[str]:
    return frozenset(codes)


def permission_for_route(method: str, route_path: str) -> frozenset[str] | None:
    """FastAPI の method + route template を許可 menu permission set へ写像する。"""

    method = method.upper()
    if route_path in PUBLIC_API_PATHS or route_path in AUTHENTICATED_WITHOUT_PERMISSION:
        return None
    if route_path.startswith("/security/users"):
        return _allowed("menu.security_users")
    if route_path.startswith("/security/roles") or route_path == "/security/permissions":
        return _allowed("menu.security_roles")
    if route_path.startswith("/security/deepsec"):
        return _allowed("menu.security_deepsec")
    if route_path.startswith("/settings/oci/object-storage"):
        return _allowed("menu.settings_oci", "menu.settings_upload_storage")
    if route_path.startswith("/settings/oci"):
        return _allowed("menu.settings_oci")
    if route_path.startswith("/settings/upload-storage"):
        return _allowed("menu.settings_upload_storage")
    if route_path.startswith("/settings/model"):
        return _allowed("menu.settings_model")
    if route_path.startswith("/settings/database/system-tables"):
        return _allowed("menu.settings_system_tables")
    if route_path.startswith("/settings/database"):
        return _allowed("menu.settings_database")
    if route_path.startswith("/schema"):
        return SCHEMA_READ_MENUS
    if route_path.startswith("/nl2sql/db-admin"):
        if route_path.startswith("/nl2sql/db-admin/tables"):
            return _allowed("menu.table_management")
        if route_path.startswith("/nl2sql/db-admin/views"):
            return _allowed("menu.view_management")
        if route_path.endswith("/drop-table") or route_path.endswith("/truncate-table"):
            return _allowed("menu.table_management")
        if route_path.endswith("/drop-view"):
            return _allowed("menu.view_management")
        if route_path.endswith("/upload-csv") or route_path.endswith("/import-tabular"):
            return _allowed("menu.data_management")
        if route_path.endswith("/preview-data") or route_path.endswith("/preview-data/export.xlsx"):
            return _allowed(
                "menu.table_management",
                "menu.view_management",
                "menu.data_management",
                "menu.comment_management",
                "menu.annotation_management",
            )
        if route_path.endswith("/execute") or route_path.endswith("/statements"):
            return _allowed("menu.admin_sql")
        if method == "GET":
            return DATA_PREP_MENUS
        return _allowed("menu.admin_sql")
    if route_path.startswith("/nl2sql/sample-data"):
        return _allowed("menu.sample_data")
    if route_path.startswith("/nl2sql/comments"):
        return _allowed("menu.comment_management")
    if route_path.startswith("/nl2sql/annotations"):
        return _allowed("menu.annotation_management")
    if route_path.startswith("/nl2sql/metadata-samples"):
        return _allowed("menu.comment_management", "menu.annotation_management")
    if route_path.startswith("/nl2sql/synthetic-data"):
        return _allowed("menu.sample_data", "menu.data_management")
    if route_path.startswith("/nl2sql/profiles/search"):
        return AI_USE_MENUS | BUSINESS_MODEL_MENUS
    if route_path.startswith("/nl2sql/profiles"):
        return _allowed("menu.profiles", "menu.glossary_rules", "menu.global_rules")
    if route_path.startswith("/nl2sql/ontology"):
        return _allowed("menu.ontology_build")
    if route_path.startswith("/nl2sql/feedback/admin-review"):
        return _allowed("menu.feedback_management")
    if (
        route_path.startswith("/nl2sql/feedback-index")
        or route_path.startswith("/nl2sql/feedback-entries")
        or route_path.startswith("/nl2sql/feedback-config")
    ):
        return _allowed("menu.feedback_management")
    if route_path == "/nl2sql/feedback":
        return _allowed("menu.query", "menu.feedback_management")
    if route_path.startswith("/nl2sql/classifier"):
        return _allowed("menu.question_classifier_models")
    if route_path.startswith("/nl2sql/quality-evaluations"):
        return _allowed("menu.evaluation")
    if route_path.startswith("/nl2sql/reverse"):
        return _allowed("menu.sql_to_question")
    if route_path.startswith("/nl2sql/history"):
        return AI_USE_MENUS
    if route_path.startswith("/nl2sql"):
        if route_path.startswith("/nl2sql/persistence") or route_path.startswith(
            "/nl2sql/diagnostics"
        ):
            return ALL_PERMISSION_CODES
        if method == "GET":
            return AI_USE_MENUS | IMPROVEMENT_MENUS
        if "export" in route_path:
            return _allowed("menu.query", "menu.direct_sql", "menu.history")
        return _allowed("menu.query", "menu.direct_sql")
    return _allowed(UNCLASSIFIED_PERMISSION)
