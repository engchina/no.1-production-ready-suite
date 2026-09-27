import type { RequestOptions, SecurityRole } from "../users-roles/types";
import type { RolePermissionTargetMessages } from "./messages";

/** `GET /api/security/permissions` の 1 件（3製品で同じ形。#220）。 */
export interface PermissionDefinition {
  code: string;
  group: string;
  label: string;
  description: string;
  /** この権限を付けると自動的に付く権限コード。 */
  implies: string[];
}

/** 権限を持つロール。製品は利用できる対象の ID などを足した型をそのまま渡してよい。 */
export interface PermissionRole extends SecurityRole {
  permissions: string[];
}

/** 利用できる対象の候補 1 件（業務プロファイル・業務ビュー・ナレッジベース・エージェントなど）。 */
export interface RolePermissionTargetItem {
  id: string;
  name: string;
  description?: string;
  /** 名前の後ろに括弧で添える補足（例: カテゴリ）。 */
  secondary?: string;
  /** 名前の横に出す状態（例: アーカイブ済み）。 */
  status?: string;
}

/**
 * ロールに付ける「利用できる対象」の 1 種類。製品固有の対象はこの形で権限管理画面へ差し込む。
 * 例: NL2SQL の業務プロファイル、RAG の業務ビュー・ナレッジベース、Agent のエージェント。
 */
export interface RolePermissionTargetSection<R extends PermissionRole = PermissionRole> {
  /** draft.targets の key。テスト ID と要素 ID の `security-roles-<key>-*` にも使う。 */
  key: string;
  messages: RolePermissionTargetMessages;
  /** 候補を読み込む。失敗しても画面は警告を出してロール一覧を表示し続ける。 */
  load: (options: RequestOptions & { signal: AbortSignal }) => Promise<RolePermissionTargetItem[]>;
  /** ロールに保存済みの対象 ID。 */
  selectedIds: (role: R) => string[];
  /**
   * 有効な権限コード（implies 展開後）から「全件が対象」かを判定する（例: 管理権限を持つ）。
   * SYSTEM_ADMIN は常に全件が対象。全件のときは個別選択を出さず、保存では空の一覧を送る。
   */
  grantsAll?: (effectivePermissions: ReadonlySet<string>) => boolean;
}

/** 保存する内容。targets は対象の key ごとの ID 一覧（全件が対象のときは空）。 */
export interface RolePermissionsDraft {
  permissions: string[];
  targets: Record<string, string[]>;
}

export interface RolePermissionsApi<R extends PermissionRole = PermissionRole> {
  /** アーカイブ済みを含むロール一覧（`GET /api/security/roles?include_archived=true`）。 */
  roles: (includeArchived: boolean, options?: RequestOptions) => Promise<R[]>;
  /** 権限カタログ（`GET /api/security/permissions`）。 */
  permissions: (options?: RequestOptions) => Promise<PermissionDefinition[]>;
  /** 製品ごとの保存 API（例: NL2SQL `PUT /api/security/roles/{id}/permissions`）。保存後のロールを返す。 */
  save: (role: R, draft: RolePermissionsDraft) => Promise<R>;
}
