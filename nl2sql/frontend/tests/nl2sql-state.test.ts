import assert from "node:assert/strict";
import test from "node:test";

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
  adjustFixedSplitFraction,
  clampFixedSplitFraction,
  fixedSplitFractionForRatio,
  fixedSplitGridTemplateColumns,
  fixedSplitStateForFraction,
  fixedSplitStateForPreferredWidePane,
  fixedSplitStateForRatio,
  fixedSplitStorageKey,
  isFixedSplitRatio,
  nearestFixedSplitRatio,
  nextFixedSplitRatio,
  nextFixedSplitStateFromFraction,
  parseFixedSplitStorageValue,
  serializeFixedSplitState,
} from "../src/lib/fixed-split-pane.ts";
import {
  previewExecutePayload,
  previewToGeneratedSqlPanelData,
  sqlExecutePayload,
} from "../src/features/nl2sql/previewState.ts";
import {
  historyRerunUrl,
  parseNl2SqlEngine,
  prefillFromSearchParams,
} from "../src/features/nl2sql/queryPrefillState.ts";
import {
  filterAndSortHistory,
  selectedVisibleHistoryId,
} from "../src/features/nl2sql/historyManagementState.ts";
import { formatSampleValues, formatSchemaCount } from "../src/features/nl2sql/schemaDisplayCore.ts";
import type { HistoryItem, PreviewData, SchemaColumn, SchemaTable } from "../src/features/nl2sql/types.ts";
import {
  buildSchemaInsertText,
  buildSchemaSqlIdentifierText,
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

test("fixed split pane stores ratio per page split id", () => {
  assert.equal(
    fixedSplitStorageKey("profile-management-oracle"),
    "production-ready-nl2sql.fixedSplitPane.profile-management-oracle"
  );
  assert.equal(isFixedSplitRatio("leftWide"), true);
  assert.equal(isFixedSplitRatio("freeResize"), false);
});

test("fixed split pane cycles equal to left-wide to right-wide", () => {
  assert.equal(nextFixedSplitRatio("equal"), "leftWide");
  assert.equal(nextFixedSplitRatio("leftWide"), "rightWide");
  assert.equal(nextFixedSplitRatio("rightWide"), "equal");
});

test("fixed split pane reads legacy and draggable storage values", () => {
  const legacy = parseFixedSplitStorageValue("leftWide");
  assert.equal(legacy.ratio, "leftWide");
  assert.ok(Math.abs(legacy.leftFraction - fixedSplitFractionForRatio("leftWide")) < 0.000001);

  const restored = parseFixedSplitStorageValue(serializeFixedSplitState(fixedSplitStateForFraction(0.6)));
  assert.equal(restored.ratio, "leftWide");
  assert.equal(restored.leftFraction, 0.6);

  const ratioOnly = parseFixedSplitStorageValue(JSON.stringify({ ratio: "rightWide" }));
  assert.deepEqual(ratioOnly, fixedSplitStateForRatio("rightWide"));

  assert.deepEqual(parseFixedSplitStorageValue("not-json"), fixedSplitStateForRatio("equal"));
});

test("fixed split pane uses the preferred wide pane when storage is absent", () => {
  const rightWide = fixedSplitStateForPreferredWidePane("right");
  assert.deepEqual(parseFixedSplitStorageValue(null, rightWide), fixedSplitStateForRatio("rightWide"));
  assert.deepEqual(parseFixedSplitStorageValue("leftWide", rightWide), fixedSplitStateForRatio("leftWide"));
  assert.deepEqual(parseFixedSplitStorageValue("not-json", rightWide), fixedSplitStateForRatio("rightWide"));
});

test("fixed split pane clamps dragged fraction and detects nearest fixed ratio", () => {
  assert.equal(clampFixedSplitFraction(0.1), 0.25);
  assert.equal(clampFixedSplitFraction(0.9), 0.75);
  assert.equal(nearestFixedSplitRatio(0.5), "equal");
  assert.equal(nearestFixedSplitRatio(0.63), "leftWide");
  assert.equal(nearestFixedSplitRatio(0.37), "rightWide");
});

test("fixed split pane double-click cycle rounds draggable fractions before advancing", () => {
  assert.deepEqual(nextFixedSplitStateFromFraction(0.5), fixedSplitStateForRatio("leftWide"));
  assert.deepEqual(
    nextFixedSplitStateFromFraction(fixedSplitFractionForRatio("leftWide")),
    fixedSplitStateForRatio("rightWide")
  );
  assert.deepEqual(
    nextFixedSplitStateFromFraction(fixedSplitFractionForRatio("rightWide")),
    fixedSplitStateForRatio("equal")
  );
  assert.deepEqual(nextFixedSplitStateFromFraction(0.6), fixedSplitStateForRatio("rightWide"));
});

test("fixed split pane keyboard adjustment uses pixel-equivalent fractions", () => {
  assert.equal(adjustFixedSplitFraction(0.5, 24, 1000), 0.524);
  assert.equal(adjustFixedSplitFraction(0.5, -72, 1000), 0.428);
  assert.equal(adjustFixedSplitFraction(0.5, -400, 1000), 0.25);
});

test("fixed split pane grid templates use equal and golden-ratio tracks", () => {
  assert.equal(fixedSplitGridTemplateColumns("equal"), "minmax(0, 1fr) 14px minmax(0, 1fr)");
  assert.match(fixedSplitGridTemplateColumns("leftWide"), /1\.618fr/);
  assert.match(fixedSplitGridTemplateColumns("rightWide"), /1\.618fr/);
  assert.equal(fixedSplitGridTemplateColumns(0.6), "minmax(0, 0.6fr) 14px minmax(0, 0.4fr)");
});

test("schema SQL insertion uses quoted physical table and column names", () => {
  assert.equal(buildSchemaSqlIdentifierText(invoiceTable, invoiceColumn), "\"INVOICES\".\"TOTAL_AMOUNT\"");
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

test("SQL execution payload trims SQL and preserves execution scope", () => {
  assert.deepEqual(
    sqlExecutePayload(
      " SELECT TOTAL_AMOUNT FROM INVOICES ",
      "finance",
      { table_names: ["INVOICES"], columns: { INVOICES: ["TOTAL_AMOUNT"] } }
    ),
    {
      sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
      profile_id: "finance",
      allowed_objects: { table_names: ["INVOICES"], columns: { INVOICES: ["TOTAL_AMOUNT"] } },
    }
  );
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
    row_limit: 0,
    note: "preview ready",
    engine: "select_ai",
    engine_meta: { profile_name: "P" },
    fallback_reason: "",
    rewritten_question: "請求金額を一覧で見る",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
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
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    explanation: "preview ready",
    safety: {
      is_safe: true,
      is_select_only: true,
      row_limit_applied: 0,
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
      { table_names: ["INVOICES"], columns: { INVOICES: ["TOTAL_AMOUNT"] } }
    ),
    {
      sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
      profile_id: "default",
      allowed_objects: {
        table_names: ["INVOICES"],
        columns: { INVOICES: ["TOTAL_AMOUNT"] },
      },
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

const historyManagementItems: HistoryItem[] = [
  {
    id: "h-new",
    question: "未入金の顧客を確認",
    engine: "select_ai_agent",
    generated_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL",
    created_at: "2026-06-22T10:00:00Z",
    elapsed_ms: 250,
    feedback_rating: "good",
    profile_id: "finance",
    profile_name: "経理プロファイル",
    rewritten_question: "未入金顧客",
    executable_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL",
    safety_is_safe: true,
    result_row_count: 2,
    result_columns: ["CUSTOMER_NAME"],
    feedback_comment: "期待通り",
  },
  {
    id: "h-old",
    question: "監査ログを確認",
    engine: "select_ai",
    generated_sql: "DELETE FROM AUDIT_LOG",
    created_at: "2026-06-20T10:00:00Z",
    elapsed_ms: 30,
    feedback_rating: null,
    profile_id: "audit",
    profile_name: "監査プロファイル",
    rewritten_question: "",
    executable_sql: "",
    safety_is_safe: false,
    result_row_count: 0,
    result_columns: [],
    feedback_comment: "",
  },
];

test("history management filters searchable fields, feedback, and safety", () => {
  const searched = filterAndSortHistory(historyManagementItems, {
    search: "経理プロファイル",
    feedback: "all",
    safety: "all",
    sort: { key: "created_at", direction: "desc" },
  });
  assert.deepEqual(searched.map((item) => item.id), ["h-new"]);

  const blockedUnrated = filterAndSortHistory(historyManagementItems, {
    search: "AUDIT_LOG",
    feedback: "unrated",
    safety: "blocked",
    sort: { key: "created_at", direction: "desc" },
  });
  assert.deepEqual(blockedUnrated.map((item) => item.id), ["h-old"]);
});

test("history management sorts by execution time and question", () => {
  const oldestFirst = filterAndSortHistory(historyManagementItems, {
    search: "",
    feedback: "all",
    safety: "all",
    sort: { key: "created_at", direction: "asc" },
  });
  assert.deepEqual(oldestFirst.map((item) => item.id), ["h-old", "h-new"]);

  const questionAscending = filterAndSortHistory(historyManagementItems, {
    search: "",
    feedback: "all",
    safety: "all",
    sort: { key: "question", direction: "asc" },
  });
  const questionDescending = filterAndSortHistory(historyManagementItems, {
    search: "",
    feedback: "all",
    safety: "all",
    sort: { key: "question", direction: "desc" },
  });
  assert.deepEqual(
    questionDescending.map((item) => item.id),
    questionAscending.map((item) => item.id).reverse()
  );
});

test("history management preserves a visible selection and falls back to the first row", () => {
  assert.equal(selectedVisibleHistoryId(historyManagementItems, "h-old"), "h-old");
  assert.equal(selectedVisibleHistoryId(historyManagementItems, "missing"), "h-new");
  assert.equal(selectedVisibleHistoryId([], "h-old"), "");
});
