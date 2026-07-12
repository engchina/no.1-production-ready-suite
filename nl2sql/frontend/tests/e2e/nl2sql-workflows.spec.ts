import { expect, test, type Page, type Route } from "@playwright/test";

type JsonValue = Record<string, unknown> | unknown[];

interface MockApiState {
  previewPayload: Record<string, unknown> | null;
  executePayload: Record<string, unknown> | null;
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

async function mockNl2SqlApi(page: Page): Promise<MockApiState> {
  const state: MockApiState = {
    previewPayload: null,
    executePayload: null,
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
      classifier_version: "classifier-001",
      updated_at: "2026-06-21T10:00:00.000Z",
      example_count: classifierTrainingExamples.length,
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
  await page.route("**/api/nl2sql/classifier/training-data", (route) =>
    fulfillJson(route, {
      total_examples: classifierTrainingExamples.length,
      categories: ["既定プロファイル", "入金管理"],
      warnings: [],
      examples: classifierTrainingExamples,
    })
  );
  await page.route("**/api/nl2sql/classifier/train", (route) =>
    fulfillJson(route, {
      ready: true,
      trained: true,
      classifier_version: "classifier-002",
      updated_at: "2026-06-21T10:05:00.000Z",
      example_count: classifierTrainingExamples.length,
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
  await page.route("**/api/nl2sql/classifier/models/*", (route) => {
    if (route.request().method() === "DELETE") {
      return fulfillJson(route, {
        active_version: "",
        models: [],
      });
    }
    return route.fallback();
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

async function useOverflowSchemaCatalog(page: Page) {
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, overflowSchemaCatalog));
}

async function expectQuerySplitPaneBounds(page: Page) {
  const pane = page.getByTestId("fixed-split-pane-nl2sql-workbench");
  const left = page.getByTestId("fixed-split-pane-nl2sql-workbench-left");
  const divider = page.getByTestId("fixed-split-pane-nl2sql-workbench-divider");
  const right = page.getByTestId("fixed-split-pane-nl2sql-workbench-right");
  const schema = page.getByTestId("nl2sql-schema-reference");
  const firstTable = page.getByTestId("nl2sql-schema-table-item").first();
  const searchInput = page.getByPlaceholder("表名、列名、コメントなど");

  await expect(pane).toBeVisible();
  await expect(schema).toBeVisible();
  await expect(firstTable).toBeVisible();

  const viewport = page.viewportSize();
  const isDesktop = (viewport?.width ?? 0) >= 1280;
  if (!isDesktop) {
    await expect(divider).toBeHidden();
    const leftBox = await left.boundingBox();
    const rightBox = await right.boundingBox();
    expect(leftBox).not.toBeNull();
    expect(rightBox).not.toBeNull();
    expect(rightBox!.y).toBeGreaterThan(leftBox!.y);
    await expectNoHorizontalScroll(page);
    return;
  }

  await expect(divider).toBeVisible();
  await expect(pane).toHaveAttribute("data-split-ratio", "right-wide");
  const leftBox = await left.boundingBox();
  const dividerBox = await divider.boundingBox();
  const rightBox = await right.boundingBox();
  const schemaBox = await schema.boundingBox();
  const tableBox = await firstTable.boundingBox();
  const searchBox = await searchInput.boundingBox();
  for (const box of [leftBox, dividerBox, rightBox, schemaBox, tableBox, searchBox]) {
    expect(box).not.toBeNull();
  }

  const tolerance = 1;
  const leftRightEdge = leftBox!.x + leftBox!.width;
  expect(leftRightEdge).toBeLessThanOrEqual(dividerBox!.x + tolerance);
  expect(rightBox!.x).toBeGreaterThanOrEqual(dividerBox!.x + dividerBox!.width - tolerance);
  expect(schemaBox!.x + schemaBox!.width).toBeLessThanOrEqual(leftRightEdge + tolerance);
  expect(searchBox!.x + searchBox!.width).toBeLessThanOrEqual(leftRightEdge + tolerance);
  expect(tableBox!.x + tableBox!.width).toBeLessThanOrEqual(leftRightEdge + tolerance);
  await expectNoHorizontalScroll(page);
}

test("query workbench keeps schema reference inside the split pane with long identifiers", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.removeItem("production-ready-nl2sql.fixedSplitPane.nl2sql-workbench");
  });
  await mockNl2SqlApi(page);
  await useOverflowSchemaCatalog(page);

  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();
  await expectQuerySplitPaneBounds(page);

  const viewport = page.viewportSize();
  if ((viewport?.width ?? 0) >= 1280) {
    await page.setViewportSize({ width: 2048, height: 900 });
    await page.reload();
    await expect(page.getByText("スキーマ参照")).toBeVisible();
    await expectQuerySplitPaneBounds(page);
  }
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
  await expect(page.getByRole("code")).toContainText("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "この SQL を実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1200000" })).toBeVisible();

  const feedbackResponse = page.getByLabel("response");
  await expect(page.getByRole("heading", { name: "DBMS_CLOUD_AI feedback" })).toBeVisible();
  await expect(feedbackResponse).toHaveValue("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expect(feedbackResponse).toHaveJSProperty("readOnly", true);
  await page.getByLabel("feedback_content").fill("期待どおりの SQL です");
  await page.getByRole("button", { name: "フィードバック送信" }).click();
  await expect(page.getByText("クエリに対する feedback を送信しました。")).toBeVisible();
  expect(api.selectAiFeedbackAddPayload).toMatchObject({
    profile_id: "default",
    question: "請求金額を一覧で見たい",
    feedback_type: "positive",
    response: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    feedback_content: "期待どおりの SQL です",
    generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
  });

  await page.getByLabel("種類").selectOption("negative");
  await expect(feedbackResponse).toHaveJSProperty("readOnly", false);
  await feedbackResponse.fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByLabel("feedback_content").fill("列を請求金額だけに修正");
  await page.getByRole("button", { name: "フィードバック送信" }).click();
  expect(api.selectAiFeedbackAddPayload).toMatchObject({
    feedback_type: "negative",
    response: "SELECT TOTAL_AMOUNT FROM INVOICES",
    feedback_content: "列を請求金額だけに修正",
  });
  expect(api.previewPayload?.allowed_objects).toEqual({
    table_names: ["INVOICES"],
    columns: { INVOICES: ["TOTAL_AMOUNT"] },
  });
  expect(api.executePayload?.sql).toBe("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expectNoHorizontalScroll(page);
});

test("検索を実行すると実処理の段階別進捗と結果を表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });

  const questionText = "今月の請求金額を確認したい";
  const createdAt = "2026-06-21T10:00:00.000Z";
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
      engine: "auto",
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
  await expect(page.getByRole("heading", { name: "生成された SQL" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  const placement = await page.evaluate(() => {
    const workspace = document.querySelector('[data-testid="nl2sql-workspace-shell"]');
    const progressPanel = document.querySelector('[data-testid="nl2sql-job-progress"]');
    const resultHeading = Array.from(document.querySelectorAll("h2, h3")).find(
      (element) => element.textContent?.trim() === "生成された SQL"
    );
    if (!workspace || !progressPanel || !resultHeading) return false;
    return Boolean(
      workspace.compareDocumentPosition(progressPanel) & Node.DOCUMENT_POSITION_FOLLOWING
    ) && Boolean(progressPanel.compareDocumentPosition(resultHeading) & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(placement).toBe(true);
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

test("query workbench executes direct SELECT SQL", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByLabel("検索クエリ")).toBeVisible();
  await expect(page.getByRole("tab", { name: "SQLの実行" })).toHaveCount(0);

  const directSql = page.getByTestId("nl2sql-direct-sql");
  await expect(directSql).not.toHaveAttribute("open", "");
  await directSql.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(directSql).toHaveAttribute("open", "");

  const sqlInput = page.getByLabel("SQL", { exact: true });
  await sqlInput.focus();
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
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
  });
  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("evaluation page renders Select AI and Agent comparison details", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/evaluation");
  await expect(page.getByRole("tab", { name: "評価セット" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "SQL 分析" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "エンジン比較" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Synthetic ケース" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "SQL から質問" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "自然言語へ変換" })).toHaveCount(0);
  await expect(page.getByText("評価セット数")).toBeVisible();
  await expect(page.getByRole("heading", { name: "最近の評価実行" })).toBeVisible();
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
  await expect(page.getByRole("status").filter({ hasText: "評価実行結果を表示しました。" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "評価実行結果を表示しました。" })).toHaveCount(0);
  await expect(page.getByText("評価結果")).toBeVisible();
  const evaluationSetSelect = page.getByRole("combobox", { name: /評価セット/ });
  await expect(evaluationSetSelect).toContainText("請求ベンチマーク");
  await evaluationSetSelect.selectOption("eval-001");
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
  await expect(page.getByRole("status").filter({ hasText: "評価セットを保存しました。" })).toBeVisible();
  expect(api.evaluationSetPayload?.name).toBe("請求ベンチマーク");
  expect(api.evaluationSetPayload?.engine).toBe("select_ai");
  expect(api.evaluationSetPayload?.cases).toEqual([
    {
      question: "保存済み請求金額",
      expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
      profile_id: "default",
    },
  ]);

  await page.getByRole("tab", { name: "エンジン比較" }).click();
  await expect(page.getByRole("heading", { name: "最近の比較記録" })).toBeVisible();
  await expect(page.getByText("履歴の請求比較")).toBeVisible();
  await page.getByRole("button", { name: "表示", exact: true }).click();
  await expect(page.getByLabel("比較レポート")).toHaveValue(/Question: 履歴の請求比較/);
  await page.getByRole("button", { name: "エンジン比較" }).click();

  await expect(page.getByRole("heading", { name: "Select AI / Agent 比較" }).first()).toBeVisible();
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

  await page.getByRole("tab", { name: "Synthetic ケース" }).click();
  await page.getByRole("button", { name: "Synthetic 生成" }).click();
  await expect(page.getByRole("heading", { name: "Synthetic 評価ケース" }).first()).toBeVisible();
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

  await page.getByRole("tab", { name: "SQL 分析" }).click();
  await page.getByLabel("分析する SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL 分析" }).click();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();

  await page.getByRole("tab", { name: "SQL から質問" }).click();
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toBeVisible();
  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();
  await expect(page.getByText("SQL から質問を生成しました。")).toBeVisible();
  expect(api.reversePayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });

  await expectNoHorizontalScroll(page);
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
  await expect(page.getByTestId("sql-analysis-result-count")).toHaveText("-");

  await page.getByLabel("分析する SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByText("分析準備完了")).toBeVisible();
  await page.getByRole("button", { name: "SQL を分析" }).click();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect(page.getByText("SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY")).toBeVisible();
  await page.getByRole("button", { name: "SELECT を実行" }).click();
  await expect(page.locator("#sql-analysis-panel-execution")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByTestId("sql-analysis-result-count")).toHaveText("1");

  // 入力を変えると分析・実行結果は無効化される（タブ切替なしで同一画面上）。
  await page.getByLabel("分析する SQL").fill("SELECT INVOICE_ID FROM INVOICES");
  await expect(page.getByRole("button", { name: "SELECT を実行" })).toBeDisabled();
  await expect(page.getByText("SQL は未分析です")).toBeVisible();
  await expect(page.getByTestId("sql-analysis-result-count")).toHaveText("-");

  await expect(page.locator("#sql-analysis-panel-repair")).toBeVisible();
  await expect(page.getByText("修復候補は未生成です")).toBeVisible();
  await page.getByLabel("修復する SQL").fill("SELECT BAD_COL FROM INVOICES");
  await page.getByLabel("Oracle error message").fill("ORA-00904: invalid identifier");
  await expect(page.getByText("修復準備完了")).toBeVisible();
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
});

test("sql to question page analyzes structure and reverse-generates a question", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sql-to-question");
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toBeVisible();
  await expect(page.locator("#sql-to-question-panel-input")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expect(page.getByText("請求情報")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("1");
  await expect(page.getByText("SQL 入力待ち")).toBeVisible();

  await page.getByLabel("対象 SQL").fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByLabel("用語集を利用").check();
  await expect(page.getByText("生成準備完了")).toBeVisible();
  await page.getByRole("button", { name: "SQL 構造を分析" }).click();
  await expect(page.locator("#sql-to-question-panel-structure")).toBeVisible();
  await expect(page.getByText("SELECT 文として安全に実行できます。")).toBeVisible();
  await expect(page.getByText("構造分析済み")).toBeVisible();
  await expect.poll(() => api.analyzePayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    use_llm: true,
  });

  await expect(page.getByLabel("対象 SQL")).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByLabel("用語集を利用")).toBeChecked();
  await page.getByRole("button", { name: "質問を生成" }).click();
  await expect(page.locator("#sql-to-question-panel-result")).toBeVisible();
  await expect(page.getByText("請求金額を一覧で確認したい")).toBeVisible();
  await expect(page.getByText("質問生成済み")).toBeVisible();
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
  await page.unroute("**/api/schema/catalog");
  let failCatalogRequests = true;
  let releaseFirstCatalog: (() => void) | undefined;
  const firstCatalogGate = new Promise<void>((resolve) => {
    releaseFirstCatalog = resolve;
  });
  await page.route("**/api/schema/catalog", async (route) => {
    const shouldFail = failCatalogRequests;
    if (shouldFail) {
      await firstCatalogGate;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Schema catalog unavailable" }),
      });
      return;
    }
    await fulfillJson(route, schemaCatalog);
  });

  await page.goto("/sql-to-question");
  await expect(page.getByTestId("sql-to-question-schema-skeleton")).toBeVisible();
  await expect(page.getByRole("button", { name: "質問を生成" })).toBeDisabled();
  releaseFirstCatalog?.();

  const errorBanner = page.getByRole("alert");
  await expect(errorBanner).toContainText("接続状態と入力内容を確認して再試行してください。");
  failCatalogRequests = false;
  await errorBanner.getByRole("button", { name: "プロファイル・スキーマを再読込" }).click();
  await expect(errorBanner).toHaveCount(0);
  await expect(page.getByText("請求情報")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("1");
  await expectNoHorizontalScroll(page);
});

test("sql to question page shows a guided empty schema state", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/schema/catalog");
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, { ...schemaCatalog, tables: [] })
  );

  await page.goto("/sql-to-question");

  await expect(page.getByText("参照できる表がありません")).toBeVisible();
  await expect(page.getByText("選択プロファイルで参照できるスキーマ情報がありません。")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("0");
  await expectNoHorizontalScroll(page);
});

test("sql to question page invalidates stale results when inputs change", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [
      ...profiles,
      {
        ...profiles[0],
        id: "alternate",
        name: "代替プロファイル",
      },
    ])
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
  await expect(page.getByText("生成準備完了")).toBeVisible();
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
  await expect(page.getByTestId("feedback-management-entry-count")).toHaveText("1");
  await expect(page.getByText("NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX").first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "CONTENT" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "SQL_ID" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "SQL_TEXT" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "ATTRIBUTES" })).toBeVisible();

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
  await page.getByLabel("Similarity_Threshold", { exact: true }).fill("0.85");
  await page.getByLabel("Match_Limit", { exact: true }).fill("4");
  await page.getByRole("button", { name: "ベクトルインデックスを更新" }).click();
  await expect(page.getByText("Select AI feedback vector index を更新しました。")).toBeVisible();
  expect(api.selectAiFeedbackUpdatePayload).toEqual({
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    similarity_threshold: 0.85,
    match_limit: 4,
  });

  await page.getByRole("tab", { name: "アプリ内フィードバック" }).click();
  await expect(page.getByText("Embedding + LogisticRegression 分類器")).toHaveCount(0);
  await expect(page.getByText("質問の学習候補")).toHaveCount(0);
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

  await page.getByRole("tab", { name: "類似検索インデックス" }).click();
  await expect(page.getByRole("heading", { name: "類似検索インデックス" })).toBeVisible();
  await expect(page.getByText("oracle_26ai")).toBeVisible();
  await expect(page.getByLabel("Oracle 26ai DDL plan")).toHaveValue(/VECTOR\(1536, FLOAT32\)/);
  await page.getByLabel("しきい値").fill("0.85");
  await page.getByLabel("件数").fill("4");
  await page.getByRole("button", { name: "設定保存" }).click();
  await expect(page.getByText("Feedback 類似検索設定を保存しました。")).toBeVisible();
  expect(api.feedbackConfigPayload).toEqual({
    similarity_threshold: 0.85,
    match_limit: 4,
  });
  await page.getByRole("button", { name: "Rebuild 実行" }).click();
  const rebuildDialog = page.getByRole("alertdialog", { name: "Feedback index 再構築の確認" });
  await expect(rebuildDialog).toBeVisible();
  await rebuildDialog.getByRole("button", { name: "Rebuild 実行" }).click();
  await expect(page.getByText(/NL2SQL_RUNTIME_MODE=oracle/)).toBeVisible();
  await expect(page.getByText("ready", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "削除" }).click();
  await expect(page.getByText("Feedback entry を削除しました。")).toBeVisible();
  expect(api.feedbackEntriesDeletePayload).toEqual({ history_ids: ["hist-001"] });
  await page.getByRole("button", { name: "Clear 実行" }).click();
  const clearDialog = page.getByRole("alertdialog", { name: "Feedback index 削除の確認" });
  await expect(clearDialog).toBeVisible();
  await clearDialog.getByRole("button", { name: "Clear 実行" }).click();
  await expect(page.getByText(/clear 実行には NL2SQL_RUNTIME_MODE=oracle/)).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("legacy learning route redirects to feedback management", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/learning");
  await expect(page).toHaveURL(/\/feedback-management$/);
  await expect(page.getByRole("heading", { name: "フィードバック管理" })).toBeVisible();
  await expect(page.getByRole("link", { name: /フィードバック学習/ })).toHaveCount(0);
});

test("question classifier model management page trains classifier and finds learning candidates", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/question-learning");
  await expect(page).toHaveURL(/\/question-classifier-models$/);
  await expect(page.getByRole("heading", { name: "質問分類モデル管理" })).toBeVisible();
  await expect(page.getByText("質問学習", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Model registry")).toBeVisible();
  await expect(page.getByText("Legacy artifact 取込")).toHaveCount(0);
  await expect(page.getByText("Model artifact 取込")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "モデル一覧" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "訓練データ" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "モデル学習" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "モデルテスト" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "質問支援" })).toBeVisible();
  await expect(page.getByText("フィードバック保存")).toHaveCount(0);

  await page.getByRole("tab", { name: "訓練データ" }).click();
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

  await page.getByRole("tab", { name: "モデル学習" }).click();
  await page.getByRole("button", { name: "Classifier 学習" }).click();
  await expect(page.getByText("LogisticRegression classifier を学習しました。")).toBeVisible();

  await page.getByRole("tab", { name: "モデルテスト" }).click();
  await page.getByRole("button", { name: "分類を試す" }).click();
  await expect(page.getByText("信頼度 92%")).toBeVisible();
  await expect(page.getByText("予測カテゴリ", { exact: true })).toBeVisible();
  await expect(page.locator("td").filter({ hasText: /^92%$/ }).first()).toBeVisible();

  await page.getByRole("tab", { name: "モデル一覧" }).click();
  await page.getByRole("button", { name: "削除" }).click();
  const deleteDialog = page.getByRole("alertdialog", { name: "Classifier model 削除の確認" });
  await expect(deleteDialog).toBeVisible();
  await expect(deleteDialog.getByRole("button", { name: "Model 削除" })).toBeDisabled();
  await deleteDialog.getByLabel("実行確認語").fill("classifier-001");
  await expect(deleteDialog.getByRole("button", { name: "Model 削除" })).toBeEnabled();
  await deleteDialog.getByRole("button", { name: "キャンセル" }).click();

  await page.getByRole("tab", { name: "質問支援" }).click();
  await expect(page.getByText("質問の学習候補")).toBeVisible();
  await page.getByRole("button", { name: "推薦・書き換え" }).click();
  await expect(page.getByText("信頼度 94%")).toBeVisible();
  await expect(page.getByText("請求金額を一覧で見たい")).toBeVisible();
  await expect(page.getByText("INVOICES", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "類似履歴検索" }).click();
  await expect(page.getByText("類似度 90%")).toBeVisible();
  await expect(page.getByText("請求金額の履歴と近い質問です。")).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await page.getByRole("tab", { name: "モデル一覧" }).click();
  await expect(page.getByText("Legacy artifact 取込")).toHaveCount(0);
  await expect(page.getByText("Model artifact 取込")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.unroute("**/api/nl2sql/classifier/models");
  await page.route("**/api/nl2sql/classifier/models", (route) =>
    fulfillJson(route, { active_version: "", models: [] })
  );
  await page.goto("/question-classifier-models");
  await expect(page.getByText("保存済み model version はありません")).toHaveCount(2);
  await expect(page.getByText("Legacy artifact 取込")).toHaveCount(0);
  await expect(page.getByText("Model artifact 取込")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("glossary page manages global terms only", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/glossary-rules");
  const statusBar = page.getByLabel("用語管理ステータス");
  await expect(statusBar.getByText("グローバル用語", { exact: true })).toBeVisible();
  await expect(statusBar.getByText("Profile 数", { exact: true })).toHaveCount(0);
  await expect(statusBar.getByText("選択中 Profile", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  // 「グローバルルール」は独立ナビ/専用ページ(/global-rules)として存在するため、
  // 用語・同義語ページ本文(main)に混在しないことのみ検証(サイドバーのナビ項目は除外)。
  await expect(page.locator("main").getByText("グローバルルール")).toHaveCount(0);
  await expect(page.getByTestId("glossary-terms-preview").getByRole("columnheader", { name: "No." })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-row-number").first()).toHaveText("1");
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "売上" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "INVOICES.TOTAL_AMOUNT" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "用語11" })).toHaveCount(0);
  await expect(page.getByTestId("glossary-terms-pagination")).toContainText("1-10 / 21 件");
  await page.getByTestId("glossary-terms-pagination").getByRole("button", { name: "次へ" }).click();
  await expect(page.getByTestId("glossary-terms-row-number").first()).toHaveText("11");
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "用語11" })).toBeVisible();

  await page.getByRole("button", { name: "サーバーから読込" }).click();
  await expect(page.getByText("サーバーの最新内容を読み込みました。")).toBeVisible();
  await expect(page.getByTestId("glossary-terms-pagination")).toContainText("1 / 3 ページ");

  await page.getByLabel("グローバル用語集 Excel 取込").setInputFiles({
    name: "terms.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mock"),
  });
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "粗利" })).toBeVisible();
  await expect(page.getByTestId("glossary-terms-preview").getByRole("cell", { name: "INVOICES.PROFIT" })).toBeVisible();
  await expect(page.getByLabel("グローバルルール Excel 取込")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "グローバルルール Excel 出力" })).toHaveCount(0);

  const termsDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "グローバル用語集 Excel 出力" }).click();
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

  const reloadButton = page.getByRole("button", { name: "サーバーから読込" });
  const reloadClick = reloadButton.click();
  await reloadStarted;
  await expect(reloadButton).toBeDisabled();
  releaseReload();
  await reloadClick;
  await expect(page.getByRole("alert")).toContainText("サーバー読込エラー");
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

  await page.getByRole("button", { name: "データを表示" }).click();
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("1-10 / 12 件");
  await expect(page.getByRole("cell", { name: "顧客11" })).toHaveCount(0);
  await page.getByRole("button", { name: "次へ" }).click();
  await expect(page.getByRole("cell", { name: "顧客11" })).toBeVisible();
  await expect(page.getByTestId("query-results-pagination")).toContainText("11-12 / 12 件");
  await expect.poll(() => api.previewDataPayload?.object_name).toBe("V_EMP_DEPT");
  expect(api.previewDataPayload?.limit).toBe(100);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "XLSX ダウンロード" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("v_emp_dept_preview.xlsx");
  await expect.poll(() => api.previewDataExportPayload?.object_name).toBe("V_EMP_DEPT");
  expect(api.previewDataExportPayload?.limit).toBe(100);
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
  await page.locator("#data-management-panel-csv").getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(page.locator("#data-management-panel-csv").getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(csvUploadButton).toBeEnabled();
  await csvUploadButton.click();
  await expect(page.getByText("UNKNOWN_COLUMN", { exact: false }).first()).toBeVisible();
  await expect.poll(() => api.csvUploadPayload?.table_name).toBe("INVOICES");
  expect(api.csvUploadPayload?.mode).toBe("insert");
  expect(api.csvUploadPayload?.confirmation).toBe("ADMIN_EXECUTE");

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
  await syntheticPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
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
  expect(api.syntheticDataPayload?.confirmation).toBe("ADMIN_EXECUTE");
  expect(api.syntheticDataPayload?.profile_name).toBe("NL2SQL_DEFAULT_PROFILE");
  expect(api.syntheticDataPayload?.object_list).toEqual([]);
  expect(api.syntheticDataPayload?.rows_per_table).toBe(1);
  expect(api.syntheticDataPayload?.sample_rows).toBe(5);
  expect(api.syntheticDataPayload?.use_comments).toBe(true);
  await expectNoHorizontalScroll(page);

  await page.goto("/settings/nl2sql-database");
  await expect(page.getByRole("heading", { name: "NL2SQL 安全境界・Readiness" })).toBeVisible();
  const boundaryTab = page.getByRole("tab", { name: "安全境界" });
  const readinessTab = page.getByRole("tab", { name: "Readiness" });
  await expect(boundaryTab).toHaveAttribute("aria-selected", "true");
  await expect(readinessTab).toHaveAttribute("aria-selected", "false");
  await boundaryTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(readinessTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#nl2sql-database-panel-readiness")).toBeVisible();
  await expect(page.getByText("Oracle-backed state persistence is ready.")).toBeVisible();
  await expect(page.getByText("Feedback vector embedding is not configured.")).toBeVisible();
  await expect(page.getByText("CSV インポート")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "投入" })).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(boundaryTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#nl2sql-database-panel-boundary")).toBeVisible();
  await expect(page.locator("#nl2sql-database-panel-boundary").getByRole("heading", { name: "SQL 安全境界" })).toBeVisible();
  await expect(page.getByText("SELECT/WITH").first()).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("table and view management pages run guarded DDL and AI workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/table-management");
  // 一覧が既定。作成はアクションボタンで開き、一覧に戻るで戻る。
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await page.getByTestId("table-management-actions").getByRole("button", { name: "テーブル作成" }).click();
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
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
  await page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByLabel("実行確認語").fill("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" }).getByText("確認済み", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropTablePayload?.confirmation).toBe("INVOICES");
  expect(api.dropTablePayload?.confirmation).toBe("INVOICES");
  await expect(page.getByRole("dialog", { name: "DROP TABLE の確認" })).toHaveCount(0);

  await page.getByTestId("table-management-actions").getByRole("button", { name: "テーブル作成" }).click();
  const createPanel = page.locator("#table-management-panel-create");
  await expect(page.getByTestId("table-management-grid")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toHaveCount(0);
  await expect(createPanel).toBeVisible();
  await createPanel.getByLabel("SQL(セミコロン区切りで複数文を入力可能)").fill("CREATE TABLE T1 (ID NUMBER)");
  await expect(createPanel.getByText("ADMIN_EXECUTE を正確に入力すると Oracle に実行できます。")).toBeVisible();
  await expect(createPanel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(createPanel.getByLabel("Oracle に実行する")).toHaveCount(0);
  await expect(createPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(createPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await createPanel.getByRole("button", { name: "SQL 実行" }).click();
  await expect.poll(() => api.statementsPayload?.policy).toBe("table_ddl");
  expect(api.statementsPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("table-management-actions").getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).click();
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
  expect(importExecuteButtonBox!.y).toBeLessThan(importConfirmationBox!.y);
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
  await expect(page.getByText("ビュー数")).toBeVisible();
  await expect(page.getByTestId("view-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "V_EMP_DEPT を表示" }).click();
  const viewColumnsTab = page.getByRole("tab", { name: "列情報" });
  const viewDdlTab = page.getByRole("tab", { name: "DDL" });
  await expect(viewColumnsTab).toHaveAttribute("aria-selected", "true");
  await viewColumnsTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(viewDdlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText('CREATE OR REPLACE VIEW "V_EMP_DEPT"')).toBeVisible();

  await page.getByTestId("view-management-actions").getByRole("button", { name: "JOIN/WHERE 条件抽出" }).click();
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

  await page.getByTestId("view-management-actions").getByRole("button", { name: "ビュー作成" }).click();
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

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/settings/nl2sql-model");
  await expect(page).toHaveURL(/\/profiles\?profile=[^#]+#profile-learning$/);
  await expect(page.locator("#profile-learning")).toBeVisible();
  await expect(page.getByLabel("few-shot 例")).toBeVisible();
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await page.getByLabel("few-shot 例").fill("粗利を見たい => SELECT PROFIT FROM INVOICES");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("プロファイルを保存しました。")).toBeVisible();
  expect(api.profilePatchPayload?.few_shot_examples).toEqual([
    { question: "粗利を見たい", sql: "SELECT PROFIT FROM INVOICES" },
  ]);

  await page.getByRole("button", { name: "Select AI Profile 更新" }).click();
  await expect(page.getByText("NL2SQL_DEFAULT_SELECT_AI")).toBeVisible();
  await page.getByRole("button", { name: "Agent assets 更新" }).click();
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
