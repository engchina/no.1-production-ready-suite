import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDeleteTemplate,
  buildInsertTemplate,
  buildMergeTemplate,
  buildMultiInsertTemplate,
  buildUpdateTemplate,
} from "../src/features/nl2sql/sqlTemplates.ts";
import { buildMetadataInputTexts } from "../src/features/nl2sql/metadataSql.ts";
import type { DbAdminObjectDetail, SchemaCatalog } from "../src/features/nl2sql/types.ts";

const detail: DbAdminObjectDetail = {
  name: "ORDERS",
  owner: "ADMIN",
  object_type: "table",
  row_count: 0,
  comment: "",
  columns: [
    {
      column_name: "ORDER_ID",
      logical_name: "注文ID",
      data_type: "NUMBER",
      nullable: false,
      comment: "",
      sample_values: [],
    },
    {
      column_name: "STATUS",
      logical_name: "状態",
      data_type: "VARCHAR2(10)",
      nullable: true,
      comment: "",
      sample_values: [],
    },
  ],
  ddl: "",
  warnings: [],
};

test("buildInsertTemplate uses real columns when detail is given", () => {
  const sql = buildInsertTemplate(detail);
  assert.match(sql, /^INSERT INTO ORDERS \(ORDER_ID, STATUS\)/);
  assert.match(sql, /VALUES \(:order_id, :status\);$/);
});

test("buildInsertTemplate falls back to generic placeholders", () => {
  const sql = buildInsertTemplate(null);
  assert.match(sql, /^INSERT INTO TABLE_NAME/);
});

test("buildMultiInsertTemplate repeats the insert statement", () => {
  const sql = buildMultiInsertTemplate(detail);
  assert.equal(sql.split("INSERT INTO ORDERS").length - 1, 2);
});

test("buildUpdateTemplate sets non-key column and filters by first column", () => {
  const sql = buildUpdateTemplate(detail);
  assert.match(sql, /^UPDATE ORDERS/);
  assert.match(sql, /SET STATUS = :status/);
  assert.match(sql, /WHERE ORDER_ID = :order_id;$/);
});

test("buildDeleteTemplate filters by first column", () => {
  assert.match(buildDeleteTemplate(detail), /^DELETE FROM ORDERS\nWHERE ORDER_ID = :order_id;$/);
});

test("buildMergeTemplate produces matched and not-matched branches", () => {
  const sql = buildMergeTemplate(detail);
  assert.match(sql, /^MERGE INTO ORDERS t/);
  assert.match(sql, /ON \(t\.ORDER_ID = s\.ORDER_ID\)/);
  assert.match(sql, /WHEN MATCHED THEN UPDATE SET t\.STATUS = s\.STATUS/);
  assert.match(sql, /WHEN NOT MATCHED THEN INSERT \(ORDER_ID, STATUS\)/);
});

test("buildMetadataInputTexts builds structure constraints and samples", () => {
  const catalog: SchemaCatalog = {
    refreshed_at: "2026-07-10T00:00:00Z",
    tables: [
      {
        table_name: "ORDERS",
        logical_name: "注文",
        owner: "ADMIN",
        table_type: "table",
        comment: "注文",
        row_count: 2,
        constraints: ["PK_ORDERS P(ORDER_ID)", "FK_ORDERS_CUSTOMER R(CUSTOMER_ID)"],
        columns: [
          {
            column_name: "ORDER_ID",
            logical_name: "注文ID",
            data_type: "NUMBER",
            nullable: false,
            comment: "注文ID",
            sample_values: ["100", "101"],
          },
        ],
      },
    ],
  };
  const texts = buildMetadataInputTexts([detail], catalog, 1);
  assert.match(texts.structureText, /OBJECT: ORDERS/);
  assert.match(texts.primaryKeyText, /PK_ORDERS/);
  assert.match(texts.foreignKeyText, /FK_ORDERS_CUSTOMER/);
  assert.match(texts.sampleText, /ORDER_ID: 100/);
});
