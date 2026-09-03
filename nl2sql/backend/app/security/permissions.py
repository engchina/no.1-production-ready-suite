"""Permission catalog と API route manifest。"""

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


def _menu_permission(
    code: str,
    group: str,
    label: str,
    *,
    implies: tuple[str, ...] = (),
) -> PermissionDefinition:
    return _permission(
        code,
        group,
        label,
        f"{label}を表示し、関連操作を利用できます。",
        implies=implies,
    )


PROFILE_READ_PERMISSION = "nl2sql.profiles.read"
PROFILE_MANAGE_PERMISSION = "nl2sql.profiles.manage"
SCHEMA_READ_PERMISSION = "nl2sql.schema.read"
SCHEMA_REFRESH_PERMISSION = "nl2sql.schema.refresh"
QUERY_GENERATE_PERMISSION = "nl2sql.query.generate"
SQL_EXECUTE_PERMISSION = "nl2sql.sql.execute"
FEEDBACK_WRITE_PERMISSION = "nl2sql.feedback.write"
FEEDBACK_MANAGE_PERMISSION = "nl2sql.feedback.manage"
SELECT_AI_ASSETS_READ_PERMISSION = "nl2sql.select_ai_assets.read"
SELECT_AI_ASSETS_REFRESH_PERMISSION = "nl2sql.select_ai_assets.refresh"
SELECT_AI_ASSETS_MANAGE_PERMISSION = "nl2sql.select_ai_assets.manage"
SAMPLE_DATA_MANAGE_PERMISSION = "nl2sql.sample_data.manage"
LEARNING_MATERIAL_MANAGE_PERMISSION = "nl2sql.learning_material.manage"
SYSTEM_STATUS_READ_PERMISSION = "nl2sql.system_status.read"
PERSISTENCE_RECOVER_PERMISSION = "nl2sql.persistence.recover"


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _menu_permission(
        "menu.query",
        "AI 活用",
        "SQL 生成",
        implies=(
            QUERY_GENERATE_PERMISSION,
            SQL_EXECUTE_PERMISSION,
            FEEDBACK_WRITE_PERMISSION,
            PROFILE_READ_PERMISSION,
            SCHEMA_READ_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.direct_sql",
        "AI 活用",
        "SELECT SQL を実行",
        implies=(SQL_EXECUTE_PERMISSION, SCHEMA_READ_PERMISSION),
    ),
    _menu_permission(
        "menu.sql_to_question",
        "AI 活用",
        "SQL から質問を生成",
        implies=(PROFILE_READ_PERMISSION, SCHEMA_READ_PERMISSION),
    ),
    _menu_permission("menu.history", "AI 活用", "実行履歴"),
    _menu_permission(
        "menu.admin_sql",
        "データ準備",
        "管理 SQL を実行",
        implies=(
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            SYSTEM_STATUS_READ_PERMISSION,
            PERSISTENCE_RECOVER_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.table_management",
        "データ準備",
        "テーブルの管理",
        implies=(SCHEMA_READ_PERMISSION, SCHEMA_REFRESH_PERMISSION),
    ),
    _menu_permission(
        "menu.view_management",
        "データ準備",
        "ビューの管理",
        implies=(SCHEMA_READ_PERMISSION, SCHEMA_REFRESH_PERMISSION),
    ),
    _menu_permission(
        "menu.data_management",
        "データ準備",
        "データの管理",
        implies=(
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            SELECT_AI_ASSETS_READ_PERMISSION,
            SELECT_AI_ASSETS_REFRESH_PERMISSION,
        ),
    ),
    _menu_permission("menu.comment_management", "データ準備", "コメント管理"),
    _menu_permission("menu.annotation_management", "データ準備", "アノテーション管理"),
    _menu_permission(
        "menu.glossary_rules",
        "データ準備",
        "用語・同義語",
        implies=(
            PROFILE_MANAGE_PERMISSION,
            LEARNING_MATERIAL_MANAGE_PERMISSION,
            SCHEMA_READ_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.global_rules",
        "データ準備",
        "共通ルール",
        implies=(
            PROFILE_MANAGE_PERMISSION,
            LEARNING_MATERIAL_MANAGE_PERMISSION,
            SCHEMA_READ_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.sample_data",
        "データ準備",
        "検証用サンプルデータ",
        implies=(SAMPLE_DATA_MANAGE_PERMISSION, SCHEMA_READ_PERMISSION),
    ),
    _menu_permission(
        "menu.profiles",
        "改善・運用",
        "業務プロファイル",
        implies=(
            PROFILE_MANAGE_PERMISSION,
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            SELECT_AI_ASSETS_READ_PERMISSION,
            SELECT_AI_ASSETS_REFRESH_PERMISSION,
            SELECT_AI_ASSETS_MANAGE_PERMISSION,
            LEARNING_MATERIAL_MANAGE_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.ontology_build",
        "改善・運用",
        "オントロジー構築",
        implies=(
            PROFILE_MANAGE_PERMISSION,
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            LEARNING_MATERIAL_MANAGE_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.feedback_management",
        "改善・運用",
        "フィードバック管理",
        implies=(
            PROFILE_READ_PERMISSION,
            FEEDBACK_WRITE_PERMISSION,
            FEEDBACK_MANAGE_PERMISSION,
            SELECT_AI_ASSETS_READ_PERMISSION,
            SELECT_AI_ASSETS_MANAGE_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.question_classifier_models",
        "改善・運用",
        "質問分類モデル管理",
        implies=(PROFILE_READ_PERMISSION,),
    ),
    _menu_permission(
        "menu.evaluation",
        "改善・運用",
        "SQL生成評価",
        implies=(PROFILE_READ_PERMISSION, QUERY_GENERATE_PERMISSION),
    ),
    _menu_permission("menu.security_users", "セキュリティ管理", "ユーザー管理"),
    _menu_permission("menu.security_roles", "セキュリティ管理", "ロール・権限管理"),
    _menu_permission("menu.security_deepsec", "セキュリティ管理", "Deep Data Security"),
    _menu_permission("menu.settings_oci", "システム設定", "OCI 認証"),
    _menu_permission("menu.settings_upload_storage", "システム設定", "アップロード保存先"),
    _menu_permission("menu.settings_model", "システム設定", "モデル"),
    _menu_permission(
        "menu.settings_database",
        "システム設定",
        "データベース",
        implies=(
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            SYSTEM_STATUS_READ_PERMISSION,
            PERSISTENCE_RECOVER_PERMISSION,
        ),
    ),
    _menu_permission(
        "menu.settings_system_tables",
        "システム設定",
        "システムテーブル",
        implies=(
            SCHEMA_READ_PERMISSION,
            SCHEMA_REFRESH_PERMISSION,
            SYSTEM_STATUS_READ_PERMISSION,
            PERSISTENCE_RECOVER_PERMISSION,
        ),
    ),
    _menu_permission("menu.settings_appearance", "システム設定", "外観"),
    _permission(
        PROFILE_READ_PERMISSION,
        "参照権限",
        "業務プロファイル参照",
        "SQL 生成や SQL から質問生成で、業務プロファイルの選択肢と利用コンテキストを参照できます。",
    ),
    _permission(
        PROFILE_MANAGE_PERMISSION,
        "管理権限",
        "業務プロファイル管理",
        "業務プロファイルの詳細表示、作成、更新、削除、Oracle 反映を実行できます。",
        implies=(PROFILE_READ_PERMISSION,),
    ),
    _permission(
        SCHEMA_READ_PERMISSION,
        "参照権限",
        "スキーマ参照",
        "SQL 生成や管理画面で、表・ビュー・列の参照情報を読み取れます。",
    ),
    _permission(
        SCHEMA_REFRESH_PERMISSION,
        "管理権限",
        "スキーマ更新",
        "Oracle から表・ビュー・列の最新情報を再取得できます。",
        implies=(SCHEMA_READ_PERMISSION,),
    ),
    _permission(
        QUERY_GENERATE_PERMISSION,
        "実行権限",
        "SQL 生成実行",
        "自然言語から SQL を生成し、推薦・書き換え・類似履歴を利用できます。",
    ),
    _permission(
        SQL_EXECUTE_PERMISSION,
        "実行権限",
        "SELECT SQL 実行",
        "SELECT/WITH SQL の安全確認と実行を利用できます。",
    ),
    _permission(
        FEEDBACK_WRITE_PERMISSION,
        "実行権限",
        "フィードバック登録",
        "自分の SQL 生成履歴へ利用者フィードバックを登録できます。",
    ),
    _permission(
        FEEDBACK_MANAGE_PERMISSION,
        "管理権限",
        "フィードバック管理",
        "全利用者のフィードバック一覧、管理者レビュー、学習 index 設定を管理できます。",
        implies=(FEEDBACK_WRITE_PERMISSION, PROFILE_READ_PERMISSION),
    ),
    _permission(
        SELECT_AI_ASSETS_READ_PERMISSION,
        "参照権限",
        "Select AI 資産参照",
        "Oracle Select AI / Agent の profile・資産状態を参照できます。",
    ),
    _permission(
        SELECT_AI_ASSETS_REFRESH_PERMISSION,
        "管理権限",
        "Select AI 資産更新",
        "Oracle Select AI / Agent の資産情報を再取得・反映できます。",
        implies=(SELECT_AI_ASSETS_READ_PERMISSION,),
    ),
    _permission(
        SELECT_AI_ASSETS_MANAGE_PERMISSION,
        "管理権限",
        "Select AI 資産管理",
        "Oracle Select AI / Agent の低レベル profile・feedback・資産を作成、更新、削除できます。",
        implies=(SELECT_AI_ASSETS_READ_PERMISSION, SELECT_AI_ASSETS_REFRESH_PERMISSION),
    ),
    _permission(
        SAMPLE_DATA_MANAGE_PERMISSION,
        "管理権限",
        "検証用サンプルデータ管理",
        "検証用サンプルデータの確認、投入、削除を実行できます。",
    ),
    _permission(
        LEARNING_MATERIAL_MANAGE_PERMISSION,
        "管理権限",
        "学習素材管理",
        "用語、ルール、few-shot などの学習素材を import/export できます。",
    ),
    _permission(
        SYSTEM_STATUS_READ_PERMISSION,
        "参照権限",
        "システム状態参照",
        "NL2SQL の永続化状態や診断情報を参照できます。",
    ),
    _permission(
        PERSISTENCE_RECOVER_PERMISSION,
        "管理権限",
        "永続化復旧",
        "DB 復旧後の永続化接続と migration 状態を再確認できます。",
        implies=(SYSTEM_STATUS_READ_PERMISSION,),
    ),
)

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)
PERMISSION_BY_CODE = {item.code: item for item in PERMISSION_CATALOG}
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
    """保存済み legacy action 権限を現在の permission code へ正規化する。"""

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
    """catalog と legacy alias のどちらにも存在しない code を返す。"""

    return {
        code.strip()
        for code in codes
        if code.strip()
        and code.strip() not in ALL_PERMISSION_CODES
        and code.strip() not in LEGACY_PERMISSION_ALIASES
    }


def expand_permissions(codes: set[str]) -> set[str]:
    """旧 action permission と implied permission を含む実効権限へ閉包する。"""

    expanded = normalize_permission_codes(codes)
    pending = list(expanded)
    while pending:
        code = pending.pop()
        definition = PERMISSION_BY_CODE.get(code)
        if definition is None:
            continue
        for implied in definition.implies:
            for normalized in normalize_permission_codes((implied,)):
                if normalized in expanded:
                    continue
                expanded.add(normalized)
                pending.append(normalized)
    return expanded


def grants_all_profile_access(codes: Iterable[str]) -> bool:
    """業務プロファイル管理系の権限は個別 profile 制限を受けない。"""

    return PROFILE_MANAGE_PERMISSION in expand_permissions(set(codes))


PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/ready/database", "/auth/login"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})


def _allowed(*codes: str) -> frozenset[str]:
    return frozenset(codes)


def permission_for_route(method: str, route_path: str) -> frozenset[str] | None:
    """FastAPI の method + route template を許可 permission set へ写像する。"""

    method = method.upper()
    if route_path in PUBLIC_API_PATHS or route_path in AUTHENTICATED_WITHOUT_PERMISSION:
        return None
    if route_path.startswith("/security/users"):
        return _allowed("menu.security_users")
    if route_path.startswith("/security/profile-access"):
        return _allowed("menu.security_roles")
    if route_path.startswith("/security/roles") and method == "GET":
        return _allowed("menu.security_users", "menu.security_roles")
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
    if route_path.startswith("/schema/refresh-jobs"):
        if method == "POST":
            return _allowed(SCHEMA_REFRESH_PERMISSION)
        return _allowed(SCHEMA_READ_PERMISSION)
    if route_path.startswith("/schema"):
        return _allowed(SCHEMA_READ_PERMISSION)
    if route_path.startswith("/nl2sql/db-admin"):
        if route_path.startswith("/nl2sql/db-admin/tables"):
            return _allowed(
                "menu.table_management",
                "menu.comment_management",
                "menu.annotation_management",
            )
        if route_path.startswith("/nl2sql/db-admin/views"):
            return _allowed(
                "menu.view_management",
                "menu.comment_management",
                "menu.annotation_management",
            )
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
            return _allowed(
                "menu.admin_sql",
                "menu.comment_management",
                "menu.annotation_management",
            )
        if method == "GET":
            return DATA_PREP_MENUS
        return _allowed("menu.admin_sql")
    if route_path.startswith("/nl2sql/sample-data"):
        return _allowed(SAMPLE_DATA_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/comments"):
        return _allowed("menu.comment_management")
    if route_path.startswith("/nl2sql/annotations"):
        return _allowed("menu.annotation_management")
    if route_path.startswith("/nl2sql/metadata-samples"):
        return _allowed("menu.comment_management", "menu.annotation_management")
    if route_path.startswith("/nl2sql/synthetic-data"):
        return _allowed("menu.sample_data", "menu.data_management")
    if route_path.startswith("/nl2sql/profiles/search"):
        return _allowed(PROFILE_READ_PERMISSION)
    if route_path.endswith("/usage-context") and route_path.startswith("/nl2sql/profiles/"):
        return _allowed(PROFILE_READ_PERMISSION)
    if "/learning-material/" in route_path and route_path.startswith("/nl2sql/profiles/"):
        return _allowed(PROFILE_MANAGE_PERMISSION, LEARNING_MATERIAL_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/profiles"):
        return _allowed(PROFILE_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/legacy-learning-material"):
        return _allowed(LEARNING_MATERIAL_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/ontology"):
        return _allowed("menu.ontology_build")
    if route_path.startswith("/nl2sql/oracle-sync-jobs"):
        return _allowed(PROFILE_MANAGE_PERMISSION, SELECT_AI_ASSETS_REFRESH_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/db-profile-refresh-jobs"):
        return _allowed(SELECT_AI_ASSETS_READ_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/db-profiles/refresh-jobs"):
        return _allowed(SELECT_AI_ASSETS_REFRESH_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/db-profiles"):
        if method == "GET":
            return _allowed(SELECT_AI_ASSETS_READ_PERMISSION)
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/feedback"):
        return _allowed(FEEDBACK_MANAGE_PERMISSION, SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/profiles/refresh"):
        return _allowed(SELECT_AI_ASSETS_REFRESH_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/profiles/export"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/profiles/import"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai/assets/cleanup"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai-agent/assets/refresh"):
        return _allowed(SELECT_AI_ASSETS_REFRESH_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai-agent/assets/cleanup"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai-agent/run"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai-agent/conversations/create"):
        return _allowed(SELECT_AI_ASSETS_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/select-ai-agent"):
        return _allowed(SELECT_AI_ASSETS_READ_PERMISSION)
    if route_path.startswith("/nl2sql/feedback/admin-review"):
        return _allowed(FEEDBACK_MANAGE_PERMISSION)
    if (
        route_path.startswith("/nl2sql/feedback-index")
        or route_path.startswith("/nl2sql/feedback-entries")
        or route_path.startswith("/nl2sql/feedback-config")
    ):
        return _allowed(FEEDBACK_MANAGE_PERMISSION)
    if route_path == "/nl2sql/feedback":
        if method == "GET":
            return _allowed(FEEDBACK_MANAGE_PERMISSION)
        return _allowed(FEEDBACK_WRITE_PERMISSION, FEEDBACK_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/feedback/"):
        return _allowed(FEEDBACK_WRITE_PERMISSION, FEEDBACK_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/classifier"):
        return _allowed("menu.question_classifier_models")
    if route_path.startswith("/nl2sql/quality-evaluations"):
        return _allowed("menu.evaluation")
    if route_path.startswith("/nl2sql/reverse"):
        return _allowed("menu.sql_to_question")
    if route_path.startswith("/nl2sql/history"):
        return _allowed("menu.history", QUERY_GENERATE_PERMISSION, FEEDBACK_MANAGE_PERMISSION)
    if route_path == "/nl2sql/preview":
        return _allowed(QUERY_GENERATE_PERMISSION)
    if route_path == "/nl2sql/execute" or route_path == "/nl2sql/analyze":
        return _allowed(SQL_EXECUTE_PERMISSION)
    if route_path == "/nl2sql/jobs":
        return _allowed(QUERY_GENERATE_PERMISSION)
    if route_path.startswith("/nl2sql/jobs/"):
        return _allowed(QUERY_GENERATE_PERMISSION, "menu.history", FEEDBACK_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql/query-sessions/") and route_path.endswith("/execute"):
        return _allowed(SQL_EXECUTE_PERMISSION)
    if route_path.startswith("/nl2sql/query-sessions"):
        return _allowed(QUERY_GENERATE_PERMISSION)
    if route_path in {
        "/nl2sql/similar-history",
        "/nl2sql/recommend-profile",
        "/nl2sql/rewrite",
    }:
        return _allowed(QUERY_GENERATE_PERMISSION)
    if route_path.startswith("/nl2sql/demo/learning"):
        return _allowed(LEARNING_MATERIAL_MANAGE_PERMISSION, FEEDBACK_MANAGE_PERMISSION)
    if route_path.startswith("/nl2sql"):
        if route_path == "/nl2sql/persistence" and method == "GET":
            return None
        if route_path.startswith("/nl2sql/persistence"):
            if method == "POST":
                return _allowed(PERSISTENCE_RECOVER_PERMISSION)
            return _allowed(SYSTEM_STATUS_READ_PERMISSION)
        if route_path.startswith("/nl2sql/diagnostics"):
            if method == "POST":
                return _allowed(PERSISTENCE_RECOVER_PERMISSION)
            return _allowed(SYSTEM_STATUS_READ_PERMISSION)
        if method == "GET":
            return AI_USE_MENUS | IMPROVEMENT_MENUS
        if "export" in route_path:
            return _allowed("menu.query", "menu.direct_sql", "menu.history")
        return _allowed("menu.query", "menu.direct_sql")
    return _allowed(UNCLASSIFIED_PERMISSION)
