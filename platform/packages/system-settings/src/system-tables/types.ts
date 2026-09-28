/**
 * システムテーブルの管理 API（`GET /api/settings/database/system-tables` と
 * `POST /api/settings/database/system-tables/initialize`。#325）の契約。
 * backend の骨格は `pr_system_settings.system_schema`、manifest と DDL の正本は製品が持つ。
 */
export type SystemTableSchemaStatus = "missing" | "partial" | "outdated" | "ready";
export type SystemTableOperationStatus = "idle" | "running" | "failed";
export type SystemTableOperationResult = "no_op" | "initialized" | "migrated" | "recreated";
/** Oracle の object の種類（NL2SQL は TABLE / INDEX / SEQUENCE / PACKAGE / PACKAGE BODY）。 */
export type SystemObjectType = string;

export interface SystemTableObjectRef {
  name: string;
  object_type: SystemObjectType;
}

export interface SystemTableMetadata {
  name: string;
  /** 接続ユーザーの schema と所有者付きの名前（NL2SQL だけが返す。無ければ名前だけを出す）。 */
  owner?: string;
  qualified_name?: string;
  exists: boolean;
  estimated_rows: number | null;
  created_at: string | null;
  last_analyzed_at: string | null;
}

export interface SystemObjectMetadata extends SystemTableMetadata {
  object_type: SystemObjectType;
}

export interface SystemTableOperationState {
  status: SystemTableOperationStatus;
  operation_kind: "initialize" | "recreate" | null;
  lease_expires_at: string | null;
  last_error_code: string | null;
  schema_epoch: number;
  updated_at: string | null;
}

export interface SystemTablesStatusData {
  status: SystemTableSchemaStatus;
  /** 最新の migration（RAG は名前、NL2SQL は番号）。 */
  schema_head: string | number;
  applied_versions: Array<string | number>;
  pending_versions: Array<string | number>;
  expected_object_count: number;
  existing_object_count: number;
  expected_table_count: number;
  existing_table_count: number;
  missing_objects: SystemTableObjectRef[];
  /** 廃止したが残っている object（RAG）。 */
  retired_objects?: SystemTableObjectRef[];
  tables: SystemTableMetadata[];
  /** 全管理 object（NL2SQL）。無ければ `tables` をテーブルとして並べる。 */
  objects?: SystemObjectMetadata[];
  operation_state: SystemTableOperationState;
}

export interface SystemTablesInitializeRequest {
  recreate: boolean;
  confirmation?: string;
}

export interface SystemTablesOperationData extends SystemTablesStatusData {
  operation: SystemTableOperationResult;
  dropped_object_count: number;
  created_object_count: number;
}

export interface SystemTablesApi {
  getSystemTablesStatus: (options?: { signal?: AbortSignal }) => Promise<SystemTablesStatusData>;
  initializeSystemTables: (body: SystemTablesInitializeRequest) => Promise<SystemTablesOperationData>;
}

/** 3 製品で同じ query key にする。 */
export const SYSTEM_TABLES_QUERY_KEY = ["settings", "database", "system-tables"] as const;
