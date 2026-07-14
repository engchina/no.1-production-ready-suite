import type { AllowedObjects, SchemaColumn, SchemaTable } from "./types";

export interface SchemaSelection {
  tableNames: string[];
  columns: Record<string, string[]>;
}

export function emptySelection(): SchemaSelection {
  return { tableNames: [], columns: {} };
}

export function toAllowedObjects(selection: SchemaSelection): AllowedObjects {
  return {
    table_names: selection.tableNames,
    columns: Object.fromEntries(
      Object.entries(selection.columns).filter(([, columns]) => columns.length > 0)
    ),
  };
}

export function toSchemaSelection(allowed: AllowedObjects): SchemaSelection {
  return {
    tableNames: allowed.table_names,
    columns: allowed.columns,
  };
}

export function toggleTableSelection(selection: SchemaSelection, tableName: string): SchemaSelection {
  const selected = selection.tableNames.includes(tableName);
  return {
    ...selection,
    tableNames: selected
      ? selection.tableNames.filter((name) => name !== tableName)
      : [...selection.tableNames, tableName],
  };
}

export function toggleColumnSelection(
  selection: SchemaSelection,
  tableName: string,
  columnName: string
): SchemaSelection {
  const columns = selection.columns[tableName] ?? [];
  const selected = columns.includes(columnName);
  const nextColumns = selected
    ? columns.filter((name) => name !== columnName)
    : [...columns, columnName];
  const nextTableNames = selection.tableNames.includes(tableName)
    ? selection.tableNames
    : [...selection.tableNames, tableName];
  return {
    tableNames: nextTableNames,
    columns: { ...selection.columns, [tableName]: nextColumns },
  };
}

export function buildSchemaInsertText(table: SchemaTable, column: SchemaColumn) {
  return `"${table.logical_name}"."${column.logical_name}"`;
}

function quoteSqlIdentifier(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

export function buildSchemaSqlIdentifierText(table: SchemaTable, column: SchemaColumn) {
  return `${quoteSqlIdentifier(table.table_name)}.${quoteSqlIdentifier(column.column_name)}`;
}

/** 表名の挿入テキスト（論理名・検索クエリ向け）。 */
export function buildTableInsertText(table: SchemaTable) {
  return `"${table.logical_name}"`;
}

/** 表名の挿入テキスト（物理名・SQL 向け）。 */
export function buildTableSqlIdentifierText(table: SchemaTable) {
  return quoteSqlIdentifier(table.table_name);
}

export function insertTextAtRange(source: string, insertText: string, start: number, end: number) {
  return `${source.slice(0, start)}${insertText}${source.slice(end)}`;
}

/**
 * スキーマ参照からの挿入で各項目を改行区切りにするための前置文字列を返す。
 * 直前に文字が無い（先頭 start<=0）、または直前が「値がこれから続く」ことを示す文字
 * （等号・比較演算子・開き括弧・カンマ・空白・改行・コロン）のときは改行しない。
 * それ以外は各項目を改行で区切るため "\n" を前置する。
 * ※ \s は半角/全角スペース・タブ・改行を含むため、空白系と二重改行防止を一括でカバーする。
 */
const INLINE_PREV_CHARS = /[：:＝=＜<＞>（(［[「、，,\s]/;

export function leadingNewlinePrefix(source: string, start: number): string {
  if (start <= 0) return "";
  const prev = source[start - 1];
  return INLINE_PREV_CHARS.test(prev) ? "" : "\n";
}

/**
 * オブジェクト識別子を正規化する（backend `_normalize_identifier` 相当）。
 * `owner.name` の最終要素を取り、ダブルクオートを除去して大文字化する。
 * 例: `EMPLOYEE` / `APP.EMPLOYEE` / `"EMPLOYEE"` / `app."Employee"` → すべて `EMPLOYEE`。
 */
export function normalizeObjectIdentifier(value: string): string {
  const parts = value
    .trim()
    .split(".")
    .map((part) => part.trim().replaceAll('"', ""));
  return (parts[parts.length - 1] ?? "").toUpperCase();
}
