import { ChevronDown, ChevronRight, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { DbObjectPanelHeader } from "./DbObjectManagementShared";
import {
  buildSchemaInsertText,
  buildSchemaSqlIdentifierText,
  type SchemaSelection,
} from "../workbenchState";
import { formatSampleValues, formatSchemaCount } from "../schemaDisplay";
import type { SchemaCatalog, SchemaColumn, SchemaTable } from "../types";

type SchemaInsertMode = "logical" | "physical";

function hasColumn(selection: SchemaSelection, tableName: string, columnName: string) {
  return selection.columns[tableName]?.includes(columnName) ?? false;
}

export function SchemaReferencePanel({
  catalog,
  loading,
  selection,
  disabled,
  insertMode = "logical",
  framed = true,
  onToggleTable,
  onToggleColumn,
  onInsert,
}: {
  catalog: SchemaCatalog | null;
  loading: boolean;
  selection: SchemaSelection;
  disabled?: boolean;
  insertMode?: SchemaInsertMode;
  framed?: boolean;
  onToggleTable: (tableName: string) => void;
  onToggleColumn: (tableName: string, columnName: string) => void;
  onInsert: (text: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const filteredTables = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!catalog) return [];
    if (!normalized) return catalog.tables;
    return catalog.tables.filter((table) => {
      return (
        table.table_name.toLowerCase().includes(normalized) ||
        table.logical_name.toLowerCase().includes(normalized) ||
        table.columns.some(
          (column) =>
            column.column_name.toLowerCase().includes(normalized) ||
            column.logical_name.toLowerCase().includes(normalized)
        )
      );
    });
  }, [catalog, query]);

  const toggleExpanded = (tableName: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(tableName)) {
        next.delete(tableName);
      } else {
        next.add(tableName);
      }
      return next;
    });
  };

  const content = (
    <>
        <label className="grid min-w-0 max-w-full gap-1 text-sm font-medium text-foreground">
          <span>{t("nl2sql.schema.search")}</span>
          <span className="relative min-w-0 max-w-full">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              aria-hidden="true"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              className="min-h-11 min-w-0 w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
              placeholder={t("nl2sql.schema.searchPlaceholder")}
              disabled={disabled}
            />
          </span>
        </label>

        {loading && (
          <div className="grid min-w-0 gap-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        )}

        {!loading && filteredTables.length === 0 && (
          <p className="min-w-0 rounded-md border border-dashed border-border p-4 text-sm text-muted [overflow-wrap:anywhere]">
            {t("nl2sql.schema.empty")}
          </p>
        )}

        <div className="grid min-w-0 max-w-full max-h-[38rem] gap-3 overflow-x-hidden overflow-y-auto pr-1">
          {filteredTables.map((table) => (
            <SchemaTableItem
              key={table.table_name}
              table={table}
              expanded={expanded.has(table.table_name)}
              selected={selection.tableNames.includes(table.table_name)}
              selection={selection}
              disabled={disabled}
              onToggleExpanded={toggleExpanded}
              onToggleTable={onToggleTable}
              onToggleColumn={onToggleColumn}
              insertMode={insertMode}
              onInsert={onInsert}
            />
          ))}
        </div>
    </>
  );

  if (!framed) {
    return (
      <section
        className="grid min-w-0 max-w-full content-start gap-4 overflow-hidden"
        aria-labelledby="nl2sql-schema-heading"
        data-testid="nl2sql-schema-reference"
      >
        <DbObjectPanelHeader
          headingId="nl2sql-schema-heading"
          icon={Table2}
          title={t("nl2sql.schema.title")}
          description={t("nl2sql.schema.description")}
        />
        <div className="min-w-0 max-w-full space-y-4">{content}</div>
      </section>
    );
  }

  return (
    <Card className="h-full min-w-0 max-w-full overflow-hidden" data-testid="nl2sql-schema-reference">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Table2 size={17} aria-hidden="true" />
          {t("nl2sql.schema.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="min-w-0 max-w-full space-y-4">{content}</CardContent>
    </Card>
  );
}

function SchemaTableItem({
  table,
  expanded,
  selected,
  selection,
  disabled,
  insertMode,
  onToggleExpanded,
  onToggleTable,
  onToggleColumn,
  onInsert,
}: {
  table: SchemaTable;
  expanded: boolean;
  selected: boolean;
  selection: SchemaSelection;
  disabled?: boolean;
  insertMode: SchemaInsertMode;
  onToggleExpanded: (tableName: string) => void;
  onToggleTable: (tableName: string) => void;
  onToggleColumn: (tableName: string, columnName: string) => void;
  onInsert: (text: string) => void;
}) {
  return (
    <article
      className="min-w-0 max-w-full overflow-hidden rounded-md border border-border bg-card"
      data-testid="nl2sql-schema-table-item"
    >
      <div className="flex min-w-0 items-start gap-2 p-3">
        <button
          type="button"
          className="mt-0.5 flex min-h-10 min-w-10 items-center justify-center rounded-md text-foreground hover:bg-muted/30 focus:outline-none focus:ring-2 focus:ring-ring/40"
          aria-expanded={expanded}
          aria-label={t("nl2sql.schema.toggleTable", { name: table.logical_name })}
          onClick={() => onToggleExpanded(table.table_name)}
        >
          {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
        </button>
        <label className="flex min-h-10 min-w-0 flex-1 cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={selected}
            disabled={disabled}
            onChange={() => onToggleTable(table.table_name)}
            className="mt-2 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
          />
          <span className="grid min-w-0 gap-1">
            <span className="min-w-0 font-semibold text-foreground [overflow-wrap:anywhere]">
              {table.logical_name}
            </span>
            <span className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted">
              <span className="min-w-0 max-w-full font-mono [overflow-wrap:anywhere]">{table.table_name}</span>
              <span>{t("schema.table.rows", { count: formatSchemaCount(table.row_count) })}</span>
              <span>{t("schema.table.constraints", { count: table.constraints.length })}</span>
            </span>
            {table.comment && (
              <span className="min-w-0 text-xs text-muted [overflow-wrap:anywhere]">{table.comment}</span>
            )}
          </span>
        </label>
      </div>
      {expanded && (
        <ul className="grid min-w-0 gap-1 border-t border-border p-2">
          {table.columns.map((column) => (
            <SchemaColumnItem
              key={column.column_name}
              table={table}
              column={column}
              selected={hasColumn(selection, table.table_name, column.column_name)}
              disabled={disabled}
              insertMode={insertMode}
              onToggleColumn={onToggleColumn}
              onInsert={onInsert}
            />
          ))}
        </ul>
      )}
    </article>
  );
}

function SchemaColumnItem({
  table,
  column,
  selected,
  disabled,
  insertMode,
  onToggleColumn,
  onInsert,
}: {
  table: SchemaTable;
  column: SchemaColumn;
  selected: boolean;
  disabled?: boolean;
  insertMode: SchemaInsertMode;
  onToggleColumn: (tableName: string, columnName: string) => void;
  onInsert: (text: string) => void;
}) {
  const insertText =
    insertMode === "physical"
      ? buildSchemaSqlIdentifierText(table, column)
      : buildSchemaInsertText(table, column);
  return (
    <li className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-2 py-1.5 hover:bg-background">
      <input
        type="checkbox"
        checked={selected}
        disabled={disabled}
        onChange={() => onToggleColumn(table.table_name, column.column_name)}
        aria-label={t("nl2sql.schema.toggleColumn", { name: column.logical_name })}
        className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
      />
      <button
        type="button"
        disabled={disabled}
        title={`${table.table_name}.${column.column_name}`}
        onClick={() => onInsert(insertText)}
        className="grid min-h-10 min-w-0 max-w-full rounded-md px-2 py-1 text-left focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:opacity-50"
      >
        <span className="min-w-0 text-sm font-medium text-foreground [overflow-wrap:anywhere]">
          {column.logical_name}
        </span>
        <span className="min-w-0 font-mono text-xs text-muted [overflow-wrap:anywhere]">
          {column.column_name} · {column.data_type}
        </span>
        {column.sample_values.length > 0 && (
          <span className="min-w-0 truncate text-xs text-muted" title={column.sample_values.join(", ")}>
            {t("schema.col.sample")}: {formatSampleValues(column.sample_values)}
          </span>
        )}
      </button>
      {!column.nullable && (
        <span className="rounded bg-muted/30 px-1.5 py-1 text-[11px] font-medium text-foreground">
          NOT NULL
        </span>
      )}
    </li>
  );
}
