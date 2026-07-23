import assert from "node:assert/strict";
import test from "node:test";

import {
  filterUserVisibleCatalog,
  filterUserVisibleDbAdminObjectPage,
  filterUserVisibleSchemaObjectPage,
  isUserVisibleObjectName,
} from "../src/features/nl2sql/objectVisibility.ts";
import type {
  DbAdminObjectSummary,
  SchemaObjectSummary,
  SchemaTable,
} from "../src/features/nl2sql/types.ts";

const schemaTable = (name: string, tableType = "TABLE"): SchemaTable => ({
  table_name: name,
  logical_name: name,
  owner: "APP",
  table_type: tableType,
  comment: "",
  columns: [],
  constraints: [],
});

const schemaObject = (name: string, objectType = "TABLE"): SchemaObjectSummary => ({
  owner: "APP",
  object_name: name,
  object_type: objectType,
  logical_name: name,
  comment: "",
  column_count: 0,
  last_ddl_at: "",
});

const dbAdminObject = (name: string, objectType = "table"): DbAdminObjectSummary => ({
  name,
  owner: "APP",
  object_type: objectType,
  row_count: null,
  comment: "",
});

test("object visibility rejects Oracle system-name markers", () => {
  assert.equal(isUserVisibleObjectName("ORDERS"), true);
  assert.equal(isUserVisibleObjectName("DBTOOLS$EXECUTION_HISTORY"), false);
  assert.equal(isUserVisibleObjectName("SYS#AUDIT"), false);
});

test("catalog and schema pages remove system objects before updating counts", () => {
  const catalog = filterUserVisibleCatalog({
    refreshed_at: "2026-07-22T00:00:00.000Z",
    tables: [schemaTable("ORDERS"), schemaTable("VECTOR_IDX$VECTAB")],
    view_dependencies: [
      {
        view_name: "ORDER_VIEW",
        referenced_name: "ORDERS",
      },
      {
        view_name: "ORDER_VIEW",
        referenced_name: "SYS#AUDIT",
      },
    ],
  });
  const page = filterUserVisibleSchemaObjectPage({
    items: [
      schemaObject("ORDERS"),
      schemaObject("VECTOR_IDX$VECTAB"),
      schemaObject("SYS#AUDIT", "VIEW"),
    ],
    next_cursor: null,
    total: 3,
    table_count: 2,
    view_count: 1,
    catalog_version: 1,
  });

  assert.deepEqual(catalog.tables.map((table) => table.table_name), ["ORDERS"]);
  assert.deepEqual(catalog.view_dependencies?.map((item) => item.referenced_name), ["ORDERS"]);
  assert.deepEqual(page.items.map((item) => item.object_name), ["ORDERS"]);
  assert.equal(page.total, 1);
  assert.equal(page.table_count, 1);
  assert.equal(page.view_count, 0);
});

test("DB admin pages remove dollar and hash objects defensively", () => {
  const page = filterUserVisibleDbAdminObjectPage({
    runtime: "oracle",
    owner: "APP",
    items: [
      dbAdminObject("ORDERS"),
      dbAdminObject("DBTOOLS$EXECUTION_HISTORY"),
      dbAdminObject("SYS#AUDIT", "view"),
    ],
    total: 3,
    table_count: 2,
    view_count: 1,
    next_cursor: null,
    refreshed_at: "2026-07-22T00:00:00.000Z",
    catalog_version: 1,
    warnings: [],
  });

  assert.deepEqual(page.items.map((item) => item.name), ["ORDERS"]);
  assert.equal(page.total, 1);
  assert.equal(page.table_count, 1);
  assert.equal(page.view_count, 0);
});
