"""RAG の権限カタログと API の権限 manifest（#214）。

- `PERMISSION_CATALOG`: ロールに付けられる権限（メニュー権限と capability）。
- `permission_for_route(method, path)`: API（method × route template）→ 必要な権限の集合
  （いずれか 1 つを持てばよい）。**既定は拒否**で、登録外の API は `UNCLASSIFIED_PERMISSION`
  を返し、認可で 403 にする。公開 API とログインだけで使える API は None を返す。

システム設定・ユーザーとロールの共通メニューは NL2SQL と同じコード・グループ名を使う
（3 製品共通の画面。#206）。権限管理は「セキュリティ設定」の製品固有メニュー
（セクション名は 3 製品で同じ。#658）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pr_system_settings.auth.dependencies import UNCLASSIFIED_PERMISSION as UNCLASSIFIED_PERMISSION

# 利用者の範囲（業務ビュー / ナレッジベース）外の対象を指定したときの 403 の error_code（#224）。
# 経路の権限拒否（SECURITY_ROUTE_FORBIDDEN）と違い、frontend はその場で理由を表示する。
SCOPE_FORBIDDEN_CODE = "RAG_SCOPE_FORBIDDEN"


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
    # 説明はナビの名前（label）と同じ用語で書く（#580）。
    return _permission(code, group, label, f"「{label}」の画面を表示し、関連操作を利用できます。")


# ---- メニュー権限 ----

MENU_SEARCH = "menu.search"
MENU_CHAT = "menu.chat"
MENU_BUSINESS_VIEWS = "menu.business_views"
MENU_EVALUATION = "menu.evaluation"
MENU_FEEDBACK = "menu.feedback"
MENU_UPLOAD = "menu.upload"
MENU_FILE_LIST = "menu.file_list"
MENU_KNOWLEDGE_BASES = "menu.knowledge_bases"
MENU_SETTINGS_PIPELINE = "menu.settings_pipeline"
MENU_SETTINGS_PREPROCESS = "menu.settings_preprocess"
MENU_SETTINGS_PARSER_ADAPTERS = "menu.settings_parser_adapters"
MENU_SETTINGS_CHUNKING = "menu.settings_chunking"
MENU_SETTINGS_VECTOR_INDEX = "menu.settings_vector_index"
MENU_SETTINGS_RETRIEVAL = "menu.settings_retrieval"
MENU_SETTINGS_PROMPTS = "menu.settings_prompts"
MENU_SETTINGS_GUARDRAIL = "menu.settings_guardrail"
MENU_SETTINGS_EVALUATION = "menu.settings_evaluation"
MENU_SETTINGS_GRAPH = "menu.settings_graph"
MENU_SETTINGS_SYSTEM_TABLES = "menu.settings_system_tables"
MENU_SETTINGS_HUGGINGFACE = "menu.settings_huggingface"
MENU_SETTINGS_SERVICES = "menu.settings_services"
MENU_SETTINGS_OCI = "menu.settings_oci"
MENU_SETTINGS_UPLOAD_STORAGE = "menu.settings_upload_storage"
MENU_SETTINGS_MODEL = "menu.settings_model"
MENU_SETTINGS_DATABASE = "menu.settings_database"
MENU_SETTINGS_APPEARANCE = "menu.settings_appearance"
MENU_SECURITY_USERS = "menu.security_users"
MENU_SECURITY_ROLES = "menu.security_roles"
MENU_SECURITY_PERMISSIONS = "menu.security_permissions"

# ---- capability ----

BUSINESS_VIEWS_MANAGE = "rag.business_views.manage"
KNOWLEDGE_BASES_MANAGE = "rag.knowledge_bases.manage"
FEEDBACK_MANAGE = "rag.feedback.manage"
SYSTEM_TABLES_MANAGE = "rag.system_tables.manage"

# グループ・名前・並び順は左のナビ（frontend の nav-config.ts と、i18n の
# サイドナビの表示名）と同じにする（#567 / #580。一致は
# tests/test_permission_catalog_nav.py が確かめる）。ナビに無い capability は後ろに置く。
_GROUP_BUSINESS = "業務ビュー"
_GROUP_INGESTION = "ナレッジ構築"
_GROUP_PIPELINE = "検索・回答設定"
_GROUP_IMPROVE = "改善・運用"
_GROUP_SECURITY = "セキュリティ設定"
_GROUP_OPERATIONS = "運用設定"
_GROUP_USERS_ROLES = "ユーザーとロール"
_GROUP_SETTINGS = "システム設定"
_GROUP_MANAGE = "管理権限"


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _menu_permission(MENU_CHAT, _GROUP_BUSINESS, "チャット"),
    _menu_permission(MENU_SEARCH, _GROUP_BUSINESS, "RAG 検索"),
    _menu_permission(MENU_UPLOAD, _GROUP_INGESTION, "アップロード"),
    _menu_permission(MENU_FILE_LIST, _GROUP_INGESTION, "文書インデックス"),
    _menu_permission(MENU_KNOWLEDGE_BASES, _GROUP_INGESTION, "ナレッジベース"),
    _menu_permission(MENU_BUSINESS_VIEWS, _GROUP_INGESTION, "業務ビュー"),
    _menu_permission(MENU_SETTINGS_PIPELINE, _GROUP_PIPELINE, "設定の概要"),
    _menu_permission(MENU_SETTINGS_PREPROCESS, _GROUP_PIPELINE, "ファイル準備"),
    _menu_permission(MENU_SETTINGS_PARSER_ADAPTERS, _GROUP_PIPELINE, "文書解析"),
    _menu_permission(MENU_SETTINGS_CHUNKING, _GROUP_PIPELINE, "文書分割"),
    _menu_permission(MENU_SETTINGS_VECTOR_INDEX, _GROUP_PIPELINE, "検索インデックス"),
    _menu_permission(MENU_SETTINGS_GRAPH, _GROUP_PIPELINE, "関係情報の構築"),
    _menu_permission(MENU_SETTINGS_RETRIEVAL, _GROUP_PIPELINE, "検索方法"),
    _menu_permission(MENU_SETTINGS_PROMPTS, _GROUP_PIPELINE, "回答プロンプト"),
    _menu_permission(MENU_SETTINGS_GUARDRAIL, _GROUP_PIPELINE, "安全チェック"),
    _menu_permission(MENU_SETTINGS_EVALUATION, _GROUP_PIPELINE, "評価の基準"),
    _menu_permission(MENU_EVALUATION, _GROUP_IMPROVE, "品質評価"),
    _permission(
        MENU_FEEDBACK,
        _GROUP_IMPROVE,
        "フィードバック",
        "「フィードバック」の画面を表示し、フィードバックの一覧・詳細の表示と評価ケースの作成が"
        "できます（自分が送った、利用できる業務ビューのフィードバックだけ。すべての利用者の分を"
        "見られるのは SYSTEM_ADMIN だけ）。",
    ),
    # 権限管理は RAG 固有。ユーザー管理・ロール管理は 3 製品共通の画面（#206）。
    _menu_permission(MENU_SECURITY_PERMISSIONS, _GROUP_SECURITY, "権限管理"),
    # 並びはサイドナビと同じ
    # （セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定。#658）。
    _menu_permission(MENU_SECURITY_USERS, _GROUP_USERS_ROLES, "ユーザー管理"),
    _menu_permission(MENU_SECURITY_ROLES, _GROUP_USERS_ROLES, "ロール管理"),
    _menu_permission(MENU_SETTINGS_SYSTEM_TABLES, _GROUP_OPERATIONS, "システムテーブル"),
    _menu_permission(MENU_SETTINGS_HUGGINGFACE, _GROUP_OPERATIONS, "HuggingFace"),
    _menu_permission(MENU_SETTINGS_SERVICES, _GROUP_OPERATIONS, "サービス"),
    _menu_permission(MENU_SETTINGS_OCI, _GROUP_SETTINGS, "OCI 認証"),
    _menu_permission(MENU_SETTINGS_UPLOAD_STORAGE, _GROUP_SETTINGS, "アップロード保存先"),
    _menu_permission(MENU_SETTINGS_MODEL, _GROUP_SETTINGS, "モデル"),
    _menu_permission(MENU_SETTINGS_DATABASE, _GROUP_SETTINGS, "データベース"),
    _menu_permission(MENU_SETTINGS_APPEARANCE, _GROUP_SETTINGS, "外観"),
    _permission(
        BUSINESS_VIEWS_MANAGE,
        _GROUP_MANAGE,
        "業務ビュー管理",
        "業務ビューの作成・アーカイブと、すべての業務ビューの利用ができます"
        "（業務ビューの対象範囲の制限を受けません）。",
        implies=(MENU_BUSINESS_VIEWS,),
    ),
    _permission(
        KNOWLEDGE_BASES_MANAGE,
        _GROUP_MANAGE,
        "ナレッジベース管理",
        "ナレッジベースの作成・アーカイブと、すべてのナレッジベースの利用ができます"
        "（ナレッジベースの対象範囲の制限を受けません）。",
        implies=(MENU_KNOWLEDGE_BASES,),
    ),
    _permission(
        FEEDBACK_MANAGE,
        _GROUP_MANAGE,
        "フィードバックの承認 FAQ 反映",
        "フィードバックの回答を業務ビューの承認済み FAQ に登録できます"
        "（自分が送った、利用できる業務ビューのフィードバックだけ。すべての利用者の分を"
        "扱えるのは SYSTEM_ADMIN だけ）。フィードバックの表示と、他の利用者の"
        "保存済みの回答の表示・評価・削除も含みます。",
        implies=(MENU_FEEDBACK,),
    ),
    _permission(
        SYSTEM_TABLES_MANAGE,
        _GROUP_MANAGE,
        "システムテーブル管理",
        "「システムテーブル」の画面で、RAG のシステムテーブルの初期化・全再作成ができます。",
        implies=(MENU_SETTINGS_SYSTEM_TABLES,),
    ),
)

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)
PERMISSION_BY_CODE = {item.code: item for item in PERMISSION_CATALOG}


def unknown_permission_codes(codes: Iterable[str]) -> set[str]:
    """カタログにない権限コード。"""
    return {
        code.strip() for code in codes if code.strip() and code.strip() not in PERMISSION_BY_CODE
    }


def normalize_permission_codes(codes: Iterable[str]) -> set[str]:
    """カタログにある権限コードだけを残す（空白除去）。"""
    return {code.strip() for code in codes if code.strip() in PERMISSION_BY_CODE}


def expand_permissions(codes: Iterable[str]) -> set[str]:
    """implies を閉包した実効権限。"""
    expanded = normalize_permission_codes(codes)
    pending = list(expanded)
    while pending:
        definition = PERMISSION_BY_CODE.get(pending.pop())
        if definition is None:
            continue
        for implied in definition.implies:
            if implied not in expanded:
                expanded.add(implied)
                pending.append(implied)
    return expanded


def grants_all_business_views(codes: Iterable[str]) -> bool:
    """業務ビュー管理はすべての業務ビューを利用できる（対象範囲の制限を受けない）。"""
    return BUSINESS_VIEWS_MANAGE in expand_permissions(codes)


def grants_all_knowledge_bases(codes: Iterable[str]) -> bool:
    """ナレッジベース管理はすべてのナレッジベースを利用できる（対象範囲の制限を受けない）。"""
    return KNOWLEDGE_BASES_MANAGE in expand_permissions(codes)


# ---- API の権限 manifest ----

PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/ready/database", "/auth/login"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})
# サービストークン（Agent から利用者として呼ぶ。#230 / #232）で認証する path。認証済みなら通し、
# 権限は MCP のツールごとに判定する（`app.mcp.tools`）。
SERVICE_TOKEN_API_PATHS = frozenset({"/mcp"})
# 上の 3 つ（権限なしで通す path）の method × path。実行時の判定（platform の共通認証）は
# path 単位のため、同じ path に method を足したときに黙って公開されないよう、
# 完全性テストでこの一覧と照合する（#476）。
OPEN_API_OPERATIONS = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/ready/database"),
        ("POST", "/auth/login"),
        ("GET", "/auth/me"),
        ("POST", "/auth/logout"),
        ("POST", "/auth/password/change"),
        ("POST", "/mcp"),
    }
)
SERVICE_TOKEN_AUDIENCE = "rag"  # nosec B105 - token の audience（呼び先の製品名）で秘密ではない


def _any(*codes: str) -> frozenset[str]:
    return frozenset(codes)


# 複数の画面から使う API の許可集合。
_DOCUMENT_WORKSPACE = _any(MENU_UPLOAD, MENU_FILE_LIST)
# 文書の要約・レシピ・原本表示は、文書ワークスペースと引用カードのプレビュー（検索・チャット・
# KB の検索テスト）が使う。文書の詳細の画面はワークスペース専用の API も使うため、frontend は
# `_DOCUMENT_WORKSPACE` の権限がある利用者にだけ詳細へのリンクを出す（#303）。
_DOCUMENT_VIEW = _any(MENU_UPLOAD, MENU_FILE_LIST, MENU_SEARCH, MENU_CHAT, MENU_KNOWLEDGE_BASES)
_KNOWLEDGE_BASE_READ = _any(
    MENU_UPLOAD,
    MENU_FILE_LIST,
    MENU_KNOWLEDGE_BASES,
    MENU_EVALUATION,
    MENU_BUSINESS_VIEWS,
    MENU_SEARCH,
    MENU_CHAT,
)
_BUSINESS_VIEW_READ = _any(
    MENU_SEARCH, MENU_CHAT, MENU_FEEDBACK, MENU_BUSINESS_VIEWS, MENU_EVALUATION
)
_ANSWER_USE = _any(MENU_SEARCH, MENU_CHAT)
_SECURITY_ROLE_READ = _any(MENU_SECURITY_USERS, MENU_SECURITY_ROLES, MENU_SECURITY_PERMISSIONS)

_D = "/documents/{document_id}"
_R = "/documents/{document_id}/recipes/{recipe_id}"
_KB = "/knowledge-bases/{knowledge_base_id}"
_BV = "/business-views/{business_view_id}"

# (METHOD, route template) → 許可する権限（いずれか）。`/api` は付けない。
ROUTE_PERMISSIONS: dict[tuple[str, str], frozenset[str]] = {
    # ---- ナレッジ構築: 文書（アップロード・文書インデックス） ----
    ("POST", "/documents/upload"): _any(MENU_UPLOAD),
    ("POST", "/documents/batch-upload"): _any(MENU_UPLOAD),
    ("GET", "/documents"): _any(MENU_FILE_LIST, MENU_KNOWLEDGE_BASES),
    # 削除の確認で使う（削除と同じ権限。#303）。
    ("GET", "/documents/delete-impact"): _any(MENU_FILE_LIST),
    # 分類の入力の候補（分類の編集と同じ権限。#547）。
    ("GET", "/documents/classification-options"): _DOCUMENT_WORKSPACE,
    ("GET", "/documents/ingestion-jobs"): _DOCUMENT_WORKSPACE,
    ("POST", "/documents/ingestion-jobs/drain"): _any(MENU_UPLOAD),
    ("POST", "/documents/ingestion-jobs/{job_id}/retry"): _DOCUMENT_WORKSPACE,
    ("POST", "/documents/ingestion-jobs/{job_id}/cancel"): _DOCUMENT_WORKSPACE,
    ("GET", "/documents/ingestion-jobs/{job_id}"): _DOCUMENT_WORKSPACE,
    ("POST", f"{_D}/ingestion-jobs"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/ingestion-jobs"): _DOCUMENT_WORKSPACE,
    ("POST", f"{_D}/ingestion-segments/retry"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/recipes"): _DOCUMENT_VIEW,
    ("POST", f"{_D}/recipes"): _DOCUMENT_WORKSPACE,
    ("PUT", _R): _DOCUMENT_WORKSPACE,
    ("DELETE", _R): _DOCUMENT_WORKSPACE,
    ("POST", f"{_R}/ingestion-jobs"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_R}/chunks"): _DOCUMENT_WORKSPACE,
    ("POST", f"{_R}/chunk-preview"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_R}/content"): _DOCUMENT_VIEW,
    ("GET", f"{_R}/preview-pages"): _DOCUMENT_VIEW,
    ("GET", f"{_R}/preview-pages/{{page_number}}"): _DOCUMENT_VIEW,
    ("GET", f"{_R}/extraction-export"): _DOCUMENT_WORKSPACE,
    ("POST", f"{_R}/approve"): _DOCUMENT_WORKSPACE,
    ("PATCH", f"{_R}/review-edits"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/chunk-sets"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/sections"): _DOCUMENT_WORKSPACE,
    ("PUT", f"{_D}/sections"): _DOCUMENT_WORKSPACE,
    ("DELETE", f"{_D}/sections"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/ingestion-segments"): _DOCUMENT_WORKSPACE,
    ("GET", _D): _DOCUMENT_VIEW,
    ("DELETE", _D): _any(MENU_FILE_LIST),
    ("GET", f"{_D}/knowledge-bases"): _DOCUMENT_WORKSPACE,
    ("PUT", f"{_D}/knowledge-bases"): _DOCUMENT_WORKSPACE,
    ("PUT", f"{_D}/classification"): _DOCUMENT_WORKSPACE,
    ("GET", f"{_D}/content"): _DOCUMENT_VIEW,
    ("GET", f"{_D}/crop"): _DOCUMENT_VIEW,
    ("GET", f"{_D}/preview-pages"): _DOCUMENT_VIEW,
    ("GET", f"{_D}/preview-pages/{{page_number}}"): _DOCUMENT_VIEW,
    # ---- ナレッジ構築: ナレッジベース ----
    ("GET", "/knowledge-bases"): _KNOWLEDGE_BASE_READ,
    ("POST", "/knowledge-bases"): _any(KNOWLEDGE_BASES_MANAGE),
    ("GET", _KB): _KNOWLEDGE_BASE_READ,
    ("PATCH", _KB): _any(MENU_KNOWLEDGE_BASES),
    ("GET", f"{_KB}/graph"): _any(MENU_KNOWLEDGE_BASES),
    # KB ごとの項目抽出の定義（#548）。
    ("GET", f"{_KB}/extraction-fields"): _any(MENU_KNOWLEDGE_BASES),
    ("PUT", f"{_KB}/extraction-fields"): _any(MENU_KNOWLEDGE_BASES),
    ("POST", f"{_KB}/archive"): _any(KNOWLEDGE_BASES_MANAGE),
    ("POST", f"{_KB}/documents"): _any(MENU_KNOWLEDGE_BASES),
    ("DELETE", f"{_KB}/documents/{{document_id}}"): _any(MENU_KNOWLEDGE_BASES),
    # ---- 業務ビュー ----
    ("GET", "/business-views"): _BUSINESS_VIEW_READ,
    ("POST", "/business-views"): _any(BUSINESS_VIEWS_MANAGE),
    ("GET", _BV): _BUSINESS_VIEW_READ,
    ("PATCH", _BV): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/archive"): _any(BUSINESS_VIEWS_MANAGE),
    ("GET", f"{_BV}/domain-keywords"): _any(MENU_BUSINESS_VIEWS),
    ("PUT", f"{_BV}/domain-keywords"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/domain-keywords/suggest"): _any(MENU_BUSINESS_VIEWS),
    ("GET", f"{_BV}/approved-faq"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/approved-faq"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/approved-faq/delete"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/approved-faq/import/preview"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/approved-faq/import"): _any(MENU_BUSINESS_VIEWS),
    # 回答前に類似の承認済み FAQ を提示する（読み取り）。
    ("POST", f"{_BV}/approved-faq/suggest"): _any(MENU_SEARCH, MENU_CHAT, MENU_BUSINESS_VIEWS),
    # 類似問の提示のオン / オフ（#684）。
    ("PUT", f"{_BV}/approved-faq/settings"): _any(MENU_BUSINESS_VIEWS),
    ("GET", f"{_BV}/runtime-knowledge"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/runtime-knowledge/edit"): _any(MENU_BUSINESS_VIEWS),
    ("POST", f"{_BV}/runtime-knowledge/preview"): _any(MENU_BUSINESS_VIEWS),
    ("GET", f"{_BV}/query-suggestions"): _ANSWER_USE,
    # ---- 業務ビュー: チャット ----
    ("GET", "/chat/models"): _any(MENU_CHAT),
    ("GET", "/chat/conversations"): _any(MENU_CHAT),
    ("POST", "/chat/conversations"): _any(MENU_CHAT),
    ("GET", "/chat/conversations/{conversation_id}"): _any(MENU_CHAT),
    ("PATCH", "/chat/conversations/{conversation_id}"): _any(MENU_CHAT),
    ("DELETE", "/chat/conversations/{conversation_id}"): _any(MENU_CHAT),
    ("POST", "/chat/conversations/{conversation_id}/messages/stream"): _any(MENU_CHAT),
    # ---- 業務ビュー: 検索と回答履歴 ----
    # 文書ワークスペースのレシピ検索テストも同期検索を使う。
    ("POST", "/search"): _any(MENU_SEARCH, MENU_UPLOAD, MENU_FILE_LIST),
    # KB 詳細の検索テストもストリーム検索を使う。
    ("POST", "/search/stream"): _any(MENU_SEARCH, MENU_KNOWLEDGE_BASES),
    # 検索の絞り込みに使える項目（業務ビューの KB の項目抽出の定義。#549）。
    ("GET", "/search/extraction-fields"): _any(MENU_SEARCH),
    # RAG 検索の回答に選べるモデル（既定のテキストモデルと Vision モデル。#675）。
    ("GET", "/search/models"): _any(MENU_SEARCH),
    ("GET", "/search/answers"): _ANSWER_USE,
    ("GET", "/search/answers/{trace_id}"): _ANSWER_USE,
    ("DELETE", "/search/answers/{trace_id}"): _ANSWER_USE,
    ("POST", "/search/answers/{trace_id}/evaluation"): _ANSWER_USE,
    # ---- 業務ビュー: 品質評価 ----
    ("POST", "/evaluation/run"): _any(MENU_EVALUATION),
    ("POST", "/evaluation/compare"): _any(MENU_EVALUATION),
    # 品質評価の job（#390）。状態の取得・取り消しは、投入した利用者の job だけ（backend が判定）。
    ("POST", "/evaluation/jobs/run"): _any(MENU_EVALUATION),
    ("POST", "/evaluation/jobs/compare"): _any(MENU_EVALUATION),
    ("GET", "/evaluation/jobs/{job_id}"): _any(MENU_EVALUATION),
    ("POST", "/evaluation/jobs/{job_id}/cancel"): _any(MENU_EVALUATION),
    # ---- 業務ビュー: フィードバック ----
    ("POST", "/feedback"): _ANSWER_USE,
    ("GET", "/feedback/current"): _ANSWER_USE,
    # 一覧・詳細・評価ケースはフィードバック画面の権限（許可された業務ビューで絞る）。
    ("GET", "/feedback"): _any(MENU_FEEDBACK),
    ("GET", "/feedback/{feedback_id}"): _any(MENU_FEEDBACK),
    ("GET", "/feedback/{feedback_id}/evaluation-case"): _any(MENU_FEEDBACK),
    # 承認済み FAQ への反映（回答の書き込み）だけは rag.feedback.manage。
    ("POST", "/feedback/{feedback_id}/approved-faq"): _any(FEEDBACK_MANAGE),
    # ---- システム設定（3 製品共通の画面） ----
    ("GET", "/settings/upload-storage"): _any(
        MENU_SETTINGS_UPLOAD_STORAGE, MENU_SETTINGS_OCI, MENU_UPLOAD
    ),
    ("PATCH", "/settings/upload-storage"): _any(MENU_SETTINGS_UPLOAD_STORAGE),
    ("GET", "/settings/oci"): _any(MENU_SETTINGS_OCI),
    ("PATCH", "/settings/oci"): _any(MENU_SETTINGS_OCI),
    ("PATCH", "/settings/oci/object-storage"): _any(
        MENU_SETTINGS_OCI, MENU_SETTINGS_UPLOAD_STORAGE
    ),
    ("POST", "/settings/oci/config/read"): _any(MENU_SETTINGS_OCI),
    ("POST", "/settings/oci/config/test"): _any(MENU_SETTINGS_OCI),
    ("POST", "/settings/oci/object-storage/namespace"): _any(
        MENU_SETTINGS_OCI, MENU_SETTINGS_UPLOAD_STORAGE
    ),
    ("POST", "/settings/oci/key-file"): _any(MENU_SETTINGS_OCI),
    # 文書ワークスペースは VLM の設定有無を表示に使う。
    ("GET", "/settings/model"): _any(MENU_SETTINGS_MODEL, MENU_UPLOAD, MENU_FILE_LIST),
    ("PATCH", "/settings/model"): _any(MENU_SETTINGS_MODEL),
    ("POST", "/settings/model/test"): _any(MENU_SETTINGS_MODEL),
    ("GET", "/settings/database"): _any(MENU_SETTINGS_DATABASE),
    ("PATCH", "/settings/database"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/wallet"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/wallet/download"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/test"): _any(MENU_SETTINGS_DATABASE),
    ("GET", "/settings/database/adb"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/settings"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/start"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/stop"): _any(MENU_SETTINGS_DATABASE),
    # システムテーブルは運用設定の専用の画面（#658）。
    ("GET", "/settings/database/system-tables"): _any(MENU_SETTINGS_SYSTEM_TABLES),
    ("POST", "/settings/database/system-tables/initialize"): _any(SYSTEM_TABLES_MANAGE),
    # 参照先のない行の削除（#511）は、作成・更新と同じ権限にする。
    ("POST", "/settings/database/system-tables/orphaned-rows/delete"): _any(SYSTEM_TABLES_MANAGE),
    # ---- 運用設定 ----
    ("GET", "/settings/huggingface"): _any(MENU_SETTINGS_HUGGINGFACE),
    ("PATCH", "/settings/huggingface"): _any(MENU_SETTINGS_HUGGINGFACE),
    ("GET", "/services/catalog"): _any(MENU_SETTINGS_SERVICES),
    # 文書解析の設定画面も parser サービスの状態を表示する。
    ("GET", "/services/{service_id}/status"): _any(
        MENU_SETTINGS_SERVICES, MENU_SETTINGS_PARSER_ADAPTERS
    ),
    ("GET", "/services/{service_id}/logs"): _any(MENU_SETTINGS_SERVICES),
    ("POST", "/services/{service_id}/start"): _any(MENU_SETTINGS_SERVICES),
    ("POST", "/services/{service_id}/stop"): _any(MENU_SETTINGS_SERVICES),
    ("POST", "/services/{service_id}/restart"): _any(MENU_SETTINGS_SERVICES),
    # ---- 検索・回答設定 ----
    # 設定の概要: 工程の自動進行と、レシピ 11 項目の全体の既定（#528）。
    ("GET", "/settings/pipeline"): _any(MENU_SETTINGS_PIPELINE),
    ("PATCH", "/settings/pipeline"): _any(MENU_SETTINGS_PIPELINE),
    # 文書ワークスペースの処理設定パネルも文書解析の設定を読む。
    ("GET", "/settings/parser-adapters"): _any(
        MENU_SETTINGS_PARSER_ADAPTERS, MENU_UPLOAD, MENU_FILE_LIST
    ),
    ("PATCH", "/settings/parser-adapters"): _any(MENU_SETTINGS_PARSER_ADAPTERS),
    ("GET", "/settings/parser-adapters/{backend}/status"): _any(MENU_SETTINGS_PARSER_ADAPTERS),
    ("GET", "/settings/preprocess"): _any(MENU_SETTINGS_PREPROCESS),
    ("PATCH", "/settings/preprocess"): _any(MENU_SETTINGS_PREPROCESS),
    ("GET", "/settings/chunking"): _any(MENU_SETTINGS_CHUNKING),
    ("PATCH", "/settings/chunking"): _any(MENU_SETTINGS_CHUNKING),
    # 回答履歴の保存設定は、検索・チャットの回答履歴表示も読む。
    # 回答の検索と生成の全体既定・回答の記録の保存期間・質問履歴は「検索方法」の画面にある(#593)。
    ("GET", "/settings/answering"): _any(MENU_SETTINGS_RETRIEVAL),
    ("PATCH", "/settings/answering"): _any(MENU_SETTINGS_RETRIEVAL),
    ("GET", "/settings/answer-records"): _any(MENU_SETTINGS_RETRIEVAL, MENU_SEARCH, MENU_CHAT),
    ("PATCH", "/settings/answer-records"): _any(MENU_SETTINGS_RETRIEVAL),
    ("GET", "/settings/query-history"): _any(MENU_SETTINGS_RETRIEVAL),
    ("PATCH", "/settings/query-history"): _any(MENU_SETTINGS_RETRIEVAL),
    # 回答生成のプロンプトは回答プロンプトと文書解析（抽出プロンプト）の両画面で編集する。
    ("GET", "/settings/answer-prompts"): _any(MENU_SETTINGS_PROMPTS, MENU_SETTINGS_PARSER_ADAPTERS),
    ("PUT", "/settings/answer-prompts/{key}"): _any(
        MENU_SETTINGS_PROMPTS, MENU_SETTINGS_PARSER_ADAPTERS
    ),
    ("DELETE", "/settings/answer-prompts/{key}"): _any(
        MENU_SETTINGS_PROMPTS, MENU_SETTINGS_PARSER_ADAPTERS
    ),
    # 抽出項目は文書ワークスペース（処理設定）と文書解析の設定画面が読む。
    ("GET", "/settings/extraction-fields"): _any(
        MENU_FILE_LIST, MENU_UPLOAD, MENU_SETTINGS_PARSER_ADAPTERS
    ),
    # 抽出項目の定義は文書解析の「解析後の処理」で編集する（#528）。
    ("PATCH", "/settings/extraction-fields"): _any(MENU_SETTINGS_PARSER_ADAPTERS),
    # 「標準の項目に戻す」（#556）。
    ("DELETE", "/settings/extraction-fields"): _any(MENU_SETTINGS_PARSER_ADAPTERS),
    ("GET", "/settings/guardrail"): _any(MENU_SETTINGS_GUARDRAIL),
    ("PATCH", "/settings/guardrail"): _any(MENU_SETTINGS_GUARDRAIL),
    ("GET", "/settings/vector-index"): _any(MENU_SETTINGS_VECTOR_INDEX),
    ("PATCH", "/settings/vector-index"): _any(MENU_SETTINGS_VECTOR_INDEX),
    # 品質評価の画面は評価設定（既定値）を読む。
    ("GET", "/settings/evaluation-suite"): _any(MENU_SETTINGS_EVALUATION, MENU_EVALUATION),
    ("PATCH", "/settings/evaluation-suite"): _any(MENU_SETTINGS_EVALUATION),
    ("GET", "/settings/graph"): _any(MENU_SETTINGS_GRAPH),
    ("PATCH", "/settings/graph"): _any(MENU_SETTINGS_GRAPH),
    # ---- ユーザーとロール（platform の共通 router。NL2SQL と同じ割り当て）----
    # 前方一致で割り当てず、route ごとに登録する（新しい route は登録するまで拒否。#476）。
    ("GET", "/security/users"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users"): _any(MENU_SECURITY_USERS),
    ("GET", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("PATCH", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("DELETE", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/disable"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/enable"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/reset-password"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/unlock"): _any(MENU_SECURITY_USERS),
    ("GET", "/security/roles"): _SECURITY_ROLE_READ,
    ("GET", "/security/roles/{role_id}"): _SECURITY_ROLE_READ,
    ("POST", "/security/roles"): _any(MENU_SECURITY_ROLES),
    ("PATCH", "/security/roles/{role_id}"): _any(MENU_SECURITY_ROLES),
    ("DELETE", "/security/roles/{role_id}"): _any(MENU_SECURITY_ROLES),
    ("POST", "/security/roles/{role_id}/archive"): _any(MENU_SECURITY_ROLES),
    ("POST", "/security/roles/{role_id}/restore"): _any(MENU_SECURITY_ROLES),
    # ---- セキュリティ設定: 権限管理 ----
    ("GET", "/security/permissions"): _any(MENU_SECURITY_PERMISSIONS),
    ("GET", "/security/access-targets/business-views"): _any(MENU_SECURITY_PERMISSIONS),
    ("GET", "/security/access-targets/knowledge-bases"): _any(MENU_SECURITY_PERMISSIONS),
    ("PUT", "/security/roles/{role_id}/access"): _any(MENU_SECURITY_PERMISSIONS),
}


def permission_for_route(method: str, route_path: str) -> frozenset[str] | None:
    """method + route template（`/api` なし）→ 許可する権限の集合。

    None は公開 API・ログインだけで使える API・MCP（ツールごとに権限を判定）。
    登録外は `UNCLASSIFIED_PERMISSION`（拒否）。
    """
    method = method.upper()
    if (method, route_path) in OPEN_API_OPERATIONS:
        return None
    exact = ROUTE_PERMISSIONS.get((method, route_path))
    if exact is not None:
        return exact
    return _any(UNCLASSIFIED_PERMISSION)
