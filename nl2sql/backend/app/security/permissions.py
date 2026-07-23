"""アプリ機能 permission catalog と API route manifest。"""

from __future__ import annotations

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


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _permission("dashboard.view", "利用", "ダッシュボード表示", "ダッシュボードを表示します。"),
    _permission("documents.view", "データ準備", "データ表示", "表・ビュー・データを表示します。"),
    _permission(
        "documents.upload",
        "データ準備",
        "データ投入",
        "CSV 等のデータを投入します。",
        implies=("documents.view",),
    ),
    _permission(
        "documents.preview",
        "データ準備",
        "データプレビュー",
        "データ内容をプレビューします。",
        implies=("documents.view",),
    ),
    _permission(
        "documents.approve",
        "データ準備",
        "データ承認",
        "データ処理を承認します。",
        implies=("documents.view",),
    ),
    _permission(
        "documents.ingest",
        "データ準備",
        "データ取込実行",
        "データ取込を実行します。",
        implies=("documents.view",),
    ),
    _permission(
        "documents.delete",
        "データ準備",
        "データ削除",
        "表またはデータを削除します。",
        implies=("documents.view",),
    ),
    _permission(
        "knowledge_bases.view", "業務モデル", "業務モデル表示", "Profile と Ontology を表示します。"
    ),
    _permission(
        "knowledge_bases.manage",
        "業務モデル",
        "業務モデル管理",
        "Profile と Ontology を変更します。",
        implies=("knowledge_bases.view",),
    ),
    _permission("search.view", "AI 活用", "検索画面表示", "NL2SQL・SQL 解析画面を表示します。"),
    _permission(
        "search.execute",
        "AI 活用",
        "検索・SQL 実行",
        "生成 SQL と検索を実行します。",
        implies=("search.view",),
    ),
    _permission(
        "search.export",
        "AI 活用",
        "検索結果出力",
        "検索結果をファイル出力します。",
        implies=("search.view",),
    ),
    _permission(
        "business_views.view", "業務モデル", "業務 Profile 表示", "業務 Profile を表示します。"
    ),
    _permission(
        "business_views.manage",
        "業務モデル",
        "業務 Profile 管理",
        "業務 Profile を変更します。",
        implies=("business_views.view",),
    ),
    _permission(
        "business_views.use",
        "AI 活用",
        "業務 Profile 利用",
        "業務 Profile を検索で利用します。",
        implies=("business_views.view",),
    ),
    _permission("evaluation.view", "改善", "評価表示", "評価データを表示します。"),
    _permission(
        "evaluation.run", "改善", "評価実行", "評価を実行します。", implies=("evaluation.view",)
    ),
    _permission(
        "evaluation.manage",
        "改善",
        "評価管理",
        "評価セットを変更します。",
        implies=("evaluation.view",),
    ),
    _permission("settings.oci.view", "システム設定", "OCI 設定表示", "OCI 設定を表示します。"),
    _permission(
        "settings.oci.manage",
        "システム設定",
        "OCI 設定変更",
        "OCI 設定を変更します。",
        implies=("settings.oci.view",),
    ),
    _permission(
        "settings.object_storage.view", "システム設定", "保存先設定表示", "保存先設定を表示します。"
    ),
    _permission(
        "settings.object_storage.manage",
        "システム設定",
        "保存先設定変更",
        "保存先設定を変更します。",
        implies=("settings.object_storage.view",),
    ),
    _permission(
        "settings.models.view", "システム設定", "モデル設定表示", "モデル設定を表示します。"
    ),
    _permission(
        "settings.models.manage",
        "システム設定",
        "モデル設定変更",
        "モデル設定を変更します。",
        implies=("settings.models.view",),
    ),
    _permission(
        "settings.database.view", "システム設定", "DB 設定表示", "データベース設定を表示します。"
    ),
    _permission(
        "settings.database.manage",
        "システム設定",
        "DB 設定変更",
        "データベース設定を変更します。",
        implies=("settings.database.view",),
    ),
    _permission(
        "settings.database.sql_execute",
        "システム設定",
        "管理 SQL 実行",
        "確認済み管理 SQL を実行します。",
        implies=("settings.database.view",),
    ),
    _permission(
        "security.users.view", "セキュリティ", "ユーザー表示", "ユーザー一覧を表示します。"
    ),
    _permission(
        "security.users.manage",
        "セキュリティ",
        "ユーザー管理",
        "ユーザーを作成・変更します。",
        implies=("security.users.view",),
    ),
    _permission("security.roles.view", "セキュリティ", "ロール表示", "ロールと権限を表示します。"),
    _permission(
        "security.roles.manage",
        "セキュリティ",
        "ロール管理",
        "ロールと権限を変更します。",
        implies=("security.roles.view",),
    ),
    _permission(
        "security.audit.view", "セキュリティ", "監査ログ表示", "認証・権限監査ログを表示します。"
    ),
    _permission(
        "security.deepsec.view",
        "セキュリティ",
        "DeepSec 表示",
        "Deep Data Security の状態と SQL を表示します。",
    ),
    _permission(
        "security.deepsec.apply",
        "セキュリティ",
        "DeepSec 適用",
        "バージョン化 DeepSec SQL を適用します。",
        implies=("security.deepsec.view",),
    ),
    _permission(
        "security.deepsec.verify",
        "セキュリティ",
        "DeepSec 検証",
        "DeepSec の分離動作を検証します。",
        implies=("security.deepsec.view",),
    ),
)

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
    PERMISSION_CATALOG += (
        _permission(
            f"pipeline.{_adapter}.view",
            "NL2SQL パイプライン",
            f"{_adapter} 表示",
            f"{_adapter} アダプターを表示します。",
        ),
        _permission(
            f"pipeline.{_adapter}.manage",
            "NL2SQL パイプライン",
            f"{_adapter} 変更",
            f"{_adapter} アダプターを変更します。",
            implies=(f"pipeline.{_adapter}.view",),
        ),
    )

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)
UNCLASSIFIED_PERMISSION = "__unclassified__"


def expand_permissions(codes: set[str]) -> set[str]:
    """操作 permission が暗黙に要求する view permission を閉包する。"""
    definitions = {item.code: item for item in PERMISSION_CATALOG}
    expanded = set(codes)
    pending = list(codes)
    while pending:
        code = pending.pop()
        definition = definitions.get(code)
        if definition is None:
            continue
        for implied in definition.implies:
            if implied not in expanded:
                expanded.add(implied)
                pending.append(implied)
    return expanded


PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/ready/database", "/auth/login"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})


def permission_for_route(method: str, route_path: str) -> str | None:
    """FastAPI の method + route template を permission code へ写像する。"""
    method = method.upper()
    if route_path in PUBLIC_API_PATHS or route_path in AUTHENTICATED_WITHOUT_PERMISSION:
        return None
    if route_path.startswith("/security/users"):
        return "security.users.view" if method == "GET" else "security.users.manage"
    if route_path.startswith("/security/roles") or route_path == "/security/permissions":
        return "security.roles.view" if method == "GET" else "security.roles.manage"
    if route_path.startswith("/security/audit"):
        return "security.audit.view"
    if route_path.startswith("/security/deepsec"):
        if route_path.endswith("/verify"):
            return "security.deepsec.verify"
        if "/steps/" in route_path:
            return "security.deepsec.apply"
        return "security.deepsec.view"
    if route_path.startswith("/settings/oci"):
        return "settings.oci.view" if method == "GET" else "settings.oci.manage"
    if route_path.startswith("/settings/upload-storage"):
        return (
            "settings.object_storage.view" if method == "GET" else "settings.object_storage.manage"
        )
    if route_path.startswith("/settings/model"):
        return "settings.models.view" if method == "GET" else "settings.models.manage"
    if route_path.startswith("/settings/database/system-tables"):
        return (
            "settings.database.view"
            if method == "GET"
            else "settings.database.sql_execute"
        )
    if route_path.startswith("/settings/database"):
        return "settings.database.view" if method == "GET" else "settings.database.manage"
    if route_path.startswith("/schema"):
        return "settings.database.view" if method == "GET" else "settings.database.manage"
    if route_path.startswith("/nl2sql/db-admin"):
        if method == "GET":
            return "settings.database.view"
        if route_path.endswith("/preview-data"):
            return "documents.preview"
        if "export" in route_path:
            return "search.export"
        return "settings.database.sql_execute"
    if route_path.startswith("/nl2sql/profiles") or route_path.startswith("/nl2sql/ontology"):
        return "business_views.view" if method == "GET" else "business_views.manage"
    if "evaluation" in route_path:
        return "evaluation.view" if method == "GET" else "evaluation.run"
    if route_path.startswith("/nl2sql/sample-data") or "training-data" in route_path:
        return "documents.view" if method == "GET" else "documents.upload"
    if route_path.startswith("/nl2sql"):
        if method == "GET":
            return "search.view"
        if "export" in route_path:
            return "search.export"
        return "search.execute"
    return UNCLASSIFIED_PERMISSION
