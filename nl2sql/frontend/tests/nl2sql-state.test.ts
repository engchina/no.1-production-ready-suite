import assert from "node:assert/strict";
import test from "node:test";

import { sqlAnalyzePayload } from "../src/features/nl2sql/analysisState.ts";
import {
  ACTIVE_JOB_ID_KEY,
  ACTIVE_JOB_STARTED_AT_KEY,
  clearActiveJobSnapshot,
  isJobInFlight,
  isJobTerminal,
  persistActiveJobSnapshot,
  readActiveJobSnapshot,
  type ActiveJobStorage,
} from "../src/features/nl2sql/jobPersistence.ts";
import { elapsedSecondsSince, formatElapsed } from "../src/features/nl2sql/operationTiming.ts";
import {
  previewExecutePayload,
  previewToGeneratedSqlPanelData,
} from "../src/features/nl2sql/previewState.ts";
import {
  historyRerunUrl,
  parseNl2SqlEngine,
  prefillFromSearchParams,
} from "../src/features/nl2sql/queryPrefillState.ts";
import {
  csvImportPayload,
  defaultCsvImportForm,
} from "../src/features/nl2sql/csvImportState.ts";
import { formatSampleValues, formatSchemaCount } from "../src/features/nl2sql/schemaDisplayCore.ts";
import type { HistoryItem, PreviewData, SchemaColumn, SchemaTable } from "../src/features/nl2sql/types.ts";
import {
  buildSchemaInsertText,
  emptySelection,
  insertTextAtRange,
  toAllowedObjects,
  toSchemaSelection,
  toggleColumnSelection,
  toggleTableSelection,
} from "../src/features/nl2sql/workbenchState.ts";

const invoiceColumn: SchemaColumn = {
  column_name: "TOTAL_AMOUNT",
  logical_name: "請求金額",
  data_type: "NUMBER",
  nullable: false,
  comment: "合計金額",
  sample_values: ["12000", "9800", "45100", "500"],
};

const invoiceTable: SchemaTable = {
  table_name: "INVOICES",
  logical_name: "請求",
  owner: "APP",
  table_type: "TABLE",
  comment: "請求データ",
  row_count: 12034,
  columns: [invoiceColumn],
  constraints: ["PK_INVOICES"],
};

class MemoryStorage implements ActiveJobStorage {
  readonly items = new Map<string, string>();

  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }
}

test("schema insertion uses denpyo-style logical table and column names", () => {
  assert.equal(buildSchemaInsertText(invoiceTable, invoiceColumn), "\"請求\".\"請求金額\"");
});

test("selection converts to allowed_objects without empty column arrays", () => {
  const selection = {
    tableNames: ["INVOICES", "CUSTOMERS"],
    columns: {
      INVOICES: ["TOTAL_AMOUNT"],
      CUSTOMERS: [],
    },
  };

  assert.deepEqual(toAllowedObjects(selection), {
    table_names: ["INVOICES", "CUSTOMERS"],
    columns: {
      INVOICES: ["TOTAL_AMOUNT"],
    },
  });
});

test("schema selection round-trips backend allowed_objects", () => {
  assert.deepEqual(
    toSchemaSelection({
      table_names: ["INVOICES"],
      columns: { INVOICES: ["TOTAL_AMOUNT"] },
    }),
    {
      tableNames: ["INVOICES"],
      columns: { INVOICES: ["TOTAL_AMOUNT"] },
    }
  );
});

test("table and column toggles keep allowed table scope in sync", () => {
  const selectedTable = toggleTableSelection(emptySelection(), "INVOICES");
  assert.deepEqual(selectedTable.tableNames, ["INVOICES"]);

  const selectedColumn = toggleColumnSelection(emptySelection(), "INVOICES", "TOTAL_AMOUNT");
  assert.deepEqual(selectedColumn, {
    tableNames: ["INVOICES"],
    columns: { INVOICES: ["TOTAL_AMOUNT"] },
  });

  const removedColumn = toggleColumnSelection(selectedColumn, "INVOICES", "TOTAL_AMOUNT");
  assert.deepEqual(removedColumn, {
    tableNames: ["INVOICES"],
    columns: { INVOICES: [] },
  });
});

test("question insertion replaces selected text at the cursor range", () => {
  assert.equal(
    insertTextAtRange("未入金の金額を見たい", "\"請求\".\"請求金額\"", 4, 6),
    "未入金の\"請求\".\"請求金額\"を見たい"
  );
});

test("elapsed time helpers format live and final timings", () => {
  assert.equal(elapsedSecondsSince(1_000, 4_499), 3);
  assert.equal(elapsedSecondsSince(5_000, 4_000), 0);
  assert.equal(formatElapsed(null), "-");
  assert.equal(formatElapsed(850), "850ms");
  assert.equal(formatElapsed(1_250), "1.3秒");
});

test("schema metadata helpers format counts and sample values for compact UI", () => {
  assert.equal(formatSchemaCount(invoiceTable.row_count), "12,034");
  assert.equal(formatSchemaCount(null), "-");
  assert.equal(formatSampleValues(invoiceColumn.sample_values), "12000, 9800, 45100");
});

test("CSV import payload keeps dry-run as the default", () => {
  const form = defaultCsvImportForm();
  assert.equal(form.execute, false);
  assert.deepEqual(csvImportPayload({ ...form, tableName: " imported_customers " }), {
    table_name: "imported_customers",
    csv_text: form.csvText,
    execute: false,
    replace_existing: false,
  });
});

test("SQL analysis payload trims SQL and clamps row limits", () => {
  assert.deepEqual(sqlAnalyzePayload(" SELECT * FROM INVOICES ", 0), {
    sql: "SELECT * FROM INVOICES",
    row_limit: 1,
  });
  assert.deepEqual(sqlAnalyzePayload("SELECT * FROM CUSTOMERS", 9000), {
    sql: "SELECT * FROM CUSTOMERS",
    row_limit: 5000,
  });
});

test("active job persistence restores and clears polling state", () => {
  const storage = new MemoryStorage();
  assert.equal(readActiveJobSnapshot(storage, 123), null);

  persistActiveJobSnapshot(storage, "job-123", 1_700_000);
  assert.deepEqual(readActiveJobSnapshot(storage, 123), {
    jobId: "job-123",
    startedAtMs: 1_700_000,
  });

  storage.setItem(ACTIVE_JOB_STARTED_AT_KEY, "not-a-number");
  assert.deepEqual(readActiveJobSnapshot(storage, 555), {
    jobId: "job-123",
    startedAtMs: 555,
  });

  clearActiveJobSnapshot(storage);
  assert.equal(storage.getItem(ACTIVE_JOB_ID_KEY), null);
  assert.equal(storage.getItem(ACTIVE_JOB_STARTED_AT_KEY), null);
});

test("job status helpers identify in-flight and terminal states", () => {
  assert.equal(isJobInFlight("pending"), true);
  assert.equal(isJobInFlight("running"), true);
  assert.equal(isJobInFlight("done"), false);
  assert.equal(isJobTerminal("done"), true);
  assert.equal(isJobTerminal("error"), true);
  assert.equal(isJobTerminal("running"), false);
});

test("preview response maps to generated SQL panel data", () => {
  const preview: PreviewData = {
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    is_safe: true,
    row_limit: 100,
    note: "preview ready",
    engine: "select_ai",
    engine_meta: { profile_name: "P" },
    fallback_reason: "",
    rewritten_question: "請求金額を一覧で見る",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
    safety: null,
    recommendations: ["SELECT-only です。"],
    repaired_sql: "",
    optimization_hints: [],
    timing: null,
  };

  assert.deepEqual(previewToGeneratedSqlPanelData(preview), {
    engine: "select_ai",
    engine_meta: { profile_name: "P" },
    fallback_reason: "",
    generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
    explanation: "preview ready",
    safety: {
      is_safe: true,
      is_select_only: true,
      row_limit_applied: 100,
      blocked_reason: "",
      warnings: [],
      referenced_tables: [],
      referenced_columns: [],
    },
    recommendations: ["SELECT-only です。"],
    repaired_sql: "",
    optimization_hints: [],
    rewritten_question: "請求金額を一覧で見る",
  });
});

test("preview execute payload preserves selected allowed objects", () => {
  assert.deepEqual(
    previewExecutePayload(
      " SELECT TOTAL_AMOUNT FROM INVOICES ",
      "default",
      { table_names: ["INVOICES"], columns: { INVOICES: ["TOTAL_AMOUNT"] } },
      50
    ),
    {
      sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
      profile_id: "default",
      allowed_objects: {
        table_names: ["INVOICES"],
        columns: { INVOICES: ["TOTAL_AMOUNT"] },
      },
      row_limit: 50,
    }
  );
});

test("history rerun URL and query prefill preserve question, engine, and profile", () => {
  const item: HistoryItem = {
    id: "h1",
    question: "請求 金額を再実行",
    engine: "select_ai_agent",
    generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    created_at: "2026-01-01T00:00:00Z",
    elapsed_ms: 12,
    feedback_rating: null,
    profile_id: "finance",
    profile_name: "経理",
    rewritten_question: "請求金額",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
    safety_is_safe: true,
    result_row_count: 1,
    result_columns: ["TOTAL_AMOUNT"],
    feedback_comment: "",
  };

  const url = historyRerunUrl(item);
  assert.equal(url, "/query?question=%E8%AB%8B%E6%B1%82+%E9%87%91%E9%A1%8D%E3%82%92%E5%86%8D%E5%AE%9F%E8%A1%8C&engine=select_ai_agent&profile_id=finance");
  assert.deepEqual(prefillFromSearchParams(new URLSearchParams(url.split("?")[1])), {
    question: "請求 金額を再実行",
    engine: "select_ai_agent",
    profileId: "finance",
  });
  assert.equal(parseNl2SqlEngine("bad_engine"), null);
});
