import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import {
  expectSplitPaneReservedTrack,
  expectSplitPaneStacked,
} from "./_helpers/fixed-split-pane";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function clickPageHeaderAction(page: Page, testId: string, name: string) {
  const actions = page.getByTestId(testId);
  await expect(actions).toBeVisible();
  const visibleButton = actions.getByRole("button", { name, exact: true });
  if (await visibleButton.isVisible()) {
    await visibleButton.click();
    return;
  }
  await actions.getByRole("button", { name: "その他の操作", exact: true }).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}

type JsonValue = Record<string, unknown> | unknown[];

interface MockApiState {
  previewPayload: Record<string, unknown> | null;
  executePayload: Record<string, unknown> | null;
  adminExecutePayload: Record<string, unknown> | null;
  feedbackPayload: Record<string, unknown> | null;
  feedbackConfigPayload: Record<string, unknown> | null;
  feedbackEntriesDeletePayload: Record<string, unknown> | null;
  selectAiFeedbackAddPayload: Record<string, unknown> | null;
  selectAiFeedbackDeletePayload: Record<string, unknown> | null;
  selectAiFeedbackUpdatePayload: Record<string, unknown> | null;
  profilePatchPayload: Record<string, unknown> | null;
  commentApplyPayload: Record<string, unknown> | null;
  commentGeneratePayload: Record<string, unknown> | null;
  metadataSamplesPayload: Record<string, unknown> | null;
  dbProfileDropPayload: Record<string, unknown> | null;
  dropTablePayload: Record<string, unknown> | null;
  evaluationSetPayload: Record<string, unknown> | null;
  samplePayload: Record<string, unknown> | null;
  sampleImportError: boolean;
  previewDataPayload: Record<string, unknown> | null;
  previewDataExportPayload: Record<string, unknown> | null;
  csvUploadPayload: Record<string, unknown> | null;
  importTabularPayload: Record<string, unknown> | null;
  statementsPayload: Record<string, unknown> | null;
  syntheticDataPayload: Record<string, unknown> | null;
  dropViewPayload: Record<string, unknown> | null;
  extractJoinWherePayload: Record<string, unknown> | null;
  analyzePayload: Record<string, unknown> | null;
  reversePayload: Record<string, unknown> | null;
  reverseDeepPayload: Record<string, unknown> | null;
  classifierTrainingImportBody: string | null;
  classifierFeedbackImportPayload: Record<string, unknown> | null;
  classifierModelListRequests: number;
}

const safety = {
  is_safe: true,
  is_select_only: true,
  row_limit_applied: 0,
  blocked_reason: "",
  warnings: [],
  referenced_tables: ["INVOICES"],
  referenced_columns: ["TOTAL_AMOUNT"],
};

const timing = {
  created_at: "2026-06-21T10:00:00.000Z",
  started_at: "2026-06-21T10:00:00.010Z",
  finished_at: "2026-06-21T10:00:00.180Z",
  elapsed_ms: 170,
  stage_timings: [{ stage: "mock", elapsed_ms: 170 }],
};

const schemaCatalog = {
  refreshed_at: "2026-06-21T10:00:00.000Z",
  tables: [
    {
      table_name: "INVOICES",
      logical_name: "請求",
      owner: "APP",
      table_type: "TABLE",
      comment: "請求情報",
      row_count: 2,
      constraints: ["PK_INVOICES"],
      columns: [
        {
          column_name: "CUSTOMER_NAME",
          logical_name: "取引先名",
          data_type: "VARCHAR2(120)",
          nullable: false,
          comment: "取引先名",
          sample_values: ["青山商事"],
        },
        {
          column_name: "TOTAL_AMOUNT",
          logical_name: "請求金額",
          data_type: "NUMBER",
          nullable: false,
          comment: "税込請求金額",
          sample_values: ["1200000"],
        },
      ],
    },
  ],
};

const overflowSchemaCatalog = {
  ...schemaCatalog,
  tables: [
    {
      table_name:
        "DENPYO_ACTIVITY_LOG_WITH_AN_INTENTIONALLY_LONG_SCHEMA_IDENTIFIER_THAT_USED_TO_FORCE_THE_LEFT_PANE_OVER_THE_DIVIDER",
      logical_name:
        "伝票活動ログ参照用の非常に長い論理テーブル名が分割ペインの境界内で折り返されることを確認する表",
      owner: "APP",
      table_type: "TABLE",
      comment:
        "コメントも長い日本語文として表示され、右側の NL2SQL 検索ワークベンチへ重ならないことを確認します。",
      row_count: 266,
      constraints: ["PK_DENPYO_ACTIVITY_LOG_WITH_AN_INTENTIONALLY_LONG_NAME"],
      columns: [
        {
          column_name:
            "CUSTOMER_PAYMENT_RECONCILIATION_STATUS_WITH_A_VERY_LONG_COLUMN_NAME_THAT_USED_TO_OVERFLOW",
          logical_name:
            "入金消込ステータス確認用の非常に長い論理列名がペイン内で折り返されることを確認する項目",
          data_type: "VARCHAR2(4000)",
          nullable: false,
          comment: "長い列コメント",
          sample_values: [
            "未消込かつ確認待ちの非常に長いサンプル値が表示されても横方向へはみ出さない",
          ],
        },
        {
          column_name: "UPDATED_AT",
          logical_name: "更新日時",
          data_type: "TIMESTAMP",
          nullable: true,
          comment: "更新日時",
          sample_values: ["2026-06-21 10:00:00"],
        },
      ],
    },
    ...schemaCatalog.tables,
  ],
};

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定プロファイル",
    description: "請求・顧客を扱う既定プロファイル",
    allowed_tables: ["INVOICES"],
    allowed_views: [],
    glossary: { 請求金額: "INVOICES.TOTAL_AMOUNT" },
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: {
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      region: "ap-osaka-1",
      model: "cohere.command-r-plus",
      embedding_model: "cohere.embed-v4.0",
      max_tokens: 32000,
      enforce_object_list: true,
      comments: true,
      annotations: false,
      constraints: false,
      role: "既定の Oracle SQL アシスタント",
      additional_instructions: "金額は円単位で表示する。",
    },
    archived: false,
  },
];

const historySql =
  "SELECT i.INVOICE_ID, c.CUSTOMER_NAME, i.DUE_DATE, p.PAID_AT, i.CUSTOMER_PAYMENT_RECONCILIATION_STATUS_WITH_A_VERY_LONG_COLUMN_NAME FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID LEFT JOIN PAYMENTS p ON p.INVOICE_ID = i.INVOICE_ID WHERE p.PAID_AT IS NULL OR p.PAID_AT > i.DUE_DATE FETCH FIRST 100 ROWS ONLY";

const historyItem = {
  id: "hist-001",
  question: "履歴から再実行したい請求金額",
  engine: "select_ai_agent",
  generated_sql: historySql,
  executable_sql: historySql,
  created_at: "2026-06-21T10:00:00.000Z",
  elapsed_ms: 210,
  feedback_rating: null,
  profile_id: "default",
  profile_name: "既定プロファイル",
  rewritten_question: "履歴から再実行したい請求金額",
  safety_is_safe: true,
  result_row_count: 1,
  result_columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
  feedback_comment: "",
};

const classifierTrainingExamples = Array.from({ length: 12 }, (_, index) => {
  const number = index + 1;
  const paddedNumber = String(number).padStart(2, "0");
  return {
    id: `example-${String(number).padStart(3, "0")}`,
    category: index % 2 === 0 ? "既定プロファイル" : "入金管理",
    text:
      index === 0
        ? "ページング対象 01: 請求金額が大きい取引先を見たい"
        : index === 1
          ? "ページング対象 02: 未入金の請求を確認したい"
          : `ページング対象 ${paddedNumber}: 訓練データ確認 ${number}`,
    profile_id: index % 2 === 0 ? "default" : "payment",
    source: "training_data.xlsx",
  };
});

function fulfillJson(route: Route, data: JsonValue) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

function createRequestGate() {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function createFileDataTransfer(
  page: Page,
  files: Array<{ name: string; type: string; content: string }>
) {
  return page.evaluateHandle((items) => {
    const dataTransfer = new DataTransfer();
    for (const item of items) {
      dataTransfer.items.add(new File([item.content], item.name, { type: item.type }));
    }
    return dataTransfer;
  }, files);
}

async function mockNl2SqlApi(page: Page): Promise<MockApiState> {
  const state: MockApiState = {
    previewPayload: null,
    executePayload: null,
    adminExecutePayload: null,
    feedbackPayload: null,
    feedbackConfigPayload: null,
    feedbackEntriesDeletePayload: null,
    selectAiFeedbackAddPayload: null,
    selectAiFeedbackDeletePayload: null,
    selectAiFeedbackUpdatePayload: null,
    profilePatchPayload: null,
    commentApplyPayload: null,
    commentGeneratePayload: null,
    metadataSamplesPayload: null,
    dbProfileDropPayload: null,
    dropTablePayload: null,
    evaluationSetPayload: null,
    samplePayload: null,
    sampleImportError: false,
    previewDataPayload: null,
    previewDataExportPayload: null,
    csvUploadPayload: null,
    importTabularPayload: null,
    statementsPayload: null,
    syntheticDataPayload: null,
    dropViewPayload: null,
    extractJoinWherePayload: null,
    analyzePayload: null,
    reversePayload: null,
    reverseDeepPayload: null,
    classifierTrainingImportBody: null,
    classifierFeedbackImportPayload: null,
    classifierModelListRequests: 0,
  };
  let classifierExamples: Record<string, unknown>[] = [...classifierTrainingExamples];
  let classifierIsStale = false;
  let feedbackCandidateAdded = false;
  const sampleObjects = ["DEPARTMENT", "EMPLOYEE", "PROJECT", "V_EMP_DEPT", "V_DEPT_PROJECT"];
  const sampleSql = {
    tables: [
      "CREATE TABLE DEPARTMENT (DEPARTMENT_ID NUMBER PRIMARY KEY, DEPARTMENT_NAME VARCHAR2(100) NOT NULL)",
      "CREATE TABLE EMPLOYEE (EMPLOYEE_ID NUMBER PRIMARY KEY, EMPLOYEE_NAME VARCHAR2(120) NOT NULL, DEPARTMENT_ID NUMBER)",
      "CREATE TABLE PROJECT (PROJECT_ID NUMBER PRIMARY KEY, PROJECT_NAME VARCHAR2(160) NOT NULL, DEPARTMENT_ID NUMBER)",
    ],
    views: [
      "CREATE OR REPLACE VIEW V_EMP_DEPT AS SELECT E.EMPLOYEE_ID, E.EMPLOYEE_NAME, D.DEPARTMENT_NAME FROM EMPLOYEE E JOIN DEPARTMENT D ON D.DEPARTMENT_ID = E.DEPARTMENT_ID",
      "CREATE OR REPLACE VIEW V_DEPT_PROJECT AS SELECT D.DEPARTMENT_NAME, P.PROJECT_NAME FROM DEPARTMENT D JOIN PROJECT P ON P.DEPARTMENT_ID = D.DEPARTMENT_ID",
    ],
    data: ["INSERT INTO DEPARTMENT (DEPARTMENT_ID, DEPARTMENT_NAME) VALUES (10, '開発部')"],
    delete: [
      "DROP VIEW V_EMP_DEPT",
      "DROP VIEW V_DEPT_PROJECT",
      "DROP TABLE EMPLOYEE PURGE",
      "DROP TABLE PROJECT PURGE",
      "DROP TABLE DEPARTMENT PURGE",
    ],
  };
  let sampleImportedObjects: string[] = [];
  let legacyMaterial: { glossary: Record<string, string>; rules: string[] } = {
    glossary: Object.fromEntries([
      ["売上", "INVOICES.TOTAL_AMOUNT"],
      ...Array.from({ length: 20 }, (_, index) => [
        `用語${index + 2}`,
        `INVOICES.COLUMN_${index + 2}`,
      ]),
    ]),
    rules: ["SELECT のみ", ...Array.from({ length: 20 }, (_, index) => `グローバルルール${index + 2}`)],
  };
  let evaluationSets: Record<string, unknown>[] = [
    {
      id: "eval-001",
      name: "請求ベンチマーク",
      description: "保存済み請求ケース",
      profile_id: "default",
      profile_name: "既定プロファイル",
      engine: "select_ai",
      cases: [
        {
          question: "保存済み請求金額",
          expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
          profile_id: "default",
        },
      ],
      created_at: "2026-06-21T10:00:00.000Z",
      updated_at: "2026-06-21T10:00:00.000Z",
      archived: false,
    },
  ];
  let evaluationRuns: Record<string, unknown>[] = [
    {
      id: "eval-run-001",
      created_at: "2026-06-21T10:02:00.000Z",
      evaluation_set_id: "eval-001",
      evaluation_set_name: "請求ベンチマーク",
      profile_id: "default",
      profile_name: "既定プロファイル",
      engine: "select_ai",
      cases: [
        {
          question: "保存済み請求金額",
          expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
          profile_id: "default",
        },
      ],
      result: {
        evaluation_suite: "deterministic_mock",
        total_cases: 1,
        executable_rate: 1,
        select_only_rate: 1,
        findings: [],
      },
      report: "NL2SQL deterministic evaluation\nSuite: deterministic_mock\nCases: 1",
    },
  ];

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-mock",
      refreshed_at: schemaCatalog.refreshed_at,
      object_count: schemaCatalog.tables.length,
      column_count: schemaCatalog.tables.reduce((total, table) => total + table.columns.length, 0),
      change_token: 1,
      etag: "schema-mock",
    })
  );
  await page.route("**/api/schema/objects?*", (route) =>
    fulfillJson(route, {
      items: schemaCatalog.tables.map((table) => ({
        owner: table.owner,
        object_name: table.table_name,
        object_type: table.table_type,
        logical_name: table.logical_name,
        comment: table.comment,
        row_count: table.row_count,
        column_count: table.columns.length,
        last_ddl_at: "",
      })),
      next_cursor: null,
      total: schemaCatalog.tables.length,
      catalog_version: 1,
    })
  );
  await page.route("**/api/schema/objects/*/*", (route) => {
    const parts = new URL(route.request().url()).pathname.split("/");
    const owner = decodeURIComponent(parts.at(-2) ?? "");
    const objectName = decodeURIComponent(parts.at(-1) ?? "");
    const table = schemaCatalog.tables.find(
      (item) => item.owner === owner && item.table_name === objectName
    );
    return fulfillJson(route, {
      table: table ?? schemaCatalog.tables[0],
      dependencies: [],
      catalog_version: 1,
      etag: "schema-mock",
    });
  });
  await page.route("**/api/nl2sql/sample-data", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      profile_id: "sql_assist_sample",
      confirmation: "SQL_ASSIST_SAMPLE",
      objects: sampleObjects,
      imported_objects: sampleImportedObjects,
      sql: sampleSql,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/sample-data/import", (route) => {
    state.samplePayload = route.request().postDataJSON() as Record<string, unknown>;
    if (state.sampleImportError) {
      return fulfillJson(route, {
        operation: "import",
        step: state.samplePayload.step ?? "all",
        runtime: "oracle",
        executed: false,
        objects: sampleObjects,
        statements: [
          {
            index: 1,
            statement_type: "CREATE",
            status: "error",
            sql: sampleSql.tables[0],
            error_message: "ORA-00922: missing or invalid option Help: https://docs.oracle.com/error-help/db/ora-00922/",
            elapsed_ms: 30,
          },
        ],
        warnings: [],
        profile_id: "sql_assist_sample",
        timing,
      });
    }
    sampleImportedObjects = [...sampleObjects];
    return fulfillJson(route, {
      operation: "import",
      step: state.samplePayload.step ?? "all",
      runtime: "deterministic",
      executed: true,
      objects: sampleObjects,
      statements: [{ index: 1, statement_type: "CREATE", status: "applied_to_local_state", sql: sampleSql.tables[0], error_message: "" }],
      warnings: [],
      profile_id: "sql_assist_sample",
      timing,
    });
  });
  await page.route("**/api/nl2sql/sample-data/delete", (route) => {
    state.samplePayload = route.request().postDataJSON() as Record<string, unknown>;
    sampleImportedObjects = [];
    return fulfillJson(route, {
      operation: "delete",
      step: "all",
      runtime: "deterministic",
      executed: true,
      objects: sampleObjects,
      statements: [{ index: 1, statement_type: "DROP", status: "applied_to_local_state", sql: sampleSql.delete[0], error_message: "" }],
      warnings: [],
      profile_id: "sql_assist_sample",
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        {
          name: "INVOICES",
          owner: "APP",
          object_type: "table",
          row_count: 2,
          comment: "請求情報",
        },
        {
          name: "PAYMENTS",
          owner: "APP",
          object_type: "table",
          row_count: 1,
          comment: "入金情報",
        },
        {
          name: "AUDIT_LOG",
          owner: "APP",
          object_type: "table",
          row_count: 1,
          comment: "監査ログ",
        },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        {
          name: "V_EMP_DEPT",
          owner: "APP",
          object_type: "view",
          row_count: null,
          comment: "社員と部署",
        },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const url = new URL(route.request().url());
    const objectType = url.searchParams.get("type") ?? "all";
    const rowState = url.searchParams.get("row_state") ?? "all";
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const allItems = [
      { name: "INVOICES", owner: "APP", object_type: "table", row_count: 2, comment: "請求情報" },
      { name: "PAYMENTS", owner: "APP", object_type: "table", row_count: 1, comment: "入金情報" },
      { name: "AUDIT_LOG", owner: "APP", object_type: "table", row_count: 1, comment: "監査ログ" },
      { name: "V_EMP_DEPT", owner: "APP", object_type: "view", row_count: null, comment: "社員と部署" },
      { name: "DBTOOLS$EXECUTION_HISTORY", owner: "APP", object_type: "table", row_count: 4, comment: "内部履歴" },
      { name: "SYS#AUDIT_VIEW", owner: "APP", object_type: "view", row_count: null, comment: "内部監査" },
    ];
    const items = allItems.filter((item) => {
      if (objectType !== "all" && item.object_type !== objectType) return false;
      if (rowState === "with_rows" && !(typeof item.row_count === "number" && item.row_count > 0)) return false;
      if (rowState === "empty_rows" && item.row_count !== 0) return false;
      if (rowState === "unknown_rows" && item.row_count !== null) return false;
      return !query || `${item.name} ${item.owner} ${item.comment}`.toLowerCase().includes(query);
    });
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: items.filter((item) => item.object_type === "table").length,
      view_count: items.filter((item) => item.object_type === "view").length,
      next_cursor: null,
      refreshed_at: schemaCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES?*", (route) =>
    fulfillJson(route, {
      name: "INVOICES",
      owner: "APP",
      object_type: "table",
      row_count: 2,
      comment: "請求情報",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER);\nCOMMENT ON TABLE "INVOICES" IS \'請求情報\';',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES", (route) =>
    fulfillJson(route, {
      name: "INVOICES",
      owner: "APP",
      object_type: "table",
      row_count: 2,
      comment: "請求情報",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER);\nCOMMENT ON TABLE "INVOICES" IS \'請求情報\';',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES/export.xlsx", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "Content-Disposition": 'attachment; filename="invoices_columns.xlsx"',
      },
      body: "xlsx",
    })
  );
  await page.route("**/api/nl2sql/db-admin/views/V_EMP_DEPT", (route) =>
    fulfillJson(route, {
      name: "V_EMP_DEPT",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "社員と部署",
      columns: [
        {
          column_name: "EMPLOYEE_NAME",
          logical_name: "社員名",
          data_type: "VARCHAR2(120)",
          nullable: false,
          comment: "社員名",
          sample_values: [],
        },
      ],
      ddl: 'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS SELECT E.EMPLOYEE_NAME FROM EMPLOYEE E JOIN DEPARTMENT D ON D.DEPARTMENT_ID = E.DEPARTMENT_ID;',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/views/V_EMP_DEPT?*", (route) =>
    fulfillJson(route, {
      name: "V_EMP_DEPT",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "社員と部署",
      columns: [
        {
          column_name: "EMPLOYEE_NAME",
          logical_name: "社員名",
          data_type: "VARCHAR2(120)",
          nullable: false,
          comment: "社員名",
          sample_values: [],
        },
      ],
      ddl: 'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS SELECT E.EMPLOYEE_NAME FROM EMPLOYEE E JOIN DEPARTMENT D ON D.DEPARTMENT_ID = E.DEPARTMENT_ID;',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/preview-data", (route) => {
    state.previewDataPayload = route.request().postDataJSON() as Record<string, unknown>;
    const rows = Array.from({ length: 12 }, (_, index) => ({
      CUSTOMER_NAME: `顧客${String(index + 1).padStart(2, "0")}`,
      TOTAL_AMOUNT: (index + 1) * 1000,
    }));
    return fulfillJson(route, {
      runtime: "deterministic",
      sql: 'SELECT * FROM "INVOICES" FETCH FIRST 100 ROWS ONLY',
      results: {
        columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
        rows,
        total: rows.length,
      },
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/preview-data/export.xlsx", (route) => {
    state.previewDataExportPayload = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "Content-Disposition": 'attachment; filename="invoices_preview.xlsx"',
      },
      body: "xlsx",
    });
  });
  await page.route("**/api/nl2sql/db-admin/upload-csv", (route) => {
    state.csvUploadPayload = route.request().postDataJSON() as Record<string, unknown>;
    const executed = true;
    return fulfillJson(route, {
      table_name: "INVOICES",
      filename: "upload.csv",
      mode: "insert",
      matched_columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      unmatched_csv_columns: ["UNKNOWN_COLUMN"],
      row_count: 2,
      success_count: executed ? 2 : 0,
      error_count: 0,
      row_errors: [],
      hint: "",
      executed,
      runtime: "deterministic",
      sample_rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: "1200000", UNKNOWN_COLUMN: "x" }],
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/import-tabular", (route) => {
    state.importTabularPayload = route.request().postDataJSON() as Record<string, unknown>;
    const tableName = String(state.importTabularPayload.table_name ?? "IMPORTED_ORDERS");
    return fulfillJson(route, {
      table_name: tableName,
      filename: state.importTabularPayload.filename ?? "orders.csv",
      sheet_name: state.importTabularPayload.sheet_name ?? "",
      mode: state.importTabularPayload.mode ?? "create",
      columns: [
        { source_name: "ORDER_ID", column_name: "ORDER_ID", data_type: "NUMBER", nullable: false },
        { source_name: "ORDER_NAME", column_name: "ORDER_NAME", data_type: "VARCHAR2(4000)", nullable: true },
      ],
      row_count: 1,
      executed: true,
      ddl: `CREATE TABLE ${tableName} (ORDER_ID NUMBER, ORDER_NAME VARCHAR2(4000))`,
      insert_sql: `INSERT INTO ${tableName} (ORDER_ID, ORDER_NAME) VALUES (:1, :2)`,
      warnings: [],
      sample_rows: [{ ORDER_ID: "1", ORDER_NAME: "青山商事" }],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/statements", (route) => {
    state.statementsPayload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(state.statementsPayload.sql ?? "INSERT INTO INVOICES (CUSTOMER_NAME) VALUES ('青山商事')");
    const policy = String(state.statementsPayload.policy ?? "data_dml");
    const executed = true;
    const invalidAnnotationName =
      policy === "annotation_sql" && /ADD\s+IF\s+NOT\s+EXISTS\s+COMMENT\b/i.test(sql);
    return fulfillJson(route, {
      executed: invalidAnnotationName ? false : executed,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: policy,
          status: invalidAnnotationName ? "blocked" : "executed",
          sql,
          row_count: null,
          message: invalidAnnotationName ? "" : "executed",
          elapsed_ms: 0,
          error_message: invalidAnnotationName
            ? "ORA-11548 相当: annotation 名 COMMENT は Oracle の予約語です。説明には UI_Display を使用するか、意図的な名前であれば \"COMMENT\" と二重引用符で囲んでください。"
            : "",
        },
      ],
      committed: invalidAnnotationName ? false : executed,
      rolled_back: false,
      warnings: invalidAnnotationName
        ? ["禁止された操作が含まれるため実行しませんでした。"]
        : [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/execute", (route) => {
    state.adminExecutePayload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(state.adminExecutePayload.sql ?? "");
    const confirmation = String(state.adminExecutePayload.confirmation ?? "");
    const hasMutation =
      /\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b/i.test(sql);
    const isSelect = !hasMutation && /^\s*(?:--[^\n]*\n\s*)*(?:select|with)\b/i.test(sql);
    if (!isSelect && confirmation !== "ADMIN_EXECUTE") {
      return fulfillJson(route, {
        executed: false,
        runtime: "oracle",
        select_result: null,
        statements: [
          {
            index: 1,
            statement_type: "UPDATE",
            status: "confirmation_required",
            sql,
            row_count: null,
            message: "",
            elapsed_ms: 0,
            error_message: "ADMIN_EXECUTE が必要です。",
          },
        ],
        committed: false,
        rolled_back: false,
        warnings: ["ADMIN_EXECUTE が必要です。"],
        timing,
      });
    }
    return fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: isSelect
        ? {
            columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
            rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
            total: 1,
          }
        : null,
      statements: [
        {
          index: 1,
          statement_type: isSelect ? "SELECT" : "UPDATE",
          status: isSelect ? "executed" : "success",
          sql,
          row_count: isSelect ? 1 : 2,
          message: isSelect ? "1 rows" : "2 rows affected",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: !isSelect,
      rolled_back: false,
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/drop-table", (route) => {
    state.dropTablePayload = route.request().postDataJSON() as Record<string, unknown>;
    const tableName = String(state.dropTablePayload.table_name ?? "INVOICES");
    const executed = true;
    return fulfillJson(route, {
      executed,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: "executed",
          sql: `DROP TABLE "${tableName}" PURGE`,
          row_count: null,
          message: "executed",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: executed,
      rolled_back: false,
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/drop-view", (route) => {
    state.dropViewPayload = route.request().postDataJSON() as Record<string, unknown>;
    const executed = true;
    return fulfillJson(route, {
      executed,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: "executed",
          sql: 'DROP VIEW "V_EMP_DEPT"',
          row_count: null,
          message: "executed",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: executed,
      rolled_back: false,
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/extract-join-where", (route) => {
    state.extractJoinWherePayload = route.request().postDataJSON() as Record<string, unknown>;
    const promptProfile = String(
      state.extractJoinWherePayload.prompt_profile ?? "join_where_strict"
    );
    return fulfillJson(route, {
      join_text:
        promptProfile === "sql_structure"
          ? "JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\nON: EMPLOYEE(e).DEPARTMENT_ID = DEPARTMENT(d).DEPARTMENT_ID"
          : "[INNER] E(EMPLOYEE).DEPARTMENT_ID = D(DEPARTMENT).DEPARTMENT_ID",
      where_text: promptProfile === "sql_structure" ? "EMPLOYEE(e).STATUS = 'A'" : "None",
      source: "deterministic",
      warnings: [],
      prompt_profile: promptProfile,
      structure_markdown:
        promptProfile === "sql_structure"
          ? "## SQL構造分析\n\n### JOIN句\n- JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\n\n### WHERE句\n- EMPLOYEE(e).STATUS = 'A'"
          : "",
    });
  });
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: profiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: Object.keys(profile.glossary).length,
        few_shot_count: profile.few_shot_examples.length,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-06-21T10:00:00.000Z",
      })),
      next_cursor: null,
      total: profiles.length,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/legacy-learning-material", (route) =>
    fulfillJson(route, legacyMaterial)
  );
  await page.route("**/api/nl2sql/legacy-learning-material/terms/import", (route) => {
    legacyMaterial = {
      ...legacyMaterial,
      glossary: { 粗利: "INVOICES.PROFIT" },
    };
    return fulfillJson(route, legacyMaterial);
  });
  await page.route("**/api/nl2sql/legacy-learning-material/rules/import", (route) => {
    legacyMaterial = {
      ...legacyMaterial,
      rules: ["集計時は NULL を除外する"],
    };
    return fulfillJson(route, legacyMaterial);
  });
  await page.route("**/api/nl2sql/legacy-learning-material/terms/export.xlsx", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      body: "terms",
    })
  );
  await page.route("**/api/nl2sql/legacy-learning-material/rules/export.xlsx", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      body: "rules",
    })
  );
  await page.route("**/api/nl2sql/profiles/default", (route) => {
    if (route.request().method() === "PATCH") {
      state.profilePatchPayload = route.request().postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, {
        ...profiles[0],
        ...state.profilePatchPayload,
        id: "default",
        archived: false,
      });
    }
    return fulfillJson(route, profiles[0]);
  });
  // 保存時に一体化された Oracle 反映(業務 profile → DBMS_CLOUD_AI profile)。
  await page.route("**/api/nl2sql/profiles/*/select-ai-profile", (route) =>
    fulfillJson(route, {
      runtime: "oracle",
      executed: true,
      status: "saved",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      original_name: "",
      ddl: ["BEGIN DBMS_CLOUD_AI.CREATE_PROFILE(profile_name => :name, attributes => :attrs); END;"],
      profile: null,
      warnings: [],
      engine_meta: {},
    })
  );
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items: [historyItem] }));
  await page.route(/\/api\/nl2sql\/feedback(?:\?.*)?$/, (route) => {
    if (route.request().method() === "GET") {
      return fulfillJson(route, {
        items: [
          {
            ...historyItem,
            feedback_rating: "good",
            feedback_comment: "SQL は期待通りです",
            training_status: feedbackCandidateAdded ? "added" : "pending",
            training_example_id: feedbackCandidateAdded ? "feedback-hist-001" : "",
          },
        ],
        total: 1,
        next_cursor: "",
      });
    }
    state.feedbackPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      history_id: "hist-001",
      rating: "good",
      saved: true,
      comment: "SQL は期待通りです",
    });
  });
  await page.route("**/api/nl2sql/feedback/*", (route) =>
    fulfillJson(route, { history_id: "hist-001", cleared: true })
  );
  await page.route("**/api/nl2sql/demo/learning", (route) =>
    fulfillJson(route, {
      seeded_history_count: 3,
      seeded_feedback_count: 3,
      history_ids: [
        "demo-learning-invoice-total",
        "demo-learning-customer-sales",
        "demo-learning-payment-delay",
      ],
      profile_ids: ["default"],
      message: "Demo 学習データを投入しました。",
    })
  );
  await page.route("**/api/nl2sql/feedback-index", (route) =>
    fulfillJson(route, {
      operation: "status",
      status: "stale",
      executed: false,
      runtime: "deterministic",
      source_history_count: 1,
      indexable_count: 1,
      indexed_count: 0,
      vector_dimension: 1536,
      vector_backend: "oracle_26ai",
      embedding_provider: "oci_genai",
      embedding_model: "cohere.embed-v4.0",
      embedding_configured: false,
      ddl: [
        "CREATE TABLE NL2SQL_FEEDBACK_VECTORS (EMBEDDING VECTOR(1536, FLOAT32))",
        "CREATE VECTOR INDEX NL2SQL_FEEDBACK_VEC_IDX ON NL2SQL_FEEDBACK_VECTORS (EMBEDDING)",
      ],
      warnings: [],
      timing,
    })
  );
  await page.route("**/api/nl2sql/feedback-index/rebuild", (route) =>
    fulfillJson(route, {
      operation: "rebuild",
      status: "ready",
      executed: false,
      runtime: "deterministic",
      source_history_count: 1,
      indexable_count: 1,
      indexed_count: 1,
      vector_dimension: 1536,
      vector_backend: "oracle_26ai",
      embedding_provider: "oci_genai",
      embedding_model: "cohere.embed-v4.0",
      embedding_configured: false,
      ddl: [
        "CREATE TABLE NL2SQL_FEEDBACK_VECTORS (EMBEDDING VECTOR(1536, FLOAT32))",
        "CREATE VECTOR INDEX NL2SQL_FEEDBACK_VEC_IDX ON NL2SQL_FEEDBACK_VECTORS (EMBEDDING)",
      ],
      warnings: ["Feedback vector index の rebuild 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"],
      timing,
    })
  );
  await page.route("**/api/nl2sql/feedback-index/clear", (route) =>
    fulfillJson(route, {
      operation: "clear",
      status: "empty",
      executed: false,
      runtime: "deterministic",
      source_history_count: 1,
      indexable_count: 1,
      indexed_count: 0,
      vector_dimension: 1536,
      vector_backend: "oracle_26ai",
      embedding_provider: "oci_genai",
      embedding_model: "cohere.embed-v4.0",
      embedding_configured: false,
      ddl: ["CREATE TABLE NL2SQL_FEEDBACK_VECTORS (EMBEDDING VECTOR(1536, FLOAT32))"],
      warnings: ["Feedback vector index の clear 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"],
      timing,
    })
  );
  await page.route("**/api/nl2sql/feedback-entries", (route) =>
    fulfillJson(route, {
      items: [
        {
          history_id: "hist-001",
          question: historyItem.question,
          generated_sql: historyItem.generated_sql,
          profile_id: "default",
          profile_name: "既定プロファイル",
          feedback_rating: null,
          feedback_comment: "",
          indexed: false,
          created_at: historyItem.created_at,
        },
      ],
      total: 1,
      indexed_count: 0,
    })
  );
  await page.route("**/api/nl2sql/feedback-entries/delete", (route) => {
    state.feedbackEntriesDeletePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      items: [],
      total: 0,
      indexed_count: 0,
    });
  });
  await page.route("**/api/nl2sql/feedback-config", (route) => {
    if (route.request().method() === "PATCH") {
      state.feedbackConfigPayload = route.request().postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, route.request().postDataJSON() as Record<string, unknown>);
    }
    return fulfillJson(route, {
      similarity_threshold: 0,
      match_limit: 3,
    });
  });
  await page.route("**/api/nl2sql/classifier", (route) =>
    fulfillJson(route, {
      ready: true,
      trained: true,
      stale: classifierIsStale,
      classifier_version: "classifier-001",
      updated_at: "2026-06-21T10:00:00.000Z",
      example_count: classifierExamples.length,
      trained_example_count: classifierTrainingExamples.length,
      pending_change_count: classifierExamples.length - classifierTrainingExamples.length,
      category_count: 2,
      categories: ["既定プロファイル", "入金管理"],
      embedding_model: "deterministic-hash-1536",
      vector_dimension: 1536,
      persistence_mode: "memory",
      recommendation_source: "classifier",
      metrics: { training_accuracy: 1 },
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/classifier/models", (route) => {
    state.classifierModelListRequests += 1;
    return route.abort();
  });
  await page.route("**/api/nl2sql/classifier/training-data", (route) =>
    fulfillJson(route, {
      total_examples: classifierExamples.length,
      categories: ["既定プロファイル", "入金管理"],
      warnings: [],
      examples: classifierExamples,
    })
  );
  await page.route("**/api/nl2sql/classifier/training-candidates*", (route) =>
    fulfillJson(route, {
      items: [
        {
          history_id: "hist-001",
          question: "履歴から再実行したい請求金額",
          profile_id: "default",
          profile_name: "既定プロファイル",
          feedback_rating: "good",
          feedback_comment: "SQL は期待通りです",
          created_at: historyItem.created_at,
          status: feedbackCandidateAdded ? "added" : "pending",
          training_example_id: feedbackCandidateAdded ? "feedback-hist-001" : "",
          conflict_profile_ids: [],
        },
        {
          history_id: "hist-conflict",
          question: "競合している請求分類を確認したい",
          profile_id: "default",
          profile_name: "既定プロファイル",
          feedback_rating: "good",
          feedback_comment: "Profile の確認が必要です",
          created_at: historyItem.created_at,
          status: "conflict",
          training_example_id: "",
          conflict_profile_ids: ["payment"],
        },
        {
          history_id: "hist-source-changed",
          question: "元 feedback が変更された質問",
          profile_id: "default",
          profile_name: "既定プロファイル",
          feedback_rating: "bad",
          feedback_comment: "後から bad に変更",
          created_at: historyItem.created_at,
          status: "source_changed",
          training_example_id: "feedback-source-changed",
          conflict_profile_ids: [],
        },
      ],
      total: 3,
      next_cursor: "",
      pending_count: feedbackCandidateAdded ? 0 : 1,
      added_count: feedbackCandidateAdded ? 1 : 0,
      attention_count: 2,
    })
  );
  await page.route("**/api/nl2sql/classifier/training-data/from-feedback", (route) => {
    state.classifierFeedbackImportPayload = route.request().postDataJSON() as Record<string, unknown>;
    feedbackCandidateAdded = true;
    classifierIsStale = true;
    classifierExamples = [
      ...classifierExamples,
      {
        id: "feedback-hist-001",
        category: "既定プロファイル",
        text: "履歴から再実行したい請求金額",
        profile_id: "default",
        profile_name: "既定プロファイル",
        source: "feedback:hist-001",
        source_type: "feedback",
        source_history_id: "hist-001",
        created_at: "2026-06-21T10:06:00.000Z",
        updated_at: "2026-06-21T10:06:00.000Z",
      },
    ];
    return fulfillJson(route, {
      imported_count: 1,
      skipped_count: 0,
      conflict_count: 0,
      results: [
        {
          history_id: "hist-001",
          status: "added",
          training_example_id: "feedback-hist-001",
          profile_id: "default",
          message: "",
        },
      ],
    });
  });
  await page.route("**/api/nl2sql/classifier/training-data/import", (route) => {
    state.classifierTrainingImportBody = route.request().postDataBuffer()?.toString("utf8") ?? "";
    return fulfillJson(route, {
      imported_count: 1,
      skipped_count: 0,
      total_examples: classifierTrainingExamples.length + 1,
      categories: ["既定プロファイル", "入金管理"],
      warnings: [],
      examples: [],
    });
  });
  await page.route("**/api/nl2sql/classifier/train", (route) => {
    classifierIsStale = false;
    return fulfillJson(route, {
      ready: true,
      trained: true,
      stale: false,
      classifier_version: "classifier-002",
      updated_at: "2026-06-21T10:05:00.000Z",
      example_count: classifierExamples.length,
      trained_example_count: classifierExamples.length,
      pending_change_count: 0,
      category_count: 2,
      categories: ["既定プロファイル", "入金管理"],
      embedding_model: "deterministic-hash-1536",
      vector_dimension: 1536,
      persistence_mode: "memory",
      recommendation_source: "classifier",
      metrics: { training_accuracy: 1 },
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/classifier/predict", (route) =>
    fulfillJson(route, {
      recommendation_source: "classifier",
      classifier_version: "classifier-002",
      predicted_category: "既定プロファイル",
      confidence: 0.92,
      candidates: [
        { category: "既定プロファイル", score: 0.92, profile_id: "default", profile_name: "既定プロファイル" },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/profiles/refresh**", (route) =>
    fulfillJson(route, {
      engine: "select_ai",
      refreshed: true,
      status: "ready",
      refreshed_at: "2026-06-21T10:00:00.000Z",
      profile_name: "既定プロファイル",
      team_name: "",
      warning: "",
      asset_names: { profile: "NL2SQL_DEFAULT_SELECT_AI" },
      engine_meta: { runtime: "mock" },
    })
  );
  await page.route("**/api/nl2sql/select-ai-agent/assets/refresh**", (route) =>
    fulfillJson(route, {
      engine: "select_ai_agent",
      refreshed: true,
      status: "ready",
      refreshed_at: "2026-06-21T10:00:00.000Z",
      profile_name: "既定プロファイル",
      team_name: "NL2SQL_DEFAULT_TEAM",
      warning: "",
      asset_names: {
        profile: "NL2SQL_DEFAULT_AGENT_PROFILE",
        tool: "NL2SQL_DEFAULT_TOOL",
        team: "NL2SQL_DEFAULT_TEAM",
      },
      engine_meta: { runtime: "mock" },
    })
  );
  await page.route("**/api/nl2sql/select-ai/assets/cleanup", (route) =>
    fulfillJson(route, [
      {
        engine: "select_ai_agent",
        executed: true,
        status: "cleaned",
        cleaned_at: "2026-06-21T10:00:00.000Z",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        team_name: "NL2SQL_DEFAULT_TEAM",
        warning: "",
        asset_names: { profile: "NL2SQL_DEFAULT_PROFILE", team: "NL2SQL_DEFAULT_TEAM" },
        engine_meta: { runtime: "mock" },
      },
    ])
  );
  const filteredDbProfiles = {
    runtime: "deterministic",
    profiles: [
      {
        name: "NL2SQL_DEFAULT_PROFILE",
        status: "ready",
        owner: "APP",
        created_at: "2026-06-21T10:00:00.000Z",
        object_list: [],
        attributes: { profile_attributes: { object_list: [{ owner: "APP", name: "INVOICES" }] } },
      },
    ],
    warnings: [],
  };
  const allDbProfiles = {
    ...filteredDbProfiles,
    profiles: [
      ...filteredDbProfiles.profiles,
      {
        name: "NL2SQL_MANUAL_AGENT_V2_PROFILE",
        status: "ready",
        owner: "APP",
        created_at: "2026-06-21T10:00:00.000Z",
        object_list: [],
        attributes: { PROFILE_ATTRIBUTES: { OBJECT_LIST: JSON.stringify([{ OWNER: "APP", NAME: "PAYMENTS" }]) } },
      },
    ],
  };
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true",
    (route) => fulfillJson(route, filteredDbProfiles)
  );
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?include_detail=true&business_profiles_only=true&include_archived_business_profiles=true",
    (route) => fulfillJson(route, filteredDbProfiles)
  );
  await page.route(
    "**/api/nl2sql/select-ai/profiles/export.json?business_profiles_only=true&include_archived_business_profiles=true",
    (route) =>
      fulfillJson(route, {
        profiles: filteredDbProfiles.profiles,
        exported_at: "2026-06-21T10:00:00.000Z",
      })
  );
  await page.route("**/api/nl2sql/select-ai/profiles/export.json", (route) =>
    fulfillJson(route, {
      profiles: allDbProfiles.profiles,
      exported_at: "2026-06-21T10:00:00.000Z",
    })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles", (route) =>
    fulfillJson(route, allDbProfiles)
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles/NL2SQL_DEFAULT_PROFILE", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      profile: {
        name: "NL2SQL_DEFAULT_PROFILE",
        status: "ready",
        owner: "APP",
        created_at: "2026-06-21T10:00:00.000Z",
        object_list: [],
        attributes: { profile_attributes: { object_list: [{ owner: "APP", name: "INVOICES" }] } },
      },
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles/NL2SQL_MANUAL_AGENT_V2_PROFILE", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      profile: {
        name: "NL2SQL_MANUAL_AGENT_V2_PROFILE",
        status: "ready",
        owner: "APP",
        created_at: "2026-06-21T10:00:00.000Z",
        object_list: [],
        attributes: { PROFILE_ATTRIBUTES: { OBJECT_LIST: JSON.stringify([{ OWNER: "APP", NAME: "PAYMENTS" }]) } },
      },
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/feedback**", (route) => {
    const url = route.request().url();
    if (route.request().method() === "POST" && url.includes("/add")) {
      state.selectAiFeedbackAddPayload = route.request().postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, {
        runtime: "oracle",
        executed: true,
        status: "added",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        index_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX",
        table_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB",
        sql_text: "select ai showsql 請求金額を一覧で見たい",
        stored_feedback_type: "NEGATIVE",
        plsql_preview: "BEGIN DBMS_CLOUD_AI.FEEDBACK(operation => 'ADD'); END;",
        warnings: [],
        engine_meta: {},
      });
    }
    if (route.request().method() === "POST" && url.includes("/delete")) {
      state.selectAiFeedbackDeletePayload = route.request().postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, {
        runtime: "oracle",
        executed: true,
        status: "deleted",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        index_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX",
        table_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB",
        warnings: [],
        engine_meta: {},
      });
    }
    if (route.request().method() === "POST" && url.includes("/vector-index")) {
      state.selectAiFeedbackUpdatePayload = route.request().postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, {
        runtime: "oracle",
        executed: true,
        status: "updated",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        index_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX",
        table_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB",
        warnings: [],
        engine_meta: {},
      });
    }
    return fulfillJson(route, {
      runtime: "oracle",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      index_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX",
      table_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB",
      items: [
        {
          content: "select ai showsql 請求金額を確認したい",
          sql_id: "sql-001",
          sql_text: "SELECT TOTAL_AMOUNT FROM INVOICES",
          attributes: {
            sql_id: "sql-001",
            sql_text: "SELECT TOTAL_AMOUNT FROM INVOICES",
          },
          raw_attributes: "{\"sql_id\":\"sql-001\"}",
        },
      ],
      total: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/select-ai-agent/assets", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        {
          profile_id: "default",
          profile_name: "NL2SQL_DEFAULT_PROFILE",
          tool_name: "NL2SQL_DEFAULT_TOOL",
          agent_name: "NL2SQL_DEFAULT_AGENT",
          task_name: "NL2SQL_DEFAULT_TASK",
          team_name: "NL2SQL_DEFAULT_TEAM",
          source: "derived",
          attributes: {},
        },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles/*/drop", (route) => {
    state.dbProfileDropPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      engine: "select_ai",
      executed: true,
      status: "cleaned",
      cleaned_at: "2026-06-21T10:00:00.000Z",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      team_name: "",
      warning: "",
      asset_names: { profile: "NL2SQL_DEFAULT_PROFILE" },
      engine_meta: { runtime: "mock" },
    });
  });
  await page.route("**/api/nl2sql/select-ai-agent/run-team", (route) =>
    fulfillJson(route, {
      team_name: "NL2SQL_DEFAULT_TEAM",
      prompt: "請求金額が大きい取引先を見たい",
      generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
      conversation_id: "conversation-001",
      runtime: "deterministic",
      warnings: [],
      engine_meta: {},
    })
  );
  await page.route("**/api/nl2sql/select-ai-agent/conversations**", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai-agent/privileges/check", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      status: "warning",
      checks: [
        {
          name: "nl2sql_runtime_mode",
          status: "warning",
          message: "NL2SQL_RUNTIME_MODE=oracle ではないため Oracle 権限を確認していません。",
        },
      ],
      warnings: ["Oracle runtime ではないため Select AI Agent 権限は未確認です。"],
    })
  );
  await page.route("**/api/nl2sql/rewrite", (route) =>
    fulfillJson(route, {
      original_question: "請求金額を一覧で見たい",
      rewritten_question: "請求金額を一覧で見たい（請求金額=INVOICES.TOTAL_AMOUNT）",
      source: "deterministic",
      model: "",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "default",
      recommended_profile_name: "既定プロファイル",
      confidence: 0.94,
      reason: "請求関連の語彙に一致しました。",
      rewritten_question: "請求金額を一覧で見たい",
      recommended_allowed_objects: {
        table_names: ["INVOICES"],
        columns: { INVOICES: ["TOTAL_AMOUNT"] },
      },
      candidates: [
        {
          profile_id: "default",
          profile_name: "既定プロファイル",
          score: 0.94,
          matched_terms: ["請求金額"],
          allowed_tables: ["INVOICES"],
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/similar-history", (route) =>
    fulfillJson(route, {
      items: [
        {
          item: historyItem,
          score: 0.9,
          reason: "請求金額の履歴と近い質問です。",
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/analyze", (route) => {
    state.analyzePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      safety,
      explanation: "SELECT 文として安全に実行できます。",
      recommendations: ["許可された表だけを参照しています。"],
      executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
      repaired_sql: "",
      optimization_hints: ["TOTAL_AMOUNT の索引を確認できます。"],
    });
  });
  await page.route("**/api/nl2sql/reverse/deep", (route) => {
    state.reverseDeepPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      question: "請求金額を条件付きで一覧確認したい",
      explanation: "INVOICES から請求金額を取得します。",
      logical_structure: "SQL 論理構造\n- SELECT: 請求金額\n- FROM: INVOICES",
      referenced_tables: ["INVOICES"],
      logical_steps: ["INVOICES を参照", "請求金額を選択"],
      source: "oci_enterprise_ai",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/reverse", (route) => {
    state.reversePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      question: "請求金額を一覧で確認したい",
      explanation: "INVOICES から請求金額を取得します。",
      logical_structure: "SQL 論理構造\n- SELECT: TOTAL_AMOUNT\n- FROM: INVOICES",
      referenced_tables: ["INVOICES"],
      logical_steps: ["INVOICES を参照", "TOTAL_AMOUNT を選択"],
      source: "deterministic",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/evaluate", (route) => {
    const payload = route.request().postDataJSON() as {
      cases?: Record<string, unknown>[];
      engine?: string;
      profile_id?: string;
      evaluation_set_id?: string;
    };
    const result = {
      evaluation_suite: "deterministic_mock",
      total_cases: payload.cases?.length ?? 0,
      executable_rate: 1,
      select_only_rate: 1,
      findings: [],
    };
    evaluationRuns = [
      {
        id: "eval-run-new",
        created_at: "2026-06-21T10:06:00.000Z",
        evaluation_set_id: payload.evaluation_set_id ?? "",
        evaluation_set_name: payload.evaluation_set_id ? "請求ベンチマーク" : "",
        profile_id: payload.profile_id ?? "default",
        profile_name: "既定プロファイル",
        engine: payload.engine ?? "auto",
        cases: payload.cases ?? [],
        result,
        report: "NL2SQL deterministic evaluation\nSuite: deterministic_mock\nCases: 1",
      },
      ...evaluationRuns,
    ];
    return fulfillJson(route, result);
  });
  await page.route("**/api/nl2sql/evaluation-runs**", (route) =>
    fulfillJson(route, { items: evaluationRuns })
  );
  await page.route("**/api/nl2sql/evaluation-sets", (route) => {
    if (route.request().method() === "POST") {
      state.evaluationSetPayload = route.request().postDataJSON() as Record<string, unknown>;
      const saved = {
        id: "eval-new",
        ...(state.evaluationSetPayload as Record<string, unknown>),
        profile_name: "既定プロファイル",
        created_at: "2026-06-21T10:05:00.000Z",
        updated_at: "2026-06-21T10:05:00.000Z",
        archived: false,
      };
      evaluationSets = [saved, ...evaluationSets];
      return fulfillJson(route, saved);
    }
    return fulfillJson(route, { items: evaluationSets });
  });
  await page.route("**/api/nl2sql/evaluation-sets/eval-001", (route) => {
    if (route.request().method() === "PATCH") {
      state.evaluationSetPayload = route.request().postDataJSON() as Record<string, unknown>;
      const saved = {
        id: "eval-001",
        ...(state.evaluationSetPayload as Record<string, unknown>),
        profile_name: "既定プロファイル",
        created_at: "2026-06-21T10:00:00.000Z",
        updated_at: "2026-06-21T10:08:00.000Z",
        archived: false,
      };
      evaluationSets = [saved, ...evaluationSets.filter((item) => item.id !== "eval-001")];
      return fulfillJson(route, saved);
    }
    return fulfillJson(route, evaluationSets.find((item) => item.id === "eval-001") ?? evaluationSets[0]);
  });
  await page.route("**/api/nl2sql/evaluation-sets/eval-001/archive", (route) => {
    evaluationSets = evaluationSets.filter((item) => item.id !== "eval-001");
    return fulfillJson(route, {
      id: "eval-001",
      name: "請求ベンチマーク",
      description: "保存済み請求ケース",
      profile_id: "default",
      profile_name: "既定プロファイル",
      engine: "select_ai",
      cases: [],
      created_at: "2026-06-21T10:00:00.000Z",
      updated_at: "2026-06-21T10:09:00.000Z",
      archived: true,
    });
  });
  await page.route("**/api/nl2sql/comments/suggest", (route) =>
    fulfillJson(route, {
      suggestions: [
        {
          object_name: "INVOICES.TOTAL_AMOUNT",
          object_type: "COLUMN",
          suggested_comment: "税込請求金額",
        },
      ],
      source: "deterministic",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/metadata-samples", (route) => {
    state.metadataSamplesPayload = route.request().postDataJSON() as Record<string, unknown>;
    const sampleLimit = state.metadataSamplesPayload.sample_limit;
    return fulfillJson(route, {
      sample_text: sampleLimit === 0 ? "" : "OBJECT: INVOICES\nCUSTOMER_NAME: 青山商事, 鈴木商店",
      sample_count: sampleLimit === 0 ? 0 : 2,
      runtime: "oracle",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/comments/generate-sql", (route) => {
    state.commentGeneratePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      sql: "COMMENT ON COLUMN \"INVOICES\".\"TOTAL_AMOUNT\" IS '税込請求金額';",
      source: "deterministic",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/comments/apply", (route) => {
    state.commentApplyPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      executed: false,
      runtime: "deterministic",
      statements: [
        {
          object_name: "INVOICES.TOTAL_AMOUNT",
          object_type: "column",
          comment: "税込請求金額",
          sql: "COMMENT ON COLUMN \"INVOICES\".\"TOTAL_AMOUNT\" IS '税込請求金額';",
          status: "requires_oracle",
          error_message: "",
        },
      ],
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/annotations/generate", (route) =>
    fulfillJson(route, {
      suggestions: [
        {
          object_name: "INVOICES.TOTAL_AMOUNT",
          object_type: "column",
          annotation_name: "Display",
          annotation_value: "税込請求金額",
        },
      ],
      source: "deterministic",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/annotations/generate-sql", (route) =>
    fulfillJson(route, {
      sql: "ALTER TABLE \"INVOICES\" MODIFY (\"TOTAL_AMOUNT\" ANNOTATIONS (ADD IF NOT EXISTS UI_Display '税込請求金額'));",
      source: "deterministic",
      warnings: [],
      timing,
    })
  );
  await page.route("**/api/nl2sql/annotations/apply", (route) =>
    fulfillJson(route, {
      executed: false,
      runtime: "deterministic",
      statements: [
        {
          object_name: "INVOICES.TOTAL_AMOUNT",
          object_type: "column",
          annotation_name: "DISPLAY",
          annotation_value: "税込請求金額",
          sql: "ALTER TABLE \"INVOICES\" MODIFY \"TOTAL_AMOUNT\" ANNOTATIONS (DISPLAY '税込請求金額');",
          status: "requires_oracle",
          error_message: "",
        },
      ],
      warnings: [],
      timing,
    })
  );
  await page.route("**/api/nl2sql/synthetic-cases**", (route) =>
    fulfillJson(route, {
      cases: [
        {
          question: "請求金額を一覧で見たい",
          expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
          profile_id: "default",
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/synthetic-data/generate", (route) => {
    state.syntheticDataPayload = route.request().postDataJSON() as Record<string, unknown>;
    const executed = true;
    const objectList = Array.isArray(state.syntheticDataPayload.object_list)
      ? state.syntheticDataPayload.object_list.filter((item): item is string => typeof item === "string")
      : [];
    const selectedTables =
      objectList.length > 0
        ? objectList
        : typeof state.syntheticDataPayload.table_name === "string" && state.syntheticDataPayload.table_name
          ? [state.syntheticDataPayload.table_name]
          : [];
    const rowCount = Number(state.syntheticDataPayload.rows_per_table ?? state.syntheticDataPayload.row_count ?? 1);
    return fulfillJson(route, {
      operation_id: "operation-001",
      table_name: "AUDIT_LOG",
      object_list: selectedTables,
      row_count: rowCount,
      executed,
      runtime: "deterministic",
      status: "submitted",
      message: executed
        ? "DBMS_CLOUD_AI synthetic data generation を開始しました。"
        : "INVOICES に 1 行の synthetic data を生成する plan です。",
      warnings: executed ? [] : ["ADMIN_EXECUTE が必要です。"],
      engine_meta: {},
      timing,
    });
  });
  await page.route("**/api/nl2sql/synthetic-data/operations/**", (route) =>
    fulfillJson(route, {
      operation_id: "operation-001",
      runtime: "deterministic",
      status: "requires_oracle",
      message: "operation status の取得には NL2SQL_RUNTIME_MODE=oracle が必要です。",
      result: {},
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/synthetic-data/results**", (route) =>
    fulfillJson(route, {
      table_name: "INVOICES",
      runtime: "deterministic",
      results: {
        columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
        rows: [{ CUSTOMER_NAME: "synthetic-customer", TOTAL_AMOUNT: 12345 }],
        total: 1,
      },
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/preview", (route) => {
    state.previewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
      is_safe: true,
      row_limit: 0,
      note: "mock preview",
      engine: "select_ai_agent",
      engine_meta: { profile: "mock_agent_profile" },
      fallback_reason: "",
      rewritten_question: "請求金額を一覧で見たい",
      executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
      safety,
      recommendations: ["許可された表だけを参照しています。"],
      repaired_sql: "",
      optimization_hints: ["TOTAL_AMOUNT に索引を検討できます。"],
      timing,
    });
  });
  await page.route("**/api/nl2sql/execute", (route) => {
    state.executePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
      total: 1,
    });
  });
  await page.route("**/api/nl2sql/repair", (route) =>
    fulfillJson(route, {
      error_code: "ORA-00904",
      repaired_sql: "SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
      explanation: "存在しない列名または alias を参照している可能性があります。",
      recommendations: ["Schema catalog の列名・alias を確認してください。"],
      safety,
      executable_sql: "SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
    })
  );
  await page.route("**/api/nl2sql/compare-history**", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: "cmp-001",
          created_at: "2026-06-21T10:00:00.000Z",
          profile_id: "default",
          profile_name: "既定プロファイル",
          question: "履歴の請求比較",
          engines: ["select_ai_agent", "select_ai"],
          execute: true,
          report: "NL2SQL engine comparison\nQuestion: 履歴の請求比較",
          comparison: {
            question: "履歴の請求比較",
            recommendation: "履歴では Select AI Agent が安定していました。",
            results: [
              {
                sql: "SELECT CUSTOMER_NAME FROM INVOICES",
                is_safe: true,
                row_limit: 100,
                note: "history",
                engine: "select_ai_agent",
                engine_meta: { team: "mock_team" },
                fallback_reason: "",
                rewritten_question: "履歴の請求比較",
                executable_sql: "SELECT CUSTOMER_NAME FROM INVOICES FETCH FIRST 100 ROWS ONLY",
                safety,
                recommendations: ["履歴記録です。"],
                repaired_sql: "",
                optimization_hints: [],
                timing,
              },
            ],
            execution_results: [],
            error_rate: 0,
          },
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/compare", (route) =>
    fulfillJson(route, {
      question: "今月の請求金額が大きい取引先を表示して",
      recommendation: "Select AI Agent が最短で安全な SQL を生成しました。",
      results: [
        {
          sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
          is_safe: true,
          row_limit: 100,
          note: "agent",
          engine: "select_ai_agent",
          engine_meta: { team: "mock_team" },
          fallback_reason: "",
          rewritten_question: "今月の請求金額が大きい取引先",
          executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
          safety,
          recommendations: ["Agent profile は最新です。"],
          repaired_sql: "",
          optimization_hints: [],
          timing,
        },
        {
          sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
          is_safe: true,
          row_limit: 100,
          note: "select ai",
          engine: "select_ai",
          engine_meta: { profile: "mock_select_ai" },
          fallback_reason: "",
          rewritten_question: "今月の請求金額が大きい取引先",
          executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
          safety,
          recommendations: ["Select AI profile は利用可能です。"],
          repaired_sql: "",
          optimization_hints: [],
          timing: { ...timing, elapsed_ms: 260 },
        },
      ],
      execution_results: [
        {
          engine: "select_ai_agent",
          executed: true,
          row_count: 1,
          error_message: "",
          elapsed_ms: 18,
          results: {
            columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
            rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
            total: 1,
          },
        },
        {
          engine: "select_ai",
          executed: false,
          row_count: 0,
          error_message: "ORA-00942 mock",
          elapsed_ms: 20,
          results: null,
        },
      ],
      error_rate: 0.5,
    })
  );

  return state;
}

async function expectNoHorizontalScroll(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
}

async function expectHorizontallyContained(content: Locator, container: Locator) {
  const [contentBox, containerBox] = await Promise.all([content.boundingBox(), container.boundingBox()]);
  expect(contentBox).not.toBeNull();
  expect(containerBox).not.toBeNull();
  expect(contentBox!.x).toBeGreaterThanOrEqual(containerBox!.x - 1);
  expect(contentBox!.x + contentBox!.width).toBeLessThanOrEqual(containerBox!.x + containerBox!.width + 1);
}

async function dragSplitDivider(page: Page, divider: Locator, deltaX: number) {
  const box = await divider.boundingBox();
  expect(box).not.toBeNull();
  const centerX = box!.x + box!.width / 2;
  const centerY = box!.y + box!.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  await page.mouse.move(centerX + deltaX, centerY, { steps: 8 });
  await page.mouse.up();
}

async function useOverflowSchemaCatalog(page: Page) {
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, overflowSchemaCatalog));
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", (route) =>
    fulfillJson(route, {
      items: overflowSchemaCatalog.tables.map((table) => ({
        owner: table.owner,
        object_name: table.table_name,
        object_type: table.table_type,
        logical_name: table.logical_name,
        comment: table.comment,
        row_count: table.row_count,
        column_count: table.columns.length,
        last_ddl_at: "",
      })),
      next_cursor: null,
      total: overflowSchemaCatalog.tables.length,
      catalog_version: 1,
    })
  );
  await page.unroute("**/api/schema/objects/*/*");
  await page.route("**/api/schema/objects/*/*", (route) => {
    const objectName = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").at(-1) ?? ""
    );
    return fulfillJson(route, {
      table:
        overflowSchemaCatalog.tables.find((table) => table.table_name === objectName) ??
        overflowSchemaCatalog.tables[0],
      dependencies: [],
      catalog_version: 1,
      etag: "schema-overflow",
    });
  });
  // スキーマ参照はプロファイルの allowed_tables で絞り込むため、レイアウト検証用の
  // 長い名前の表を表示できるよう、既定プロファイルを全表表示（allowed 空）に上書きする。
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [{ ...profiles[0], allowed_tables: [], allowed_views: [] }])
  );
  await page.unroute("**/api/nl2sql/profiles/default");
  await page.route("**/api/nl2sql/profiles/default", (route) =>
    fulfillJson(route, { ...profiles[0], allowed_tables: [], allowed_views: [] })
  );
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: profiles[0].id,
          name: profiles[0].name,
          category: profiles[0].category,
          description: profiles[0].description,
          archived: profiles[0].archived,
          allowed_table_count: 0,
          allowed_view_count: 0,
          glossary_count: Object.keys(profiles[0].glossary).length,
          few_shot_count: profiles[0].few_shot_examples.length,
          version: 1,
          etag: `etag-${profiles[0].id}`,
          updated_at: "2026-06-21T10:00:00.000Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
}

async function openSchemaPicker(page: Page) {
  // スキーマ参照は検索クエリの右に常時表示（トグルなし）。可視確認のみ行う。
  await expect(page.getByTestId("nl2sql-schema-reference")).toBeVisible();
}

async function expectQuerySingleColumnLayout(page: Page) {
  // 単一カラム化: 分割ペインの testid は存在しない。
  await expect(page.getByTestId("fixed-split-pane-nl2sql-workbench")).toHaveCount(0);
  const shell = page.getByTestId("nl2sql-workspace-shell");
  await expect(shell).toBeVisible();

  // スキーマ参照は検索クエリ直下の折りたたみ補助ツール。開いて内容を検証。
  await openSchemaPicker(page);
  const schema = page.getByTestId("nl2sql-schema-reference");
  const firstTable = page.getByTestId("nl2sql-schema-table-item").first();
  await expect(schema).toBeVisible();
  await expect(firstTable).toBeVisible();

  // 長い識別子でも入力カード幅を超えず（折り返す）、横スクロールを起こさない。
  const shellBox = await shell.boundingBox();
  const tableBox = await firstTable.boundingBox();
  expect(shellBox).not.toBeNull();
  expect(tableBox).not.toBeNull();
  expect(tableBox!.x + tableBox!.width).toBeLessThanOrEqual(shellBox!.x + shellBox!.width + 1);
  await expectNoHorizontalScroll(page);
}

test("query workbench keeps the schema picker inside a single column with long identifiers", async ({ page }) => {
  await mockNl2SqlApi(page);
  await useOverflowSchemaCatalog(page);

  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();
  await expectQuerySingleColumnLayout(page);

  const viewport = page.viewportSize();
  if ((viewport?.width ?? 0) >= 1280) {
    await page.setViewportSize({ width: 2048, height: 900 });
    await page.reload();
    await expect(page.getByText("スキーマ参照")).toBeVisible();
    await expectQuerySingleColumnLayout(page);
  }
});

test("実行エンジンは自動を廃し Select AI を既定にする", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  // 「自動」オプションは削除
  await expect(page.getByRole("button", { name: /Agent → Select AI → Direct/ })).toHaveCount(0);
  // 既定は Select AI（先頭・押下状態）
  const selectAi = page.getByRole("button", { name: /DBMS_CLOUD_AI profile を利用/ });
  await expect(selectAi).toHaveAttribute("aria-pressed", "true");
  // 3 択（select_ai / agent / direct）
  const engineGroup = page.getByRole("group", { name: "実行エンジン" });
  await expect(engineGroup.getByRole("button")).toHaveCount(3);
});

test("スキーマ参照から連続挿入すると各項目が改行区切りになる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = page.getByLabel("検索クエリ");
  await openSchemaPicker(page);
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  const column = page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ });
  await column.click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"");
  // 2 回目は先頭に改行が入り、項目が行分割される
  await column.click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"\n\"請求\".\"請求金額\"");
});

test("検索クエリのテンプレートボタンで穴埋めテンプレートを全置換挿入できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = page.getByLabel("検索クエリ");

  // SQL 一括実行と統一の「テンプレート:」行が textarea の上に見える
  await expect(page.getByText("テンプレート:", { exact: true })).toBeVisible();
  for (const label of ["項目抽出", "集計・グループ化", "上位N件・並び替え", "複数テーブル結合"]) {
    await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
  }

  // クリックで穴埋め本文が入り(既存入力は全置換)、カーソルは 1 行目「対象テーブル：」の直後
  await question.fill("既存の入力");
  await page.getByRole("button", { name: "項目抽出", exact: true }).click();
  await expect(question).toHaveValue("対象テーブル：\n抽出項目：\n抽出条件：");
  await expect(question).toBeFocused();
  const caret = await question.evaluate((el) => (el as HTMLTextAreaElement).selectionStart);
  expect(caret).toBe("対象テーブル：".length);

  // 別テンプレートは全置換（SQL 一括実行と同じ挙動）
  await page.getByRole("button", { name: "集計・グループ化", exact: true }).click();
  await expect(question).toHaveValue(
    "対象テーブル：\n集計内容（件数・合計・平均など）：\n集計単位（グループ化）：\n抽出条件：",
  );

  // スキーマ参照のカーソル挿入と組み合わせて空欄を埋められる
  await page.getByRole("button", { name: "項目抽出", exact: true }).click();
  await openSchemaPicker(page);
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue("対象テーブル：\"請求\".\"請求金額\"\n抽出項目：\n抽出条件：");

  // 375px でも折返しで収まり、横スクロールが発生しない
  await page.setViewportSize({ width: 375, height: 800 });
  await expect(page.getByRole("button", { name: "複数テーブル結合", exact: true })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("スキーマピッカーは compact（checkbox なし・挿入でページがスクロールしない）", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await openSchemaPicker(page);

  // checkbox は存在しない（クリック=挿入の 1 アクション）
  const picker = page.getByTestId("nl2sql-schema-reference");
  await expect(picker.locator('input[type="checkbox"]')).toHaveCount(0);

  // 検索入力で一致テーブルが自動展開され、列がそのまま見える
  await picker.getByLabel("表・項目検索").fill("請求金額");
  const column = page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ });
  await expect(column).toBeVisible();

  // 挿入してもページスクロールが飛ばない（focus の preventScroll）
  await column.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  await column.click();
  await expect(page.getByLabel("検索クエリ")).toHaveValue("\"請求\".\"請求金額\"");
  const after = await page.evaluate(() => window.scrollY);
  expect(Math.abs(after - before)).toBeLessThanOrEqual(2);
});

test("スキーマ参照はアコーディオンで、表名クリックで表名を挿入できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await useOverflowSchemaCatalog(page); // 2 表（伝票活動ログ… + 請求）
  await page.goto("/query");
  await openSchemaPicker(page);

  // アコーディオン: 1 表目を開いた後に 2 表目を開くと 1 表目が自動で閉じる
  const firstToggle = page.getByRole("button", { name: /伝票活動ログ.*を開閉/ });
  const secondToggle = page.getByRole("button", { name: "請求 を開閉" });
  await firstToggle.click();
  await expect(firstToggle).toHaveAttribute("aria-expanded", "true");
  await secondToggle.click();
  await expect(secondToggle).toHaveAttribute("aria-expanded", "true");
  await expect(firstToggle).toHaveAttribute("aria-expanded", "false");

  // 展開した表の全列に到達できる（列数が catalog と一致し、最後の列も可視化できる）
  const columns = page
    .getByTestId("nl2sql-schema-table-item")
    .filter({ has: secondToggle })
    .getByRole("listitem");
  await expect(columns).toHaveCount(2); // 取引先名 + 請求金額
  const lastColumn = page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ });
  await lastColumn.scrollIntoViewIfNeeded();
  await expect(lastColumn).toBeVisible();

  // 表名クリック=表名（論理名）を挿入。chevron クリックでは挿入されない。
  const question = page.getByLabel("検索クエリ");
  await expect(question).toHaveValue("");
  await page.getByRole("button", { name: /^請求 INVOICES/ }).click();
  await expect(question).toHaveValue("\"請求\"");
  await secondToggle.click();
  await expect(question).toHaveValue("\"請求\""); // chevron では変化しない
});

test("検索クエリとスキーマ参照は desktop で左右並置、mobile で縦積みになる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = page.getByLabel("検索クエリ");
  const picker = page.getByTestId("nl2sql-schema-reference");
  await expect(question).toBeVisible();
  await expect(picker).toBeVisible();

  const questionBox = await question.boundingBox();
  const pickerBox = await picker.boundingBox();
  expect(questionBox).not.toBeNull();
  expect(pickerBox).not.toBeNull();

  const viewport = page.viewportSize();
  if ((viewport?.width ?? 0) >= 1024) {
    // 左右並置: ピッカーが textarea の右にあり、縦方向が重なる
    expect(pickerBox!.x).toBeGreaterThan(questionBox!.x + questionBox!.width - 1);
    expect(pickerBox!.y).toBeLessThan(questionBox!.y + questionBox!.height);
  } else {
    // 縦積み: ピッカーが textarea の下
    expect(pickerBox!.y).toBeGreaterThan(questionBox!.y);
  }
  await expectNoHorizontalScroll(page);
});

test("検索クエリは内容に応じて最大10行まで自動拡張し、挿入行へ内部スクロールする", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = page.getByLabel("検索クエリ");

  // 少量入力では既定高（5 行相当 = min-h-36 = 144px）付近
  await question.fill("1行だけ");
  const smallHeight = await question.evaluate((el) => el.clientHeight);
  expect(smallHeight).toBeLessThanOrEqual(160);

  // 12 行入力 → 10 行相当上限（max-h 264px）で止まり、内部スクロールが発生
  await question.fill(Array.from({ length: 12 }, (_, i) => `行 ${i + 1}`).join("\n"));
  const grown = await question.evaluate((el) => ({
    clientHeight: el.clientHeight,
    scrollHeight: el.scrollHeight,
  }));
  expect(grown.clientHeight).toBeLessThanOrEqual(270);
  // field-sizing 対応ブラウザでは自動拡張し、内容が上限を超えるので内部スクロールになる
  expect(grown.scrollHeight).toBeGreaterThan(grown.clientHeight);

  // スキーマ挿入すると、textarea 内部がいま挿入した行（末尾）まで追従する
  await openSchemaPicker(page);
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue(/請求金額"$/);
  const scrollTop = await question.evaluate((el) => el.scrollTop);
  expect(scrollTop).toBeGreaterThan(0);
});

test("質問から業務プロファイルを自動判定して選択できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  const paymentProfile = {
    ...profiles[0],
    id: "payment",
    name: "入金管理",
    category: "入金管理",
    allowed_tables: ["INVOICES"],
    allowed_views: [],
  };
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [profiles[0], paymentProfile])
  );
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [profiles[0], paymentProfile].map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: false,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: Object.keys(profile.glossary).length,
        few_shot_count: profile.few_shot_examples.length,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-06-21T10:00:00.000Z",
      })),
      next_cursor: null,
      total: 2,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/payment", (route) => fulfillJson(route, paymentProfile));
  await page.unroute("**/api/nl2sql/recommend-profile");
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "payment",
      recommended_profile_name: "入金管理",
      confidence: 0.82,
      reason: "入金関連の語彙に一致しました。",
      rewritten_question: "",
      recommended_allowed_objects: { table_names: ["INVOICES"], columns: {} },
      candidates: [],
      recommendation_source: "classifier",
    })
  );

  await page.goto("/query");
  const profileSelect = page.locator("#nl2sql-profile-select");
  await expect(profileSelect).toHaveValue("default");

  const detect = page.getByRole("button", { name: "プロファイルを自動判定" });
  await expect(detect).toBeDisabled(); // 質問未入力では押せない
  await page.getByLabel("検索クエリ").fill("未入金の請求を確認したい");
  await expect(detect).toBeEnabled();
  await detect.click();

  await expect(profileSelect).toHaveValue("payment");
  await expect(page.getByText(/入金管理 を選択しました/)).toBeVisible();
});

test("catalog 空のときスキーマ参照からスキーマを更新して表を取得できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  // 初回 GET は空、更新（POST /refresh）で実表を返す
  let refreshed = false;
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(
      route,
      refreshed ? schemaCatalog : { refreshed_at: "2026-06-21T10:00:00.000Z", tables: [] }
    )
  );
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", (route) => {
    const tables = refreshed ? schemaCatalog.tables : [];
    return fulfillJson(route, {
      items: tables.map((table) => ({
        owner: table.owner,
        object_name: table.table_name,
        object_type: table.table_type,
        logical_name: table.logical_name,
        comment: table.comment,
        row_count: table.row_count,
        column_count: table.columns.length,
        last_ddl_at: "",
      })),
      next_cursor: null,
      total: tables.length,
      catalog_version: refreshed ? 2 : 1,
    });
  });
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [{ ...profiles[0], allowed_tables: [], allowed_views: [] }])
  );
  await page.unroute("**/api/schema/refresh");
  await page.route("**/api/schema/refresh", (route) => {
    refreshed = true;
    return fulfillJson(route, schemaCatalog);
  });
  await page.route("**/api/schema/refresh-jobs", (route) => {
    refreshed = true;
    return fulfillJson(route, {
      job_id: "schema-refresh-test",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      scanned_objects: schemaCatalog.tables.length,
      changed_objects: schemaCatalog.tables.length,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/schema-refresh-test", (route) =>
    fulfillJson(route, {
      job_id: "schema-refresh-test",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      scanned_objects: schemaCatalog.tables.length,
      changed_objects: schemaCatalog.tables.length,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    })
  );

  await page.goto("/query");
  await openSchemaPicker(page);
  await expect(page.getByText(/スキーマ未取得/)).toBeVisible();

  await page.getByRole("button", { name: "スキーマを更新" }).click();
  await expect(page.getByRole("button", { name: "請求 を開閉" })).toBeVisible();
  expect(refreshed).toBe(true);
});

test("owner 付きの許可表でもスキーマ参照が対象表に絞り込める", async ({ page }) => {
  await mockNl2SqlApi(page);
  // allowed_tables に owner 修飾（APP.INVOICES）が入っていても INVOICES にスコープされる
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [{ ...profiles[0], allowed_tables: ["APP.INVOICES"], allowed_views: [] }])
  );

  await page.goto("/query");
  await openSchemaPicker(page);
  await expect(page.getByRole("button", { name: "請求 を開閉" })).toBeVisible();
});

test("query workbench previews SQL and executes the preview result", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();
  await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);

  const removedSamples = [
    "登録済みの表から主要な列を一覧して",
    "社員と部署の一覧を確認したい",
    "部署別のプロジェクト数を集計して",
  ];
  for (const sample of removedSamples) {
    await expect(page.getByRole("button", { name: sample, exact: true })).toHaveCount(0);
  }

  const question = page.getByLabel("検索クエリ");
  const placeholder = (await question.getAttribute("placeholder")) ?? "";
  for (const sample of removedSamples) expect(placeholder).not.toContain(sample);
  await openSchemaPicker(page);
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"");

  await question.fill("請求金額を一覧で見たい");
  // 推薦は現在の profile（default）と同一のため、progressive-disclosure によりヒントは出さない。
  await expect(page.getByTestId("nl2sql-recommend-hint")).toHaveCount(0);

  await page.getByRole("button", { name: "SQL プレビュー" }).click();

  const generatedSqlStep = page.getByTestId("nl2sql-job-step-generate_sql");
  await expect(generatedSqlStep).toContainText("SQL を生成");
  await expect(generatedSqlStep.getByRole("code")).toContainText(
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
  );
  await page.getByRole("button", { name: "この SQL を実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1200000" })).toBeVisible();

  const feedbackResponse = page.getByLabel("response");
  await expect(page.getByRole("heading", { name: "DBMS_CLOUD_AI feedback" })).toBeVisible();
  await expect(feedbackResponse).toHaveValue("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  // 結果フィードバックカードは廃止され、良い/違うボタンへ統合された。
  await expect(page.getByRole("heading", { name: "結果フィードバック" })).toHaveCount(0);
  // response は常に編集可（種類 select 廃止）。
  await expect(feedbackResponse).toHaveJSProperty("readOnly", false);

  // 「良い」= positive。preview 実行のみでは一致する履歴が無いため DBMS_CLOUD_AI のみ保存する。
  await page.getByLabel("feedback_content").fill("期待どおりの SQL です");
  await page.getByRole("button", { name: "良い" }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.selectAiFeedbackAddPayload).toMatchObject({
    profile_id: "default",
    question: "請求金額を一覧で見たい",
    feedback_type: "positive",
    response: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    feedback_content: "期待どおりの SQL です",
    generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
  });
  // 一致履歴なし → 結果フィードバック保存はスキップ。
  expect(api.feedbackPayload).toBeNull();

  // 「違う」= negative。feedback_content 未入力なら送信をブロックする。
  await page.getByLabel("feedback_content").fill("");
  await feedbackResponse.fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "違う" }).click();
  await expect(page.getByText("「違う」の場合は feedback_content の入力が必須です。")).toBeVisible();

  await page.getByLabel("feedback_content").fill("列を請求金額だけに修正");
  await page.getByRole("button", { name: "違う" }).click();
  expect(api.selectAiFeedbackAddPayload).toMatchObject({
    feedback_type: "negative",
    response: "SELECT TOTAL_AMOUNT FROM INVOICES",
    feedback_content: "列を請求金額だけに修正",
  });
  // checkbox 廃止後、スコープは profile / 推薦適用が決める（手動選択なしは空）。
  expect(api.previewPayload?.allowed_objects).toEqual({
    table_names: [],
    columns: {},
  });
  expect(api.executePayload?.sql).toBe("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expectNoHorizontalScroll(page);
});

test("参考履歴は既定で折りたたまれ、ヘッダークリックで過去 SQL を展開できる", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();

  // 4 文字以上の質問を入力すると参考履歴を取得する（debounce 650ms）。
  await page.getByLabel("検索クエリ").fill("請求金額を一覧で見たい");

  const header = page.getByRole("button", { name: "参考履歴" });
  await expect(header).toBeVisible();
  // 既定は折りたたみ: 中身（類似度・過去 SQL）は表示されない。
  await expect(header).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("類似度 90%")).toHaveCount(0);

  await header.click();
  await expect(header).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("類似度 90%")).toBeVisible();
  await expect(page.getByText("請求金額の履歴と近い質問です。")).toBeVisible();
});

test("検索結果は 10 件ごとにページングする", async ({ page }) => {
  await mockNl2SqlApi(page);
  // 実行結果を 12 件へ上書き（後勝ちルートで mockNl2SqlApi の execute を差し替える）
  const rows = Array.from({ length: 12 }, (_, index) => ({
    CUSTOMER_NAME: `顧客${String(index + 1).padStart(2, "0")}`,
    TOTAL_AMOUNT: (index + 1) * 1000,
  }));
  await page.route("**/api/nl2sql/execute", (route) =>
    fulfillJson(route, { columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"], rows, total: rows.length })
  );

  await page.goto("/query");
  await page.getByLabel("検索クエリ").fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "SQL プレビュー" }).click();
  await page.getByRole("button", { name: "この SQL を実行" }).click();

  await expect(page.getByText("検索結果（12件）")).toBeVisible();
  // 1 ページ目 = 先頭 10 件。11 件目以降は次ページ。
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "顧客10" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "顧客11" })).toHaveCount(0);

  const pagination = page.getByTestId("nl2sql-result-pagination");
  await expect(pagination).toContainText("1-10 / 12 件");
  await expect(pagination).toContainText("1 / 2 ページ");

  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11-12 / 12 件");
  await expect(page.getByRole("cell", { name: "顧客11" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "顧客12" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "顧客01" })).toHaveCount(0);

  await pagination.getByRole("button", { name: "前へ" }).click();
  await expect(pagination).toContainText("1-10 / 12 件");
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
});

test("検索を実行すると実処理の段階別進捗と結果を表示する", async ({ page, context }) => {
  const api = await mockNl2SqlApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: "http://127.0.0.1:3101",
  });

  const questionText = "今月の請求金額を確認したい";
  const createdAt = "2026-06-21T10:00:00.000Z";
  // 実行結果の generated_sql に一致する履歴を用意し、良い/違う の同時保存を検証できるようにする。
  const runHistoryItem = {
    ...historyItem,
    id: "hist-run-001",
    question: questionText,
    generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    feedback_rating: null,
    feedback_comment: "",
  };
  await page.route("**/api/nl2sql/history", (route) =>
    fulfillJson(route, { items: [runHistoryItem] })
  );
  const stageTimings = [
    { stage: "prepare_context", elapsed_ms: 10 },
    { stage: "generate_sql", elapsed_ms: 100 },
    { stage: "safety_check", elapsed_ms: 20 },
    { stage: "execute_sql", elapsed_ms: 30 },
    { stage: "format_results", elapsed_ms: 10 },
  ];
  const finishedTiming = {
    created_at: createdAt,
    started_at: "2026-06-21T10:00:00.010Z",
    finished_at: "2026-06-21T10:00:00.180Z",
    elapsed_ms: 170,
    stage_timings: stageTimings,
  };
  let jobPayload: Record<string, unknown> | null = null;
  let finishJob!: () => void;
  const terminalGate = new Promise<void>((resolve) => {
    finishJob = resolve;
  });

  await page.route("**/api/nl2sql/jobs", (route) => {
    jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      job_id: "job-step-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-step-001", async (route) => {
    await terminalGate;
    return fulfillJson(route, {
      job_id: "job-step-001",
      status: "done",
      created_at: createdAt,
      started_at: finishedTiming.started_at,
      finished_at: finishedTiming.finished_at,
      elapsed_ms: finishedTiming.elapsed_ms,
      error_message: null,
      steps: stageTimings.map((item) => ({ ...item, status: "done" })),
      timing: finishedTiming,
      result: {
        engine: "select_ai_agent",
        engine_meta: { team_name: "mock_team" },
        fallback_reason: "",
        original_question: questionText,
        rewritten_question: questionText,
        generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        explanation: "請求情報を取得します。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
          rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
          total: 1,
        },
        timing: finishedTiming,
      },
    });
  });

  await page.goto("/query");
  await page.getByLabel("検索クエリ").fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  const prepare = page.getByTestId("nl2sql-job-step-prepare_context");
  const generate = page.getByTestId("nl2sql-job-step-generate_sql");
  const safetyStep = page.getByTestId("nl2sql-job-step-safety_check");

  try {
    await expect(progress).toBeVisible();
    await expect(progress).toHaveAttribute("data-job-status", "running");
    await expect(prepare).toHaveAttribute("data-step-status", "done");
    await expect(prepare).toContainText("質問と実行条件を準備");
    await expect(generate).toHaveAttribute("data-step-status", "running");
    await expect(generate).toHaveAttribute("aria-current", "step");
    await expect(generate).toContainText("SQL を生成");
    const runningIcon = generate.locator("svg.animate-spin");
    await expect(runningIcon).toBeVisible();
    expect(await runningIcon.evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
    await expect(safetyStep).toHaveAttribute("data-step-status", "pending");
    expect(jobPayload).toMatchObject({
      question: questionText,
      engine: "select_ai",
      profile_id: "default",
      allowed_objects: { table_names: [], columns: {} },
    });
  } finally {
    finishJob();
  }

  await expect(progress).toHaveAttribute("data-job-status", "done");
  for (const item of stageTimings) {
    const step = page.getByTestId(`nl2sql-job-step-${item.stage}`);
    await expect(step).toHaveAttribute("data-step-status", "done");
    await expect(step).toContainText(item.elapsed_ms < 1000 ? `${item.elapsed_ms}ms` : "");
  }
  await expect(progress).toContainText("処理時間 170ms");
  await prepare.locator("summary").click();
  await expect(prepare).toContainText("今月の請求金額を確認したい");
  await expect(prepare).toContainText("INVOICES");
  await expect(prepare).toContainText("TOTAL_AMOUNT");
  // 生成 SQL は「SQL を生成」ステップ内(自動展開)に一本化して表示する。
  await expect(generate).toContainText("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expect(generate).toContainText("請求情報を取得します。");
  await expect(generate.getByText("安全", { exact: true })).toBeVisible();
  const copySql = generate.getByRole("button", { name: "コピー" });
  await expect(copySql).toBeVisible();
  await copySql.click();
  await expect(page.getByRole("status").filter({ hasText: "クリップボードにコピーしました。" })).toBeVisible();
  await page.getByRole("status").filter({ hasText: "クリップボードにコピーしました。" }).getByRole("button", { name: "閉じる" }).click();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: () => Promise.reject(new Error("clipboard denied")),
    });
  });
  await copySql.click();
  await expect(page.getByRole("alert").filter({ hasText: "コピーできませんでした。" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();

  // 実行結果が履歴と一致するため、良い 押下で DBMS_CLOUD_AI と結果フィードバックへ同時保存する。
  await expect(page.getByRole("heading", { name: "DBMS_CLOUD_AI feedback" })).toBeVisible();
  await page.getByLabel("feedback_content").fill("想定どおりの SQL です");
  await page.getByRole("button", { name: "良い" }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.selectAiFeedbackAddPayload).toMatchObject({
    feedback_type: "positive",
    response: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
  });
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-run-001",
    rating: "good",
    comment: "想定どおりの SQL です",
  });

  await expectNoHorizontalScroll(page);
});

test("検索ジョブの失敗段階を示し、入力を保持して再実行できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  const questionText = "未入金の請求を確認したい";
  const createdAt = "2026-06-21T10:00:00.000Z";

  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-error-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 12 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  await page.route("**/api/nl2sql/jobs/job-error-001", (route) =>
    fulfillJson(route, {
      job_id: "job-error-001",
      status: "error",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 80,
      result: null,
      error_message: "SQL 生成サービスに接続できませんでした。",
      timing: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 12 },
        { stage: "generate_sql", status: "error", elapsed_ms: 68 },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );

  await page.goto("/query");
  const question = page.getByLabel("検索クエリ");
  await question.fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  await expect(progress).toHaveAttribute("data-job-status", "error");
  await expect(page.getByTestId("nl2sql-job-step-generate_sql")).toHaveAttribute(
    "data-step-status",
    "error"
  );
  await expect(progress).toContainText("SQL 生成サービスに接続できませんでした。");
  await expect(question).toHaveValue(questionText);
  await expect(page.getByRole("button", { name: "検索を実行" })).toBeEnabled();
  await expectNoHorizontalScroll(page);
});

test("Query Rewrite の補助フラグは既定で未チェック、業務プロファイルでスキーマ参照を絞り込む", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();

  // 用語・同義語 / Schema は既定で未チェック
  await expect(page.getByLabel("用語・同義語を使う")).not.toBeChecked();
  await expect(page.getByLabel("Schema を使う")).not.toBeChecked();

  // 独立した「質問を書き換え」ボタンは廃止済み（検索実行時に統合）
  await expect(page.getByRole("button", { name: "質問を書き換え" })).toHaveCount(0);

  // default プロファイル（allowed_tables=["INVOICES"]）で対象表に絞り込まれる
  await openSchemaPicker(page);
  await expect(page.getByRole("button", { name: "請求 を開閉" })).toBeVisible();
});

test("補助フラグ ON のとき、検索を実行すると書き換え後の質問でジョブを投入する", async ({ page }) => {
  await mockNl2SqlApi(page);
  const questionText = "請求金額を一覧で見たい";
  const rewrittenText = "請求金額を一覧で見たい（請求金額=INVOICES.TOTAL_AMOUNT）";
  const createdAt = "2026-06-21T10:00:00.000Z";

  let jobPayload: Record<string, unknown> | null = null;
  await page.route("**/api/nl2sql/jobs", (route) => {
    jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      job_id: "job-rewrite-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-rewrite-001", (route) =>
    fulfillJson(route, {
      job_id: "job-rewrite-001",
      status: "done",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 20,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "done", elapsed_ms: 5 },
        { stage: "safety_check", status: "done", elapsed_ms: 2 },
        { stage: "execute_sql", status: "done", elapsed_ms: 2 },
        { stage: "format_results", status: "done", elapsed_ms: 1 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        original_question: questionText,
        rewritten_question: rewrittenText,
        generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
        explanation: "請求金額を取得します。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: { columns: ["TOTAL_AMOUNT"], rows: [{ TOTAL_AMOUNT: 1200000 }], total: 1 },
        timing: null,
      },
    })
  );

  await page.goto("/query");
  await page.getByLabel("検索クエリ").fill(questionText);
  await page.getByLabel("用語・同義語を使う").check();
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect(page.getByTestId("nl2sql-job-progress")).toHaveAttribute("data-job-status", "done");
  // ジョブへ渡す question が書き換え後の文になっている
  expect(jobPayload).toMatchObject({ question: rewrittenText, engine: "select_ai" });
  // 入力欄は書き換えずユーザー入力のまま保持する
  await expect(page.getByLabel("検索クエリ")).toHaveValue(questionText);
});

test("schema catalog が空のとき、ジョブ失敗からサンプルデータ投入で復旧できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  let catalogPopulated = false;
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(
      route,
      catalogPopulated ? schemaCatalog : { refreshed_at: "2026-06-21T10:00:00.000Z", tables: [] }
    )
  );
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", (route) => {
    const tables = catalogPopulated ? schemaCatalog.tables : [];
    return fulfillJson(route, {
      items: tables.map((table) => ({
        owner: table.owner,
        object_name: table.table_name,
        object_type: table.table_type,
        logical_name: table.logical_name,
        comment: table.comment,
        row_count: table.row_count,
        column_count: table.columns.length,
        last_ddl_at: "",
      })),
      next_cursor: null,
      total: tables.length,
      catalog_version: catalogPopulated ? 2 : 1,
    });
  });
  // 絞り込みの影響を無くすため全表表示（allowed 空）のプロファイルにする
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [{ ...profiles[0], allowed_tables: [], allowed_views: [] }])
  );
  await page.unroute("**/api/nl2sql/sample-data/import");
  await page.route("**/api/nl2sql/sample-data/import", (route) => {
    catalogPopulated = true;
    return fulfillJson(route, {
      operation: "import",
      step: "all",
      runtime: "deterministic",
      executed: true,
      objects: [],
      statements: [],
      warnings: [],
      profile_id: "default",
      timing,
    });
  });

  const createdAt = "2026-06-21T10:00:00.000Z";
  const errorSteps = [
    { stage: "prepare_context", status: "done", elapsed_ms: 10 },
    { stage: "generate_sql", status: "error", elapsed_ms: 5 },
    { stage: "safety_check", status: "pending", elapsed_ms: null },
    { stage: "execute_sql", status: "pending", elapsed_ms: null },
    { stage: "format_results", status: "pending", elapsed_ms: null },
  ];
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-empty-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  await page.route("**/api/nl2sql/jobs/job-empty-001", (route) =>
    fulfillJson(route, {
      job_id: "job-empty-001",
      status: "error",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 30,
      result: null,
      error_message:
        "NL2SQL ジョブに失敗しました: Schema catalog が空です。Oracle schema を refresh するか、Data Tools から sample data を明示的に import してください。",
      timing: null,
      steps: errorSteps,
    })
  );

  await page.goto("/query");
  await openSchemaPicker(page);
  // catalog 空のときは「スキーマ未取得」+「スキーマを更新」導線を表示する。
  await expect(page.getByText(/スキーマ未取得/)).toBeVisible();

  await page.getByLabel("検索クエリ").fill("すべてプロジェクトを教えてください。");
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  await expect(progress).toHaveAttribute("data-job-status", "error");
  const importButton = progress.getByRole("button", { name: "サンプルデータを投入" });
  await expect(importButton).toBeVisible();
  await importButton.click();

  // 投入後、catalog が populate され表が表示される（＝復旧）
  await expect(page.getByRole("button", { name: "請求 を開閉" })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("Select AI の今回指示を preview に渡し、reset で消去できる", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/query");

  await page.getByRole("button", { name: /Select AI DBMS_CLOUD_AI profile/ }).click();
  const disclosure = page.getByRole("button", { name: "今回の Select AI 指示" });
  await expect(disclosure).toHaveAttribute("aria-expanded", "false");
  await disclosure.click();
  await expect(disclosure).toHaveAttribute("aria-expanded", "true");

  await page.getByLabel("今回のアシスタントロール").fill("CFO 向け財務 SQL アシスタント");
  await page.getByLabel("今回の追加指示").fill("現在日付を基準に四半期を計算する。");
  await page.getByLabel("検索クエリ").fill("前四半期の売上を確認したい");
  await page.getByRole("button", { name: "SQL プレビュー" }).click();

  expect(api.previewPayload).toMatchObject({
    engine: "select_ai",
    select_ai_overrides: {
      role: "CFO 向け財務 SQL アシスタント",
      additional_instructions: "現在日付を基準に四半期を計算する。",
    },
  });

  await page.getByRole("button", { name: "リセット" }).click();
  await expect(disclosure).toHaveAttribute("aria-expanded", "false");
  await disclosure.click();
  await expect(page.getByLabel("今回のアシスタントロール")).toHaveValue("");
  await expect(page.getByLabel("今回の追加指示")).toHaveValue("");
  await expectNoHorizontalScroll(page);
});

test("AI 活用の SELECT SQL 画面は通常 API だけを使用し、更新 SQL を拒否する", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/execute", (route) => {
    api.executePayload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(api.executePayload.sql ?? "");
    if (!/^\s*(?:select|with)\b/i.test(sql)) {
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          data: null,
          error_messages: ["SELECT/WITH のみ実行できます。SQL を修正して再試行してください。"],
          warning_messages: [],
        }),
      });
    }
    return fulfillJson(route, {
      columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
      total: 1,
    });
  });

  await page.goto("/query");
  await page.getByRole("link", { name: "SELECT SQL を実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await expect(page.getByRole("heading", { level: 1, name: "SELECT SQL を実行" })).toBeVisible();

  const directSql = page.getByTestId("nl2sql-direct-sql");
  await expect(directSql).toBeVisible();

  const sqlInput = page.getByLabel("SQL", { exact: true });
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL 実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  expect(api.executePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    profile_id: null,
    allowed_objects: { table_names: [], columns: {} },
  });
  expect(api.adminExecutePayload).toBeNull();

  await page.getByRole("button", { name: "クリア" }).click();
  await expect(sqlInput).toHaveValue("");
  await expect(page.getByText("検索結果（1件）")).toHaveCount(0);

  await sqlInput.fill("UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1");
  await page.getByRole("button", { name: "SQL 実行" }).click();
  await expect(page.getByRole("alert")).toContainText("SELECT/WITH のみ実行できます。");
  expect(api.adminExecutePayload).toBeNull();

  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("データ準備の管理 SQL 画面は SELECT と確認済み更新 SQL を実行する", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await page.getByRole("link", { name: "管理 SQL を実行" }).click();
  await expect(page).toHaveURL(/\/admin-sql$/);
  await expect(page.getByRole("heading", { level: 1, name: "管理 SQL を実行" })).toBeVisible();

  const adminSql = page.getByTestId("nl2sql-admin-sql");
  const sqlInput = adminSql.getByLabel("管理 SQL", { exact: true });
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expect(
    adminSql.getByText("単一 SELECT/WITH は、ログインユーザーの DeepSec context を設定した data plane で実行します。")
  ).toHaveCount(0);
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await expect(adminSql.getByTestId("query-results-table")).toBeVisible();
  await expect(adminSql.getByRole("cell", { name: "青山商事" })).toBeVisible();
  expect(api.adminExecutePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    row_limit: 100,
    confirmation: "",
    reason: "admin-sql-select",
  });

  await adminSql.getByRole("button", { name: "クリア" }).click();
  await expect(sqlInput).toHaveValue("");
  await expect(adminSql.getByTestId("query-results-table")).toHaveCount(0);

  await adminSql.getByLabel("SQL ファイル読込 (.sql/.txt)").setInputFiles({
    name: "review-invoices.sql",
    mimeType: "text/plain",
    buffer: Buffer.from("UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1"),
  });
  await expect(sqlInput).toHaveValue(
    "UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1"
  );
  const removedAdminHint = adminSql.getByText(
    /非 SELECT \/ 複数 statement は管理 SQL として扱います/
  );
  await expect(removedAdminHint).toHaveCount(0);
  await expect(
    adminSql.getByText("非 SELECT / 複数 statement は ADMIN_EXECUTE を入力すると実行できます。")
  ).toBeVisible();
  await expect(adminSql.getByLabel("実行確認語")).toBeVisible();
  const executeButton = adminSql.getByRole("button", { name: "SQL 実行" });
  await expect(executeButton).toBeDisabled();
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await executeButton.focus();
  await expect(executeButton).toBeFocused();
  await executeButton.press("Enter");

  await expect(adminSql.getByText("コミット済み")).toBeVisible();
  expect(api.adminExecutePayload).toEqual({
    sql: "UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1",
    row_limit: 100,
    confirmation: "ADMIN_EXECUTE",
    reason: "admin-sql-admin",
  });

  await adminSql.getByRole("button", { name: "クリア" }).click();
  const withUpdateSql =
    "WITH TARGET AS (SELECT INVOICE_ID FROM INVOICES WHERE STATUS = 'NEW') " +
    "UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID IN (SELECT INVOICE_ID FROM TARGET)";
  await sqlInput.fill(withUpdateSql);
  await expect(adminSql.getByLabel("実行確認語")).toBeVisible();
  await expect(executeButton).toBeDisabled();
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await executeButton.click();
  await expect(adminSql.getByText("コミット済み")).toBeVisible();
  expect(api.adminExecutePayload).toEqual({
    sql: withUpdateSql,
    row_limit: 100,
    confirmation: "ADMIN_EXECUTE",
    reason: "admin-sql-admin",
  });

  for (const managedSql of [
    "CREATE TABLE REVIEW_QUEUE (ID NUMBER)",
    "UPDATE INVOICES SET STATUS = 'REVIEWED'; DELETE FROM REVIEW_QUEUE WHERE ID = 1",
  ]) {
    await adminSql.getByRole("button", { name: "クリア" }).click();
    await sqlInput.fill(managedSql);
    await expect(removedAdminHint).toHaveCount(0);
    await expect(adminSql.getByLabel("実行確認語")).toBeVisible();
    await expect(executeButton).toBeDisabled();
  }

  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("SQL ファイル入力は 44px のまま選択とドラッグ＆ドロップで読み込める", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await page.getByRole("link", { name: "管理 SQL を実行" }).click();

  const adminSql = page.getByTestId("nl2sql-admin-sql");
  const sqlInput = adminSql.getByLabel("管理 SQL", { exact: true });
  const fileInput = adminSql.getByLabel("SQL ファイル読込 (.sql/.txt)");
  const dropzone = adminSql.getByTestId("sql-file-input-dropzone");

  await expect(dropzone).toHaveClass(/\bborder-dashed\b/);
  await expect(dropzone).toHaveAttribute("data-drag-active", "false");
  const desktopBox = await dropzone.boundingBox();
  expect(desktopBox).not.toBeNull();
  expect(desktopBox!.height).toBe(44);

  await fileInput.focus();
  await expect(fileInput).toBeFocused();
  expect(await dropzone.evaluate((element) => element.matches(":focus-within"))).toBe(true);

  await fileInput.setInputFiles({
    name: "selected-query.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("SELECT CUSTOMER_NAME FROM INVOICES"),
  });
  await expect(sqlInput).toHaveValue("SELECT CUSTOMER_NAME FROM INVOICES");
  await expect(dropzone.getByText("selected-query.txt", { exact: true })).toBeVisible();

  const activeTransfer = await createFileDataTransfer(page, [
    {
      name: "drag-active.sql",
      type: "text/plain",
      content: "SELECT TOTAL_AMOUNT FROM INVOICES",
    },
  ]);
  await dropzone.dispatchEvent("dragenter", { dataTransfer: activeTransfer });
  await dropzone.dispatchEvent("dragover", { dataTransfer: activeTransfer });
  await expect(dropzone).toHaveAttribute("data-drag-active", "true");
  await expect(dropzone.getByText("ここにドロップして読み込む", { exact: true })).toBeVisible();
  await dropzone.dispatchEvent("dragleave", { dataTransfer: activeTransfer });
  await expect(dropzone).toHaveAttribute("data-drag-active", "false");
  await activeTransfer.dispose();

  const validTransfer = await createFileDataTransfer(page, [
    {
      name: "dragged-query.SQL",
      type: "text/plain",
      content: "SELECT TOTAL_AMOUNT FROM INVOICES",
    },
  ]);
  await dropzone.dispatchEvent("drop", { dataTransfer: validTransfer });
  await validTransfer.dispose();
  await expect(sqlInput).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(dropzone.getByText("dragged-query.SQL", { exact: true })).toBeVisible();
  await expect(adminSql.getByRole("alert")).toHaveCount(0);

  const invalidTransfer = await createFileDataTransfer(page, [
    {
      name: "not-sql.csv",
      type: "text/csv",
      content: "CUSTOMER_NAME,TOTAL_AMOUNT",
    },
  ]);
  await dropzone.dispatchEvent("drop", { dataTransfer: invalidTransfer });
  await invalidTransfer.dispose();
  await expect(adminSql.getByRole("alert")).toContainText(
    "このファイルは読み込めません。.sql または .txt ファイルを選択してください。"
  );
  await expect(sqlInput).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");

  const multipleTransfer = await createFileDataTransfer(page, [
    { name: "one.sql", type: "text/plain", content: "SELECT 1 FROM DUAL" },
    { name: "two.txt", type: "text/plain", content: "SELECT 2 FROM DUAL" },
  ]);
  await dropzone.dispatchEvent("drop", { dataTransfer: multipleTransfer });
  await multipleTransfer.dispose();
  await expect(adminSql.getByRole("alert")).toContainText(
    "一度に読み込めるファイルは 1 件です。.sql または .txt ファイルを 1 件だけドロップしてください。"
  );
  await expect(sqlInput).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");

  await fileInput.setInputFiles({
    name: "enabled-query.sql",
    mimeType: "text/plain",
    buffer: Buffer.from("SELECT 1 FROM DUAL"),
  });
  await expect(adminSql.getByRole("alert")).toHaveCount(0);
  await expect(sqlInput).toHaveValue("SELECT 1 FROM DUAL");

  await page.unroute("**/api/nl2sql/db-admin/execute");
  const executionGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/execute", async (route) => {
    await executionGate.promise;
    return fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: {
        columns: ["RESULT"],
        rows: [{ RESULT: 1 }],
        total: 1,
      },
      statements: [
        {
          index: 1,
          statement_type: "SELECT",
          status: "executed",
          sql: "SELECT 1 FROM DUAL",
          row_count: 1,
          message: "1 rows",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: false,
      rolled_back: false,
      warnings: [],
      timing,
    });
  });
  const executionRequest = page.waitForRequest("**/api/nl2sql/db-admin/execute");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await executionRequest;
  try {
    await expect(fileInput).toBeDisabled();
    const disabledTransfer = await createFileDataTransfer(page, [
      { name: "ignored.sql", type: "text/plain", content: "SELECT 9 FROM DUAL" },
    ]);
    await dropzone.dispatchEvent("drop", { dataTransfer: disabledTransfer });
    await disabledTransfer.dispose();
    await expect(sqlInput).toHaveValue("SELECT 1 FROM DUAL");
  } finally {
    executionGate.release();
  }
  await expect(fileInput).toBeEnabled();

  const selectedBox = await dropzone.boundingBox();
  expect(selectedBox).not.toBeNull();
  expect(selectedBox!.height).toBe(44);
  await page.setViewportSize({ width: 375, height: 812 });
  const mobileBox = await dropzone.boundingBox();
  expect(mobileBox).not.toBeNull();
  expect(mobileBox!.height).toBe(44);
  await expectNoHorizontalScroll(page);
});

test("AI 活用の 4 画面はナビ切替で入力を保持し、リセットで消える", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");

  // SQL 生成に検索クエリを入力する。
  const question = page.getByLabel("検索クエリ");
  await question.fill("保持テスト: 未入金の請求金額を確認したい");

  // SPA ナビで SELECT SQL 実行へ移動し、そちらにも入力する。
  await page.getByRole("link", { name: "SELECT SQL を実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  const directSql = page.getByLabel("SQL", { exact: true });
  await directSql.fill("SELECT CUSTOMER_NAME FROM INVOICES");

  // SQL 生成へ戻ると入力が残っている(unmount で破棄されない = keep-alive)。
  await page.getByRole("link", { name: /SQL 生成/ }).first().click();
  await expect(page).toHaveURL(/\/query$/);
  await expect(page.getByLabel("検索クエリ")).toHaveValue("保持テスト: 未入金の請求金額を確認したい");

  // SELECT SQL 実行へ再び移動しても入力が残っている。
  await page.getByRole("link", { name: "SELECT SQL を実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await expect(page.getByLabel("SQL", { exact: true })).toHaveValue("SELECT CUSTOMER_NAME FROM INVOICES");

  // クリアは明示ボタンでのみ行われる(ナビ切替では消えない)。
  await page.getByRole("button", { name: "クリア" }).click();
  await expect(page.getByLabel("SQL", { exact: true })).toHaveValue("");

  await page.getByRole("link", { name: /SQL 生成/ }).first().click();
  await page.getByRole("button", { name: "リセット" }).click();
  await expect(page.getByLabel("検索クエリ")).toHaveValue("");
});

test("history rerun deep-links back to query with question, engine, and profile", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/history");
  await expect(page.getByRole("button", { name: "履歴から再実行したい請求金額 の履歴を表示" })).toBeVisible();
  await page.getByRole("tab", { name: "SQL" }).click();
  const sqlBlock = page.locator("pre").filter({ hasText: "CUSTOMER_PAYMENT_RECONCILIATION_STATUS" }).first();
  await expect(sqlBlock).toBeVisible();
  await expect(sqlBlock).toHaveCSS("overflow-x", "auto");
  await expect(sqlBlock).toHaveCSS("white-space", "pre-wrap");
  await expectNoHorizontalScroll(page);
  await page.getByRole("button", { name: "この質問で再実行" }).click();

  await expect(page).toHaveURL(/\/query\?/);
  await expect(page.getByLabel("検索クエリ")).toHaveValue("履歴から再実行したい請求金額");
  await expect(page.getByRole("button", { name: /Select AI Agent/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expectNoHorizontalScroll(page);
});

test("root route opens SQL generation and sidebar exposes feature surfaces", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/");

  await expect(page).toHaveURL(/\/query$/);
  await expect(page.getByRole("heading", { name: "NL2SQL 検索ワークベンチ" })).toBeVisible();
  await expect(page.getByText("データ準備", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("AI 活用", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("改善・運用", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /ダッシュボード/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /テーブルの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /ビューの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /データの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /検証用サンプルデータ/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /業務プロファイル/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /用語・同義語/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 生成/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 確認・修復/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL から質問を生成/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "SELECT SQL を実行" })).toBeVisible();
  await expect(page.getByRole("link", { name: "管理 SQL を実行" })).toBeVisible();
  await expect(page.getByRole("link", { name: /フィードバック管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /フィードバック学習/ })).toHaveCount(0);
  const navTexts = await page.locator("nav a").allTextContents();
  expect(navTexts.findIndex((label) => label.includes("フィードバック管理"))).toBeLessThan(
    navTexts.findIndex((label) => label.includes("質問分類モデル管理"))
  );
  await expect(page.getByRole("link", { name: /質問分類モデル管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /質問学習/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /エンジン運用/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /NL2SQL 接続診断/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql analysis page analyzes SQL and repairs Oracle errors", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/sql-analysis");
  await expect(page.locator("#sql-analysis-panel-analysis")).toBeVisible();
  await expect(page.getByText("SQL は未分析です")).toBeVisible();
  await expect(page.getByTestId("sql-analysis-result-count")).toHaveCount(0);

  await page.getByLabel("分析する SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByRole("button", { name: "SQL を分析" })).toBeEnabled();
  await page.getByRole("button", { name: "SQL を分析" }).click();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();
  await page.getByRole("button", { name: "SELECT を実行" }).click();
  await expect(page.locator("#sql-analysis-panel-execution")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByTestId("sql-analysis-result-count")).toContainText("1");

  // 入力を変えると分析・実行結果は無効化される（タブ切替なしで同一画面上）。
  await page.getByLabel("分析する SQL").fill("SELECT INVOICE_ID FROM INVOICES");
  await expect(page.getByRole("button", { name: "SELECT を実行" })).toBeDisabled();
  await expect(page.getByText("SQL は未分析です")).toBeVisible();
  await expect(page.getByTestId("sql-analysis-result-count")).toHaveCount(0);

  await expect(page.locator("#sql-analysis-panel-repair")).toBeVisible();
  await expect(page.getByText("修復候補は未生成です")).toBeVisible();
  await page.getByLabel("修復する SQL").fill("SELECT BAD_COL FROM INVOICES");
  await page.getByLabel("Oracle error message").fill("ORA-00904: invalid identifier");
  await expect(page.getByRole("button", { name: "修復案を生成" })).toBeEnabled();
  await page.getByRole("button", { name: "修復案を生成" }).click();
  await expect(page.getByText("ORA-00904", { exact: true })).toBeVisible();
  await expect(page.getByText("SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();

  await page.getByLabel("Oracle error message").fill("ORA-00942: table or view does not exist");
  await expect(page.getByText("修復候補は未生成です")).toBeVisible();
  await expect(page.getByText("ORA-00904", { exact: true })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql analysis page uses the shared panel styling and step indicator responsively", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1365, height: 900 });
  await page.goto("/sql-analysis");

  const panelStyle = async (id: string) =>
    page.locator(`#sql-analysis-panel-${id}`).evaluate((node) => {
      const style = window.getComputedStyle(node);
      return {
        backgroundColor: style.backgroundColor,
        borderTopWidth: style.borderTopWidth,
        paddingTop: style.paddingTop,
      };
    });

  const analysisStyle = await panelStyle("analysis");
  expect(analysisStyle.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(analysisStyle.borderTopWidth).toBe("1px");
  expect(Number.parseFloat(analysisStyle.paddingTop)).toBeGreaterThan(0);

  // タブではなく工程ステッパー。3 工程セクションは常時縦積みで同じカード枠を共有する。
  await expect(page.getByTestId("sql-analysis-steps")).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.locator("#sql-analysis-panel-execution")).toBeVisible();
  expect(await panelStyle("execution")).toEqual(analysisStyle);
  await expect(page.locator("#sql-analysis-panel-repair")).toBeVisible();
  expect(await panelStyle("repair")).toEqual(analysisStyle);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/sql-analysis");
  await expect(page.getByTestId("sql-analysis-steps")).toBeVisible();
  await expect(page.getByRole("button", { name: "SQL を分析" })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("sql analysis page shows actionable errors beside the active action", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/analyze");
  await page.route("**/api/nl2sql/analyze", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "SQL 分析サービスに接続できません。" }),
    })
  );

  await page.goto("/sql-analysis");
  await page.getByLabel("分析する SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  const analyzeButton = page.getByRole("button", { name: "SQL を分析" });
  await analyzeButton.click();
  const error = page.getByRole("alert");
  await expect(error).toContainText("SQL 分析サービスに接続できません。");
  await expect(error).toContainText("接続状態と入力内容を確認して再試行してください。");
  await expect(analyzeButton).toBeEnabled();
  // エラーはボタン行から独立した行に置かれ、長文でも回り込み/横溢れを起こさない。
  await expect(error).toHaveCount(1);
  await expectNoHorizontalScroll(page);
});

test("sql to question page analyzes structure and reverse-generates a question", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sql-to-question");
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toBeVisible();
  await expect(page.locator("#sql-to-question-panel-input")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expect(page.getByText("請求情報")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("参照表 1");

  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByLabel("用語集を利用").check();
  await expect(page.getByRole("button", { name: "質問を生成" })).toBeEnabled();
  await page.getByRole("button", { name: "SQL 構造を分析" }).click();
  await expect(page.locator("#sql-to-question-panel-structure")).toBeVisible();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect.poll(() => api.analyzePayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    use_llm: true,
  });

  await expect(page.getByLabel("対象 SQL")).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByLabel("用語集を利用")).toBeChecked();
  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.locator("#sql-to-question-panel-result")).toBeVisible();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();
  await expect.poll(() => api.reversePayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });

  await expect(page.getByText("SQL 論理構造").first()).toBeVisible();
  await expect(page.getByText("SELECT: TOTAL_AMOUNT")).toBeVisible();
  await page.getByRole("button", { name: "Deep 逆生成" }).click();
  await expect(page.getByText("請求金額を条件付きで一覧確認したい")).toBeVisible();
  await expect.poll(() => api.reverseDeepPayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });
  await expectNoHorizontalScroll(page);
});

test("sql to question page uses the shared panel styling and a step indicator", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/table-management");
  const tablePanelStyle = await page.locator("#table-management-panel-list").evaluate((node) => {
    const style = window.getComputedStyle(node);
    return {
      backgroundColor: style.backgroundColor,
      borderTopWidth: style.borderTopWidth,
      borderRadius: style.borderRadius,
      paddingTop: style.paddingTop,
      boxShadow: style.boxShadow,
    };
  });

  await page.goto("/sql-to-question");
  const inputPanel = page.locator("#sql-to-question-panel-input");
  await expect(inputPanel).toBeVisible();
  const inputPanelStyle = await inputPanel.evaluate((node) => {
    const style = window.getComputedStyle(node);
    return {
      backgroundColor: style.backgroundColor,
      borderTopWidth: style.borderTopWidth,
      borderRadius: style.borderRadius,
      paddingTop: style.paddingTop,
      boxShadow: style.boxShadow,
    };
  });
  expect(inputPanelStyle).toEqual(tablePanelStyle);

  // タブではなく工程ステッパー。3 工程セクションは常に縦積みで表示される。
  const steps = page.getByTestId("sql-to-question-steps");
  await expect(steps).toBeVisible();
  await expect(steps.getByText("SQL入力・生成")).toBeVisible();
  await expect(steps.getByText("SQL論理構造")).toBeVisible();
  await expect(steps.getByText("質問候補")).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.locator("#sql-to-question-panel-structure")).toBeVisible();
  await expect(page.locator("#sql-to-question-panel-result")).toBeVisible();
  await expect(page.getByText("質問候補は未生成です")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("sql to question page shows a reserved loading state and retries reference-data errors", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/schema/objects?*");
  let failObjectRequests = true;
  let releaseFirstObjectRequest: (() => void) | undefined;
  const firstObjectRequestGate = new Promise<void>((resolve) => {
    releaseFirstObjectRequest = resolve;
  });
  await page.route("**/api/schema/objects?*", async (route) => {
    const shouldFail = failObjectRequests;
    if (shouldFail) {
      await firstObjectRequestGate;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Schema objects unavailable" }),
      });
      return;
    }
    await fulfillJson(route, {
      items: schemaCatalog.tables.map((table) => ({
        owner: table.owner,
        object_name: table.table_name,
        object_type: table.table_type,
        logical_name: table.logical_name,
        comment: table.comment,
        row_count: table.row_count,
        column_count: table.columns.length,
        last_ddl_at: "",
      })),
      next_cursor: null,
      total: schemaCatalog.tables.length,
      catalog_version: 1,
    });
  });

  await page.goto("/sql-to-question");
  await expect(page.getByTestId("sql-to-question-schema-skeleton")).toBeVisible();
  await expect(page.getByRole("button", { name: "質問を生成" })).toBeDisabled();
  releaseFirstObjectRequest?.();

  const errorBanner = page.getByRole("alert");
  await expect(errorBanner).toContainText("接続状態と入力内容を確認して再試行してください。");
  failObjectRequests = false;
  await errorBanner.getByRole("button", { name: "プロファイル・スキーマを再読込" }).click();
  await expect(errorBanner).toHaveCount(0);
  await expect(page.getByText("請求情報")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("参照表 1");
  await expectNoHorizontalScroll(page);
});

test("sql to question page shows a guided empty schema state", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", (route) =>
    fulfillJson(route, {
      items: [],
      next_cursor: null,
      total: 0,
      catalog_version: 1,
    })
  );

  await page.goto("/sql-to-question");

  await expect(page.getByText("参照できる表がありません")).toBeVisible();
  await expect(page.getByText("選択プロファイルで参照できるスキーマ情報がありません。")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("参照表 0");
  await expectNoHorizontalScroll(page);
});

test("sql to question page invalidates stale results when inputs change", async ({ page }) => {
  await mockNl2SqlApi(page);
  const alternateProfile = {
    ...profiles[0],
    id: "alternate",
    name: "代替プロファイル",
  };
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [...profiles, alternateProfile].map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: Object.keys(profile.glossary).length,
        few_shot_count: profile.few_shot_examples.length,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-06-21T10:00:00.000Z",
      })),
      next_cursor: null,
      total: 2,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/alternate", (route) =>
    fulfillJson(route, alternateProfile)
  );
  await page.goto("/sql-to-question");

  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();

  // 入力を変えると生成済み結果は無効化され、質問セクションは空状態へ戻る。
  await page.getByRole("combobox", { name: "業務プロファイル" }).selectOption("alternate");
  await expect(page.getByText("質問候補は未生成です")).toBeVisible();

  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();
  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES WHERE TOTAL_AMOUNT > 0");
  await expect(page.getByRole("button", { name: "質問を生成" })).toBeEnabled();
  await expect(page.getByText("SQL 構造は未分析です")).toBeVisible();
  await expect(page.getByText("質問候補は未生成です")).toBeVisible();
});

test("sql to question page keeps controls usable without page overflow at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockNl2SqlApi(page);
  await page.goto("/sql-to-question");

  await expect(page.getByTestId("sql-to-question-steps")).toBeVisible();
  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  for (const label of ["質問を生成", "SQL 構造を分析", "Deep 逆生成"]) {
    const button = page.getByRole("button", { name: label });
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThan(250);
  }
  await expectNoHorizontalScroll(page);
});

test("sql to question page remains usable at 150 percent zoom", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockNl2SqlApi(page);
  await page.goto("/sql-to-question");
  await page.evaluate(() => {
    document.documentElement.style.zoom = "1.5";
  });

  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  const steps = page.getByTestId("sql-to-question-steps");
  for (const label of ["SQL入力・生成", "SQL論理構造", "質問候補"]) {
    await expect(steps.getByText(label)).toBeVisible();
  }
  for (const label of ["質問を生成", "SQL 構造を分析", "Deep 逆生成"]) {
    const button = page.getByRole("button", { name: label });
    await expect(button).toBeVisible();
    await expect(button).toHaveCSS("white-space", "nowrap");
  }
  await expectNoHorizontalScroll(page);
});

test("sql to question page reports analyze errors beside the action bar", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/analyze");
  let releaseAnalyze: (() => void) | undefined;
  const analyzeGate = new Promise<void>((resolve) => {
    releaseAnalyze = resolve;
  });
  await page.route("**/api/nl2sql/analyze", async (route) => {
    await analyzeGate;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "SQL analysis unavailable" }),
    });
  });
  await page.goto("/sql-to-question");

  const inputPanel = page.locator("#sql-to-question-panel-input");
  await inputPanel.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await inputPanel.getByRole("button", { name: "SQL 構造を分析" }).click();
  await expect(inputPanel.getByLabel("対象 SQL")).toBeDisabled();
  await expect(inputPanel.getByRole("combobox", { name: "業務プロファイル" })).toBeDisabled();
  await expect(inputPanel.getByLabel("用語集を利用")).toBeDisabled();
  releaseAnalyze?.();

  await expect(inputPanel.getByRole("alert")).toContainText("接続状態と入力内容を確認して再試行してください。");
  await expect(inputPanel.getByLabel("対象 SQL")).toBeEnabled();
});

test("feedback management page mirrors Select AI feedback operations", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/feedback-management");
  await expect(page.getByRole("heading", { name: "フィードバック管理" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Select AI feedback" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Select AI ベクトルインデックス" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "アプリ内フィードバック" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "類似検索インデックス" })).toBeVisible();
  const profileSelect = page.getByLabel("DBMS_CLOUD_AI profile");
  await expect(profileSelect).toHaveValue("NL2SQL_DEFAULT_PROFILE");
  await expect(profileSelect.locator("option")).toHaveCount(1);
  await expect(profileSelect.locator("option", { hasText: "NL2SQL_MANUAL_AGENT_V2_PROFILE" })).toHaveCount(0);
  await expect(page.getByTestId("feedback-management-entry-count")).toContainText("1");
  await expect(page.getByText("NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX").first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "CONTENT" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "SQL_ID" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "SQL_TEXT" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "ATTRIBUTES" })).toBeVisible();
  const pageRefreshButton = page.getByRole("button", { name: "表示を更新", exact: true });
  if ((page.viewportSize()?.width ?? 0) < 640) {
    await expect(pageRefreshButton).toHaveCSS("height", "44px");
  } else {
    await expect(pageRefreshButton).toHaveClass(/\bh-8\b/);
  }
  const entryRefreshButtons = page.getByRole("button", { name: "最新エントリを取得" });
  await expect(entryRefreshButtons).toHaveCount(1);
  await expect(entryRefreshButtons).toHaveCSS("height", "44px");

  await page.getByRole("button", { name: "sql-001 の feedback を選択" }).click();
  await expect(page.getByLabel("選択された SQL_TEXT")).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "選択したフィードバックを削除" }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "選択したフィードバックを削除" })
    .click();
  await expect(page.getByText("Select AI feedback を削除しました。")).toBeVisible();
  expect(api.selectAiFeedbackDeletePayload).toEqual({
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    sql_text: "SELECT TOTAL_AMOUNT FROM INVOICES",
  });

  await page.getByRole("tab", { name: "Select AI ベクトルインデックス" }).click();
  const vectorIndexActions = page.getByTestId("feedback-vector-index-actions");
  const vectorIndexUpdate = vectorIndexActions.getByRole("button", {
    name: "ベクトルインデックスを更新",
  });
  await expect(vectorIndexActions).toHaveClass(/\bborder-t\b/);
  await expect(vectorIndexUpdate).toHaveClass(/\bh-10\b/);
  await expect(vectorIndexUpdate).toHaveClass(/\bbg-primary\b/);
  await page.getByLabel("Similarity_Threshold", { exact: true }).fill("0.85");
  await page.getByLabel("Match_Limit", { exact: true }).fill("4");
  await vectorIndexUpdate.click();
  await expect(page.getByText("Select AI feedback vector index を更新しました。")).toBeVisible();
  expect(api.selectAiFeedbackUpdatePayload).toEqual({
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    similarity_threshold: 0.85,
    match_limit: 4,
  });

  await page.getByRole("tab", { name: "アプリ内フィードバック" }).click();
  await expect(page.getByText("Embedding + LogisticRegression 分類器")).toHaveCount(0);
  await expect(page.getByText("質問の学習候補")).toHaveCount(0);
  await expect(page.getByText("既定プロファイル").last()).toBeVisible();
  await expect(page.getByLabel("生成 SQL")).toContainText("SELECT");
  await expect(page.getByText("確認待ち", { exact: true }).first()).toBeVisible();
  const feedbackFilters = page.getByTestId("feedback-app-filters");
  const feedbackSearch = feedbackFilters.getByLabel("履歴検索");
  const feedbackFilterButton = feedbackFilters.getByRole("button", { name: "絞り込み" });
  const feedbackSearchBox = await feedbackSearch.boundingBox();
  const feedbackFilterButtonBox = await feedbackFilterButton.boundingBox();
  expect(feedbackSearchBox).not.toBeNull();
  expect(feedbackFilterButtonBox).not.toBeNull();
  expect(feedbackFilterButtonBox!.height).toBeCloseTo(feedbackSearchBox!.height, 0);
  expect(feedbackFilterButtonBox!.height).toBe(44);
  const appFeedbackActions = page.getByTestId("feedback-app-actions");
  const saveAppFeedbackButton = appFeedbackActions.getByRole("button", {
    name: "フィードバック保存",
  });
  const openCandidateLink = appFeedbackActions.getByRole("link", { name: "学習候補で確認" });
  const clearAppFeedbackButton = appFeedbackActions.getByRole("button", {
    name: "フィードバックを解除",
  });
  await expect(appFeedbackActions).toHaveClass(/\bborder-t\b/);
  await expect(saveAppFeedbackButton).toHaveClass(/\bh-10\b/);
  await expect(saveAppFeedbackButton).toHaveClass(/\bbg-primary\b/);
  await expect(openCandidateLink).toHaveClass(/\bh-10\b/);
  await expect(openCandidateLink).toHaveClass(/\bbg-card\b/);
  await expect(clearAppFeedbackButton).toHaveClass(/\bh-10\b/);
  await expect(clearAppFeedbackButton).toHaveClass(/\bbg-danger\b/);
  await expect(openCandidateLink).toHaveAttribute(
    "href",
    /question-classifier-models\?tab=candidates&history_id=hist-001/
  );
  await page.getByRole("combobox", { name: "評価", exact: true }).selectOption("good");
  await page.getByLabel("コメント（任意）").fill("SQL は期待通りです");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "good",
    comment: "SQL は期待通りです",
  });
  await page.getByRole("button", { name: "フィードバックを解除" }).click();
  const clearAppFeedbackDialog = page.getByRole("alertdialog", {
    name: "フィードバックを解除しますか",
  });
  await expect(clearAppFeedbackDialog).toBeVisible();
  await clearAppFeedbackDialog.getByRole("button", { name: "フィードバックを解除" }).click();
  await expect(page.getByText("フィードバックを解除しました。")).toBeVisible();
  const feedbackFilterOptions = page.getByLabel("評価フィルター").locator("option");
  await expect(feedbackFilterOptions).toHaveText(["すべて", "良い", "違う", "未評価"]);
  await expect(feedbackFilterOptions.filter({ hasText: "要確認" })).toHaveCount(0);
  await page.getByLabel("評価フィルター").selectOption("good");
  await expect(
    page.getByTestId("feedback-history-row").filter({ hasText: "履歴から再実行したい請求金額" }).filter({ hasText: "良い" })
  ).toBeVisible();
  const filterGate = createRequestGate();
  await page.route(/\/api\/nl2sql\/feedback(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "GET" && url.searchParams.get("q") === "該当なし") {
      await filterGate.promise;
    }
    await route.fallback();
  });
  await feedbackSearch.fill("該当なし");
  const filterRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/nl2sql/feedback" && url.searchParams.get("q") === "該当なし";
  });
  await feedbackSearch.press("Enter");
  await filterRequest;
  await expect(feedbackFilterButton).toBeDisabled();
  await expect(feedbackFilterButton.locator("svg.animate-spin")).toBeVisible();
  filterGate.release();
  await expect(feedbackFilterButton).toBeEnabled();
  await expect(page.getByText("一致する履歴がありません")).toBeVisible();

  await page.getByRole("tab", { name: "類似検索インデックス" }).click();
  await expect(page.getByRole("heading", { name: "類似検索インデックス" })).toBeVisible();
  await expect(page.getByText("oracle_26ai")).toBeVisible();
  await expect(page.getByLabel("Oracle 26ai DDL plan")).toHaveValue(/VECTOR\(1536, FLOAT32\)/);
  const similarityConfigSave = page.getByRole("button", { name: "設定保存" });
  const similarityIndexActions = page.getByTestId("feedback-similarity-index-actions");
  const rebuildFeedbackIndexButton = similarityIndexActions.getByRole("button", {
    name: "Rebuild 実行",
  });
  const clearFeedbackIndexButton = similarityIndexActions.getByRole("button", {
    name: "Clear 実行",
  });
  await expect(similarityConfigSave).toHaveClass(/\bh-10\b/);
  await expect(similarityConfigSave).toHaveClass(/\bbg-primary\b/);
  await expect(similarityIndexActions).toHaveClass(/\bborder-t\b/);
  await expect(rebuildFeedbackIndexButton).toHaveClass(/\bh-10\b/);
  await expect(rebuildFeedbackIndexButton).toHaveClass(/\bbg-card\b/);
  await expect(clearFeedbackIndexButton).toHaveClass(/\bh-10\b/);
  await expect(clearFeedbackIndexButton).toHaveClass(/\bbg-danger\b/);
  await page.getByLabel("しきい値").fill("0.85");
  await page.getByLabel("件数").fill("4");
  await similarityConfigSave.click();
  await expect(page.getByText("Feedback 類似検索設定を保存しました。")).toBeVisible();
  expect(api.feedbackConfigPayload).toEqual({
    similarity_threshold: 0.85,
    match_limit: 4,
  });
  await rebuildFeedbackIndexButton.click();
  const rebuildDialog = page.getByRole("alertdialog", { name: "Feedback index 再構築の確認" });
  await expect(rebuildDialog).toBeVisible();
  await rebuildDialog.getByRole("button", { name: "Rebuild 実行" }).click();
  await expect(page.getByText(/NL2SQL_RUNTIME_MODE=oracle/)).toBeVisible();
  await expect(page.getByText("ready", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "削除" }).click();
  await expect(page.getByText("Feedback entry を削除しました。")).toBeVisible();
  expect(api.feedbackEntriesDeletePayload).toEqual({ history_ids: ["hist-001"] });
  await clearFeedbackIndexButton.click();
  const clearDialog = page.getByRole("alertdialog", { name: "Feedback index 削除の確認" });
  await expect(clearDialog).toBeVisible();
  await clearDialog.getByRole("button", { name: "Clear 実行" }).click();
  await expect(page.getByText(/clear 実行には NL2SQL_RUNTIME_MODE=oracle/)).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("feedback missing-table warning hides the Oracle physical table name", async ({ page }) => {
  await mockNl2SqlApi(page);
  const missingTableWarning =
    "Select AI feedback vector table が未作成です。feedback vector index を再構築してください。";
  const physicalTableName = "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB";
  await page.route("**/api/nl2sql/select-ai/feedback?*", (route) =>
    fulfillJson(route, {
      runtime: "oracle",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      index_name: "",
      table_name: "",
      items: [],
      total: 0,
      warnings: [missingTableWarning],
    })
  );

  await page.goto("/feedback-management");

  const warning = page.getByText(missingTableWarning, { exact: true });
  await expect(warning).toBeVisible();
  await expect(warning).not.toContainText(physicalTableName);
  await expect(page.getByText(physicalTableName, { exact: false })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("app feedback uses the shared responsive pagination for cursor pages", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNl2SqlApi(page);
  await page.unroute(/\/api\/nl2sql\/feedback(?:\?.*)?$/);

  const singlePageItems = Array.from({ length: 15 }, (_, index) => ({
    ...historyItem,
    id: `single-page-feedback-${index + 1}`,
    question: `単一ページのフィードバック ${index + 1}`,
    training_status: "pending",
    training_example_id: "",
  }));
  const cursorPageItems = Array.from({ length: 21 }, (_, index) => ({
    ...historyItem,
    id: `cursor-feedback-${index + 1}`,
    question: `ページング対象 ${index + 1}`,
    training_status: "pending",
    training_example_id: "",
  }));

  await page.route(/\/api\/nl2sql\/feedback(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("q") !== "ページング対象") {
      return fulfillJson(route, {
        items: singlePageItems,
        total: singlePageItems.length,
        next_cursor: "",
      });
    }
    const secondPage = url.searchParams.get("cursor") === "feedback-cursor-2";
    return fulfillJson(route, {
      items: secondPage ? cursorPageItems.slice(20) : cursorPageItems.slice(0, 20),
      total: cursorPageItems.length,
      next_cursor: secondPage ? "" : "feedback-cursor-2",
    });
  });

  await page.goto("/feedback-management?tab=appFeedback");

  const historyPane = page.getByTestId("feedback-history-pane");
  const rows = historyPane.getByTestId("feedback-history-row");
  const pagination = historyPane.getByTestId("app-feedback-pagination");
  await expect(rows).toHaveCount(15);
  await expect(pagination).toHaveCount(0);

  await page.getByLabel("履歴検索").fill("ページング対象");
  await page.getByRole("button", { name: "絞り込み" }).click();

  await expect(rows).toHaveCount(20);
  await expect(pagination).toBeVisible();
  await expect(
    historyPane.getByRole("navigation", { name: "フィードバック履歴一覧のページ切替" })
  ).toBeVisible();
  await expect(pagination).toContainText("1-20 / 21 件");
  await expect(pagination).toContainText("1 / 2 ページ");
  const previousButton = pagination.getByRole("button", { name: "前へ" });
  const nextButton = pagination.getByRole("button", { name: "次へ" });
  await expect(previousButton).toBeDisabled();
  await expect(nextButton).toBeEnabled();
  await pagination.scrollIntoViewIfNeeded();
  await expectHorizontallyContained(pagination, historyPane);
  await expectNoHorizontalScroll(page);

  await nextButton.click();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("ページング対象 21");
  await expect(pagination).toContainText("21-21 / 21 件");
  await expect(pagination).toContainText("2 / 2 ページ");
  await expect(previousButton).toBeEnabled();
  await expect(nextButton).toBeDisabled();

  await previousButton.focus();
  await expect(previousButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(rows).toHaveCount(20);
  await expect(pagination).toContainText("1-20 / 21 件");
  await expect(previousButton).toBeDisabled();

  await page.setViewportSize({ width: 375, height: 900 });
  await pagination.scrollIntoViewIfNeeded();
  await expect(pagination).toBeVisible();
  await expectHorizontallyContained(pagination, historyPane);
  await expectNoHorizontalScroll(page);
});

test("feedback management keeps utility actions usable in empty and load error states", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/feedback-config", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Feedback 類似検索設定を取得できません。" }),
    })
  );

  await page.goto("/feedback-management");

  await expect(page.getByRole("alert")).toContainText("Feedback 類似検索設定を取得できません。");
  await expect(page.getByText("Select AI feedback はありません")).toBeVisible();
  const reloadButton = page.getByRole("button", { name: "表示を更新", exact: true });
  if ((page.viewportSize()?.width ?? 0) < 640) {
    await expect(reloadButton).toHaveCSS("height", "44px");
  } else {
    await expect(reloadButton).toHaveClass(/\bh-8\b/);
  }
  await expect(reloadButton).toHaveClass(/\bbg-card\b/);
  await reloadButton.focus();
  await expect(reloadButton).toBeFocused();
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("app feedback keeps history left of the editor without crossing the divider", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop project covers desktop resizing and the 375px stacked layout");
  await page.setViewportSize({ width: 2048, height: 1100 });
  await page.addInitScript(() => {
    window.localStorage.removeItem(
      "production-ready-nl2sql.fixedSplitPane.feedback-management-app-feedback-history-left-v2"
    );
  });
  await mockNl2SqlApi(page);
  await page.goto("/feedback-management?tab=appFeedback");

  const pane = page.getByTestId(
    "fixed-split-pane-feedback-management-app-feedback-history-left-v2"
  );
  const left = page.getByTestId(
    "fixed-split-pane-feedback-management-app-feedback-history-left-v2-left"
  );
  const divider = page.getByTestId(
    "fixed-split-pane-feedback-management-app-feedback-history-left-v2-divider"
  );
  const right = page.getByTestId(
    "fixed-split-pane-feedback-management-app-feedback-history-left-v2-right"
  );
  const historyPane = page.getByTestId("feedback-history-pane");
  const editorPane = page.getByTestId("app-feedback-editor-pane");
  const historyRow = page.getByTestId("feedback-history-row").first();
  const generatedSql = page.getByLabel("生成 SQL");

  await expect(pane).toHaveAttribute("data-split-layout", "split");
  await expect(pane).toHaveAttribute("data-split-ratio", "left-wide");
  await expect(left.getByRole("heading", { name: "フィードバック履歴" })).toBeVisible();
  await expect(right.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();
  await expectSplitPaneReservedTrack(pane);
  await expectHorizontallyContained(historyPane, left);
  await expectHorizontallyContained(historyRow, historyPane);
  await expectHorizontallyContained(editorPane, right);
  await expectHorizontallyContained(generatedSql, editorPane);
  const [initialLeft, initialRight] = await Promise.all([left.boundingBox(), right.boundingBox()]);
  expect(initialLeft).not.toBeNull();
  expect(initialRight).not.toBeNull();
  expect(initialLeft!.width).toBeGreaterThan(initialRight!.width);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(pane).toHaveAttribute("data-split-layout", "split");
  for (const deltaX of [-4_000, 8_000]) {
    await dragSplitDivider(page, divider, deltaX);
    await expectSplitPaneReservedTrack(pane);
    await expectHorizontallyContained(historyPane, left);
    await expectHorizontallyContained(historyRow, historyPane);
    await expectHorizontallyContained(editorPane, right);
    await expectHorizontallyContained(generatedSql, editorPane);
    await expectNoHorizontalScroll(page);
  }

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expect(divider).toBeVisible();
  await expect(divider.locator(".fixed-split-pane__line")).not.toHaveCSS(
    "background-color",
    "rgba(0, 0, 0, 0)"
  );

  await page.setViewportSize({ width: 375, height: 900 });
  await expectSplitPaneStacked(pane);
  const [stackedHistory, stackedEditor] = await Promise.all([
    historyPane.boundingBox(),
    editorPane.boundingBox(),
  ]);
  expect(stackedHistory).not.toBeNull();
  expect(stackedEditor).not.toBeNull();
  expect(stackedHistory!.y + stackedHistory!.height).toBeLessThanOrEqual(stackedEditor!.y + 1);
  const mobileFeedbackFilters = page.getByTestId("feedback-app-filters");
  const mobileFeedbackSearch = mobileFeedbackFilters.getByLabel("履歴検索");
  const mobileFilterButton = mobileFeedbackFilters.getByRole("button", { name: "絞り込み" });
  const [mobileFilterFormBox, mobileSearchBox, mobileFilterButtonBox] = await Promise.all([
    mobileFeedbackFilters.boundingBox(),
    mobileFeedbackSearch.boundingBox(),
    mobileFilterButton.boundingBox(),
  ]);
  expect(mobileFilterFormBox).not.toBeNull();
  expect(mobileSearchBox).not.toBeNull();
  expect(mobileFilterButtonBox).not.toBeNull();
  expect(mobileSearchBox!.width).toBeCloseTo(mobileFilterFormBox!.width, 0);
  expect(mobileFilterButtonBox!.width).toBeCloseTo(mobileFilterFormBox!.width, 0);
  expect(mobileFilterButtonBox!.height).toBe(44);
  const mobileActionBar = page.getByTestId("feedback-app-actions");
  const mobileActionControls = [
    mobileActionBar.getByRole("button", { name: "フィードバック保存" }),
    mobileActionBar.getByRole("link", { name: "学習候補で確認" }),
    mobileActionBar.getByRole("button", { name: "フィードバックを解除" }),
  ];
  const mobileActionBarBox = await mobileActionBar.boundingBox();
  expect(mobileActionBarBox).not.toBeNull();
  for (const action of mobileActionControls) {
    const actionBox = await action.boundingBox();
    expect(actionBox).not.toBeNull();
    expect(actionBox!.width).toBeCloseTo(mobileActionBarBox!.width, 0);
    await action.focus();
    await expect(action).toBeFocused();
    await expectHorizontallyContained(action, editorPane);
  }
  await expectNoHorizontalScroll(page);
});

test("shared split panes reserve their divider track across NL2SQL management pages", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop-only cross-page geometry audit");
  await page.setViewportSize({ width: 2048, height: 1000 });
  await mockNl2SqlApi(page);

  const scenarios: Array<{ path: string; splitIds: string[] }> = [
    { path: "/table-management", splitIds: ["table-management-list"] },
    { path: "/view-management", splitIds: ["view-management-list"] },
    { path: "/data-management", splitIds: ["data-management-preview"] },
    { path: "/sample-data", splitIds: ["sample-data-import"] },
    {
      path: "/sql-analysis",
      splitIds: ["sql-analysis-workspace", "sql-analysis-repair"],
    },
    { path: "/sql-to-question", splitIds: ["sql-to-question-input"] },
    { path: "/feedback-management", splitIds: ["feedback-management-entries-split"] },
    {
      path: "/feedback-management?tab=appFeedback",
      splitIds: ["feedback-management-app-feedback-history-left-v2"],
    },
    { path: "/history", splitIds: ["history-management-list"] },
  ];

  for (const scenario of scenarios) {
    await page.goto(scenario.path);
    for (const splitId of scenario.splitIds) {
      const pane = page.getByTestId(`fixed-split-pane-${splitId}`);
      await expect(pane).toHaveAttribute("data-split-layout", "split");
      await expectSplitPaneReservedTrack(pane);
    }
    await expectNoHorizontalScroll(page);
  }
});

test("legacy learning route redirects to feedback management", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/learning");
  await expect(page).toHaveURL(/\/feedback-management$/);
  await expect(page.getByRole("heading", { name: "フィードバック管理" })).toBeVisible();
  await expect(page.getByRole("link", { name: /フィードバック学習/ })).toHaveCount(0);
});

test("question classifier training data follows the CATEGORY/TEXT contract", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/question-classifier-models");
  await page.getByRole("tab", { name: "訓練データ" }).click();
  const trainingWorkspace = page.getByRole("tabpanel", { name: "訓練データ" });

  await expect(trainingWorkspace.getByRole("heading", { name: "訓練データ一覧" })).toBeVisible();
  await expect(trainingWorkspace.getByRole("combobox", { name: "業務プロファイル" })).toHaveCount(0);
  await expect(trainingWorkspace.getByRole("link", { name: "Training JSONL 出力" })).toHaveCount(0);
  await expect(trainingWorkspace.getByRole("link", { name: "Training XLSX 出力" })).toBeVisible();
  await expect(
    trainingWorkspace.getByText("旧モデル管理と同じ CATEGORY / TEXT 形式の訓練データを一覧・取込・出力します。")
  ).toHaveCount(0);
  await expect(trainingWorkspace.getByText("既存 training data を置き換える")).toBeVisible();

  await trainingWorkspace.getByLabel("Excel/CSV ファイル").setInputFiles({
    name: "training_data.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("CATEGORY,TEXT\n監査,監査ログを確認したい\n"),
  });
  await expect(page.getByText("1 件の training data を取り込みました。")).toBeVisible();
  expect(api.classifierTrainingImportBody).toContain('name="file"');
  expect(api.classifierTrainingImportBody).toContain('name="replace"');
  expect(api.classifierTrainingImportBody).not.toContain('name="profile_id"');

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(page.getByRole("link", { name: "Training XLSX 出力" })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.getByRole("tab", { name: "学習候補" }).click();
  await expect(page.getByText("フィードバック学習候補", { exact: true })).toBeVisible();
  await expect(page.getByText("履歴から再実行したい請求金額")).toBeVisible();
  const conflictCandidate = page
    .getByTestId("qcm-training-candidate")
    .filter({ hasText: "競合している請求分類を確認したい" });
  const changedCandidate = page
    .getByTestId("qcm-training-candidate")
    .filter({ hasText: "元 feedback が変更された質問" });
  await expect(conflictCandidate.getByText("Profile 競合", { exact: true })).toBeVisible();
  await expect(changedCandidate.getByText("元 feedback 変更あり", { exact: true })).toBeVisible();
  await expect(page.getByLabel("競合している請求分類を確認したい を選択")).toBeDisabled();
  await expect(page.getByLabel("元 feedback が変更された質問 を選択")).toBeDisabled();
  await expect(page.getByRole("button", { name: "推薦・書き換え" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "類似履歴検索" })).toHaveCount(0);
});

test("question classifier model management page trains classifier and finds learning candidates", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/question-learning");
  await expect(page).toHaveURL(/\/question-classifier-models$/);
  await expect(page.getByRole("heading", { name: "質問分類モデル管理" })).toBeVisible();
  await expect(page.getByText("質問学習", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Model registry")).toHaveCount(0);
  await expect(page.getByText("Legacy artifact 取込")).toHaveCount(0);
  await expect(page.getByText("Model artifact 取込")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "モデル一覧" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "訓練データ" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "モデル学習" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "モデルテスト" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "学習候補" })).toBeVisible();
  await expect(page.getByText("フィードバック保存")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "訓練データ一覧" })).toBeVisible();
  const classifierStatus = page.getByTestId("qcm-model-status");
  await expect(classifierStatus).toHaveText("学習済み");
  await expect(page.getByText(/最終更新日時:/)).toBeVisible();
  expect(api.classifierModelListRequests).toBe(0);

  const trainingDataTab = page.getByRole("tab", { name: "訓練データ" });
  await trainingDataTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "モデル学習" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "モデル学習" })).toBeVisible();

  await trainingDataTab.click();
  await expect(page.getByRole("heading", { name: "訓練データ一覧" })).toBeVisible();
  await expect(page.getByTestId("qcm-training-data-table").getByText("CATEGORY")).toBeVisible();
  await expect(page.getByText("請求金額が大きい取引先を見たい")).toBeVisible();
  await expect(page.getByText("ページング対象 11: 訓練データ確認 11")).toHaveCount(0);
  await expect(page.getByTestId("qcm-training-data-pagination")).toContainText("1-10 / 12 件");
  await expect(page.getByTestId("qcm-training-data-pagination")).toContainText("1 / 2 ページ");
  await page.getByTestId("qcm-training-data-pagination").getByRole("button", { name: "次へ" }).click();
  await expect(page.getByText("ページング対象 11: 訓練データ確認 11")).toBeVisible();
  await expect(page.getByText("請求金額が大きい取引先を見たい")).toHaveCount(0);
  await expect(page.getByTestId("qcm-training-data-pagination")).toContainText("11-12 / 12 件");
  await page.getByTestId("qcm-training-data-pagination").getByRole("button", { name: "前へ" }).click();
  await expect(page.getByText("請求金額が大きい取引先を見たい")).toBeVisible();
  await page.getByTestId("qcm-training-data-pagination").getByRole("button", { name: "次へ" }).click();
  await page.getByPlaceholder("CATEGORY / TEXT / SOURCE で絞り込み").fill("ページング対象");
  await expect(page.getByTestId("qcm-training-data-pagination")).toContainText("1-10 / 12 件");
  await expect(page.getByTestId("qcm-training-data-pagination")).toContainText("1 / 2 ページ");
  await expect(page.getByText("請求金額が大きい取引先を見たい")).toBeVisible();
  await expect(page.getByText("ページング対象 11: 訓練データ確認 11")).toHaveCount(0);
  await page.getByRole("button", { name: "訓練データ一覧を取得" }).click();

  await page.getByRole("tab", { name: "モデルテスト" }).click();
  await page.getByRole("button", { name: "分類を試す" }).click();
  await expect(page.getByText("信頼度 92%")).toBeVisible();
  await expect(page.getByText("予測カテゴリ", { exact: true })).toBeVisible();
  await expect(page.locator("td").filter({ hasText: /^92%$/ }).first()).toBeVisible();

  await page.getByRole("tab", { name: "学習候補" }).click();
  await expect(page.getByText("フィードバック学習候補", { exact: true })).toBeVisible();
  await expect(page.getByText("履歴から再実行したい請求金額")).toBeVisible();
  await page.getByLabel("履歴から再実行したい請求金額 を選択").check();
  await page.getByRole("button", { name: "選択した 1 件を追加" }).click();
  const addDialog = page.getByRole("alertdialog", { name: "訓練データへ追加しますか" });
  await expect(addDialog).toBeVisible();
  await addDialog.getByRole("button", { name: "選択した候補を追加" }).click();
  await expect(page.getByText("1 件を訓練データへ追加しました。")).toBeVisible();
  expect(api.classifierFeedbackImportPayload).toEqual({
    items: [{ history_id: "hist-001", profile_id: "default" }],
  });
  await expect(classifierStatus).toHaveText("学習済み・再学習待ち");
  await expect(page.getByRole("button", { name: "推薦・書き換え" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "類似履歴検索" })).toHaveCount(0);

  await page.getByRole("tab", { name: "訓練データ" }).click();
  await page.getByPlaceholder("CATEGORY / TEXT / SOURCE で絞り込み").fill("履歴から再実行");
  await expect(page.getByText("SQL feedback", { exact: true })).toBeVisible();
  await expect(page.getByText("feedback:hist-001", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "モデル学習" }).click();
  await page.getByRole("button", { name: "Classifier 学習" }).click();
  await expect(page.getByText("LogisticRegression classifier を学習しました。")).toBeVisible();
  await expect(classifierStatus).toHaveText("学習済み");

  await page.setViewportSize({ width: 375, height: 900 });
  await page.getByRole("tab", { name: "訓練データ" }).click();
  await expect(page.getByRole("link", { name: "Training XLSX 出力" })).toBeVisible();
  await expect(page.getByText("Legacy artifact 取込")).toHaveCount(0);
  await expect(page.getByText("Model artifact 取込")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/question-classifier-models");
  await expect(page.getByRole("heading", { name: "訓練データ一覧" })).toBeVisible();
  expect(api.classifierModelListRequests).toBe(0);
  await expectNoHorizontalScroll(page);
});

test("learning candidates use the shared responsive list, filters, paging, and recovery states", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop context covers both 1440px and 375px geometry");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNl2SqlApi(page);

  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [
      ...profiles,
      {
        ...profiles[0],
        id: "payment",
        name: "入金管理",
        category: "入金管理",
      },
    ])
  );

  const initialCandidate = {
    history_id: "hist-001",
    question: "履歴から再実行したい請求金額",
    profile_id: "default",
    profile_name: "既定プロファイル",
    feedback_rating: "good",
    feedback_comment: "SQL は期待通りです",
    created_at: historyItem.created_at,
    status: "pending",
    training_example_id: "",
    conflict_profile_ids: [],
  };
  const secondPageCandidate = {
    ...initialCandidate,
    history_id: "hist-021",
    question: "21 件目のページング対象候補",
    feedback_comment: "次ページの候補です",
  };
  const initialGate = createRequestGate();
  let holdInitialRequests = true;
  let failRecoveryRequest = true;
  const candidateRequests: URL[] = [];

  await page.unroute("**/api/nl2sql/classifier/training-candidates*");
  await page.route("**/api/nl2sql/classifier/training-candidates*", async (route) => {
    const url = new URL(route.request().url());
    candidateRequests.push(url);
    if (holdInitialRequests) {
      await initialGate.promise;
    }
    if (url.searchParams.get("q") === "error" && failRecoveryRequest) {
      failRecoveryRequest = false;
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          data: null,
          error_messages: ["学習候補を取得できません。接続状態を確認して再試行してください。"],
        }),
      });
    }
    if (url.searchParams.get("q") === "一致なし") {
      return fulfillJson(route, {
        items: [],
        total: 0,
        next_cursor: "",
        pending_count: 0,
        added_count: 0,
        attention_count: 0,
      });
    }
    if (url.searchParams.get("cursor") === "cursor-2") {
      return fulfillJson(route, {
        items: [secondPageCandidate],
        total: 21,
        next_cursor: "",
        pending_count: 21,
        added_count: 0,
        attention_count: 0,
      });
    }
    return fulfillJson(route, {
      items: [initialCandidate],
      total: 21,
      next_cursor: "cursor-2",
      pending_count: 21,
      added_count: 0,
      attention_count: 0,
    });
  });

  let failedImportPayload: Record<string, unknown> | null = null;
  await page.unroute("**/api/nl2sql/classifier/training-data/from-feedback");
  await page.route("**/api/nl2sql/classifier/training-data/from-feedback", (route) => {
    failedImportPayload = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: ["学習候補を追加できません。Profile を確認して再試行してください。"],
      }),
    });
  });

  const navigation = page.goto("/question-classifier-models?tab=candidates");
  await expect(page.getByTestId("qcm-candidates-list-skeleton")).toBeVisible();
  holdInitialRequests = false;
  initialGate.release();
  await navigation;

  const candidateList = page.getByTestId("qcm-candidate-list");
  const firstCandidate = page.getByTestId("qcm-training-candidate").filter({
    hasText: initialCandidate.question,
  });
  await expect(candidateList).toBeVisible();
  await expect(page.getByText("条件一致 21 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("qcm-candidate-pagination")).toContainText("1 / 2 ページ");

  const candidateProfile = firstCandidate.getByRole("combobox", { name: "追加する Profile" });
  await expect(candidateProfile).toHaveText("既定プロファイル");
  await expect(candidateProfile).not.toContainText("default");
  await candidateProfile.focus();
  await page.keyboard.press("ArrowDown");
  await expect(candidateProfile).toHaveAttribute("aria-expanded", "true");
  const profileListbox = firstCandidate.getByRole("listbox");
  await expect(profileListbox.getByText("default", { exact: true })).toBeVisible();
  await expect(profileListbox.getByText("payment", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(candidateProfile).toHaveAttribute("aria-expanded", "false");
  await expect(candidateProfile).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await expect(candidateProfile).toHaveText("入金管理");
  await expect(candidateProfile).not.toContainText("payment");
  await expect(candidateProfile).toBeFocused();

  await page.getByLabel("履歴から再実行したい請求金額 を選択").check();
  await page.getByRole("button", { name: "選択した 1 件を追加" }).click();
  const failedAddDialog = page.getByRole("alertdialog", { name: "訓練データへ追加しますか" });
  await failedAddDialog.getByRole("button", { name: "選択した候補を追加" }).click();
  await expect(
    page.getByTestId("qcm-candidate-bulk-actions").getByText(
      "学習候補を追加できません。Profile を確認して再試行してください。"
    )
  ).toBeVisible();
  expect(failedImportPayload).toEqual({
    items: [{ history_id: "hist-001", profile_id: "payment" }],
  });

  const search = page.getByPlaceholder("質問・コメントで絞り込み");
  await search.fill("error");
  await search.press("Enter");
  const candidateError = page.getByRole("alert").filter({
    hasText: "学習候補を取得できません。接続状態を確認して再試行してください。",
  });
  await expect(candidateError).toBeVisible();
  await candidateError.getByRole("button", { name: "再読込" }).click();
  await expect(candidateList).toBeVisible();

  const statusFilter = page.getByRole("combobox", { name: "状態" });
  await statusFilter.click();
  await page.getByRole("option", { name: "確認待ち", exact: true }).click();
  const profileFilter = page.getByRole("combobox", { name: "業務プロファイル" });
  await profileFilter.click();
  await page.getByRole("option", { name: /入金管理/ }).click();
  await search.fill("一致なし");
  await search.press("Enter");
  await expect(page.getByText("条件に一致する学習候補がありません", { exact: true })).toBeVisible();
  const lastFilteredRequest = candidateRequests.at(-1);
  expect(lastFilteredRequest?.searchParams.get("q")).toBe("一致なし");
  expect(lastFilteredRequest?.searchParams.get("status")).toBe("pending");
  expect(lastFilteredRequest?.searchParams.get("profile_id")).toBe("payment");

  await page.getByRole("button", { name: "絞り込みを解除" }).click();
  await expect(search).toHaveValue("");
  await expect(statusFilter).toHaveText("すべて");
  await expect(profileFilter).toHaveText("すべての Profile");
  await expect(candidateList).toBeVisible();
  await page.getByTestId("qcm-candidate-pagination").getByRole("button", { name: "次へ" }).click();
  await expect(page.getByText(secondPageCandidate.question, { exact: true })).toBeVisible();
  await expect(page.getByTestId("qcm-candidate-pagination")).toContainText("21-21 / 21 件");
  await page.getByTestId("qcm-candidate-pagination").getByRole("button", { name: "前へ" }).click();
  await expect(page.getByText(initialCandidate.question, { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  const mobileCandidate = page.getByTestId("qcm-training-candidate").first();
  const mobileProfile = mobileCandidate.getByRole("combobox", { name: "追加する Profile" });
  await mobileProfile.click();
  await expectHorizontallyContained(mobileCandidate.getByRole("listbox"), mobileCandidate);
  await page.keyboard.press("Escape");
  await expect(mobileCandidate.getByRole("link", { name: "フィードバック管理で確認" })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await expect(candidateList).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("question classifier model management handles untrained, empty, and load error states", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/classifier");
  await page.route("**/api/nl2sql/classifier", (route) =>
    fulfillJson(route, {
      ready: false,
      trained: false,
      classifier_version: "",
      updated_at: "",
      example_count: 0,
      category_count: 0,
      categories: [],
      embedding_model: "deterministic-hash-1536",
      vector_dimension: 1536,
      persistence_mode: "memory",
      recommendation_source: "deterministic",
      metrics: {},
      warnings: ["LogisticRegression classifier は未学習です。"],
    })
  );
  await page.unroute("**/api/nl2sql/classifier/training-data");
  await page.route("**/api/nl2sql/classifier/training-data", (route) =>
    fulfillJson(route, {
      total_examples: 0,
      categories: [],
      warnings: ["分類器の training data が未登録です。"],
      examples: [],
    })
  );

  await page.goto("/question-classifier-models");
  const classifierStatus = page.getByTestId("qcm-model-status");
  await expect(classifierStatus).toHaveText("未学習");
  await expect(page.getByText("訓練データは未登録です")).toBeVisible();
  await page.getByRole("tab", { name: "モデルテスト" }).click();
  await expect(page.getByRole("button", { name: "分類を試す" })).toBeDisabled();
  expect(api.classifierModelListRequests).toBe(0);

  await page.unroute("**/api/nl2sql/classifier");
  await page.route("**/api/nl2sql/classifier", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: ["分類モデル状態を取得できません。接続を確認して再試行してください。"],
      }),
    })
  );
  await page.goto("/question-classifier-models");
  await expect(
    page.getByText("分類モデル状態を取得できません。接続を確認して再試行してください。")
  ).toBeVisible();
  await expect(
    page.getByRole("alert").getByRole("button", { name: "再読込" })
  ).toBeVisible();
});

test("glossary page manages global terms only", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/glossary-rules");
  await expect(page.getByRole("heading", { level: 1, name: "用語・同義語" })).toBeVisible();
  await expect(page.getByLabel("用語・同義語管理ステータス")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "表示を更新", exact: true })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 2, name: "用語・同義語", exact: true })).toBeVisible();
  await expect(page.getByText("用語・同義語 21", { exact: true })).toBeVisible();
  await expect(page.locator("main").getByText(/グローバル用語(?:集)?/)).toHaveCount(0);
  await expect(page.locator("main").getByText(/語彙/)).toHaveCount(0);
  // 「グローバルルール」は独立ナビ/専用ページ(/global-rules)として存在するため、
  // 用語・同義語ページ本文(main)に混在しないことのみ検証(サイドバーのナビ項目は除外)。
  await expect(page.locator("main").getByText("グローバルルール")).toHaveCount(0);
  await expect(page.getByTestId("glossary-terms-preview").getByRole("columnheader", { name: "No." })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-row-number").first()).toHaveText("1");
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "売上" })).toBeVisible();
  await expect(page.getByTestId("glossary-term-preview-cell").first()).toHaveCSS("vertical-align", "middle");
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "INVOICES.TOTAL_AMOUNT" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "用語11" })).toHaveCount(0);
  await expect(page.getByTestId("glossary-terms-pagination")).toContainText("1-10 / 21 件");
  await expect(page.getByLabel("用語・同義語データのページ切替")).toBeVisible();
  await page.getByTestId("glossary-terms-pagination").getByRole("button", { name: "次へ" }).click();
  await expect(page.getByTestId("glossary-terms-row-number").first()).toHaveText("11");
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "用語11" })).toBeVisible();

  await page.getByRole("button", { name: "表示を更新", exact: true }).click();
  await expect(page.getByText("サーバーの最新内容を読み込みました。")).toBeVisible();
  await expect(page.getByTestId("glossary-terms-pagination")).toContainText("1 / 3 ページ");

  await page.getByLabel("用語・同義語 Excel 取込").setInputFiles({
    name: "terms.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mock"),
  });
  await expect(page.getByText("用語・同義語を 1 件取り込みました。")).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "粗利" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "INVOICES.PROFIT" })).toBeVisible();
  await expect(page.getByLabel("グローバルルール Excel 取込")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "グローバルルール Excel 出力" })).toHaveCount(0);

  const termsDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "用語・同義語 Excel 出力" }).click();
  const termsDownload = await termsDownloadPromise;
  expect(termsDownload.suggestedFilename()).toBe("terms.xlsx");

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("glossary previews preserve Excel cell newlines and wrap long content", async ({ page }) => {
  await mockNl2SqlApi(page);
  const multilineDefinition = [
    "INVOICES.TOTAL_AMOUNT",
    "",
    "    AS 売上金額",
    ...Array.from({ length: 16 }, (_, index) => `    detail_${index + 1}`),
  ].join("\n");
  await page.route("**/api/nl2sql/legacy-learning-material", (route) =>
    fulfillJson(route, {
      glossary: { 売上: multilineDefinition },
      rules: [],
    })
  );

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/glossary-rules");

  const definitionText = page.getByTestId("glossary-definition-preview-text").first();
  await expect(definitionText).toHaveCSS("white-space", "pre-wrap");
  await expect(definitionText).toHaveCSS("overflow-y", "auto");
  expect(await definitionText.textContent()).toBe(multilineDefinition);
  const definitionMetrics = await definitionText.evaluate((element) => {
    const style = getComputedStyle(element);
    const lineHeight = Number.parseFloat(style.lineHeight);
    return {
      height: element.getBoundingClientRect().height,
      lineHeight,
      maxHeight: Number.parseFloat(style.maxHeight),
      scrollHeight: element.scrollHeight,
    };
  });
  expect(definitionMetrics.height).toBeLessThanOrEqual(definitionMetrics.lineHeight * 10 + 1);
  expect(definitionMetrics.maxHeight).toBeCloseTo(definitionMetrics.lineHeight * 10, 1);
  expect(definitionMetrics.scrollHeight).toBeGreaterThan(definitionMetrics.height);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(definitionText).toHaveCSS("overflow-wrap", "anywhere");
  expect(await definitionText.textContent()).toBe(multilineDefinition);
  await expectNoHorizontalScroll(page);
});

test("glossary global data shows empty, loading, and server error states", async ({ page }) => {
  await mockNl2SqlApi(page);
  let requestCount = 0;
  let releaseReload: () => void = () => undefined;
  let markReloadStarted: () => void = () => undefined;
  const reloadStarted = new Promise<void>((resolve) => {
    markReloadStarted = resolve;
  });
  await page.route("**/api/nl2sql/legacy-learning-material", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      return fulfillJson(route, { glossary: {}, rules: [] });
    }
    markReloadStarted();
    await new Promise<void>((resolve) => {
      releaseReload = resolve;
    });
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "サーバー読込エラー" }),
    });
  });

  await page.goto("/glossary-rules");
  await expect(page.getByText("データがありません。")).toBeVisible();

  const reloadButton = page.getByRole("button", { name: "表示を更新" });
  const reloadClick = reloadButton.click();
  await reloadStarted;
  await expect(reloadButton).toBeDisabled();
  await expect(page.getByText("データがありません。")).toBeVisible();
  await expect(page.getByTestId("glossary-terms-list-skeleton")).toHaveCount(0);
  releaseReload();
  await reloadClick;
  await expect(page.getByRole("alert")).toContainText("サーバー読込エラー");
  await expectNoHorizontalScroll(page);
});

test("data preparation read results use the shared detail skeleton without stale or empty overlap", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });

  const objectsGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/objects?*", async (route) => {
    await objectsGate.promise;
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: [
        { name: "INVOICES", owner: "APP", object_type: "table", row_count: 2, comment: "請求情報" },
        { name: "V_EMP_DEPT", owner: "APP", object_type: "view", row_count: null, comment: "社員と部署" },
      ],
      total: 2,
      table_count: 1,
      view_count: 1,
      next_cursor: null,
      refreshed_at: schemaCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });

  const firstPreviewGate = createRequestGate();
  const failedPreviewGate = createRequestGate();
  let previewAttempts = 0;
  const previewResponse = {
    runtime: "deterministic",
    sql: 'SELECT * FROM "INVOICES" FETCH FIRST 100 ROWS ONLY',
    results: {
      columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      rows: [{ CUSTOMER_NAME: "スケルトン確認顧客", TOTAL_AMOUNT: 1200000 }],
      total: 1,
    },
    warnings: [],
  };
  await page.route("**/api/nl2sql/db-admin/preview-data", async (route) => {
    previewAttempts += 1;
    if (previewAttempts === 1) {
      await firstPreviewGate.promise;
      return fulfillJson(route, previewResponse);
    }
    if (previewAttempts === 3) {
      await failedPreviewGate.promise;
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "表示結果の取得に失敗しました" }),
      });
    }
    return fulfillJson(route, previewResponse);
  });

  await page.goto("/data-management");
  await expect(page.getByTestId("data-preview-object-list-skeleton")).toBeVisible();
  await expect(page.getByTestId("data-preview-object-list")).toHaveCount(0);
  await expect(page.getByText("対象テーブル/ビューがありません")).toHaveCount(0);
  objectsGate.release();
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();

  const resultsPanel = page.locator("#data-management-panel-preview").locator("section").filter({
    has: page.getByRole("heading", { name: "表示結果" }),
  });
  const invoicesPreviewAction = page.getByRole("button", { name: "INVOICES のデータを表示" });
  const viewPreviewAction = page.getByRole("button", { name: "V_EMP_DEPT のデータを表示" });
  await invoicesPreviewAction.click();
  const dataSkeleton = page.getByTestId("data-preview-results-detail-skeleton");
  await expect(dataSkeleton).toBeVisible();
  await expect(invoicesPreviewAction).toBeDisabled();
  await expect(viewPreviewAction).toBeDisabled();
  await expect(page.getByTestId("data-preview-object-list").locator("svg.animate-spin")).toHaveCount(1);
  await expect(resultsPanel.getByText("データ未表示")).toHaveCount(0);
  await expect(resultsPanel.getByText("スケルトン確認顧客")).toHaveCount(0);

  const dataSkeletonShape = await dataSkeleton
    .getByTestId("db-management-skeleton-block")
    .evaluateAll((elements) =>
      elements.map((element) => {
        const style = window.getComputedStyle(element);
        return {
          height: Math.round(Number.parseFloat(style.height)),
          backgroundColor: style.backgroundColor,
          borderRadius: style.borderRadius,
        };
      })
    );
  expect(dataSkeletonShape.map((block) => block.height)).toEqual([64, 40, 288]);
  await expect(dataSkeleton.getByTestId("db-management-skeleton-block").first()).toHaveCSS(
    "animation-name",
    "none"
  );
  expect(
    await dataSkeleton
      .getByTestId("db-management-skeleton-block")
      .evaluateAll((elements) => elements.every((element) => !element.hasAttribute("tabindex")))
  ).toBe(true);

  const viewSelect = page.getByRole("button", { name: "V_EMP_DEPT を選択" });
  await viewSelect.click();
  await expect(viewSelect).toHaveAttribute("aria-current", "true");
  await expect(dataSkeleton).toHaveCount(0);
  firstPreviewGate.release();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toHaveCount(0);

  await invoicesPreviewAction.click();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  await invoicesPreviewAction.click();
  await expect(dataSkeleton).toBeVisible();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toHaveCount(0);
  failedPreviewGate.release();
  await expect(resultsPanel.getByRole("alert")).toContainText("表示結果の取得に失敗しました");
  await expect(dataSkeleton).toHaveCount(0);
  await resultsPanel.getByRole("button", { name: "再試行" }).click();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 900 });
  const tableDetailGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES*", async (route) => {
    await tableDetailGate.promise;
    return fulfillJson(route, {
      name: "INVOICES",
      owner: "APP",
      object_type: "table",
      row_count: 2,
      comment: "請求情報",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER)',
      warnings: [],
    });
  });
  await page.goto("/table-management");
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "INVOICES を表示" }).click();
  const tableSkeleton = page.getByTestId("table-management-detail-skeleton");
  await expect(tableSkeleton).toBeVisible();
  const tableSkeletonShape = await tableSkeleton
    .getByTestId("db-management-skeleton-block")
    .evaluateAll((elements) =>
      elements.map((element) => {
        const style = window.getComputedStyle(element);
        return {
          height: Math.round(Number.parseFloat(style.height)),
          backgroundColor: style.backgroundColor,
          borderRadius: style.borderRadius,
        };
      })
    );
  expect(tableSkeletonShape).toEqual(dataSkeletonShape);
  tableDetailGate.release();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toHaveCount(0);
});

test("JOIN WHERE and metadata read result branches replace their result areas with shared skeletons", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  const commentDetailGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES*", async (route) => {
    await commentDetailGate.promise;
    return fulfillJson(route, {
      name: "INVOICES",
      owner: "APP",
      object_type: "table",
      row_count: 2,
      comment: "請求情報",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER)',
      warnings: [],
    });
  });

  await page.goto("/comment-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  const commentInputSkeleton = page.getByTestId("comment-management-input-detail-skeleton");
  await expect(commentInputSkeleton).toBeVisible();
  await expect(page.locator("#comment-management-panel-input").getByText("対象情報が未取得です")).toHaveCount(0);
  commentDetailGate.release();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);

  const commentGenerateGate = createRequestGate();
  await page.route("**/api/nl2sql/metadata-samples", async (route) => {
    await commentGenerateGate.promise;
    return fulfillJson(route, {
      sample_text: "OBJECT: INVOICES\nCUSTOMER_NAME: 青山商事",
      sample_count: 1,
      runtime: "oracle",
      warnings: [],
    });
  });
  await page.getByRole("button", { name: "SQL 生成" }).click();
  const commentExecuteSkeleton = page.getByTestId("comment-management-execute-result-detail-skeleton");
  await expect(commentExecuteSkeleton).toBeVisible();
  await expect(page.locator("#comment-management-panel-execute").getByText("生成済み SQL がありません")).toHaveCount(0);
  commentGenerateGate.release();
  await expect(page.locator("#comment-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/COMMENT ON COLUMN/);

  await page.goto("/annotation-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);
  const annotationGenerateGate = createRequestGate();
  await page.route("**/api/nl2sql/annotations/generate-sql", async (route) => {
    await annotationGenerateGate.promise;
    return fulfillJson(route, {
      sql: "ALTER TABLE \"INVOICES\" MODIFY (\"TOTAL_AMOUNT\" ANNOTATIONS (ADD IF NOT EXISTS UI_Display '税込請求金額'));",
      source: "deterministic",
      warnings: [],
      timing,
    });
  });
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.getByTestId("annotation-management-execute-result-detail-skeleton")).toBeVisible();
  annotationGenerateGate.release();
  await expect(page.locator("#annotation-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/ALTER TABLE/);

  await page.goto("/view-management");
  await expect(page.getByTestId("view-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "V_EMP_DEPT を表示" }).click();
  await clickPageHeaderAction(page, "view-management-actions", "JOIN/WHERE 条件抽出");
  const joinWhereGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/extract-join-where", async (route) => {
    await joinWhereGate.promise;
    return fulfillJson(route, {
      join_text: "[INNER] E(EMPLOYEE).DEPARTMENT_ID = D(DEPARTMENT).DEPARTMENT_ID",
      where_text: "None",
      source: "deterministic",
      warnings: [],
      prompt_profile: "join_where_strict",
      structure_markdown: "",
    });
  });
  await page.getByRole("button", { name: "AI で抽出" }).click();
  await expect(page.getByTestId("view-join-where-result-detail-skeleton")).toBeVisible();
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveCount(0);
  joinWhereGate.release();
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE/);
  await expectNoHorizontalScroll(page);
});

test("synthetic data profile tables status and results show the matching shared skeleton preset", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  const profilesGate = createRequestGate();
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true",
    async (route) => {
      await profilesGate.promise;
      return fulfillJson(route, {
        runtime: "deterministic",
        profiles: [
          {
            name: "NL2SQL_DEFAULT_PROFILE",
            status: "ready",
            owner: "APP",
            created_at: "2026-06-21T10:00:00.000Z",
            object_list: [],
            attributes: {},
          },
        ],
        warnings: [],
      });
    }
  );

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  await expect(page.getByTestId("data-synthetic-profiles-detail-skeleton")).toBeVisible();
  await expect(page.getByRole("heading", { name: "対象選択" })).toHaveCount(0);
  profilesGate.release();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await expect(syntheticPanel.getByRole("heading", { name: "対象選択" })).toBeVisible();

  const tablesGate = createRequestGate();
  await page.route("**/api/nl2sql/select-ai/db-profiles/NL2SQL_DEFAULT_PROFILE", async (route) => {
    await tablesGate.promise;
    return fulfillJson(route, {
      runtime: "deterministic",
      profile: {
        name: "NL2SQL_DEFAULT_PROFILE",
        status: "ready",
        owner: "APP",
        created_at: "2026-06-21T10:00:00.000Z",
        object_list: [],
        attributes: {
          profile_attributes: {
            object_list: [{ owner: "APP", name: "INVOICES" }],
          },
        },
      },
      warnings: [],
    });
  });
  await syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" }).click();
  await expect(page.getByTestId("data-synthetic-tables-list-skeleton")).toBeVisible();
  await expect(syntheticPanel.getByText("対象テーブルが未取得です")).toHaveCount(0);
  tablesGate.release();
  await expect(syntheticPanel.getByLabel("INVOICES を選択")).toBeVisible();
  await syntheticPanel.getByLabel("INVOICES を選択").check();
  await syntheticPanel.getByLabel("実行確認語").fill("INVOICES");
  await syntheticPanel.getByRole("button", { name: "生成開始" }).click();
  await expect(syntheticPanel.getByText("operation-001").first()).toBeVisible();

  const statusGate = createRequestGate();
  await page.route("**/api/nl2sql/synthetic-data/operations/**", async (route) => {
    await statusGate.promise;
    return fulfillJson(route, {
      operation_id: "operation-001",
      status: "requires_oracle",
      message: "Oracle 接続環境で operation status を確認してください。",
      runtime: "deterministic",
      result: {},
      warnings: [],
    });
  });
  await syntheticPanel.getByRole("button", { name: "ステータスを更新" }).click();
  await expect(page.getByTestId("data-synthetic-status-compact-skeleton")).toBeVisible();
  await expect(syntheticPanel.getByLabel("ステータス", { exact: true })).toHaveCount(0);
  statusGate.release();
  await expect(syntheticPanel.getByText("requires_oracle")).toBeVisible();

  const resultsGate = createRequestGate();
  await page.route("**/api/nl2sql/synthetic-data/results**", async (route) => {
    await resultsGate.promise;
    return fulfillJson(route, {
      runtime: "deterministic",
      table_name: "INVOICES",
      results: {
        columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
        rows: [{ CUSTOMER_NAME: "synthetic-loading-customer", TOTAL_AMOUNT: 12345 }],
        total: 1,
      },
      warnings: [],
    });
  });
  await syntheticPanel.getByRole("button", { name: "データを表示" }).click();
  await expect(page.getByTestId("data-synthetic-results-detail-skeleton")).toBeVisible();
  await expect(syntheticPanel.getByText("表示するデータはまだありません")).toHaveCount(0);
  resultsGate.release();
  await expect(syntheticPanel.getByRole("cell", { name: "synthetic-loading-customer" })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("glossary and global rules use an initial list skeleton and preserve fetched rows on background reload", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.unroute("**/api/nl2sql/legacy-learning-material");

  const glossaryInitialGate = createRequestGate();
  const glossaryReloadGate = createRequestGate();
  let glossaryRequests = 0;
  await page.route("**/api/nl2sql/legacy-learning-material", async (route) => {
    glossaryRequests += 1;
    if (glossaryRequests === 1) await glossaryInitialGate.promise;
    if (glossaryRequests === 2) await glossaryReloadGate.promise;
    return fulfillJson(route, {
      glossary: { 売上: "INVOICES.TOTAL_AMOUNT" },
      rules: ["SELECT のみ"],
    });
  });

  await page.goto("/glossary-rules");
  await expect(page.getByTestId("glossary-terms-list-skeleton")).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview")).toHaveCount(0);
  await expect(page.getByText("データがありません。")).toHaveCount(0);
  glossaryInitialGate.release();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "売上" })).toBeVisible();

  await page.getByRole("button", { name: "表示を更新" }).click();
  await expect.poll(() => glossaryRequests).toBe(2);
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "売上" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-list-skeleton")).toHaveCount(0);
  glossaryReloadGate.release();
  await expect(page.getByRole("button", { name: "表示を更新" })).toBeEnabled();

  await page.unroute("**/api/nl2sql/legacy-learning-material");
  const rulesInitialGate = createRequestGate();
  const rulesReloadGate = createRequestGate();
  let rulesRequests = 0;
  await page.route("**/api/nl2sql/legacy-learning-material", async (route) => {
    rulesRequests += 1;
    if (rulesRequests === 1) await rulesInitialGate.promise;
    if (rulesRequests === 2) await rulesReloadGate.promise;
    return fulfillJson(route, {
      glossary: { 売上: "INVOICES.TOTAL_AMOUNT" },
      rules: ["SELECT のみ"],
    });
  });

  await page.goto("/global-rules");
  await expect(page.getByTestId("global-rules-list-skeleton")).toBeVisible();
  await expect(page.getByTestId("global-rules-preview")).toHaveCount(0);
  await expect(page.getByText("共通ルールがありません。")).toHaveCount(0);
  rulesInitialGate.release();
  await expect(page.getByTestId("global-rules-preview").getByText("SELECT のみ")).toBeVisible();

  await page.getByRole("button", { name: "表示を更新" }).click();
  await expect.poll(() => rulesRequests).toBe(2);
  await expect(page.getByTestId("global-rules-preview").getByText("SELECT のみ")).toBeVisible();
  await expect(page.getByTestId("global-rules-list-skeleton")).toHaveCount(0);
  rulesReloadGate.release();
  await expect(page.getByRole("button", { name: "表示を更新" })).toBeEnabled();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("sample data and data management run imported workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sample-data");
  await expect(page.getByText("検証用サンプルデータ管理")).toBeVisible();
  await expect(page.getByRole("heading", { name: "取り込み実行", exact: true })).toBeVisible();
  await expect(page.getByText("DEPARTMENT").first()).toBeVisible();
  await expect(page.getByLabel("実行する", { exact: true })).toHaveCount(0);

  const importPanel = page.locator("#sample-data-panel-import");
  const importConfirmationField = importPanel.getByTestId("execution-confirmation-field");
  await expect(importPanel.getByText("未入力", { exact: true })).toHaveCount(1);
  const importButton = page.getByRole("button", { name: "取り込み実行" }).last();
  await expect(importButton).toBeDisabled();
  await page.getByLabel("実行確認語").fill("SQL_ASSIST_SAMPLE");
  await expect(importConfirmationField.getByText("確認済み", { exact: true })).toBeVisible();
  await expect(importPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(importButton).toBeEnabled();
  await importButton.click();
  await expect.poll(() => api.samplePayload?.confirmation).toBe("SQL_ASSIST_SAMPLE");
  await expect(page.getByTestId("sample-data-imported-count")).toHaveText("5");
  expect(api.samplePayload?.confirmation).toBe("SQL_ASSIST_SAMPLE");

  await page.getByRole("tab", { name: "削除実行" }).click();
  const deletePanel = page.locator("#sample-data-panel-delete");
  await expect(deletePanel.getByTestId("execution-confirmation-field").getByText("確認済み", { exact: true })).toBeVisible();
  await expect(deletePanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  const deleteButton = page.getByRole("button", { name: "削除実行" }).last();
  await expect(deleteButton).toBeEnabled();
  await deleteButton.click();
  await expect.poll(() => api.samplePayload?.confirmation).toBe("SQL_ASSIST_SAMPLE");
  await expect(page.getByTestId("sample-data-imported-count")).toHaveText("0");
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/sample-data");
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  api.sampleImportError = true;
  await page.getByLabel("実行確認語").fill("SQL_ASSIST_SAMPLE");
  await page.getByRole("button", { name: "取り込み実行" }).last().click();
  await expect(page.getByText("実行エラー").first()).toBeVisible();
  await expect(page.getByText("エラー概要")).toBeVisible();
  await expect(page.getByText("原因候補")).toBeVisible();
  await expect(page.getByText("次の対応")).toBeVisible();
  await expect(page.getByText("ORA-00922: missing or invalid option", { exact: true })).toBeVisible();
  api.sampleImportError = false;

  await page.goto("/data-management");
  const dataPreviewTab = page.getByRole("tab", { name: "テーブル・ビューデータの表示" });
  const dataCsvTab = page.getByRole("tab", { name: "CSV アップロード(既存テーブル)" });
  const dataSqlTab = page.getByRole("tab", { name: "SQL 一括実行" });
  const dataSyntheticTab = page.getByRole("tab", { name: "合成データ生成" });
  await expect(dataPreviewTab).toHaveAttribute("aria-selected", "true");
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "false");
  await expect(page.getByRole("tab", { name: "Synthetic NL2SQL ケース" })).toHaveCount(0);
  await dataPreviewTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(dataPreviewTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "テーブル・ビューデータの表示" })).toBeVisible();
  await expect(page.getByText("Excel/CSV 取込(新規テーブル)", { exact: true })).toHaveCount(0);
  await expect(page.getByText("サンプルデータ管理", { exact: true })).toHaveCount(0);

  const dataPreviewPanel = page.locator("#data-management-panel-preview");
  await expect(dataPreviewPanel.getByText("全4件")).toBeVisible();
  await expect(dataPreviewPanel.getByText("テーブル3")).toBeVisible();
  await expect(dataPreviewPanel.getByText("ビュー1")).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: / のデータを表示$/ })).toHaveCount(4);
  await expect(dataPreviewPanel.getByRole("button", { name: "データを表示", exact: true })).toHaveCount(0);
  await dataPreviewPanel.getByLabel("対象検索").fill("EMP");
  await expect(dataPreviewPanel.getByRole("button", { name: "V_EMP_DEPT を選択" })).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "INVOICES を選択" })).toHaveCount(0);
  await dataPreviewPanel.getByLabel("対象検索").fill("ZZZ");
  await expect(dataPreviewPanel.getByText("条件に一致する対象がありません")).toBeVisible();
  await dataPreviewPanel.getByLabel("対象検索").fill("");
  await dataPreviewPanel.getByLabel("種別フィルタ").selectOption("view");
  await expect(dataPreviewPanel.getByRole("button", { name: "V_EMP_DEPT を選択" })).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "INVOICES を選択" })).toHaveCount(0);
  await dataPreviewPanel.getByLabel("行数フィルタ").selectOption("unknown_rows");
  const viewPreviewTarget = dataPreviewPanel.getByRole("button", { name: "V_EMP_DEPT を選択" });
  await viewPreviewTarget.focus();
  await page.keyboard.press("Enter");
  await expect(viewPreviewTarget).toHaveAttribute("aria-current", "true");

  await dataPreviewPanel.getByLabel("取得件数上限").fill("75");
  await dataPreviewPanel.getByLabel("WHERE 条件(任意)").fill("STATUS = 'A'");
  const viewPreviewAction = dataPreviewPanel.getByRole("button", { name: "V_EMP_DEPT のデータを表示" });
  await viewPreviewAction.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("1-10 / 12 件");
  await expect(page.getByRole("cell", { name: "顧客11" })).toHaveCount(0);
  await page.getByRole("button", { name: "次へ" }).click();
  await expect(page.getByRole("cell", { name: "顧客11" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("11-12 / 12 件");
  await expect.poll(() => api.previewDataPayload?.object_name).toBe("V_EMP_DEPT");
  expect(api.previewDataPayload?.limit).toBe(75);
  expect(api.previewDataPayload?.where_clause).toBe("STATUS = 'A'");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "XLSX ダウンロード" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("v_emp_dept_preview.xlsx");
  await expect.poll(() => api.previewDataExportPayload?.object_name).toBe("V_EMP_DEPT");
  expect(api.previewDataExportPayload?.limit).toBe(75);
  expect(api.previewDataExportPayload?.where_clause).toBe("STATUS = 'A'");
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/data-management");
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/data-management");

  await dataCsvTab.click();
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#data-management-panel-csv")).toBeVisible();
  await page.getByLabel("CSV 選択").setInputFiles({
    name: "invoices.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("CUSTOMER_NAME,TOTAL_AMOUNT,UNKNOWN_COLUMN\n青山商事,1200000,x\n"),
  });
  await expect(page.getByText("選択中: invoices.csv")).toBeVisible();
  const csvUploadButton = page.getByRole("button", { name: "アップロード実行" });
  await expect(csvUploadButton).toBeDisabled();
  await page.locator("#data-management-panel-csv").getByLabel("実行確認語").fill("INVOICES");
  await expect(page.locator("#data-management-panel-csv").getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(csvUploadButton).toBeEnabled();
  await csvUploadButton.click();
  await expect(page.getByText("UNKNOWN_COLUMN", { exact: false }).first()).toBeVisible();
  await expect.poll(() => api.csvUploadPayload?.table_name).toBe("INVOICES");
  expect(api.csvUploadPayload?.mode).toBe("insert");
  expect(api.csvUploadPayload?.confirmation).toBe("INVOICES");

  await dataSqlTab.click();
  await expect(dataSqlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#data-management-panel-sql")).toBeVisible();
  await page.getByRole("button", { name: "INSERT(単一行)" }).click();
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/^INSERT INTO TABLE_NAME/);
  await expect(page.locator("#data-management-panel-sql").getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(page.locator("#data-management-panel-sql").getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(page.locator("#data-management-panel-sql").getByText("Oracle への SQL 実行")).toBeVisible();
  const dataSqlExecuteButton = page.locator("#data-management-panel-sql").getByRole("button", { name: "SQL 実行" });
  await expect(dataSqlExecuteButton).toBeDisabled();
  await page.locator("#data-management-panel-sql").getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(page.locator("#data-management-panel-sql").getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(dataSqlExecuteButton).toBeEnabled();
  await dataSqlExecuteButton.click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("data_dml");
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await dataSyntheticTab.click();
  await expect(dataSyntheticTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#data-management-panel-synthetic")).toBeVisible();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await expect(syntheticPanel.getByText("Synthetic NL2SQL ケース")).toHaveCount(0);
  await expect(syntheticPanel.getByRole("button", { name: "ケース生成" })).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("Oracle に synthetic data を生成")).toHaveCount(0);
  await expect(syntheticPanel.getByText("Oracle への synthetic data 生成")).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "対象選択" })).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "進捗と状態" })).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "結果確認" })).toBeVisible();
  const syntheticGenerateButton = syntheticPanel.getByRole("button", { name: "生成開始" });
  await expect(syntheticGenerateButton).toBeDisabled();
  const syntheticProfileSelect = syntheticPanel.getByLabel("Profile");
  await expect(syntheticProfileSelect).toHaveValue("NL2SQL_DEFAULT_PROFILE");
  await expect(syntheticProfileSelect.locator("option")).toHaveCount(1);
  await expect(syntheticProfileSelect.locator("option", { hasText: "NL2SQL_MANUAL_AGENT_V2_PROFILE" })).toHaveCount(0);
  await syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" }).click();
  await expect(syntheticPanel.getByLabel("INVOICES を選択")).toBeVisible();
  await expect(syntheticPanel.getByLabel("PAYMENTS を選択")).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("AUDIT_LOG を選択")).toHaveCount(0);
  await syntheticPanel.getByLabel("INVOICES を選択").check();
  await expect(syntheticPanel.getByText("選択 1 件")).toBeVisible();
  await expect(syntheticGenerateButton).toBeDisabled();
  await syntheticPanel.getByLabel("実行確認語").fill("INVOICES");
  await expect(syntheticPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(syntheticGenerateButton).toBeEnabled();
  await syntheticGenerateButton.click();
  await expect(page.getByText("DBMS_CLOUD_AI synthetic data generation を開始しました。")).toBeVisible();
  await expect(syntheticPanel.getByText("operation-001").first()).toBeVisible();
  await expect(syntheticPanel.getByTestId("synthetic-result-table-select")).toHaveValue("INVOICES");
  await expect(syntheticPanel.getByRole("option", { name: "AUDIT_LOG" })).toHaveCount(0);
  await syntheticPanel.getByRole("button", { name: "ステータスを更新" }).click();
  await expect(syntheticPanel.getByText("requires_oracle")).toBeVisible();
  await syntheticPanel.getByRole("button", { name: "データを表示" }).click();
  await expect(syntheticPanel.getByRole("cell", { name: "synthetic-customer" })).toBeVisible();
  await expect.poll(() => api.syntheticDataPayload?.table_name).toBe("INVOICES");
  expect(api.syntheticDataPayload?.confirmation).toBe("INVOICES");
  expect(api.syntheticDataPayload?.profile_name).toBe("NL2SQL_DEFAULT_PROFILE");
  expect(api.syntheticDataPayload?.object_list).toEqual([]);
  expect(api.syntheticDataPayload?.rows_per_table).toBe(1);
  expect(api.syntheticDataPayload?.sample_rows).toBe(5);
  expect(api.syntheticDataPayload?.use_comments).toBe(true);
  await expectNoHorizontalScroll(page);
});

test("data management avoids full catalog and tracks schema refresh jobs", async ({ page }) => {
  await mockNl2SqlApi(page);
  let catalogRequests = 0;
  let profileRequests = 0;
  let jobPolls = 0;
  let submitErrorJob = false;
  page.on("request", (request) => {
    const url = request.url();
    if (url.endsWith("/api/schema/catalog")) catalogRequests += 1;
    if (url.includes("/api/nl2sql/select-ai/db-profiles")) profileRequests += 1;
  });
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) => {
    catalogRequests += 1;
    return route.fulfill({ status: 500, body: "full catalog must not be called" });
  });
  await page.route("**/api/schema/refresh-jobs", (route) =>
    fulfillJson(route, {
      job_id: submitErrorJob ? "data-schema-job-error" : "data-schema-job",
      status: "pending",
      phase: "queued",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: null,
      error_code: "",
    })
  );
  await page.route("**/api/schema/refresh-jobs/data-schema-job", (route) => {
    jobPolls += 1;
    const done = jobPolls >= 2;
    return fulfillJson(route, {
      job_id: "data-schema-job",
      status: done ? "done" : "running",
      phase: done ? "done" : "fetching",
      processed_objects: done ? 4 : 2,
      total_objects: 4,
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: done ? 4 : 0,
      changed_objects: done ? 1 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : null,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/data-schema-job-error", (route) =>
    fulfillJson(route, {
      job_id: "data-schema-job-error",
      status: "error",
      phase: "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: null,
      error_code: "schema_refresh_failed",
    })
  );

  await page.goto("/data-management");
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();
  expect(catalogRequests).toBe(0);
  expect(profileRequests).toBe(0);

  await clickPageHeaderAction(
    page,
    "data-management-actions",
    "表示を更新"
  );
  await expect(page.getByText("最新の状態に更新しました。")).toBeVisible();
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();
  expect(catalogRequests).toBe(0);

  await clickPageHeaderAction(
    page,
    "data-management-actions",
    "DB 構造を再取得"
  );
  await expect(page.getByText("DB 構造の再取得が完了しました。")).toBeVisible();
  expect(jobPolls).toBeGreaterThanOrEqual(2);
  expect(catalogRequests).toBe(0);

  submitErrorJob = true;
  await clickPageHeaderAction(
    page,
    "data-management-actions",
    "DB 構造を再取得"
  );
  await expect(page.getByText("DB 構造の再取得に失敗しました。再試行してください。")).toBeVisible();

  await page.getByRole("tab", { name: "合成データ生成" }).click();
  await expect.poll(() => profileRequests).toBeGreaterThan(0);
  await expectNoHorizontalScroll(page);
});

test("data management stops an 8 second object timeout and retries in place", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/db-admin/objects?*");
  let attempts = 0;
  let allowSuccess = false;
  await page.route("**/api/nl2sql/db-admin/objects?*", async (route) => {
    attempts += 1;
    if (!allowSuccess) {
      await new Promise((resolve) => setTimeout(resolve, 8_500));
    }
    try {
      await fulfillJson(route, {
        runtime: "deterministic",
        owner: "APP",
        items: [
          { name: "INVOICES", owner: "APP", object_type: "table", row_count: 2, comment: "請求情報" },
        ],
        total: 1,
        table_count: 1,
        view_count: 0,
        next_cursor: null,
        refreshed_at: schemaCatalog.refreshed_at,
        catalog_version: 1,
        warnings: [],
      });
    } catch {
      // timeout で browser が最初の route を破棄した場合は retry request を待つ。
    }
  });

  await page.goto("/data-management");
  await expect(page.getByLabel("テーブル・ビュー一覧を読み込んでいます")).toBeVisible();
  await expect(page.getByText(/一覧の更新が8秒以内に完了しませんでした/)).toBeVisible({
    timeout: 12_000,
  });
  allowSuccess = true;
  const retry = page.getByRole("button", { name: "再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "INVOICES を選択" })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("system objects stay hidden across database object management pages", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", (route) => {
    const objectType = new URL(route.request().url()).searchParams.get("type") ?? "";
    const allItems = [
      { owner: "APP", object_name: "INVOICES", object_type: "TABLE", logical_name: "請求", comment: "請求情報", row_count: 2, column_count: 2, last_ddl_at: "" },
      { owner: "APP", object_name: "DBTOOLS$EXECUTION_HISTORY", object_type: "TABLE", logical_name: "内部履歴", comment: "内部履歴", row_count: 4, column_count: 2, last_ddl_at: "" },
      { owner: "APP", object_name: "SYS#AUDIT_VIEW", object_type: "VIEW", logical_name: "内部監査", comment: "内部監査", row_count: null, column_count: 2, last_ddl_at: "" },
    ];
    const items = allItems.filter(
      (item) => !objectType || item.object_type === objectType.toUpperCase()
    );
    return fulfillJson(route, {
      items,
      next_cursor: null,
      total: items.length,
      table_count: items.filter((item) => item.object_type === "TABLE").length,
      view_count: items.filter((item) => item.object_type === "VIEW").length,
      catalog_version: 1,
    });
  });
  const hiddenNames = ["DBTOOLS$EXECUTION_HISTORY", "SYS#AUDIT_VIEW"];
  const pages = [
    { path: "/data-management", ready: "data-preview-object-list" },
    { path: "/table-management", ready: "table-management-grid" },
    { path: "/view-management", ready: "view-management-grid" },
    { path: "/comment-management", ready: "comment-management-steps" },
    { path: "/annotation-management", ready: "annotation-management-steps" },
    { path: "/profiles?profile=new", ready: "profile-allowed-object-list" },
  ];

  for (const target of pages) {
    await page.goto(target.path);
    await expect(page.getByTestId(target.ready)).toBeVisible();
    for (const hiddenName of hiddenNames) {
      await expect(page.getByText(hiddenName, { exact: true })).toHaveCount(0);
    }
  }

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/data-management");
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();
  for (const hiddenName of hiddenNames) {
    await expect(page.getByText(hiddenName, { exact: true })).toHaveCount(0);
  }
  await expectNoHorizontalScroll(page);
});

test("table and view management pages run guarded DDL and AI workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/table-management");
  // 一覧が既定。作成はアクションボタンで開き、一覧に戻るで戻る。
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await clickPageHeaderAction(page, "table-management-actions", "テーブル作成");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByText("テーブル数", { exact: true })).toHaveCount(0);
  await expect(page.getByText("取得元", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/DB 構造の最終取得:/)).toBeVisible();
  const tablePageActions = page.getByTestId("table-management-actions");
  if ((page.viewportSize()?.width ?? 0) < 640) {
    await tablePageActions.getByRole("button", { name: "その他の操作" }).click();
    await expect(page.getByRole("menuitem", { name: "表示を更新" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeVisible();
    await page.keyboard.press("Escape");
  } else {
    await expect(tablePageActions.getByRole("button", { name: "表示を更新" })).toBeVisible();
    await expect(tablePageActions.getByRole("button", { name: "DB 構造を再取得" })).toBeVisible();
  }
  await page.getByLabel("検索").fill("請求");
  await expect(page.getByTestId("table-management-grid").getByText("INVOICES")).toBeVisible();
  await page.getByRole("button", { name: "INVOICES を表示" }).click();
  const columnsPanel = page.getByTestId("db-admin-detail-columns");
  // 論理名(業務名)とコメント(生カラムコメント)は別列で表示される。
  await expect(columnsPanel.getByRole("columnheader", { name: "論理名" })).toBeVisible();
  await expect(columnsPanel.getByRole("columnheader", { name: "コメント" })).toBeVisible();
  await expect(columnsPanel.getByRole("cell", { name: "取引先名" }).first()).toBeVisible();
  await expect(columnsPanel.getByRole("cell", { name: "税込請求金額" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByText("2 列")).toBeVisible();
  await expect(page.getByRole("button", { name: /XLSX ダウンロード/ })).toBeVisible();
  const columnsDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /XLSX ダウンロード/ }).click();
  const columnsDownload = await columnsDownloadPromise;
  expect(columnsDownload.suggestedFilename()).toBe("invoices_columns.xlsx");
  await expect
    .poll(() =>
      page.getByTestId("db-admin-detail-columns").evaluate((wrapper) => {
        const table = wrapper.querySelector("table");
        if (!table) return false;
        return table.getBoundingClientRect().width >= wrapper.clientWidth - 1;
      })
    )
    .toBeTruthy();

  const columnsTab = page.getByRole("tab", { name: "列情報" });
  const ddlTab = page.getByRole("tab", { name: "DDL" });
  await columnsTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(ddlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(columnsTab).toHaveAttribute("aria-selected", "true");
  await ddlTab.click();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toBeVisible();
  const ddlDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "SQL 出力" }).click();
  const ddlDownload = await ddlDownloadPromise;
  expect(ddlDownload.suggestedFilename()).toBe("invoices_ddl.sql");

  await page.getByRole("button", { name: "削除" }).first().click();
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" })).toBeVisible();
  await page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByLabel("実行確認語").fill("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByText("確認済み", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropTablePayload?.confirmation).toBe("INVOICES");
  expect(api.dropTablePayload?.confirmation).toBe("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" })).toHaveCount(0);

  await clickPageHeaderAction(page, "table-management-actions", "テーブル作成");
  const createPanel = page.locator("#table-management-panel-create");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toHaveCount(0);
  await expect(createPanel).toBeVisible();
  await createPanel.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill("CREATE TABLE T1 (ID NUMBER)");
  await expect(createPanel.getByText("ADMIN_EXECUTE を入力すると実行できます。")).toBeVisible();
  await expect(createPanel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(createPanel.getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(createPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(createPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await createPanel.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("table_ddl");
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );
  const importPanel = page.locator("#table-management-panel-import");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toHaveCount(0);
  await expect(importPanel).toBeVisible();
  const importExecuteButton = importPanel.getByRole("button", { name: "取込を実行" });
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(importPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(importExecuteButton).toBeDisabled();
  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  const importFileClearButton = importPanel.getByRole("button", { name: "取込ファイルをクリア" });
  await expect(importFileClearButton).toBeDisabled();
  await importPanel.getByLabel("CSV/XLSX 選択", { exact: true }).setInputFiles({
    name: "orders.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("ORDER_ID,ORDER_NAME\n1,青山商事\n"),
  });
  await expect(importPanel.getByText("選択中: orders.csv")).toBeVisible();
  await expect(importFileClearButton).toBeEnabled();
  await importFileClearButton.click();
  await expect(importPanel.getByText("CSV / XLSX ファイルを選択")).toBeVisible();
  await expect(importFileClearButton).toBeDisabled();
  await importPanel.getByLabel("CSV/XLSX 選択").setInputFiles({
    name: "orders.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("ORDER_ID,ORDER_NAME\n1,青山商事\n"),
  });
  const importExecuteButtonBox = await importExecuteButton.boundingBox();
  const importConfirmationBox = await importPanel.getByLabel("実行確認語").boundingBox();
  expect(importExecuteButtonBox).not.toBeNull();
  expect(importConfirmationBox).not.toBeNull();
  // 取込を実行ボタンは確認ゲート(ExecutionConfirmationField)の actions スロット、
  // すなわち実行確認語 input の下に置く(CSV/Sample 取込パネルと統一)。
  expect(importExecuteButtonBox!.y).toBeGreaterThan(importConfirmationBox!.y);
  await expect(importPanel.getByRole("button", { name: "確認語に表名を入れる" })).toHaveCount(0);
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await expect(importPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(importExecuteButton).toBeEnabled();
  await importExecuteButton.click();
  await expect(importPanel.getByText("IMPORTED_ORDERS", { exact: true })).toBeVisible();
  await expect(importPanel.getByTestId("table-import-steps").getByText("実行確認")).toBeVisible();
  await expect.poll(() => api.importTabularPayload?.table_name).toBe("IMPORTED_ORDERS");
  expect(api.importTabularPayload?.mode).toBe("create");
  expect(api.importTabularPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toHaveCount(0);
  await page.getByRole("tab", { name: "DDL" }).click();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/comment-management");
  await expect(page.getByRole("heading", { name: "コメント管理" })).toBeVisible();
  await expect(page.getByTestId("comment-management-steps")).toBeVisible();
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);
  await page.getByLabel("サンプル件数").fill("10");
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect.poll(() => api.metadataSamplesPayload?.sample_limit).toBe(10);
  await expect.poll(() => api.commentGeneratePayload?.sample_text).toContain(
    "CUSTOMER_NAME: 青山商事, 鈴木商店"
  );
  const commentExecutePanel = page.locator("#comment-management-panel-execute");
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /COMMENT ON COLUMN "INVOICES"."TOTAL_AMOUNT" IS '税込請求金額';/
  );
  await expect(commentExecutePanel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(commentExecutePanel.getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(commentExecutePanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await commentExecutePanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(commentExecutePanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await commentExecutePanel.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("comment_sql");
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await page.goto("/annotation-management");
  await expect(page.getByRole("heading", { name: "アノテーション管理" })).toBeVisible();
  await expect(page.getByTestId("annotation-management-steps")).toBeVisible();
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  const annotationExecutePanel = page.locator("#annotation-management-panel-execute");
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /ALTER TABLE "INVOICES" MODIFY/
  );
  await expect(annotationExecutePanel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(annotationExecutePanel.getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(annotationExecutePanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await annotationExecutePanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(annotationExecutePanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await annotationExecutePanel.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("annotation_sql");
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");
  await expectNoHorizontalScroll(page);

  await page.goto("/view-management");
  await expect(page.getByRole("heading", { name: "ビュー一覧と詳細" })).toBeVisible();
  await expect(page.getByText("ビュー数", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("view-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "V_EMP_DEPT を表示" }).click();
  const viewColumnsTab = page.getByRole("tab", { name: "列情報" });
  const viewDdlTab = page.getByRole("tab", { name: "DDL" });
  await expect(viewColumnsTab).toHaveAttribute("aria-selected", "true");
  await viewColumnsTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(viewDdlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText('CREATE OR REPLACE VIEW "V_EMP_DEPT"')).toBeVisible();

  await clickPageHeaderAction(page, "view-management-actions", "JOIN/WHERE 条件抽出");
  const stepIndicatorBox = await page.getByTestId("view-join-where-steps").boundingBox();
  const selectedViewBox = await page.getByTestId("view-join-where-selected-view").boundingBox();
  const promptProfileBox = await page.getByText("提示詞プロファイル").boundingBox();
  expect(stepIndicatorBox).not.toBeNull();
  expect(selectedViewBox).not.toBeNull();
  expect(promptProfileBox).not.toBeNull();
  expect(stepIndicatorBox!.y).toBeLessThan(promptProfileBox!.y);
  expect(selectedViewBox!.y).toBeLessThan(promptProfileBox!.y);
  const strictPrompt = page.getByRole("radio", { name: /JOIN\/WHERE 抽出/ });
  const structurePrompt = page.getByRole("radio", { name: /SQL構造解析/ });
  await expect(strictPrompt).toBeChecked();
  await strictPrompt.focus();
  await page.keyboard.press("ArrowRight");
  await expect(structurePrompt).toBeChecked();
  await strictPrompt.check();
  await page.getByRole("button", { name: "AI で抽出" }).click();
  await expect.poll(() => api.extractJoinWherePayload?.prompt_profile).toBe("join_where_strict");
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE.*DEPARTMENT/);
  await expect(page.getByLabel("抽出条件 (WHERE)")).toHaveValue("None");
  await structurePrompt.check();
  await page.getByRole("button", { name: "AI で抽出" }).click();
  await expect.poll(() => api.extractJoinWherePayload?.prompt_profile).toBe("sql_structure");
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE.*DEPARTMENT/);
  await expect(page.getByLabel("抽出条件 (WHERE)")).toHaveValue("EMPLOYEE(e).STATUS = 'A'");
  await page.getByText("SQL構造解析結果").click();
  await expect(page.getByText("## SQL構造分析")).toBeVisible();

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("view-management-grid").getByRole("button", { name: "削除" }).click();
  await expect(page.getByRole("dialog", { name: "DROP VIEW の確認" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Drop 実行" })).toBeDisabled();
  await page.getByLabel("実行確認語").fill("V_EMP_DEPT");
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropViewPayload?.confirmation).toBe("V_EMP_DEPT");

  await clickPageHeaderAction(page, "view-management-actions", "ビュー作成");
  await page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill("CREATE OR REPLACE VIEW V1 AS SELECT 1 FROM DUAL");
  await expect(page.getByText("Oracle への SQL 実行")).toBeVisible();
  await expect(page.getByRole("button", { name: "SQL 実行" })).toBeDisabled();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("view_ddl");
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("annotation management explains ORA-11548 before Oracle execution", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto("/annotation-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await page.getByRole("button", { name: "SQL 生成" }).click();

  const executePanel = page.locator("#annotation-management-panel-execute");
  await executePanel.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill(
    "ALTER TABLE INVOICES ANNOTATIONS " +
      "(ADD IF NOT EXISTS COMMENT '請求情報を管理するテーブル');"
  );
  await executePanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await executePanel.getByRole("button", { name: "SQL 実行" }).click();

  await expect.poll(() => api.statementsPayload?.policy).toBe("annotation_sql");
  await expect(executePanel.getByText("ブロック", { exact: true }).first()).toBeVisible();
  await expect(
    executePanel.getByText(/ANNOTATIONS 句の annotation 名が不足しているか/)
  ).toBeVisible();
  await expect(executePanel.getByText("説明用の annotation 名は UI_Display に変更してください。"))
    .toBeVisible();
  await expect(executePanel.getByText(/\"COMMENT\" のように二重引用符/)).toBeVisible();

  const details = executePanel.locator("details");
  const summary = details.getByText("詳細ログ");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(details).toHaveAttribute("open", "");
  await expect(details.getByText(/ORA-11548/)).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  await expect(executePanel.getByText("説明用の annotation 名は UI_Display に変更してください。"))
    .toBeVisible();
});

test("metadata sample limit zero omits samples and reports retrieval errors", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/comment-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await page.getByLabel("サンプル件数").fill("0");
  await page.getByRole("button", { name: "SQL 生成" }).click();

  await expect.poll(() => api.metadataSamplesPayload?.sample_limit).toBe(0);
  await expect.poll(() => api.commentGeneratePayload?.sample_text).toBe("");

  await page.route("**/api/nl2sql/metadata-samples", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "取得失敗" }),
    })
  );
  await page.goto("/annotation-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});

test("legacy model-learning URL opens profile learning and preserves asset refresh", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/profiles/default/oracle-sync-jobs", (route) =>
    fulfillJson(route, {
      job_id: "legacy-profile-sync",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "queued",
      phase: "queued",
      rebuild_agent_assets: true,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-23T00:00:00Z",
    })
  );
  await page.route("**/api/nl2sql/oracle-sync-jobs/legacy-profile-sync", (route) =>
    fulfillJson(route, {
      job_id: "legacy-profile-sync",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "succeeded",
      phase: "succeeded",
      rebuild_agent_assets: true,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-23T00:00:00Z",
      finished_at: "2026-07-23T00:00:01Z",
      agent_result: {
        engine: "select_ai_agent",
        refreshed: true,
        status: "ready",
        refreshed_at: "2026-07-23T00:00:01Z",
        profile_name: "既定プロファイル",
        team_name: "NL2SQL_DEFAULT_TEAM",
        warning: "",
        asset_names: {
          profile: "NL2SQL_DEFAULT_AGENT_PROFILE",
          tool: "NL2SQL_DEFAULT_TOOL",
          agent: "NL2SQL_DEFAULT_AGENT",
          task: "NL2SQL_DEFAULT_TASK",
          team: "NL2SQL_DEFAULT_TEAM",
        },
        engine_meta: { runtime: "mock" },
      },
    })
  );

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/settings/nl2sql-model");
  await expect(page).toHaveURL(/\/profiles\?profile=[^#]+#profile-learning$/);
  await expect(page.locator("#profile-learning")).toBeVisible();
  await expect(page.getByLabel("few-shot 例")).toBeVisible();
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await page.getByLabel("few-shot 例").fill("粗利を見たい => SELECT PROFIT FROM INVOICES");
  // ADMIN_EXECUTE ゲート + Agent アセット再構築チェックを付けて保存 1 クリックに集約。
  await page.getByRole("checkbox", { name: /Select AI Agent アセット/ }).check();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("プロファイルを保存しました。")).toBeVisible();
  expect(api.profilePatchPayload?.few_shot_examples).toEqual([
    { question: "粗利を見たい", sql: "SELECT PROFIT FROM INVOICES" },
  ]);
  await expect(page.getByText("NL2SQL_DEFAULT_AGENT_PROFILE")).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/settings/nl2sql-model");
  await expect(page).toHaveURL(/\/profiles\?profile=[^#]+#profile-learning$/);
  await expect(page.locator("#profile-learning")).toBeVisible();
  await expect(page.getByLabel("few-shot 例")).toBeVisible();
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("全 NL2SQL ルートのページヘッダーは 1440px / 375px で横方向に溢れない", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await mockNl2SqlApi(page);
  const routes = [
    "/table-management",
    "/view-management",
    "/data-management",
    "/sample-data",
    "/comment-management",
    "/annotation-management",
    "/query",
    "/profiles",
    "/ontology-build",
    "/glossary-rules",
    "/global-rules",
    "/sql-analysis",
    "/sql-to-question",
    "/direct-sql",
    "/admin-sql",
    "/feedback-management",
    "/question-classifier-models",
    "/history",
    "/evaluation",
  ];
  const mobile = test.info().project.name === "mobile-375";
  await page.setViewportSize(
    mobile ? { width: 375, height: 812 } : { width: 1440, height: 900 }
  );

  for (const path of routes) {
    await page.goto(path);
    const heading = page.getByRole("heading", { level: 1 }).first();
    await expect(heading, `${path} にページタイトルがある`).toBeVisible();
    const header = page.locator("header").filter({ has: heading }).first();
    await expect(header, `${path} にローカル PageHeader がある`).toBeVisible();
    const headerBox = await header.boundingBox();
    expect(headerBox, `${path} の PageHeader bounds`).not.toBeNull();
    expect(headerBox!.height, `${path} の PageHeader が首屏を占有しすぎない`).toBeLessThan(240);
    expect(
      await header.locator('[data-page-action-kind="primary"]:visible').count(),
      `${path} の primary は最大 1 件`
    ).toBeLessThanOrEqual(1);
    await expectNoHorizontalScroll(page);
  }
});
