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

export function insertTextAtRange(source: string, insertText: string, start: number, end: number) {
  return `${source.slice(0, start)}${insertText}${source.slice(end)}`;
}
