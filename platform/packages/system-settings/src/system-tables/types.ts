/**
 * システムテーブルの管理 API（`GET /api/settings/database/system-tables` と
 * `POST /api/settings/database/system-tables/initialize`。#325）と、参照先のない行の削除
 * （RAG の `POST /api/settings/database/system-tables/orphaned-rows/delete`。#511）の契約。
 * backend の骨格は `pr_system_settings.system_schema`、manifest と DDL の正本は製品が持つ。
 */
export type SystemTableSchemaStatus = "missing" | "partial" | "outdated" | "ready";
/**
 * 操作の状態。`stale` は制御テーブルの行が running のまま lease の期限を過ぎたもの（操作したプロセスが
 * 異常終了した可能性。#5278）。実行中として塞がず、管理者が確かめて再実行できる。
 */
export type SystemTableOperationStatus = "idle" | "running" | "stale" | "failed";
export type SystemTableOperationResult = "no_op" | "initialized" | "migrated" | "recreated";
/** 参照先のない行の削除の結果（削除も検査も要らなかったときは `no_op`。#511）。 */
export type SystemTableOrphanOperationResult = "no_op" | "orphans_deleted";
/** Oracle の object の種類（NL2SQL は TABLE / INDEX / SEQUENCE / PACKAGE / PACKAGE BODY）。 */
export type SystemObjectType = string;

export interface SystemTableObjectRef {
  name: string;
  object_type: SystemObjectType;
}

/** 既存の表に無い正本の列の 1 件（#2450 / #2458。3 製品が返す）。 */
export interface SystemTableColumnRef {
  table_name: string;
  column_name: string;
}

/** 外部キーの差分の 1 件（#505。RAG だけが返す）。 */
export interface SystemTableForeignKey {
  name: string;
  table_name: string;
  columns: string[];
  referenced_table_name: string;
  referenced_columns: string[];
  delete_rule: string;
  /** 参照先の無い既存の行の件数（数えられなかったときは null）。 */
  orphan_rows: number | null;
  /** 削除規則が正本と違う FK（#511）の、既存の FK の名前と削除規則（それ以外は null / 無し）。 */
  current_name?: string | null;
  current_delete_rule?: string | null;
}

/** 未適用の、データを消す（テーブルの DROP・行の DELETE を含む）migration（RAG だけが返す。#619）。 */
export interface SystemTableDestructiveMigration {
  name: string;
  /** 消えるデータと、適用の前にすること（書き出しなど）の説明。 */
  description: string;
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
  /** 操作の開始時刻（running / stale のときだけ。#5278）。 */
  started_at?: string | null;
  last_error_code: string | null;
  /** 前回の失敗の原因・影響・対処の説明（製品が分かる失敗だけに返す。例: RAG の vector memory の不足。#5261）。 */
  last_error_detail?: string | null;
  schema_epoch: number;
  updated_at: string | null;
}

export interface SystemTablesStatusData {
  status: SystemTableSchemaStatus;
  /** 最新の migration（RAG は名前、NL2SQL は番号）。 */
  schema_head: string | number;
  applied_versions: Array<string | number>;
  pending_versions: Array<string | number>;
  /** 未適用の、データを消す migration（「作成・更新」の前に承認が要る。RAG。#619）。 */
  pending_destructive_migrations?: SystemTableDestructiveMigration[];
  expected_object_count: number;
  existing_object_count: number;
  expected_table_count: number;
  existing_table_count: number;
  missing_objects: SystemTableObjectRef[];
  /** 廃止したが残っている object（RAG）。 */
  retired_objects?: SystemTableObjectRef[];
  /** 既存の表に無い正本の列（台帳が適用済みでも欠けた列。3 製品。#2450 / #2458）。 */
  missing_columns?: SystemTableColumnRef[];
  /** 既存の表に無い正本の外部キー（「作成・更新」で追加する。RAG。#505）。 */
  missing_foreign_keys?: SystemTableForeignKey[];
  /** 既存の行を検査せずに追加した外部キーに残る、参照先の無い行（RAG。#505）。 */
  orphaned_foreign_keys?: SystemTableForeignKey[];
  /** 削除規則が正本と違う外部キー（「作成・更新」で作り直す。RAG。#511）。 */
  mismatched_foreign_keys?: SystemTableForeignKey[];
  /** 無効化（DISABLED）された外部キー（「作成・更新」で有効にする。RAG。#511）。 */
  disabled_foreign_keys?: SystemTableForeignKey[];
  tables: SystemTableMetadata[];
  /** 全管理 object（NL2SQL）。無ければ `tables` をテーブルとして並べる。 */
  objects?: SystemObjectMetadata[];
  operation_state: SystemTableOperationState;
  /** この状態を返したプロセスが後台で終えた直近の操作の要約（無ければ画面は状態の差から求める。#5278）。 */
  last_operation?: SystemTableLastOperation | null;
  /** HNSW 索引ごとの vector memory の見込みの必要量と今の空き（HNSW を持つ製品だけ。#5278）。 */
  vector_memory?: SystemTableVectorMemory | null;
}

/** 後台で終えた直近の操作の要約（#5278）。 */
export interface SystemTableLastOperation {
  operation: SystemTableOperationResult | null;
  schema_epoch: number;
  restart_required?: SystemTableRestartRequired | null;
  finished_at?: string | null;
}

/** HNSW 索引 1 件の vector memory の見積もり（取れない値は null。#5278）。 */
export interface SystemTableVectorMemoryIndex {
  name: string;
  table_name: string;
  column_name: string;
  /** vector memory に載っているか（読めなければ null）。 */
  loaded: boolean | null;
  /** 見込みの必要量（`DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR`）。 */
  estimated_bytes: number | null;
  /** 今の使用量（`V$VECTOR_INDEX`）。 */
  used_bytes: number | null;
  error_code?: string | null;
}

/** vector memory の見積もり（#5278）。`available` が false なら「取得できません」。 */
export interface SystemTableVectorMemory {
  available: boolean;
  error_code?: string | null;
  pool_allocated_bytes?: number | null;
  pool_used_bytes?: number | null;
  /** Vector Pool の今の空き（`V$VECTOR_MEMORY_POOL` の ALLOC − USED）。 */
  pool_free_bytes?: number | null;
  indexes: SystemTableVectorMemoryIndex[];
}

export interface SystemTablesInitializeRequest {
  recreate: boolean;
  confirmation?: string;
  /** データを消す未適用の migration の適用を承認する（確認ダイアログの後だけ true。#619）。 */
  allow_destructive?: boolean;
}

/**
 * 保存済みのデータを書き換えた後に、再起動が要るプロセス（Agent だけが返す。#1844）。
 * 操作を処理した backend のメモリは読み直し済みで、読み直せない別のプロセスだけを挙げる。
 */
export interface SystemTableRestartRequired {
  /** 再起動が要る理由（製品が日本語で返す）。 */
  message: string;
  /** 再起動するプロセス（名前と、systemd の unit・起動の command）。 */
  processes: string[];
}

/** 参照先のない行の削除（#511）。`expected_orphan_rows` は利用者が確認した件数。 */
export interface SystemTablesDeleteOrphansRequest {
  constraint_name: string;
  expected_orphan_rows: number;
}

export interface SystemTablesOrphanDeletionData extends SystemTablesStatusData {
  operation: SystemTableOrphanOperationResult;
  deleted_row_count: number;
  foreign_key: SystemTableForeignKey;
}

export interface SystemTablesApi {
  getSystemTablesStatus: (options?: { signal?: AbortSignal }) => Promise<SystemTablesStatusData>;
  /**
   * 作成・更新 / 全再作成を始める（後台の操作。#5278）。backend は lease を取ったら 202 と開始した時点の
   * 状態を返し、画面は状態の polling で進み具合と終わりを見る。要求の時間の上限は
   * `SYSTEM_TABLES_INITIALIZE_TIMEOUT_MS`（全体の上限とは別）。
   */
  initializeSystemTables: (body: SystemTablesInitializeRequest) => Promise<SystemTablesStatusData>;
  /** 参照先のない行の削除（RAG だけが持つ。無ければ削除の操作を出さない。#511）。 */
  deleteSystemTableOrphanedRows?: (
    body: SystemTablesDeleteOrphansRequest,
  ) => Promise<SystemTablesOrphanDeletionData>;
}

/**
 * 作成・更新 / 全再作成の開始の要求の時間の上限（#5278）。backend は lease を取ったらすぐ返すが、
 * 前提の確認（状態の読み取り・制御テーブルの用意）に時間がかかることがあるため、全体の上限より長くする。
 */
export const SYSTEM_TABLES_INITIALIZE_TIMEOUT_MS = 60_000;

/** 3 製品で同じ query key にする。 */
export const SYSTEM_TABLES_QUERY_KEY = ["settings", "database", "system-tables"] as const;
