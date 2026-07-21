import { ChevronDown, ChevronRight, Plus, RefreshCw, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button, Skeleton } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  buildSchemaInsertText,
  buildSchemaSqlIdentifierText,
  buildTableInsertText,
  buildTableSqlIdentifierText,
  normalizeObjectIdentifier,
  schemaTableQualifiedName,
} from "../workbenchState";
import { formatSampleValues, formatSchemaCount } from "../schemaDisplay";
import type { SchemaCatalog, SchemaColumn, SchemaTable } from "../types";

type SchemaInsertMode = "logical" | "physical";

/**
 * 検索クエリ/SQL への挿入補助に特化した compact なスキーマピッカー。
 * 業界のスキーマブラウザ慣行に合わせ、1 行密度のツリー + 行クリック=挿入 +
 * 詳細（型・サンプル・コメント）は tooltip、検索時は一致テーブルを自動展開する。
 */
export function SchemaReferencePanel({
  catalog,
  loading,
  disabled,
  insertMode = "logical",
  allowedTableNames = null,
  listMaxHeightClass = "max-h-72",
  onRefreshSchema,
  refreshing = false,
  searchQuery,
  onSearchQueryChange,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  onExpandTable,
  onInsert,
}: {
  catalog: SchemaCatalog | null;
  loading: boolean;
  disabled?: boolean;
  insertMode?: SchemaInsertMode;
  /** 非 null のとき、この表名集合（正規化比較）に絞り込む。null は全表表示。 */
  allowedTableNames?: string[] | null;
  /** リスト領域の最大高さ Tailwind クラス。 */
  listMaxHeightClass?: string;
  /** catalog が空のときに「スキーマを更新」導線を出す（POST /api/schema/refresh）。 */
  onRefreshSchema?: () => void;
  refreshing?: boolean;
  searchQuery?: string;
  onSearchQueryChange?: (value: string) => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onExpandTable?: (table: SchemaTable) => void;
  onInsert: (text: string) => void;
}) {
  const [localQuery, setLocalQuery] = useState("");
  const query = searchQuery ?? localQuery;
  // アコーディオン: 同時に展開できる表は 1 つだけ（外側スクロール量を抑える）。
  const [expandedTable, setExpandedTable] = useState<string | null>(null);

  const allowedSet = useMemo(
    () =>
      allowedTableNames
        ? new Set(allowedTableNames.map(normalizeObjectIdentifier))
        : null,
    [allowedTableNames]
  );

  const catalogEmpty = (catalog?.tables.length ?? 0) === 0;
  const searching = query.trim().length > 0;

  const scopedTables = useMemo(() => {
    if (!catalog) return [];
    if (!allowedSet) return catalog.tables;
    return catalog.tables.filter((table) => {
      const qualifiedName = normalizeObjectIdentifier(schemaTableQualifiedName(table));
      const objectName = normalizeObjectIdentifier(table.table_name);
      // legacy profile は非修飾名、新しい cross-schema profile は OWNER.OBJECT を保持する。
      return allowedSet.has(qualifiedName) || allowedSet.has(objectName);
    });
  }, [catalog, allowedSet]);

  const filteredTables = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return scopedTables;
    // controlled query は server-side 検索済み。summary に未取得の列が無くても結果を隠さない。
    if (onSearchQueryChange) return scopedTables;
    return scopedTables.filter((table) => {
      return (
        table.owner.toLowerCase().includes(normalized) ||
        schemaTableQualifiedName(table).toLowerCase().includes(normalized) ||
        table.table_name.toLowerCase().includes(normalized) ||
        table.logical_name.toLowerCase().includes(normalized) ||
        table.columns.some(
          (column) =>
            column.column_name.toLowerCase().includes(normalized) ||
            column.logical_name.toLowerCase().includes(normalized)
        )
      );
    });
  }, [onSearchQueryChange, scopedTables, query]);

  const toggleExpanded = (qualifiedName: string) => {
    setExpandedTable((current) => {
      const next = current === qualifiedName ? null : qualifiedName;
      if (next) {
        const table = catalog?.tables.find(
          (item) => schemaTableQualifiedName(item) === qualifiedName
        );
        if (table) onExpandTable?.(table);
      }
      return next;
    });
  };

  return (
    <section
      className="grid min-w-0 max-w-full content-start gap-2 overflow-hidden"
      aria-label={t("nl2sql.schema.title")}
      data-testid="nl2sql-schema-reference"
    >
      <p className="flex min-w-0 items-center gap-2 text-sm font-semibold text-foreground">
        <Table2 size={15} className="shrink-0" aria-hidden="true" />
        <span>{t("nl2sql.schema.title")}</span>
        <span className="min-w-0 truncate text-xs font-normal text-muted">
          {t("nl2sql.schema.insertHint")}
        </span>
      </p>

      <span className="relative min-w-0 max-w-full">
        <Search
          size={15}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
          aria-hidden="true"
        />
        <input
          value={query}
          onChange={(event) => {
            const value = event.currentTarget.value;
            if (onSearchQueryChange) onSearchQueryChange(value);
            else setLocalQuery(value);
          }}
          className="min-h-9 min-w-0 w-full rounded-md border border-border bg-card py-1.5 pl-8 pr-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
          placeholder={t("nl2sql.schema.searchPlaceholder")}
          aria-label={t("nl2sql.schema.search")}
          disabled={disabled}
        />
      </span>

      {loading && (
        <div className="grid min-w-0 gap-1.5">
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
      )}

      {!loading && filteredTables.length === 0 && catalogEmpty && onRefreshSchema && (
        <div className="grid min-w-0 gap-3 rounded-md border border-dashed border-border p-4 text-sm text-muted">
          <p className="[overflow-wrap:anywhere]">{t("nl2sql.schema.emptyCatalog")}</p>
          <div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={refreshing}
              disabled={disabled}
              onClick={onRefreshSchema}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("nl2sql.schema.refresh")}</span>
            </Button>
          </div>
        </div>
      )}

      {!loading && filteredTables.length === 0 && !(catalogEmpty && onRefreshSchema) && (
        <p className="min-w-0 rounded-md border border-dashed border-border p-3 text-sm text-muted [overflow-wrap:anywhere]">
          {t("nl2sql.schema.empty")}
        </p>
      )}

      {!loading && (
      <div
        className={`grid min-w-0 max-w-full ${listMaxHeightClass} content-start gap-1 overflow-x-hidden overflow-y-auto pr-1`}
      >
        {filteredTables.map((table) => (
          <SchemaTableItem
            key={schemaTableQualifiedName(table)}
            table={table}
            expanded={searching || expandedTable === schemaTableQualifiedName(table)}
            disabled={disabled}
            onToggleExpanded={toggleExpanded}
            insertMode={insertMode}
            onInsert={onInsert}
          />
        ))}
        {hasMore && onLoadMore && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={loadingMore}
            disabled={disabled}
            onClick={onLoadMore}
          >
            {t("profiles.action.loadMore")}
          </Button>
        )}
      </div>
      )}
    </section>
  );
}

function SchemaTableItem({
  table,
  expanded,
  disabled,
  insertMode,
  onToggleExpanded,
  onInsert,
}: {
  table: SchemaTable;
  expanded: boolean;
  disabled?: boolean;
  insertMode: SchemaInsertMode;
  onToggleExpanded: (tableName: string) => void;
  onInsert: (text: string) => void;
}) {
  const tableInsertText =
    insertMode === "physical" ? buildTableSqlIdentifierText(table) : buildTableInsertText(table);
  return (
    <article
      className="min-w-0 max-w-full overflow-hidden rounded-md border border-border bg-card"
      data-testid="nl2sql-schema-table-item"
    >
      <div className="flex min-h-9 w-full min-w-0 items-stretch">
        {/* chevron=展開/折りたたみ、表名クリック=挿入（列行と同じ「クリック=挿入」で一貫）。 */}
        <button
          type="button"
          className="flex shrink-0 items-center px-2 text-muted hover:bg-muted/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          aria-expanded={expanded}
          aria-label={t("nl2sql.schema.toggleTable", { name: table.logical_name })}
          onClick={() => onToggleExpanded(schemaTableQualifiedName(table))}
        >
          {expanded ? (
            <ChevronDown size={15} aria-hidden="true" />
          ) : (
            <ChevronRight size={15} aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          disabled={disabled}
          title={table.comment || table.table_name}
          onClick={() => onInsert(tableInsertText)}
          className="group flex min-w-0 flex-1 items-center gap-2 py-1.5 pr-2.5 text-left hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50"
        >
          <span className="min-w-0 truncate text-sm font-medium text-foreground">
            {table.logical_name}
          </span>
          <span className="min-w-0 truncate font-mono text-xs text-muted">{table.table_name}</span>
          <span className="shrink-0 rounded border border-border bg-muted/20 px-1.5 py-0.5 font-mono text-[10px] text-muted">
            {table.owner}
          </span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5 text-xs text-muted">
            {t("schema.table.rows", { count: formatSchemaCount(table.row_count) })}
            <Plus
              size={14}
              className="text-primary opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none"
              aria-hidden="true"
            />
          </span>
        </button>
      </div>
      {expanded && (
        <ul className="grid min-w-0 content-start gap-0.5 border-t border-border p-1">
          {table.columns.map((column) => (
            <SchemaColumnItem
              key={column.column_name}
              table={table}
              column={column}
              disabled={disabled}
              insertMode={insertMode}
              onInsert={onInsert}
            />
          ))}
        </ul>
      )}
    </article>
  );
}

function columnTooltip(table: SchemaTable, column: SchemaColumn): string {
  const lines = [
    `${schemaTableQualifiedName(table)}.${column.column_name} · ${column.data_type}${column.nullable ? "" : " · NOT NULL"}`,
  ];
  if (column.comment) lines.push(column.comment);
  if (column.sample_values.length > 0) {
    lines.push(`${t("schema.col.sample")}: ${formatSampleValues(column.sample_values)}`);
  }
  return lines.join("\n");
}

function SchemaColumnItem({
  table,
  column,
  disabled,
  insertMode,
  onInsert,
}: {
  table: SchemaTable;
  column: SchemaColumn;
  disabled?: boolean;
  insertMode: SchemaInsertMode;
  onInsert: (text: string) => void;
}) {
  const insertText =
    insertMode === "physical"
      ? buildSchemaSqlIdentifierText(table, column)
      : buildSchemaInsertText(table, column);
  return (
    <li className="min-w-0">
      <button
        type="button"
        disabled={disabled}
        title={columnTooltip(table, column)}
        onClick={() => onInsert(insertText)}
        className="group flex min-h-9 w-full min-w-0 items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-primary/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50"
      >
        <span className="min-w-0 truncate text-sm text-foreground">{column.logical_name}</span>
        <span className="min-w-0 truncate font-mono text-xs text-muted">
          {column.column_name} · {column.data_type}
        </span>
        <Plus
          size={14}
          className="ml-auto shrink-0 text-primary opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none"
          aria-hidden="true"
        />
      </button>
    </li>
  );
}
