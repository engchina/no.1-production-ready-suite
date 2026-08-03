import assert from "node:assert/strict";
import test from "node:test";

import { buildMetadataInputTexts } from "../src/features/nl2sql/metadataSql.ts";
import type { DbAdminObjectDetail } from "../src/features/nl2sql/types.ts";

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

test("buildMetadataInputTexts builds structure constraints and samples", () => {
  const metadataDetail: DbAdminObjectDetail = {
    ...detail,
    constraints: ["PK_ORDERS P(ORDER_ID)", "FK_ORDERS_CUSTOMER R(CUSTOMER_ID)"],
    columns: [
      { ...detail.columns[0], sample_values: ["100", "101"] },
      detail.columns[1],
    ],
  };
  const texts = buildMetadataInputTexts([metadataDetail], 1);
  assert.match(texts.structureText, /OBJECT: ORDERS/);
  assert.match(texts.primaryKeyText, /PK_ORDERS/);
  assert.match(texts.foreignKeyText, /FK_ORDERS_CUSTOMER/);
  assert.match(texts.sampleText, /ORDER_ID: 100/);
});
