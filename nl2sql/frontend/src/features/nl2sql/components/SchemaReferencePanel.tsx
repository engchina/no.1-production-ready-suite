import { ChevronDown, ChevronRight, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
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
  onToggleTable,
  onToggleColumn,
  onInsert,
}: {
  catalog: SchemaCatalog | null;
  loading: boolean;
  selection: SchemaSelection;
  disabled?: boolean;
  insertMode?: SchemaInsertMode;
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

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Table2 size={17} aria-hidden="true" />
          {t("nl2sql.schema.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="grid gap-1 text-sm font-medium text-slate-800">
          <span>{t("nl2sql.schema.search")}</span>
          <span className="relative">
            <Search
              size={16}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              className="min-h-11 w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              placeholder={t("nl2sql.schema.searchPlaceholder")}
              disabled={disabled}
            />
          </span>
        </label>

        {loading && (
          <div className="grid gap-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        )}

        {!loading && filteredTables.length === 0 && (
          <p className="rounded-md border border-dashed border-slate-300 p-4 text-sm text-slate-600">
            {t("nl2sql.schema.empty")}
          </p>
        )}

        <div className="grid max-h-[38rem] gap-3 overflow-auto pr-1">
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
      </CardContent>
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
    <article className="rounded-md border border-slate-200 bg-white">
      <div className="flex items-start gap-2 p-3">
        <button
          type="button"
          className="mt-0.5 flex min-h-10 min-w-10 items-center justify-center rounded-md text-slate-700 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-300"
          aria-expanded={expanded}
          aria-label={t("nl2sql.schema.toggleTable", { name: table.logical_name })}
          onClick={() => onToggleExpanded(table.table_name)}
        >
          {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
        </button>
        <label className="flex min-h-10 flex-1 cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={selected}
            disabled={disabled}
            onChange={() => onToggleTable(table.table_name)}
            className="mt-2 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
          />
          <span className="grid gap-1">
            <span className="font-semibold text-slate-900">{table.logical_name}</span>
            <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              <span className="font-mono">{table.table_name}</span>
              <span>{t("schema.table.rows", { count: formatSchemaCount(table.row_count) })}</span>
              <span>{t("schema.table.constraints", { count: table.constraints.length })}</span>
            </span>
            {table.comment && <span className="text-xs text-slate-600">{table.comment}</span>}
          </span>
        </label>
      </div>
      {expanded && (
        <ul className="grid gap-1 border-t border-slate-100 p-2">
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
    <li className="grid grid-cols-[auto_1fr_auto] items-center gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
      <input
        type="checkbox"
        checked={selected}
        disabled={disabled}
        onChange={() => onToggleColumn(table.table_name, column.column_name)}
        aria-label={t("nl2sql.schema.toggleColumn", { name: column.logical_name })}
        className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
      />
      <button
        type="button"
        disabled={disabled}
        title={`${table.table_name}.${column.column_name}`}
        onClick={() => onInsert(insertText)}
        className="grid min-h-10 rounded-md px-2 py-1 text-left focus:outline-none focus:ring-2 focus:ring-sky-300 disabled:opacity-50"
      >
        <span className="text-sm font-medium text-slate-800">{column.logical_name}</span>
        <span className="font-mono text-xs text-slate-500">
          {column.column_name} · {column.data_type}
        </span>
        {column.sample_values.length > 0 && (
          <span className="truncate text-xs text-slate-500" title={column.sample_values.join(", ")}>
            {t("schema.col.sample")}: {formatSampleValues(column.sample_values)}
          </span>
        )}
      </button>
      {!column.nullable && (
        <span className="rounded bg-slate-100 px-1.5 py-1 text-[11px] font-medium text-slate-700">
          NOT NULL
        </span>
      )}
    </li>
  );
}
