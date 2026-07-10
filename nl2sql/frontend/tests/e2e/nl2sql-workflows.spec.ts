import { expect, test, type Page, type Route } from "@playwright/test";

type JsonValue = Record<string, unknown> | unknown[];

interface MockApiState {
  previewPayload: Record<string, unknown> | null;
  executePayload: Record<string, unknown> | null;
  feedbackPayload: Record<string, unknown> | null;
  selectAiFeedbackDeletePayload: Record<string, unknown> | null;
  selectAiFeedbackUpdatePayload: Record<string, unknown> | null;
  profilePatchPayload: Record<string, unknown> | null;
  commentApplyPayload: Record<string, unknown> | null;
  dbProfileDropPayload: Record<string, unknown> | null;
  dropTablePayload: Record<string, unknown> | null;
  evaluationSetPayload: Record<string, unknown> | null;
  samplePayload: Record<string, unknown> | null;
  previewDataPayload: Record<string, unknown> | null;
  previewDataExportPayload: Record<string, unknown> | null;
  csvUploadPayload: Record<string, unknown> | null;
  importTabularPayload: Record<string, unknown> | null;
  statementsPayload: Record<string, unknown> | null;
  dropViewPayload: Record<string, unknown> | null;
  reversePayload: Record<string, unknown> | null;
  reverseDeepPayload: Record<string, unknown> | null;
}

const safety = {
  is_safe: true,
  is_select_only: true,
  row_limit_applied: 100,
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

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定プロファイル",
    description: "請求・顧客を扱う既定プロファイル",
    allowed_tables: ["INVOICES"],
    glossary: { 請求金額: "INVOICES.TOTAL_AMOUNT" },
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
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

function fulfillJson(route: Route, data: JsonValue) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

async function mockNl2SqlApi(page: Page): Promise<MockApiState> {
  const state: MockApiState = {
    previewPayload: null,
    executePayload: null,
    feedbackPayload: null,
    selectAiFeedbackDeletePayload: null,
    selectAiFeedbackUpdatePayload: null,
    profilePatchPayload: null,
    commentApplyPayload: null,
    dbProfileDropPayload: null,
    dropTablePayload: null,
    evaluationSetPayload: null,
    samplePayload: null,
    previewDataPayload: null,
    previewDataExportPayload: null,
    csvUploadPayload: null,
    importTabularPayload: null,
    statementsPayload: null,
    dropViewPayload: null,
    reversePayload: null,
    reverseDeepPayload: null,
  };
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
  let legacyMaterial: { glossary: Record<string, string>; rule_entries: Array<{ category: string; rule: string }> } = {
    glossary: { 売上: "INVOICES.TOTAL_AMOUNT" },
    rule_entries: [{ category: "共通", rule: "SELECT のみ" }],
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
    if (state.samplePayload.execute) {
      sampleImportedObjects = [...sampleObjects];
    }
    return fulfillJson(route, {
      operation: "import",
      step: state.samplePayload.step ?? "all",
      runtime: "deterministic",
      executed: Boolean(state.samplePayload.execute),
      dry_run: !state.samplePayload.execute,
      objects: sampleObjects,
      statements: [{ index: 1, statement_type: "CREATE", status: state.samplePayload.execute ? "applied_to_local_state" : "dry_run", sql: sampleSql.tables[0], error_message: "" }],
      warnings: [],
      profile_id: "sql_assist_sample",
      timing,
    });
  });
  await page.route("**/api/nl2sql/sample-data/delete", (route) => {
    state.samplePayload = route.request().postDataJSON() as Record<string, unknown>;
    if (state.samplePayload.execute) {
      sampleImportedObjects = [];
    }
    return fulfillJson(route, {
      operation: "delete",
      step: "all",
      runtime: "deterministic",
      executed: Boolean(state.samplePayload.execute),
      dry_run: !state.samplePayload.execute,
      objects: sampleObjects,
      statements: [{ index: 1, statement_type: "DROP", status: state.samplePayload.execute ? "applied_to_local_state" : "dry_run", sql: sampleSql.delete[0], error_message: "" }],
      warnings: [],
      profile_id: "sql_assist_sample",
      timing,
    });
  });
  await page.route("**/api/schema/import-csv", (route) =>
    fulfillJson(route, {
      table_name: "IMPORTED_CUSTOMERS",
      columns: [
        { source_name: "CUSTOMER_ID", column_name: "CUSTOMER_ID", data_type: "NUMBER", nullable: false },
        { source_name: "CUSTOMER_NAME", column_name: "CUSTOMER_NAME", data_type: "VARCHAR2(4000)", nullable: true },
      ],
      row_count: 1,
      dry_run: true,
      executed: false,
      ddl: "CREATE TABLE IMPORTED_CUSTOMERS (CUSTOMER_ID NUMBER, CUSTOMER_NAME VARCHAR2(4000))",
      insert_sql: "INSERT INTO IMPORTED_CUSTOMERS (CUSTOMER_ID, CUSTOMER_NAME) VALUES (:1, :2)",
      warnings: [],
      sample_rows: [{ CUSTOMER_ID: "1", CUSTOMER_NAME: "青山商事" }],
      timing,
    })
  );
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
    return fulfillJson(route, {
      table_name: "INVOICES",
      filename: "upload.csv",
      mode: "insert",
      matched_columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      unmatched_csv_columns: ["UNKNOWN_COLUMN"],
      row_count: 2,
      success_count: 0,
      error_count: 0,
      row_errors: [],
      hint: "",
      dry_run: true,
      executed: false,
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
      dry_run: !state.importTabularPayload.execute,
      executed: Boolean(state.importTabularPayload.execute),
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
    const executed = Boolean(state.statementsPayload.execute);
    return fulfillJson(route, {
      executed,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: policy,
          status: executed ? "executed" : "dry_run",
          sql,
          row_count: null,
          message: executed ? "executed" : "",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: executed,
      rolled_back: false,
      warnings: executed ? [] : ["Dry-run のため SQL は実行していません。"],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/drop-table", (route) => {
    state.dropTablePayload = route.request().postDataJSON() as Record<string, unknown>;
    const tableName = String(state.dropTablePayload.table_name ?? "INVOICES");
    const executed = Boolean(state.dropTablePayload.execute);
    return fulfillJson(route, {
      executed,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: executed ? "executed" : "dry_run",
          sql: `DROP TABLE "${tableName}" PURGE`,
          row_count: null,
          message: executed ? "executed" : "",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: executed,
      rolled_back: false,
      warnings: executed ? [] : ["Dry-run のため SQL は実行していません。"],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/drop-view", (route) => {
    state.dropViewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      executed: false,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: "dry_run",
          sql: 'DROP VIEW "V_EMP_DEPT"',
          row_count: null,
          message: "",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: false,
      rolled_back: false,
      warnings: ["Dry-run のため SQL は実行していません。"],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/analyze-error", (route) =>
    fulfillJson(route, {
      analysis: "1) エラー原因: mock\n2) 解決方法: mock\n3) 結論: mock",
      source: "deterministic",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/extract-join-where", (route) =>
    fulfillJson(route, {
      join_text: "[INNER] E(EMPLOYEE).DEPARTMENT_ID = D(DEPARTMENT).DEPARTMENT_ID",
      where_text: "None",
      source: "deterministic",
      warnings: [],
    })
  );
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
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
      rule_entries: [{ category: "共通", rule: "集計時は NULL を除外する" }],
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
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items: [historyItem] }));
  await page.route("**/api/nl2sql/feedback", (route) => {
    state.feedbackPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      history_id: "hist-001",
      rating: "good",
      saved: true,
      comment: "SQL は期待通りです",
    });
  });
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
      warnings: ["Dry-run のため feedback index は再構築していません。"],
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
      warnings: ["Dry-run のため feedback index は削除していません。"],
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
  await page.route("**/api/nl2sql/feedback-entries/delete", (route) =>
    fulfillJson(route, {
      items: [],
      total: 0,
      indexed_count: 0,
    })
  );
  await page.route("**/api/nl2sql/feedback-config", (route) => {
    if (route.request().method() === "PATCH") {
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
      classifier_version: "classifier-001",
      updated_at: "2026-06-21T10:00:00.000Z",
      example_count: 4,
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
  await page.route("**/api/nl2sql/classifier/models", (route) =>
    fulfillJson(route, {
      active_version: "classifier-001",
      models: [
        {
          version: "classifier-001",
          active: true,
          updated_at: "2026-06-21T10:00:00.000Z",
          category_count: 2,
          categories: ["既定プロファイル", "入金管理"],
          embedding_model: "deterministic-hash-1536",
          vector_dimension: 1536,
          metrics: { training_accuracy: 1 },
          source: "oracle_state",
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/classifier/train", (route) =>
    fulfillJson(route, {
      ready: true,
      trained: true,
      classifier_version: "classifier-002",
      updated_at: "2026-06-21T10:05:00.000Z",
      example_count: 4,
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
  await page.route("**/api/nl2sql/diagnostics", (route) =>
    fulfillJson(route, {
      readiness: [
        {
          area: "oracle_adb",
          label: "Oracle / ADB",
          status: "ok",
          summary: "Oracle / ADB runtime is ready.",
          next_action: "",
          related_checks: ["oracle"],
        },
        {
          area: "select_ai_agent",
          label: "Oracle Select AI Agent",
          status: "warning",
          summary: "Agent assets need refresh.",
          next_action: "Agent assets 更新を実行してください。",
          related_checks: ["select_ai_agent"],
        },
        {
          area: "select_ai",
          label: "Oracle Select AI",
          status: "ok",
          summary: "Select AI profile is ready.",
          next_action: "",
          related_checks: ["select_ai"],
        },
        {
          area: "persistence",
          label: "NL2SQL persistence",
          status: "ok",
          summary: "Oracle-backed state persistence is ready.",
          next_action: "",
          related_checks: ["persistence"],
        },
        {
          area: "feedback_embedding",
          label: "Feedback vector learning",
          status: "warning",
          summary: "Feedback vector embedding is not configured.",
          next_action: "OCI GenAI embedding 設定を確認してください。",
          related_checks: ["feedback_embedding"],
        },
        {
          area: "enterprise_ai_direct",
          label: "Enterprise AI Direct",
          status: "warning",
          summary: "Enterprise AI Direct is not configured.",
          next_action: "Enterprise AI endpoint / API key / model を設定してください。",
          related_checks: ["enterprise_ai_direct"],
        },
      ],
      smoke_checks: [
        {
          id: "refresh_select_ai_agent_assets",
          label: "Select AI Agent assets refresh",
          category: "asset_refresh",
          status: "warning",
          method: "POST",
          endpoint: "/api/nl2sql/select-ai-agent/assets/refresh?profile_id=default",
          request_hint: "",
          command: "",
          expected: "tool / agent / task / team 名と status=ready が返ること。",
          next_action: "Agent assets 更新を実行してください。",
          related_readiness: ["oracle_adb", "select_ai_agent"],
        },
        {
          id: "manual_integration_script",
          label: "Manual integration script",
          category: "manual_script",
          status: "warning",
          method: "",
          endpoint: "",
          request_hint: "",
          command:
            "cd backend && uv run python scripts/nl2sql_manual_integration.py --require-oracle --refresh-assets",
          expected: "[ok] diagnostics / refresh / preview / job lines が表示されること。",
          next_action: "Oracle / Select AI / Agent readiness を ok にしてください。",
          related_readiness: ["oracle_adb", "select_ai_agent"],
        },
      ],
      config_guides: [
        {
          id: "enterprise_ai_direct",
          label: "Enterprise AI Direct",
          status: "warning",
          summary: "Enterprise AI Direct is not configured.",
          next_action: "Enterprise AI endpoint / API key / model を設定してください。",
          required_env_vars: [
            {
              name: "OCI_ENTERPRISE_AI_ENDPOINT",
              status: "warning",
              required: true,
              note: "endpoint missing",
            },
            {
              name: "OCI_ENTERPRISE_AI_API_KEY",
              status: "warning",
              required: true,
              note: "api key missing",
            },
            {
              name: "OCI_ENTERPRISE_AI_LLM_MODEL",
              status: "warning",
              required: true,
              note: "model missing",
            },
          ],
          optional_env_vars: [
            {
              name: "OCI_ENTERPRISE_AI_PROJECT_OCID",
              status: "optional",
              required: false,
              note: "",
            },
          ],
          env_template:
            "NL2SQL_ENTERPRISE_AI_DIRECT_ENABLED=true\nOCI_ENTERPRISE_AI_ENDPOINT=<enterprise-ai-endpoint>\nOCI_ENTERPRISE_AI_API_KEY=<enterprise-ai-api-key>\nOCI_ENTERPRISE_AI_LLM_MODEL=<enterprise-ai-model>",
          smoke_command:
            "uv run python scripts/nl2sql_manual_integration.py --require-enterprise-ai --engines enterprise_ai_direct --execute --json-report reports/nl2sql-enterprise-ai-direct.json",
          related_readiness: ["enterprise_ai_direct"],
        },
        {
          id: "feedback_embedding",
          label: "Feedback vector learning",
          status: "warning",
          summary: "Feedback vector embedding is not configured.",
          next_action: "OCI GenAI embedding 設定を確認してください。",
          required_env_vars: [
            {
              name: "NL2SQL_FEEDBACK_EMBEDDING_ENABLED",
              status: "warning",
              required: true,
              note: "disabled",
            },
            {
              name: "OCI_GENAI_ENDPOINT",
              status: "warning",
              required: true,
              note: "endpoint missing",
            },
            {
              name: "OCI_GENAI_EMBED_MODEL_ID",
              status: "ok",
              required: true,
              note: "model configured",
            },
          ],
          optional_env_vars: [],
          env_template:
            "NL2SQL_FEEDBACK_EMBEDDING_ENABLED=true\nOCI_GENAI_ENDPOINT=<oci-genai-endpoint>\nOCI_GENAI_EMBED_MODEL_ID=cohere.embed-v4.0",
          smoke_command:
            "uv run python scripts/nl2sql_manual_integration.py --require-oracle --require-feedback-embedding --execute-feedback-index --json-report reports/nl2sql-feedback-vector.json",
          related_readiness: ["oracle_adb", "feedback_embedding"],
        },
        {
          id: "production_release_gate",
          label: "Production release gate",
          status: "warning",
          summary: "Oracle / persistence / Select AI / Agent assets の本番 gate 設定です。",
          next_action: "Agent assets 更新を実行してください。",
          required_env_vars: [
            { name: "ORACLE_USER", status: "ok", required: true, note: "" },
            { name: "ORACLE_DSN", status: "ok", required: true, note: "" },
          ],
          optional_env_vars: [
            { name: "NL2SQL_SELECT_AI_MODEL", status: "optional", required: false, note: "" },
          ],
          env_template:
            "NL2SQL_RUNTIME_MODE=oracle\nNL2SQL_PERSISTENCE_MODE=oracle\nNL2SQL_SELECT_AI_CREDENTIAL_NAME=<dbms-cloud-ai-credential>",
          smoke_command:
            "uv run python scripts/nl2sql_manual_integration.py --release-gate --engines select_ai_agent,select_ai --allowed-table YOUR_TABLE --json-report reports/nl2sql-release-gate.json",
          related_readiness: ["oracle_adb", "persistence", "select_ai", "select_ai_agent"],
        },
      ],
      checks: [
        { name: "oracle", status: "ok", message: "Oracle runtime is configured." },
        { name: "select_ai_agent", status: "warning", message: "Agent assets need refresh." },
      ],
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
        executed: false,
        status: "dry_run",
        cleaned_at: "2026-06-21T10:00:00.000Z",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        team_name: "NL2SQL_DEFAULT_TEAM",
        warning: "",
        asset_names: { profile: "NL2SQL_DEFAULT_PROFILE", team: "NL2SQL_DEFAULT_TEAM" },
        engine_meta: { runtime: "mock" },
      },
    ])
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      profiles: [
        {
          name: "NL2SQL_DEFAULT_PROFILE",
          status: "ready",
          owner: "APP",
          created_at: "2026-06-21T10:00:00.000Z",
          attributes: {},
        },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/feedback**", (route) => {
    const url = route.request().url();
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
      executed: false,
      status: "dry_run",
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
  await page.route("**/api/nl2sql/analyze", (route) =>
    fulfillJson(route, {
      safety,
      explanation: "SELECT 文として安全に実行できます。",
      recommendations: ["許可された表だけを参照しています。"],
      executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
      repaired_sql: "",
      optimization_hints: ["TOTAL_AMOUNT の索引を確認できます。"],
    })
  );
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
  await page.route("**/api/nl2sql/comments/generate-sql", (route) =>
    fulfillJson(route, {
      sql: "COMMENT ON COLUMN \"INVOICES\".\"TOTAL_AMOUNT\" IS '税込請求金額';",
      source: "deterministic",
      warnings: [],
      timing,
    })
  );
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
          status: "dry_run",
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
      sql: "ALTER TABLE \"INVOICES\" MODIFY (\"TOTAL_AMOUNT\" ANNOTATIONS (UI_Display '税込請求金額'));",
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
          status: "dry_run",
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
  await page.route("**/api/nl2sql/synthetic-data/generate", (route) =>
    fulfillJson(route, {
      operation_id: "operation-001",
      table_name: "INVOICES",
      row_count: 10,
      executed: false,
      runtime: "deterministic",
      status: "dry_run",
      message: "INVOICES に 10 行の synthetic data を生成する plan です。",
      warnings: [],
      engine_meta: {},
      timing,
    })
  );
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
  await page.route("**/api/nl2sql/preview", (route) => {
    state.previewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
      is_safe: true,
      row_limit: 100,
      note: "mock preview",
      engine: "select_ai_agent",
      engine_meta: { profile: "mock_agent_profile" },
      fallback_reason: "",
      rewritten_question: "請求金額を一覧で見たい",
      executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
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

test("query workbench previews SQL and executes the preview result", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();

  const question = page.getByLabel("検索クエリ");
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"");

  await question.fill("請求金額を一覧で見たい");
  await expect(page.getByText("候補プロファイル")).toBeVisible();
  await expect(page.getByText("スコア 94%")).toBeVisible();
  await page.getByRole("button", { name: "候補を適用" }).click();

  await page.getByLabel(/請求金額 を選択/).check();
  await page.getByRole("button", { name: "SQL プレビュー" }).click();

  await expect(page.getByText("生成された SQL")).toBeVisible();
  await expect(page.getByText("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES")).toBeVisible();
  await page.getByRole("button", { name: "この SQL を実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1200000" })).toBeVisible();
  expect(api.previewPayload?.allowed_objects).toEqual({
    table_names: ["INVOICES"],
    columns: { INVOICES: ["TOTAL_AMOUNT"] },
  });
  expect(api.executePayload?.sql).toContain("FETCH FIRST 100 ROWS ONLY");
  await expectNoHorizontalScroll(page);
});

test("query workbench executes direct SELECT SQL", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await page.getByRole("tab", { name: "SQLの実行" }).click();

  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  const sqlInput = page.getByLabel("SQL", { exact: true });
  await expect(sqlInput).toHaveValue("\"INVOICES\".\"TOTAL_AMOUNT\"");

  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL 実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  expect(api.executePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    allowed_objects: {
      table_names: [],
      columns: {},
    },
    row_limit: 100,
  });
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("evaluation page renders Select AI and Agent comparison details", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/evaluation");
  await expect(page.getByText("最近の評価実行")).toBeVisible();
  await expect(page.getByText("NL2SQL deterministic evaluation")).toBeVisible();
  const reportDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "レポートDL" }).first().click();
  const reportDownload = await reportDownloadPromise;
  expect(reportDownload.suggestedFilename()).toBe(
    "nl2sql_evaluation_run_20260621100200_eval-run-001.md"
  );
  const runCasesDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "ケースDL" }).first().click();
  const runCasesDownload = await runCasesDownloadPromise;
  expect(runCasesDownload.suggestedFilename()).toBe(
    "nl2sql_evaluation_run_20260621100200_eval-run-001_cases.csv"
  );
  await page.getByRole("button", { name: "結果表示" }).first().click();
  await expect(page.getByText("評価実行結果を表示しました。")).toBeVisible();
  await expect(page.getByText("評価結果")).toBeVisible();
  await expect(page.getByLabel("評価セット")).toContainText("請求ベンチマーク");
  await page.getByLabel("評価セット").selectOption("eval-001");
  await expect(page.getByLabel("セット名")).toHaveValue("請求ベンチマーク");
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("保存済み請求ケース");
  await expect(page.getByText("保存済み請求金額")).toBeVisible();
  await page.getByRole("button", { name: "評価を実行" }).click();
  await expect(page.getByText("評価結果")).toBeVisible();
  await expect(page.getByText("1", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("eval-run-new")).toHaveCount(0);
  await expect(page.getByText("実行可能 100%").first()).toBeVisible();
  await page.getByRole("button", { name: "評価セット保存" }).click();
  await expect(page.getByText("評価セットを保存しました。")).toBeVisible();
  expect(api.evaluationSetPayload?.name).toBe("請求ベンチマーク");
  expect(api.evaluationSetPayload?.engine).toBe("select_ai");
  expect(api.evaluationSetPayload?.cases).toEqual([
    {
      question: "保存済み請求金額",
      expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
      profile_id: "default",
    },
  ]);

  await expect(page.getByText("最近の比較記録")).toBeVisible();
  await expect(page.getByText("履歴の請求比較")).toBeVisible();
  await page.getByRole("button", { name: "表示", exact: true }).click();
  await expect(page.getByText("履歴では Select AI Agent が安定していました。").first()).toBeVisible();
  await expect(page.getByLabel("比較レポート")).toHaveValue(/Question: 履歴の請求比較/);
  await page.getByRole("button", { name: "エンジン比較" }).click();

  await expect(page.getByRole("heading", { name: "Select AI / Agent 比較" })).toBeVisible();
  await expect(page.getByText("比較エンジン", { exact: true })).toBeVisible();
  await expect(page.getByText("安全生成", { exact: true })).toBeVisible();
  await expect(page.getByText("最短", { exact: true })).toBeVisible();
  await expect(page.getByText("実行エラー率", { exact: true })).toBeVisible();
  await expect(page.getByText("50%", { exact: true })).toBeVisible();
  await expect(page.getByText("生成 SQL", { exact: true })).toHaveCount(2);
  await expect(page.getByText("参照表", { exact: true })).toHaveCount(2);
  await expect(page.getByText("実行済み", { exact: true })).toBeVisible();
  await expect(page.getByText("未実行", { exact: true })).toBeVisible();
  await expect(page.getByText("実行結果", { exact: true })).toBeVisible();
  await expect(page.getByText("ORA-00942 mock", { exact: true })).toBeVisible();
  await expect(page.getByText("Agent profile は最新です。")).toBeVisible();
  await expect(page.getByRole("button", { name: "レポートコピー" })).toBeVisible();
  await expect(page.getByLabel("比較レポート")).toHaveValue(/NL2SQL engine comparison/);
  await expect(page.getByLabel("比較レポート")).toHaveValue(/Select AI Agent/);
  await expect(page.getByLabel("比較レポート")).toHaveValue(/ORA-00942 mock/);

  await page.getByRole("button", { name: "Synthetic 生成" }).click();
  await expect(page.getByText("Synthetic 評価ケース")).toBeVisible();
  await page.getByRole("button", { name: "Synthetic を保存" }).click();
  await expect(page.getByText("Synthetic case を few-shot 例として保存しました。")).toBeVisible();
  expect(api.profilePatchPayload?.few_shot_examples).toEqual([
    { question: "請求金額を一覧で見たい", sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY" },
  ]);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "ケース CSV", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("nl2sql_evaluation_cases.csv");
  await page.getByLabel("ケース CSV 取込").setInputFiles({
    name: "evaluation_cases.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("QUESTION,EXPECTED_SQL\n入金遅延を確認,SELECT STATUS FROM PAYMENTS"),
  });
  await expect(page.getByText("1 件の評価 case を取り込みました。")).toBeVisible();
  await expect(page.getByText("入金遅延を確認")).toBeVisible();
  await page.getByRole("button", { name: "評価セット保存" }).click();
  expect(api.evaluationSetPayload?.cases).toEqual([
    {
      question: "入金遅延を確認",
      expected_sql: "SELECT STATUS FROM PAYMENTS",
      profile_id: "default",
    },
  ]);
  await page.getByRole("button", { name: "Synthetic を保存" }).click();
  expect(api.profilePatchPayload?.few_shot_examples).toEqual(
    expect.arrayContaining([
      { question: "入金遅延を確認", sql: "SELECT STATUS FROM PAYMENTS" },
    ])
  );
  await expectNoHorizontalScroll(page);
});

test("history rerun deep-links back to query with question, engine, and profile", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/history");
  await expect(page.getByText("履歴から再実行したい請求金額")).toBeVisible();
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
  await expect(page.getByRole("link", { name: /用語・ルール/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 生成/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 確認・修復/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL から質問を生成/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /フィードバック学習/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /質問学習/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /エンジン運用/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /NL2SQL 接続診断/ })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql analysis page analyzes SQL and repairs Oracle errors", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/sql-analysis");
  await page.getByLabel("分析する SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL を分析" }).click();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();
  await page.getByRole("button", { name: "SELECT を実行" }).click();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();

  await page.getByLabel("修復する SQL").fill("SELECT BAD_COL FROM INVOICES");
  await page.getByLabel("Oracle error message").fill("ORA-00904: invalid identifier");
  await page.getByRole("button", { name: "修復案を生成" }).click();
  await expect(page.getByText("ORA-00904", { exact: true })).toBeVisible();
  await expect(page.getByText("SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("sql to question page analyzes structure and reverse-generates a question", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sql-to-question");
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expect(page.getByText("請求情報")).toBeVisible();

  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByLabel("用語集を利用").check();
  await page.getByRole("button", { name: "SQL 構造を分析" }).click();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();

  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();
  await expect(page.getByText("SQL 論理構造").first()).toBeVisible();
  expect(api.reversePayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });

  await page.getByRole("button", { name: "Deep 逆生成" }).click();
  await expect(page.getByText("請求金額を条件付きで一覧確認したい")).toBeVisible();
  expect(api.reverseDeepPayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });
  await expectNoHorizontalScroll(page);
});

test("learning page manages feedback and feedback vector index only", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/learning");
  await expect(page.getByText("Select AI Feedback 管理")).toBeVisible();
  await expect(page.getByText("Embedding + LogisticRegression 分類器")).toHaveCount(0);
  await expect(page.getByText("質問の学習候補")).toHaveCount(0);
  await expect(page.getByText("NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX")).toBeVisible();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES").first()).toBeVisible();
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
  await page.getByLabel("しきい値").first().fill("0.85");
  await page.getByLabel("件数").first().fill("4");
  await page.getByRole("button", { name: "ベクトルインデックスを更新" }).click();
  await expect(page.getByText("Select AI feedback vector index を更新しました。")).toBeVisible();
  expect(api.selectAiFeedbackUpdatePayload).toEqual({
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    similarity_threshold: 0.85,
    match_limit: 4,
  });

  await page.getByRole("combobox", { name: "評価", exact: true }).selectOption("good");
  await page.getByLabel("コメント（任意）").fill("SQL は期待通りです");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "good",
    comment: "SQL は期待通りです",
  });
  await page.getByLabel("評価フィルター").selectOption("unrated");
  await expect(
    page.getByTestId("feedback-history-row").filter({ hasText: "履歴から再実行したい請求金額" }).filter({ hasText: "未評価" })
  ).toBeVisible();
  await page.getByLabel("履歴検索").fill("該当なし");
  await expect(page.getByText("一致する履歴がありません")).toBeVisible();

  await expect(page.getByRole("heading", { name: "Feedback Vector Index" })).toBeVisible();
  await expect(page.getByText("oracle_26ai")).toBeVisible();
  await expect(page.getByLabel("Oracle 26ai DDL plan")).toHaveValue(/VECTOR\(1536, FLOAT32\)/);
  await page.getByRole("button", { name: "Rebuild Dry-run" }).click();
  await expect(page.getByText("Dry-run のため feedback index は再構築していません。")).toBeVisible();
  await expect(page.getByText("ready", { exact: true })).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("question learning page trains classifier and finds learning candidates", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/question-learning");
  await expect(page.getByRole("heading", { name: "質問学習" })).toBeVisible();
  await expect(page.getByText("Embedding + LogisticRegression 分類器")).toBeVisible();
  await expect(page.getByText("Model registry")).toBeVisible();
  await expect(page.getByText("質問の学習候補")).toBeVisible();
  await expect(page.getByText("フィードバック保存")).toHaveCount(0);

  await page.getByRole("button", { name: "Classifier 学習" }).click();
  await expect(page.getByText("LogisticRegression classifier を学習しました。")).toBeVisible();

  await page.getByRole("button", { name: "分類を試す" }).click();
  await expect(page.getByText("信頼度 92%")).toBeVisible();
  await expect(page.getByText("既定プロファイル / 92%")).toBeVisible();

  await page.getByRole("button", { name: "推薦・書き換え" }).click();
  await expect(page.getByText("信頼度 94%")).toBeVisible();
  await expect(page.getByText("請求金額を一覧で見たい")).toBeVisible();
  await expect(page.getByText("INVOICES", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "類似履歴検索" }).click();
  await expect(page.getByText("類似度 90%")).toBeVisible();
  await expect(page.getByText("請求金額の履歴と近い質問です。")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("glossary rules page imports CSV files and saves profile learning material", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/glossary-rules");
  await expect(page.getByText("旧版互換 用語集・ルール")).toBeVisible();
  await expect(page.getByText("売上 = INVOICES.TOTAL_AMOUNT")).toBeVisible();
  await page.getByLabel("用語集 Excel 取込").setInputFiles({
    name: "terms.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mock"),
  });
  await expect(page.getByText("粗利 = INVOICES.PROFIT")).toBeVisible();
  await page.getByLabel("ルール Excel 取込").setInputFiles({
    name: "rules.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mock"),
  });
  await expect(page.getByText("共通: 集計時は NULL を除外する")).toBeVisible();

  await page.getByLabel("語彙・同義語 CSV 取込").setInputFiles({
    name: "terms.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("TERM,DEFINITION\n粗利,INVOICES.PROFIT"),
  });
  await expect(page.getByRole("textbox", { name: "語彙・同義語" })).toHaveValue("粗利=INVOICES.PROFIT");

  await page.getByLabel("SQL ルール CSV 取込").setInputFiles({
    name: "rules.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("CATEGORY,RULE\n共通,集計時は NULL を除外する"),
  });
  await expect(page.getByRole("textbox", { name: "SQL ルール" })).toHaveValue("集計時は NULL を除外する");

  await page.getByLabel("few-shot 例 CSV 取込").setInputFiles({
    name: "examples.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("QUESTION,SQL\n粗利を見たい,SELECT PROFIT FROM INVOICES"),
  });
  await expect(page.getByRole("textbox", { name: "few-shot 例" })).toHaveValue("粗利を見たい => SELECT PROFIT FROM INVOICES");

  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("語彙・ルールを保存しました。")).toBeVisible();
  expect(api.profilePatchPayload?.glossary).toEqual({ 粗利: "INVOICES.PROFIT" });
  expect(api.profilePatchPayload?.sql_rules).toEqual(["集計時は NULL を除外する"]);
  expect(api.profilePatchPayload?.few_shot_examples).toEqual([
    { question: "粗利を見たい", sql: "SELECT PROFIT FROM INVOICES" },
  ]);
  await expectNoHorizontalScroll(page);
});

test("sample data, data management, and engine operations run imported workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sample-data");
  await expect(page.getByText("検証用サンプルデータ管理")).toBeVisible();
  await expect(page.getByRole("heading", { name: "サンプルデータ管理", exact: true })).toBeVisible();
  await expect(page.getByText("DEPARTMENT").first()).toBeVisible();
  await page.getByRole("button", { name: "Import Dry-run" }).first().click();
  await expect.poll(() => api.samplePayload?.execute).toBe(false);
  await page.getByLabel("Sample 確認語").fill("SQL_ASSIST_SAMPLE");
  await page.getByLabel("実行する", { exact: true }).check();
  await page.getByRole("button", { name: "Import 実行" }).first().click();
  await expect(page.getByText("導入済み 5")).toBeVisible();
  expect(api.samplePayload?.confirmation).toBe("SQL_ASSIST_SAMPLE");
  await page.getByRole("button", { name: "Delete 実行" }).first().click();
  await expect(page.getByText("導入済み 0")).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/data-management");
  await expect(page.getByText("テーブル・ビューデータの表示")).toBeVisible();
  await expect(page.getByText("Excel/CSV 取込(新規テーブル)", { exact: true })).toHaveCount(0);
  await expect(page.getByText("サンプルデータ管理", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "データを表示" }).click();
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("1-10 / 12 件");
  await expect(page.getByRole("cell", { name: "顧客11" })).toHaveCount(0);
  await page.getByRole("button", { name: "次へ" }).click();
  await expect(page.getByRole("cell", { name: "顧客11" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("11-12 / 12 件");
  await expect.poll(() => api.previewDataPayload?.object_name).toBe("INVOICES");
  expect(api.previewDataPayload?.limit).toBe(100);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "XLSX ダウンロード" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("invoices_preview.xlsx");
  await expect.poll(() => api.previewDataExportPayload?.object_name).toBe("INVOICES");
  expect(api.previewDataExportPayload?.limit).toBe(100);

  await page.locator("details").filter({ hasText: "CSV アップロード(既存テーブル)" }).locator("summary").click();
  await page.getByLabel("CSV 選択").setInputFiles({
    name: "invoices.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("CUSTOMER_NAME,TOTAL_AMOUNT,UNKNOWN_COLUMN\n青山商事,1200000,x\n"),
  });
  await page.getByRole("button", { name: "アップロード Dry-run" }).click();
  await expect(page.getByText("UNKNOWN_COLUMN", { exact: false }).first()).toBeVisible();
  await expect.poll(() => api.csvUploadPayload?.table_name).toBe("INVOICES");
  expect(api.csvUploadPayload?.mode).toBe("insert");
  expect(api.csvUploadPayload?.execute).toBe(false);

  await page.locator("details").filter({ hasText: "SQL 一括実行" }).locator("summary").click();
  await page.getByRole("button", { name: "INSERT(単一行)" }).click();
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/^INSERT INTO TABLE_NAME/);
  await expect(page.getByText("Oracle には送信せず、SQL 分割・許可ポリシー・実行対象だけ確認します。")).toBeVisible();
  await page.getByRole("button", { name: "SQL プレビュー" }).click();
  await expect(page.getByText("Dry-run のため SQL は実行していません。")).toBeVisible();
  await expect.poll(() => api.statementsPayload?.policy).toBe("data_dml");
  expect(api.statementsPayload?.execute).toBe(false);
  await page.getByRole("button", { name: "AI 分析を実行" }).click();
  await expect(page.getByText("1) エラー原因: mock")).toBeVisible();

  await page.locator("details").filter({ hasText: "Synthetic NL2SQL ケース" }).locator("summary").click();
  await page.getByRole("button", { name: "ケース生成" }).click();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/engine-operations");
  await expect(page.getByText("運用 readiness")).toBeVisible();
  await expect(page.getByText("Oracle / ADB runtime is ready.").first()).toBeVisible();
  await expect(page.getByText("Live smoke checklist")).toBeVisible();
  await expect(page.getByText("Select AI Agent assets refresh")).toBeVisible();
  await expect(page.getByText("Manual integration script")).toBeVisible();
  await expect(page.getByText("/api/nl2sql/select-ai-agent/assets/refresh?profile_id=default")).toBeVisible();
  await expect(page.getByText("必須 smoke コマンド")).toBeVisible();
  await expect(page.getByText("Diagnostics-only 設定確認")).toBeVisible();
  await expect(page.getByText("--diagnostics-only").first()).toBeVisible();
  await expect(page.getByText("本番 release gate")).toBeVisible();
  await expect(page.getByText("--release-gate").first()).toBeVisible();
  await expect(page.getByText("--allowed-table YOUR_TABLE").first()).toBeVisible();
  await expect(page.getByText("--json-report reports/nl2sql-release-gate.json").first()).toBeVisible();
  await expect(page.getByText("不足: Oracle Select AI Agent").first()).toBeVisible();
  await expect(page.getByText("旧版吸収 smoke")).toBeVisible();
  await expect(page.getByText("--check-legacy-absorption").first()).toBeVisible();
  await expect(page.getByText("--require-classifier-oracle-state").first()).toBeVisible();
  await expect(page.getByText("必要な環境変数").first()).toBeVisible();
  await expect(page.getByText("OCI_ENTERPRISE_AI_ENDPOINT").first()).toBeVisible();
  await expect(page.getByText("NL2SQL_FEEDBACK_EMBEDDING_ENABLED").first()).toBeVisible();
  await expect(page.getByText("--require-enterprise-ai").first()).toBeVisible();
  await expect(page.getByText("--require-feedback-embedding").first()).toBeVisible();
  await expect(page.getByText("--require-oracle-persistence").first()).toBeVisible();
  await expect(page.getByText("設定完了ガイド")).toBeVisible();
  await expect(page.getByText("必須 env").first()).toBeVisible();
  await expect(page.getByText("任意 env").first()).toBeVisible();
  await expect(page.getByText("OCI_ENTERPRISE_AI_ENDPOINT=<enterprise-ai-endpoint>")).toBeVisible();
  await expect(page.getByText("OCI_GENAI_ENDPOINT=<oci-genai-endpoint>")).toBeVisible();
  await expect(page.getByText("--json-report reports/nl2sql-enterprise-ai-direct.json")).toBeVisible();
  await expect(page.getByText("Manual integration report")).toBeVisible();
  await page.getByLabel("JSON 取込").setInputFiles({
    name: "nl2sql-release-gate.json",
    mimeType: "application/json",
    buffer: Buffer.from(
      JSON.stringify({
        schema_version: "nl2sql_manual_integration_report_v1",
        generated_at: "2026-06-21T10:20:00Z",
        started_at: "2026-06-21T10:19:57.600Z",
        finished_at: "2026-06-21T10:20:00Z",
        elapsed_ms: 2400,
        release_gate: true,
        ok: true,
        exit_code: 0,
        profile_id: "manual_integration",
        engines: ["select_ai_agent", "select_ai"],
        allowed_tables: ["DENPYO_REGISTRATIONS"],
        summary: { total: 3, passed: 3, failed: 0 },
        steps: [
          { name: "diagnostics", ok: true, message: "runtime=oracle" },
          { name: "job_select_ai_agent", ok: true, message: "status=done; rows=1" },
          { name: "compare_engines", ok: true, message: "error_rate=0.0" },
        ],
      })
    ),
  });
  await expect(page.getByText("nl2sql_manual_integration_report_v1").last()).toBeVisible();
  await expect(page.getByText("manual_integration").last()).toBeVisible();
  await expect(page.getByText("DENPYO_REGISTRATIONS").first()).toBeVisible();
  await expect(page.getByText("job_select_ai_agent").last()).toBeVisible();
  await expect(page.getByText("status=done; rows=1").last()).toBeVisible();
  await expect(page.getByText("2.4秒").last()).toBeVisible();
  const manualReportDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Markdown DL" }).click();
  const manualReportDownload = await manualReportDownloadPromise;
  expect(manualReportDownload.suggestedFilename()).toBe("nl2sql_manual_report_20260621102000.md");
  await expect(page.getByText("Enterprise AI Direct is not configured.")).toHaveCount(3);
  await expect(page.getByText("Feedback vector embedding is not configured.")).toHaveCount(3);
  await expect(page.getByText("Oracle-backed state persistence is ready.")).toHaveCount(2);
  await expect(page.getByText("Oracle runtime is configured.")).toBeVisible();
  await page.getByRole("button", { name: "Agent assets 更新" }).click();
  await expect(page.getByText("NL2SQL_DEFAULT_AGENT_PROFILE")).toBeVisible();
  await page.getByRole("button", { name: "Profile 更新" }).click();
  await expect(page.getByText("NL2SQL_DEFAULT_SELECT_AI")).toBeVisible();
  await page.getByRole("button", { name: "Drop Dry-run" }).click();
  await expect(page.getByText("DB profile drop 結果")).toBeVisible();
  await expect(page.getByText("dry_run").first()).toBeVisible();
  expect(api.dbProfileDropPayload?.execute).toBe(false);
  await expectNoHorizontalScroll(page);

  await page.goto("/settings/nl2sql-connection");
  await expect(page).toHaveURL(/\/engine-operations$/);
  await expect(page.getByRole("heading", { name: "エンジン運用" })).toBeVisible();
  await expect(page.getByText("Agent assets need refresh.").first()).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/settings/nl2sql-database");
  await expect(page.getByText("データベース安全境界")).toBeVisible();
  await expect(page.getByText("Oracle 運用サマリー")).toBeVisible();
  await expect(page.getByText("Oracle-backed state persistence is ready.")).toBeVisible();
  await expect(page.getByText("Feedback vector embedding is not configured.")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("table and view management pages run guarded DDL and AI workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/table-management");
  const listTab = page.getByRole("tab", { name: "テーブル一覧と詳細" });
  const createTab = page.getByRole("tab", { name: "テーブル作成" });
  const importTab = page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" });

  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await expect(createTab).toHaveAttribute("aria-selected", "false");
  await expect(importTab).toHaveAttribute("aria-selected", "false");
  await listTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByTestId("db-admin-detail-columns")).toBeVisible();
  await expect(page.getByText("テーブル数")).toBeVisible();
  await expect(page.getByText("取得元")).toBeVisible();
  await expect(page.getByText("DB 構造の取得日時")).toBeVisible();
  await expect(page.getByRole("button", { name: /表示を更新/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /DB 構造を再取得/ })).toBeVisible();
  await page.getByLabel("検索").fill("請求");
  await expect(page.getByTestId("table-management-grid").getByText("INVOICES")).toBeVisible();
  await page.getByRole("button", { name: "INVOICES を表示" }).click();
  await expect(page.getByRole("cell", { name: "取引先名" })).toBeVisible();
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
  await expect(page.getByRole("button", { name: "Drop Dry-run" })).toHaveCount(0);
  await page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByLabel("実行確認語").fill("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByText("確認済み")).toBeVisible();
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropTablePayload?.execute).toBe(true);
  expect(api.dropTablePayload?.confirmation).toBe("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" })).toHaveCount(0);

  await createTab.click();
  const createPanel = page.locator("#table-management-panel-create");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toHaveCount(0);
  await expect(createPanel).toBeVisible();
  await createPanel.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill("CREATE TABLE T1 (ID NUMBER)");
  await expect(createPanel.getByText("ADMIN_EXECUTE を正確に入力すると Oracle に実行できます。")).toBeVisible();
  await expect(createPanel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(createPanel.getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(createPanel.getByText("AI 分析")).toHaveCount(0);
  await expect(createPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(createPanel.getByText("確認済み")).toBeVisible();
  await createPanel.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("table_ddl");
  expect(api.statementsPayload?.execute).toBe(true);
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await importTab.click();
  const importPanel = page.locator("#table-management-panel-import");
  await expect(importTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toHaveCount(0);
  await expect(importPanel).toBeVisible();
  const importExecuteButton = importPanel.getByRole("button", { name: "取込を実行" });
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(importPanel.getByText("確認済み")).toBeVisible();
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
  await expect(importPanel.getByRole("button", { name: "取込 Dry-run" })).toHaveCount(0);
  await expect(importPanel.getByText("2. Dry-run 確認")).toHaveCount(0);
  const importExecuteButtonBox = await importExecuteButton.boundingBox();
  const importConfirmationBox = await importPanel.getByLabel("実行確認語").boundingBox();
  expect(importExecuteButtonBox).not.toBeNull();
  expect(importConfirmationBox).not.toBeNull();
  expect(importExecuteButtonBox!.y).toBeLessThan(importConfirmationBox!.y);
  await expect(importPanel.getByRole("button", { name: "確認語に表名を入れる" })).toHaveCount(0);
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await expect(importPanel.getByText("確認済み")).toBeVisible();
  await expect(importExecuteButton).toBeEnabled();
  await importExecuteButton.click();
  await expect(importPanel.getByText("IMPORTED_ORDERS", { exact: true })).toBeVisible();
  await expect(importPanel.getByText("2. 実行確認")).toBeVisible();
  await expect.poll(() => api.importTabularPayload?.table_name).toBe("IMPORTED_ORDERS");
  expect(api.importTabularPayload?.mode).toBe("create");
  expect(api.importTabularPayload?.execute).toBe(true);
  expect(api.importTabularPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await listTab.click();
  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/comment-management");
  await expect(page.getByRole("heading", { name: "コメント管理" })).toBeVisible();
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /COMMENT ON COLUMN "INVOICES"."TOTAL_AMOUNT" IS '税込請求金額';/
  );
  await page.getByRole("button", { name: "SQL プレビュー" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("comment_sql");
  expect(api.statementsPayload?.execute).toBe(false);

  await page.goto("/annotation-management");
  await expect(page.getByRole("heading", { name: "アノテーション管理" })).toBeVisible();
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: INVOICES/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /ALTER TABLE "INVOICES" MODIFY/
  );
  await page.getByRole("button", { name: "SQL プレビュー" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("annotation_sql");
  expect(api.statementsPayload?.execute).toBe(false);
  await expectNoHorizontalScroll(page);

  await page.goto("/view-management");
  await expect(page.getByText("ビュー一覧と詳細")).toBeVisible();
  await expect(page.getByText("ビュー数")).toBeVisible();
  await page.getByRole("listitem").filter({ hasText: "V_EMP_DEPT" }).click();
  await page.locator("details").filter({ hasText: "CREATE SQL" }).locator("summary").click();
  await expect(page.getByText('CREATE OR REPLACE VIEW "V_EMP_DEPT"')).toBeVisible();

  await page.locator("details").filter({ hasText: "JOIN/WHERE 条件抽出" }).locator("summary").click();
  await page.getByRole("button", { name: "AI で抽出" }).click();
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE.*DEPARTMENT/);
  await expect(page.getByLabel("抽出条件 (WHERE)")).toHaveValue("None");

  await page.locator("details").filter({ hasText: "危険操作" }).locator("summary").click();
  await page.getByRole("button", { name: "Drop Dry-run" }).click();
  await expect(page.getByText('DROP VIEW "V_EMP_DEPT"')).toBeVisible();
  await expect.poll(() => api.dropViewPayload?.execute).toBe(false);

  await page.locator("details").filter({ hasText: "ビュー作成" }).locator("summary").click();
  await page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill("CREATE OR REPLACE VIEW V1 AS SELECT 1 FROM DUAL");
  await expect(page.getByText("Oracle には送信せず、SQL 分割・許可ポリシー・実行対象だけ確認します。")).toBeVisible();
  await page.getByRole("button", { name: "SQL プレビュー" }).click();
  await expect(page.getByText("Dry-run のため SQL は実行していません。").first()).toBeVisible();
  await expect.poll(() => api.statementsPayload?.policy).toBe("view_ddl");
  expect(api.statementsPayload?.execute).toBe(false);
  await expectNoHorizontalScroll(page);
});

test("model settings manages training data and evaluation cases", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/settings/nl2sql-model");
  await page.getByLabel("few-shot 訓練データ").fill("粗利を見たい => SELECT PROFIT FROM INVOICES");
  await page.getByRole("button", { name: "訓練データ保存" }).click();
  await expect(page.getByText("訓練データを保存しました。")).toBeVisible();
  expect(api.profilePatchPayload?.few_shot_examples).toEqual([
    { question: "粗利を見たい", sql: "SELECT PROFIT FROM INVOICES" },
  ]);

  await page.getByRole("button", { name: "合成ケース追加" }).click();
  await expect(page.getByText("請求金額を一覧で見たい").first()).toBeVisible();

  await page.getByRole("button", { name: "評価を実行" }).click();
  await expect(page.getByText("実行可能率 100%")).toBeVisible();
  await expect(page.getByText("SELECT-only 100%")).toBeVisible();
  await expectNoHorizontalScroll(page);
});
