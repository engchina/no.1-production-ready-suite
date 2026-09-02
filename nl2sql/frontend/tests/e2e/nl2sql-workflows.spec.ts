import { expect, test, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import {
  expectSplitPaneReservedTrack,
  expectSplitPaneStacked,
} from "./_helpers/fixed-split-pane";
import { dropFiles } from "./_helpers/file-dropzone";

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

async function clickRowAction(page: Page, testId: string, name: string) {
  const trigger = page.getByTestId(`${testId}-trigger`);
  await expect(trigger).toBeVisible();
  await trigger.click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}

async function clickObjectDetailAction(page: Page, testId: string, name: string) {
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

async function expectContentActionsRightAligned(actions: Locator) {
  const metrics = await actions.evaluate((node) => {
    const group = node.querySelector('[role="group"]');
    const firstButton = group?.querySelector("button");
    const panel = node.closest('[role="tabpanel"]') ?? node.parentElement;
    const code = panel?.querySelector("pre, textarea");
    if (!group || !firstButton || !panel || !code) return null;
    const buttonRect = firstButton.getBoundingClientRect();
    const groupRect = group.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const codeRect = code.getBoundingClientRect();
    return {
      codeLeft: codeRect.left,
      firstButtonLeft: buttonRect.left,
      groupRight: groupRect.right,
      panelRight: panelRect.right,
    };
  });
  expect(metrics).not.toBeNull();
  expect(metrics!.groupRight).toBeGreaterThanOrEqual(metrics!.panelRight - 20);
  expect(metrics!.firstButtonLeft).toBeGreaterThan(metrics!.codeLeft);
}

async function expectButtonBelowInput(input: Locator, button: Locator) {
  await expect(input).toBeVisible();
  await expect(button).toBeVisible();
  const inputBox = await input.boundingBox();
  const buttonBox = await button.boundingBox();
  expect(inputBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(buttonBox!.y).toBeGreaterThan(inputBox!.y + inputBox!.height);
}

async function expectOneLineWithoutOverflow(locator: Locator) {
  await expect(locator).toBeVisible();
  const metrics = await locator.evaluate((node) => {
    const element = node as HTMLElement;
    const style = window.getComputedStyle(element);
    return {
      clientWidth: element.clientWidth,
      lineHeight: Number.parseFloat(style.lineHeight),
      offsetHeight: element.offsetHeight,
      scrollWidth: element.scrollWidth,
      whiteSpace: style.whiteSpace,
    };
  });
  expect(metrics.whiteSpace).toBe("nowrap");
  expect(metrics.offsetHeight).toBeLessThanOrEqual(metrics.lineHeight + 1);
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function expectEqualFilterWidths(search: Locator, owner: Locator) {
  await expect
    .poll(async () => {
      const [searchBox, ownerBox] = await Promise.all([search.boundingBox(), owner.boundingBox()]);
      if (!searchBox || !ownerBox) return Number.POSITIVE_INFINITY;
      return Math.abs(searchBox.width - ownerBox.width);
    })
    .toBeLessThanOrEqual(2);
}

function nl2sqlQuestionInput(scope: Page | Locator) {
  return scope.locator("#nl2sql-question-input");
}

async function openNl2SqlExecutionOptions(page: Page) {
  const disclosure = page.getByRole("button", { name: /実行オプション/ });
  if ((await disclosure.getAttribute("aria-expanded")) !== "true") {
    await disclosure.click();
  }
  await expect(disclosure).toHaveAttribute("aria-expanded", "true");
}

function directSqlInput(scope: Page | Locator) {
  return scope.locator("#direct-sql-input");
}

function adminSqlInput(scope: Page | Locator) {
  return scope.locator("#admin-sql-input");
}

const ADMIN_SQL_MUTATION_TOKEN =
  /\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b/i;
const ADMIN_SQL_Q_QUOTE_CLOSERS: Record<string, string> = {
  "[": "]",
  "(": ")",
  "{": "}",
  "<": ">",
};

function stripAdminSqlLeadingComments(sql: string): string {
  let rest = sql.trim();
  let changed = true;
  while (changed) {
    changed = false;
    if (rest.startsWith("--")) {
      const nextLine = rest.indexOf("\n");
      rest = nextLine >= 0 ? rest.slice(nextLine + 1).trimStart() : "";
      changed = true;
    }
    if (rest.startsWith("/*")) {
      const close = rest.indexOf("*/");
      if (close < 0) return rest;
      rest = rest.slice(close + 2).trimStart();
      changed = true;
    }
  }
  return rest;
}

function maskAdminSqlLiteralsAndComments(sql: string): string {
  const text = String(sql || "");
  let out = "";
  let index = 0;
  while (index < text.length) {
    const char = text[index] ?? "";
    const nextChar = text[index + 1] ?? "";
    if (char === "-" && nextChar === "-") {
      const newline = text.indexOf("\n", index);
      const end = newline >= 0 ? newline : text.length;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    if (char === "/" && nextChar === "*") {
      const close = text.indexOf("*/", index + 2);
      if (close < 0) {
        out += text.slice(index);
        break;
      }
      const end = close + 2;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    const previous = index > 0 ? text[index - 1] ?? "" : "";
    if (
      (char === "q" || char === "Q") &&
      nextChar === "'" &&
      index + 2 < text.length &&
      !/[A-Za-z0-9_$#]/u.test(previous)
    ) {
      const opener = text[index + 2] ?? "";
      const closer = ADMIN_SQL_Q_QUOTE_CLOSERS[opener] ?? opener;
      const close = text.indexOf(`${closer}'`, index + 3);
      if (close < 0) {
        out += text.slice(index);
        break;
      }
      const end = close + 2;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    if (char === "'" || char === '"') {
      let end = index + 1;
      let closed = false;
      while (end < text.length) {
        if (text[end] !== char) {
          end += 1;
          continue;
        }
        if (char === "'" && text[end + 1] === "'") {
          end += 2;
          continue;
        }
        closed = true;
        break;
      }
      if (!closed) {
        out += text.slice(index);
        break;
      }
      end += 1;
      out += " ".repeat(end - index);
      index = end;
      continue;
    }
    out += char;
    index += 1;
  }
  return out;
}

function isMockAdminSqlSelect(sql: string): boolean {
  const stripped = stripAdminSqlLeadingComments(sql);
  const masked = maskAdminSqlLiteralsAndComments(stripped);
  const normalized = masked.trim().replace(/;+$/g, "").trim();
  if (!normalized || normalized.includes(";")) return false;
  if (ADMIN_SQL_MUTATION_TOKEN.test(normalized)) return false;
  return /^(select|with)\b/i.test(normalized.replace(/^\(+/u, "").trimStart());
}

function sqlToQuestionInput(scope: Page | Locator) {
  return scope.locator("#sql-to-question-sql-input");
}

async function expectRequiredTextarea(scope: Page | Locator, id: string, label: string) {
  const field = scope.locator(`#${id}`);
  const fieldLabel = scope.locator(`label[for="${id}"]`);
  await expect(fieldLabel).toContainText(label);
  await expect(fieldLabel.locator('[aria-hidden="true"]')).toHaveText("*");
  await expect(field).toHaveAttribute("required", "");
  await expect(field).toHaveAttribute("aria-required", "true");
}

async function expectSameVisualWidth(actual: Locator, expected: Locator, tolerance = 4) {
  await expect(actual).toBeVisible();
  await expect(expected).toBeVisible();
  const actualBox = await actual.boundingBox();
  const expectedBox = await expected.boundingBox();
  expect(actualBox).not.toBeNull();
  expect(expectedBox).not.toBeNull();
  expect(Math.abs(actualBox!.width - expectedBox!.width)).toBeLessThanOrEqual(tolerance);
}

async function expectTopToBottomOrder(...locators: Locator[]) {
  const boxes: Array<{ x: number; y: number; width: number; height: number }> = [];
  for (const locator of locators) {
    await expect(locator).toBeVisible();
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    boxes.push(box!);
  }
  for (let index = 1; index < boxes.length; index += 1) {
    expect(boxes[index].y).toBeGreaterThanOrEqual(boxes[index - 1].y + boxes[index - 1].height - 1);
  }
}

async function expectCsvUploadLayout(csvPanel: Locator) {
  const tableSection = csvPanel.getByTestId("data-csv-table-section");
  const fileField = csvPanel.getByTestId("data-csv-file-field");
  const modeField = csvPanel.getByTestId("data-csv-mode-field");
  const executionFieldset = csvPanel.getByTestId("data-csv-execution-fieldset");

  await expectSameVisualWidth(tableSection, fileField);
  await expectSameVisualWidth(modeField, fileField);
  await expectSameVisualWidth(executionFieldset, fileField);
  await expectTopToBottomOrder(tableSection, fileField, modeField, executionFieldset);
  await expect(modeField.getByText("DELETE & INSERT(全置換)", { exact: true })).toHaveCount(1);
}

function mainScroller(page: Page) {
  return page.locator('main[aria-label="メイン領域"]');
}

async function mainScrollTop(page: Page) {
  return mainScroller(page).evaluate((node) => node.scrollTop);
}

async function expectMainScrolledBelowTop(page: Page) {
  await expect.poll(() => mainScrollTop(page)).toBeGreaterThan(0);
}

async function expectAppDialogOverlayCoversViewport(page: Page) {
  const overlay = page.getByTestId("app-dialog-overlay");
  await expect(overlay).toBeVisible();
  const metrics = await overlay.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      backgroundColor: window.getComputedStyle(node).backgroundColor,
    };
  });

  expect(Math.abs(metrics.left)).toBeLessThanOrEqual(1);
  expect(Math.abs(metrics.top)).toBeLessThanOrEqual(1);
  expect(Math.abs(metrics.right - metrics.viewportWidth)).toBeLessThanOrEqual(1);
  expect(Math.abs(metrics.bottom - metrics.viewportHeight)).toBeLessThanOrEqual(1);
  expect(Math.abs(metrics.width - metrics.viewportWidth)).toBeLessThanOrEqual(1);
  expect(Math.abs(metrics.height - metrics.viewportHeight)).toBeLessThanOrEqual(1);
  expect(metrics.backgroundColor).toMatch(/^(rgba\(0, 0, 0, 0\.6\)|oklab\(0 0 0 \/ 0\.6\))$/u);
}

export async function expectNeutralDangerConfirmationSurface(surface: Locator) {
  await expect(surface).toHaveClass(/max-w-md/);
  await expect(surface).toHaveClass(/bg-card/);
  await expect(surface).toHaveClass(/border-border/);
  await expect(surface).not.toHaveClass(/border-l-danger/);
  await expect(surface).not.toHaveClass(/bg-danger-bg/);
  await expect(surface.locator(".border-l-danger")).toHaveCount(0);
  await expect(surface.getByRole("button", { name: "閉じる" })).toHaveCount(0);
}

async function expectExecutionConfirmationFieldNoLeftAccent(surface: Locator) {
  await expect(surface).toHaveClass(/bg-(card|background)/);
  await expect(surface).toHaveClass(/border-border/);
  await expect(surface).not.toHaveClass(/border-l-danger/);
  await expect(surface).not.toHaveClass(/bg-danger-bg/);
}

async function expectOnlyConfirmationFieldHasNoLeftAccent(dialog: Locator) {
  const confirmationField = dialog.getByTestId("execution-confirmation-field");
  await expect(confirmationField).toHaveCount(1);
  await expectExecutionConfirmationFieldNoLeftAccent(confirmationField);
  await expect(dialog.locator(".border-l-danger")).toHaveCount(0);
  await expect(dialog.locator(".bg-danger-bg")).toHaveCount(0);
}

type JsonValue = Record<string, unknown> | unknown[];

interface MockApiState {
  previewPayload: Record<string, unknown> | null;
  executePayload: Record<string, unknown> | null;
  jobPayload: Record<string, unknown> | null;
  adminExecutePayload: Record<string, unknown> | null;
  feedbackPayload: Record<string, unknown> | null;
  adminFeedbackPayload: Record<string, unknown> | null;
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
  truncateTablePayload: Record<string, unknown> | null;
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
  classifierTrainingDeleteId: string | null;
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
  profile_category: "既定プロファイル",
  rewritten_question: "履歴から再実行したい請求金額",
  safety_is_safe: true,
  result_row_count: 1,
  result_columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
  feedback_comment: "",
  admin_feedback_rating: null,
  admin_feedback_content: "",
  admin_feedback_updated_at: "",
};

const longQuestionText =
  '対象テーブル："部署情報を管理するテーブル" 抽出項目："DEPARTMENT_ID", "DEPARTMENT_NAME", "LOCATION", "CREATED_AT" 抽出条件：管理部門および地方拠点を含むすべての部署を確認し、VERY_LONG_UNBROKEN_BUSINESS_QUERY_IDENTIFIER_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ も条件として扱う';

const longHistoryItem = {
  ...historyItem,
  id: "hist-long-query",
  question: longQuestionText,
  rewritten_question: longQuestionText,
  elapsed_ms: 9400,
  feedback_rating: "good",
  feedback_comment: "長い質問でも一覧と詳細の表示が崩れないことを確認します。",
  admin_feedback_rating: "good",
  admin_feedback_content: "管理者確認済みの長い質問です。",
  admin_feedback_updated_at: "2026-06-21T10:02:00.000Z",
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
    profile_name: index % 2 === 0 ? "既定プロファイル" : "入金管理",
    profile_category: index % 2 === 0 ? "既定プロファイル" : "入金管理",
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
    jobPayload: null,
    adminExecutePayload: null,
    feedbackPayload: null,
    adminFeedbackPayload: null,
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
    truncateTablePayload: null,
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
    classifierTrainingDeleteId: null,
    classifierModelListRequests: 0,
  };
  let classifierExamples: Record<string, unknown>[] = [...classifierTrainingExamples];
  let classifierIsStale = false;
  let feedbackCandidateAdded = false;
  let appAdminFeedbackRating: "good" | "bad" | null = "good";
  let appAdminFeedbackContent = "管理者確認済み";
  let appAdminFeedbackUpdatedAt = "2026-06-21T10:03:00.000Z";
  const classifierTrainingDataResponse = () => ({
    total_examples: classifierExamples.length,
    categories: Array.from(new Set(classifierExamples.map((example) => String(example.category)))),
    warnings: [],
    examples: classifierExamples,
  });
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
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [{ owner: "APP", is_current: true, table_count: 3, view_count: 1 }],
      excluded_oracle_maintained_count: 0,
    })
  );
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
      profile_id: "",
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
        profile_id: "",
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
      profile_id: "",
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
      profile_id: "",
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
          qualified_name: "APP.INVOICES",
          object_type: "table",
          row_count: 2,
          comment: "請求情報",
        },
        {
          name: "PAYMENTS",
          owner: "APP",
          qualified_name: "APP.PAYMENTS",
          object_type: "table",
          row_count: 1,
          comment: "入金情報",
        },
        {
          name: "AUDIT_LOG",
          owner: "APP",
          qualified_name: "APP.AUDIT_LOG",
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
          qualified_name: "APP.V_EMP_DEPT",
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
    const ownerPrefix = (url.searchParams.get("owner_prefix") ?? "").toUpperCase();
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const cursor = url.searchParams.get("cursor") ?? "";
    const allItems = [
      { name: "INVOICES", owner: "APP", qualified_name: "APP.INVOICES", object_type: "table", row_count: 2, comment: "請求情報" },
      { name: "PAYMENTS", owner: "APP", qualified_name: "APP.PAYMENTS", object_type: "table", row_count: 1, comment: "入金情報" },
      { name: "AUDIT_LOG", owner: "APP", qualified_name: "APP.AUDIT_LOG", object_type: "table", row_count: 1, comment: "監査ログ" },
      { name: "V_EMP_DEPT", owner: "APP", qualified_name: "APP.V_EMP_DEPT", object_type: "view", row_count: null, comment: "社員と部署" },
      { name: "DBTOOLS$EXECUTION_HISTORY", owner: "APP", qualified_name: "APP.DBTOOLS$EXECUTION_HISTORY", object_type: "table", row_count: 4, comment: "内部履歴" },
      { name: "SYS#AUDIT_VIEW", owner: "APP", qualified_name: "APP.SYS#AUDIT_VIEW", object_type: "view", row_count: null, comment: "内部監査" },
    ];
    const items = allItems.filter((item) => {
      if (ownerPrefix && !item.owner.toUpperCase().startsWith(ownerPrefix)) return false;
      if (objectType !== "all" && item.object_type !== objectType) return false;
      if (rowState === "with_rows" && !(typeof item.row_count === "number" && item.row_count > 0)) return false;
      if (rowState === "empty_rows" && item.row_count !== 0) return false;
      if (rowState === "unknown_rows" && item.row_count !== null) return false;
      return !query || `${item.name} ${item.owner} ${item.comment}`.toLowerCase().includes(query);
    });
    const pagedItems =
      objectType === "table" && !query
        ? cursor === "tables-page-2"
          ? items.slice(2)
          : items.slice(0, 2)
        : items;
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: pagedItems,
      total: items.length,
      table_count: items.filter((item) => item.object_type === "table").length,
      view_count: items.filter((item) => item.object_type === "view").length,
      next_cursor: objectType === "table" && !query && !cursor && items.length > 2 ? "tables-page-2" : null,
      refreshed_at: schemaCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES?*", (route) =>
    {
      const includeDdl = new URL(route.request().url()).searchParams.get("include_ddl") === "1";
      return fulfillJson(route, {
        name: "INVOICES",
        owner: "APP",
        qualified_name: "APP.INVOICES",
        object_type: "table",
        row_count: 2,
        comment: "請求情報",
        columns: schemaCatalog.tables[0].columns,
        ddl: includeDdl
          ? 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER);\nCOMMENT ON TABLE "INVOICES" IS \'請求情報\';'
          : "",
        warnings: [],
      });
    }
  );
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES", (route) =>
    fulfillJson(route, {
      name: "INVOICES",
      owner: "APP",
      qualified_name: "APP.INVOICES",
      object_type: "table",
      row_count: 2,
      comment: "請求情報",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "INVOICES" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER);\nCOMMENT ON TABLE "INVOICES" IS \'請求情報\';',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/INVOICES/export.xlsx**", (route) =>
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
      qualified_name: "APP.V_EMP_DEPT",
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
    {
      const includeDdl = new URL(route.request().url()).searchParams.get("include_ddl") === "1";
      return fulfillJson(route, {
        name: "V_EMP_DEPT",
        owner: "APP",
        qualified_name: "APP.V_EMP_DEPT",
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
        ddl: includeDdl
          ? 'CREATE OR REPLACE VIEW "V_EMP_DEPT" AS SELECT E.EMPLOYEE_NAME FROM EMPLOYEE E JOIN DEPARTMENT D ON D.DEPARTMENT_ID = E.DEPARTMENT_ID;'
          : "",
        warnings: [],
      });
    }
  );
  await page.route("**/api/nl2sql/db-admin/views/V_EMP_DEPT/export.xlsx**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "Content-Disposition": 'attachment; filename="v_emp_dept_columns.xlsx"',
      },
      body: "xlsx",
    })
  );
  await page.route("**/api/nl2sql/db-admin/preview-data", (route) => {
    state.previewDataPayload = route.request().postDataJSON() as Record<string, unknown>;
    const owner = String(state.previewDataPayload.owner ?? "").trim();
    const objectName = String(state.previewDataPayload.object_name ?? "INVOICES");
    const rowLimit = Number(state.previewDataPayload.limit ?? 100);
    const returnedRowCount = rowLimit === 0 ? 25 : rowLimit;
    const objectRef = owner ? `"${owner}"."${objectName}"` : `"${objectName}"`;
    const rows = Array.from({ length: returnedRowCount }, (_, index) => ({
      CUSTOMER_NAME: `顧客${String(index + 1).padStart(2, "0")}`,
      TOTAL_AMOUNT: (index + 1) * 1000,
    }));
    return fulfillJson(route, {
      runtime: "deterministic",
      sql:
        rowLimit === 0
          ? `SELECT * FROM ${objectRef}`
          : `SELECT * FROM ${objectRef} FETCH FIRST ${rowLimit} ROWS ONLY`,
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
    const uploadOwner = String(state.csvUploadPayload.owner ?? "").trim();
    const uploadTable = String(state.csvUploadPayload.table_name ?? "INVOICES");
    return fulfillJson(route, {
      table_name: uploadOwner ? `${uploadOwner}.${uploadTable}` : uploadTable,
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
        {
          source_name: "ORDER_NAME",
          column_name: "ORDER_NAME",
          data_type: "VARCHAR2(4 CHAR)",
          nullable: true,
        },
      ],
      row_count: 1,
      executed: true,
      ddl: `CREATE TABLE ${tableName} (ORDER_ID NUMBER, ORDER_NAME VARCHAR2(4 CHAR))`,
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
    const isSelect = isMockAdminSqlSelect(sql);
    const isPartialDml = /\bMISSING_TABLE\b/i.test(sql);
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
    if (isPartialDml) {
      const [successfulSql, failedSql] = sql.split(";").map((statement) => statement.trim());
      return fulfillJson(route, {
        executed: true,
        runtime: "oracle",
        select_result: null,
        statements: [
          {
            index: 1,
            statement_type: "INSERT",
            status: "success",
            sql: successfulSql,
            row_count: 1,
            message: "1 rows affected",
            elapsed_ms: 1,
            error_message: "",
          },
          {
            index: 2,
            statement_type: "UPDATE",
            status: "error",
            sql: failedSql,
            row_count: null,
            message: "",
            elapsed_ms: 2,
            error_message: "ORA-00942: table or view does not exist",
          },
        ],
        committed: true,
        rolled_back: false,
        warnings: ["部分的に成功しました（1/2 件）。成功した SQL はコミット済みです。"],
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
    const owner = String(state.dropTablePayload.owner ?? "").trim();
    const tableRef = owner ? `"${owner}"."${tableName}"` : `"${tableName}"`;
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
          sql: `DROP TABLE ${tableRef} PURGE`,
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
  await page.route("**/api/nl2sql/db-admin/truncate-table", (route) => {
    state.truncateTablePayload = route.request().postDataJSON() as Record<string, unknown>;
    const tableName = String(state.truncateTablePayload.table_name ?? "INVOICES");
    const owner = String(state.truncateTablePayload.owner ?? "").trim();
    const tableRef = owner ? `"${owner}"."${tableName}"` : `"${tableName}"`;
    return fulfillJson(route, {
      executed: true,
      runtime: "deterministic",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "TRUNCATE",
          status: "success",
          sql: `TRUNCATE TABLE ${tableRef}`,
          row_count: 0,
          message: "executed",
          elapsed_ms: 0,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/nl2sql/db-admin/drop-view", (route) => {
    state.dropViewPayload = route.request().postDataJSON() as Record<string, unknown>;
    const viewName = String(state.dropViewPayload.view_name ?? "V_EMP_DEPT");
    const owner = String(state.dropViewPayload.owner ?? "").trim();
    const viewRef = owner ? `"${owner}"."${viewName}"` : `"${viewName}"`;
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
          sql: `DROP VIEW ${viewRef}`,
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
    return fulfillJson(route, {
      join_text:
        "JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\nON: EMPLOYEE(e).DEPARTMENT_ID = DEPARTMENT(d).DEPARTMENT_ID",
      where_text: "EMPLOYEE(e).STATUS = 'A'",
      source: "deterministic",
      warnings: [],
      prompt_profile: "sql_structure",
      structure_markdown:
        "## SQL構造分析\n\n### JOIN句\n- JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\n\n### WHERE句\n- EMPLOYEE(e).STATUS = 'A'",
    });
  });
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
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) => {
    const profileId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2) ?? "");
    const profile = profiles.find((item) => item.id === profileId) ?? profiles[0];
    return fulfillJson(route, {
      id: profile.id,
      name: profile.name,
      category: profile.category,
      description: profile.description,
      allowed_tables: profile.allowed_tables,
      allowed_views: profile.allowed_views,
      archived: profile.archived,
      object_scope_version: 1,
      version: 1,
      etag: `etag-${profile.id}`,
      updated_at: "2026-06-21T10:00:00.000Z",
    });
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
            admin_feedback_rating: appAdminFeedbackRating,
            admin_feedback_content: appAdminFeedbackContent,
            admin_feedback_updated_at: appAdminFeedbackUpdatedAt,
            training_status: feedbackCandidateAdded ? "added" : "pending",
            training_example_id: feedbackCandidateAdded ? "feedback-hist-001" : "",
          },
          {
            ...historyItem,
            id: "hist-002",
            question: "別プロファイルの請求確認",
            generated_sql: "SELECT 2 FROM DUAL",
            executable_sql: "SELECT 2 FROM DUAL",
            feedback_rating: "bad",
            feedback_comment: "条件が違います",
            admin_feedback_rating: null,
            admin_feedback_content: "",
            admin_feedback_updated_at: "",
            training_status: "pending",
            training_example_id: "",
          },
        ],
        total: 2,
        next_cursor: "",
      });
    }
    state.feedbackPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      history_id: "hist-001",
      rating: "good",
      saved: true,
      comment: "SQL は期待通りです",
      feedback_content: "SQL は期待通りです",
    });
  });
  await page.route("**/api/nl2sql/feedback/admin-review", (route) => {
    state.adminFeedbackPayload = route.request().postDataJSON() as Record<string, unknown>;
    const content = String(state.adminFeedbackPayload.feedback_content ?? "");
    const rating = state.adminFeedbackPayload.rating === "bad" ? "bad" : "good";
    const registerSelectAi = Boolean(state.adminFeedbackPayload.register_select_ai_feedback);
    appAdminFeedbackRating = rating;
    appAdminFeedbackContent = content;
    appAdminFeedbackUpdatedAt = "2026-06-21T10:04:00.000Z";
    return fulfillJson(route, {
      history_id: state.adminFeedbackPayload.history_id ?? "hist-001",
      rating,
      saved: true,
      feedback_content: content,
      similar_history_publish: {
        history_id: state.adminFeedbackPayload.history_id ?? "hist-001",
        status: rating === "good" ? "published" : "unpublished",
        runtime: "deterministic",
        executed: false,
        table_name: "",
        index_name: "",
        warnings: [],
      },
      select_ai_feedback: registerSelectAi
        ? {
            runtime: "oracle",
            executed: true,
            status: "added",
            profile_name: state.adminFeedbackPayload.select_ai_profile_name ?? "NL2SQL_DEFAULT_PROFILE",
            index_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX",
            table_name: "NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX$VECTAB",
            sql_text: "select ai showsql 請求金額を一覧で見たい",
            stored_feedback_type: "POSITIVE",
            plsql_preview: "BEGIN DBMS_CLOUD_AI.FEEDBACK(operation => 'ADD'); END;",
            warnings: [],
            engine_meta: {},
          }
        : null,
    });
  });
  await page.route("**/api/nl2sql/feedback/*", (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/admin-review")) {
      return route.fallback();
    }
    if (route.request().method() === "DELETE") {
      appAdminFeedbackRating = null;
      appAdminFeedbackContent = "";
      appAdminFeedbackUpdatedAt = "";
    }
    return fulfillJson(route, { history_id: "hist-001", cleared: true });
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
          admin_feedback_rating: "good",
          admin_feedback_content: "管理者確認済み",
          admin_feedback_updated_at: "2026-06-21T10:03:00.000Z",
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
    fulfillJson(route, classifierTrainingDataResponse())
  );
  await page.route("**/api/nl2sql/classifier/training-data/*", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/from-feedback") || url.pathname.endsWith("/import")) {
      return route.fallback();
    }

    const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    if (request.method() === "PATCH") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      const profile = profiles.find((item) => item.id === payload.profile_id);
      classifierExamples = classifierExamples.map((example) =>
        example.id === id
          ? {
              ...example,
              text: payload.text ?? example.text,
              profile_id: payload.profile_id ?? example.profile_id,
              profile_name: profile?.name ?? example.profile_name,
              profile_category: profile?.category ?? example.profile_category,
              category: profile?.category ?? example.category,
              updated_at: "2026-06-21T10:07:00.000Z",
            }
          : example
      );
      classifierIsStale = true;
      return fulfillJson(route, classifierTrainingDataResponse());
    }
    if (request.method() === "DELETE") {
      state.classifierTrainingDeleteId = id;
      classifierExamples = classifierExamples.filter((example) => example.id !== id);
      classifierIsStale = true;
      return fulfillJson(route, classifierTrainingDataResponse());
    }

    return route.fallback();
  });
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
  await page.route("**/api/nl2sql/select-ai/db-profiles/refresh-jobs", (route) =>
    fulfillJson(route, {
      job_id: "db-profile-list-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-06-21T10:00:00.000Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    })
  );
  await page.route("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*", (route) =>
    fulfillJson(route, {
      job_id: "db-profile-list-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:01.000Z",
      total_profiles: 1,
      processed_profiles: 1,
      scanned_profiles: 1,
      changed_profiles: 1,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    })
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
      items: Array.from({ length: 30 }, (_, index) => {
        const ordinal = String(index + 1).padStart(3, "0");
        const sqlId = `sql-${ordinal}`;
        return {
          content: index === 0 ? "select ai showsql 請求金額を確認したい" : `select ai feedback 長い確認内容 ${index + 1}`,
          sql_id: sqlId,
          sql_text: index === 0 ? "SELECT TOTAL_AMOUNT FROM INVOICES" : `SELECT COL_${index + 1} FROM FEEDBACK_SOURCE`,
          attributes: {
            sql_id: sqlId,
            sql_text: index === 0 ? "SELECT TOTAL_AMOUNT FROM INVOICES" : `SELECT COL_${index + 1} FROM FEEDBACK_SOURCE`,
          },
          raw_attributes: `{"sql_id":"${sqlId}"}`,
        };
      }),
      total: 30,
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
  await page.route("**/api/nl2sql/rewrite", (route) => {
    // 入力質問を反映した決定論変換で返す(rewrite を ON にしても payload の質問を判別できる)。
    const body = route.request().postDataJSON() as { question?: string };
    const original = String(body?.question ?? "");
    return fulfillJson(route, {
      original_question: original,
      rewritten_question: `${original}（請求金額=INVOICES.TOTAL_AMOUNT）`,
      source: "deterministic",
      model: "",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "default",
      recommended_profile_name: "既定プロファイル",
      recommended_profile_category: "既定プロファイル",
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
          category: "既定プロファイル",
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
          item: { ...historyItem, admin_feedback_rating: "good", admin_feedback_content: "管理者確認済み" },
          score: 0.9,
          reason: "請求金額の履歴と近い質問です。管理者レビュー結果が良い履歴です。",
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
      explanation: "請求情報を対象に、一覧の取得を行います。(SQL 構造: SELECT)",
      logical_structure: "SQL 論理構造\n- SELECT: 請求金額\n- FROM: INVOICES",
      logical_structure_items: [
        {
          kind: "summary",
          business: "請求情報を対象に、一覧の取得を行います。",
          technical: "INVOICES を参照し、SELECT 操作を行います。",
        },
        { kind: "statement", business: "データを取り出すだけの参照 SQL です", technical: "SELECT" },
        { kind: "operations", business: "一覧の取得", technical: "SELECT" },
      ],
      referenced_tables: ["INVOICES"],
      logical_steps: ["INVOICES を参照", "請求金額を選択"],
      logical_step_details: [
        {
          kind: "summary",
          business: "請求情報を対象に、一覧の取得を行います。",
          technical: "INVOICES を参照",
        },
        { kind: "aggregation", business: "合計を計算します", technical: "集計: SUM" },
      ],
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
      sample_text: sampleLimit === 0 ? "" : "OBJECT: APP.INVOICES\nCUSTOMER_NAME: 青山商事, 鈴木商店",
      sample_count: sampleLimit === 0 ? 0 : 2,
      runtime: "oracle",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/comments/generate-sql", (route) => {
    state.commentGeneratePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      sql: "COMMENT ON COLUMN \"APP\".\"INVOICES\".\"TOTAL_AMOUNT\" IS '税込請求金額';",
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
          sql: "COMMENT ON COLUMN \"APP\".\"INVOICES\".\"TOTAL_AMOUNT\" IS '税込請求金額';",
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
      sql: "ALTER TABLE \"APP\".\"INVOICES\" MODIFY (\"TOTAL_AMOUNT\" ANNOTATIONS (ADD IF NOT EXISTS UI_Display '税込請求金額'));",
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
          sql: "ALTER TABLE \"APP\".\"INVOICES\" MODIFY \"TOTAL_AMOUNT\" ANNOTATIONS (DISPLAY '税込請求金額');",
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
      table_name: selectedTables[0] ?? "APP.INVOICES",
      object_list: selectedTables,
      row_count: rowCount,
      executed,
      runtime: "deterministic",
      status: "executed",
      message: executed
        ? "DBMS_CLOUD_AI synthetic data generation を実行しました。"
        : "INVOICES に 1 行の synthetic data を生成する plan です。",
      warnings: executed ? [] : ["ADMIN_EXECUTE が必要です。"],
      engine_meta: {},
      timing,
    });
  });
  await page.route("**/api/nl2sql/synthetic-data/results**", (route) =>
    fulfillJson(route, {
      table_name: "APP.INVOICES",
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
  await page.route("**/api/nl2sql/jobs", (route) => {
    state.jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      job_id: "job-default-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-default-001", (route) => {
    const question = String(state.jobPayload?.question ?? "請求金額を一覧で見たい");
    const engine = String(state.jobPayload?.engine ?? "select_ai");
    return fulfillJson(route, {
      job_id: "job-default-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.050Z",
      elapsed_ms: 50,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 12 },
        { stage: "format_results", status: "done", elapsed_ms: 6 },
      ],
      timing: null,
      result: {
        history_id: "hist-001",
        engine,
        engine_meta: engine === "select_ai" ? { profile: "mock_agent_profile" } : {},
        fallback_reason: "",
        original_question: question,
        rewritten_question: question,
        generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        explanation: "請求情報を取得します。",
        safety,
        recommendations: ["許可された表だけを参照しています。"],
        repaired_sql: "",
        optimization_hints: ["TOTAL_AMOUNT に索引を検討できます。"],
        results: {
          columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
          rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
          total: 1,
        },
        timing,
      },
    });
  });
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

async function expectNoElementHorizontalOverflow(locator: Locator) {
  const size = await locator.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.clientWidth + 1);
}

async function expectQuestionClamp(locator: Locator, fullText: string, lines: number) {
  await expect(locator).toHaveAttribute("title", fullText);
  await expect(locator).toHaveAttribute("aria-label", fullText);
  const metrics = await locator.evaluate((element, expectedLines) => {
    const style = window.getComputedStyle(element);
    const lineHeight = Number.parseFloat(style.lineHeight);
    const height = element.getBoundingClientRect().height;
    return {
      clamp: style.getPropertyValue("-webkit-line-clamp"),
      fontWeight: Number.parseInt(style.fontWeight, 10),
      height,
      maxHeight: Number.isFinite(lineHeight) ? lineHeight * expectedLines + 8 : null,
      overflow: style.overflow,
    };
  }, lines);
  expect(metrics.clamp).toBe(String(lines));
  expect(metrics.overflow).toBe("hidden");
  expect(metrics.fontWeight).toBeLessThan(600);
  if (metrics.maxHeight !== null) {
    expect(metrics.height).toBeLessThanOrEqual(metrics.maxHeight);
  }
}

const SPLIT_PANE_RENDER_TIMEOUT_MS = 15_000;

async function expectToastStackBottomRight(page: Page) {
  const region = page.getByRole("region", { name: "通知" });
  const [box, viewport] = await Promise.all([region.boundingBox(), page.viewportSize()]);
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(viewport!.width - (box!.x + box!.width)).toBeGreaterThanOrEqual(0);
  expect(viewport!.width - (box!.x + box!.width)).toBeLessThanOrEqual(24);
  expect(viewport!.height - (box!.y + box!.height)).toBeGreaterThanOrEqual(0);
  expect(viewport!.height - (box!.y + box!.height)).toBeLessThanOrEqual(24);
}

function expectedInformationRows(testInfo: TestInfo) {
  return testInfo.project.name === "mobile-375" ? 5 : 8;
}

async function expectInformationTableRowLimit(
  list: Locator,
  rowSelector: string,
  visibleRows: number
) {
  const fit = await list.evaluate(
    (node, { rowSelector: selector, visibleRows: limit }) => {
      const listBox = node.getBoundingClientRect();
      const rows = Array.from(node.querySelectorAll(selector)).map((row) =>
        row.getBoundingClientRect()
      );
      const header = node.querySelector("thead");
      const computed = window.getComputedStyle(node);
      const maxHeight = Number.parseFloat(computed.maxHeight);
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      );
      const limitRow = rows[limit - 1];
      const nextRow = rows[limit];
      return {
        listHeight: listBox.height,
        maxHeight,
        rootFontSize,
        headerPosition: header ? window.getComputedStyle(header).position : "",
        limitInside: Boolean(limitRow && limitRow.bottom <= listBox.bottom + 1),
        nextBelow: Boolean(nextRow && nextRow.bottom > listBox.bottom + 1),
        scrollHeight: node.scrollHeight,
        clientHeight: node.clientHeight,
      };
    },
    { rowSelector, visibleRows }
  );

  const expectedMaxHeight = fit.rootFontSize * (2.5 + 3.5 * visibleRows);
  expect(fit.maxHeight).toBeGreaterThanOrEqual(expectedMaxHeight - 2);
  expect(fit.maxHeight).toBeLessThanOrEqual(expectedMaxHeight + 2);
  expect(Math.abs(fit.listHeight - fit.maxHeight)).toBeLessThanOrEqual(2);
  expect(fit.limitInside).toBe(true);
  expect(fit.nextBelow).toBe(true);
  expect(fit.scrollHeight).toBeGreaterThan(fit.clientHeight);
  expect(fit.headerPosition).toBe("sticky");
}

async function expectCompactExecutionActivity(activity: Locator) {
  await expect(activity).toHaveAttribute("role", "status");
  await expect(
    activity.locator('[data-testid$="-summary"], [data-testid$="-steps"], [data-testid$="-step"]')
  ).toHaveCount(0);
  for (const label of ["実行基盤", "SQL 文", "結果行", "取得上限"]) {
    await expect(activity.getByText(label, { exact: true })).toHaveCount(0);
  }
  await expect(
    activity.getByText(/^\s*\d+\.\s+(SELECT|CREATE|INSERT|UPDATE|DELETE|ALTER|DROP|MERGE|TRUNCATE)/u)
  ).toHaveCount(0);
  await expect(activity.getByText(/処理時間\s+\d+ms/u)).toHaveCount(0);
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


/** 初期折りたたみのサイドナビセクションを必要に応じて展開してからリンクを開く。 */
async function openSidebarLink(page: Page, name: string | RegExp) {
  const link = page.getByRole("link", { name }).first();
  if (!(await link.isVisible())) {
    // クリックすると「を展開」→「を折りたたむ」に変わるため、残数が 0 になるまで先頭を開く。
    const expandButtons = page.getByRole("button", { name: /を展開/ });
    while ((await expandButtons.count()) > 0) {
      await expandButtons.first().click();
    }
  }
  await link.click();
}

async function useOverflowSchemaCatalog(page: Page) {
  // スキーマ参照は選択 profile の許可表でフィルタされるため、overflow 表も許可に含める。
  await page.unroute("**/api/nl2sql/profiles/*/usage-context");
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) => {
    const routeProfileId = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").at(-2) ?? ""
    );
    const profile = profiles.find((item) => item.id === routeProfileId) ?? profiles[0];
    return fulfillJson(route, {
      id: profile.id,
      name: profile.name,
      category: profile.category,
      description: profile.description,
      allowed_tables: overflowSchemaCatalog.tables.map((table) => table.table_name),
      allowed_views: [],
      archived: profile.archived,
      object_scope_version: 1,
      version: 1,
      etag: `etag-${profile.id}`,
      updated_at: "2026-08-14T00:00:00.000Z",
    });
  });
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

test("スキーマ参照の読込状態は利用者向けの日本語ラベルを表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  const schemaGate = createRequestGate();
  await page.unroute("**/api/schema/objects?*");
  await page.route("**/api/schema/objects?*", async (route) => {
    await schemaGate.promise;
    return fulfillJson(route, {
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

  await page.goto("/query");

  const loading = page.getByTestId("schema-reference-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toHaveAccessibleName("スキーマ情報を読み込んでいます");
  await expect(loading).toContainText("スキーマ情報を読み込んでいます");
  await expect(page.getByText("nl2sql.schema.loading", { exact: true })).toHaveCount(0);

  schemaGate.release();
  await expect(loading).toBeHidden();
});

test("query workbench waits for a real profile id before profile detail and schema requests", async ({ page }) => {
  const profileId = "0f2767ed-da07-4d35-bee0-52b5a92ec694";
  const profile = {
    ...profiles[0],
    id: profileId,
    name: "部門分析プロファイル",
    allowed_tables: ["APP.DEPARTMENT"],
    allowed_views: [],
  };
  const unexpectedDefaultRequests: string[] = [];
  const schemaProfileIds: string[] = [];
  let profileDetailRequests = 0;

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (
      url.pathname === "/api/nl2sql/profiles/default" ||
      url.searchParams.get("profile_id") === "default"
    ) {
      unexpectedDefaultRequests.push(request.url());
    }
  });

  // 認証・DB gate・schema refresh discovery は共通 helper で満たす(未モックだとログイン画面で止まる)。
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/persistence", (route) =>
    fulfillJson(route, { mode: "oracle", ready: true, writable: true, reason_code: "" })
  );
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items: [] }));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "uuid-profile-catalog",
      refreshed_at: "2026-08-14T00:00:00.000Z",
      object_count: 1,
      column_count: 0,
      change_token: 1,
      etag: "uuid-profile-catalog",
    })
  );
  await page.route(`**/api/nl2sql/profiles/${profileId}/usage-context`, (route) => {
    profileDetailRequests += 1;
    return fulfillJson(route, {
      id: profile.id,
      name: profile.name,
      category: profile.category ?? "",
      description: profile.description,
      allowed_tables: profile.allowed_tables,
      allowed_views: profile.allowed_views,
      archived: false,
      object_scope_version: 1,
      version: 1,
      etag: `etag-${profile.id}`,
      updated_at: "2026-08-14T00:00:00.000Z",
    });
  });
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "unexpected default profile request" }),
    })
  );
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: profile.id,
          name: profile.name,
          category: profile.category,
          description: profile.description,
          archived: false,
          allowed_table_count: profile.allowed_tables.length,
          allowed_view_count: profile.allowed_views.length,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: `etag-${profile.id}`,
          updated_at: "2026-08-14T00:00:00.000Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route(`**/api/nl2sql/profiles/${profileId}`, (route) => fulfillJson(route, profile));
  await page.route("**/api/nl2sql/profiles/default", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "unexpected default profile request" }),
    })
  );
  await page.route("**/api/schema/objects?*", (route) => {
    const url = new URL(route.request().url());
    schemaProfileIds.push(url.searchParams.get("profile_id") ?? "");
    return fulfillJson(route, {
      items: [
        {
          owner: "APP",
          object_name: "DEPARTMENT",
          object_type: "TABLE",
          logical_name: "部門",
          comment: "部門情報",
          row_count: null,
          column_count: 0,
          last_ddl_at: "",
        },
      ],
      next_cursor: null,
      total: 1,
      catalog_version: 1,
    });
  });

  await page.goto("/query");

  await expect(page.locator("#nl2sql-profile-select")).toHaveValue(profileId);
  await expect.poll(() => profileDetailRequests).toBe(1);
  await expect.poll(() => schemaProfileIds.includes(profileId)).toBe(true);
  expect(schemaProfileIds).not.toContain("");
  expect(schemaProfileIds).not.toContain("default");
  expect(unexpectedDefaultRequests).toEqual([]);
});

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

test("SQL 系の必須入力欄は既存の必須マークと required 属性で統一する", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.locator("#nl2sql-profile-select")).toHaveValue("default");
  await expectRequiredTextarea(page, "nl2sql-question-input", "検索クエリ");
  const runQueryButton = page.getByRole("button", { name: "検索を実行" });
  await expect(runQueryButton).toBeDisabled();
  await nl2sqlQuestionInput(page).fill("未入金の請求を確認したい");
  await expect(runQueryButton).toBeEnabled();

  await page.goto("/direct-sql");
  const directSql = page.getByTestId("nl2sql-direct-sql");
  await expectRequiredTextarea(directSql, "direct-sql-input", "SQL");
  const directExecuteButton = directSql.getByRole("button", { name: "SQL 実行" });
  await expect(directExecuteButton).toBeDisabled();
  await directSqlInput(directSql).fill("SELECT 1 FROM DUAL");
  await expect(directExecuteButton).toBeEnabled();

  await page.goto("/sql-to-question");
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expectRequiredTextarea(page, "sql-to-question-sql-input", "対象 SQL");
  const generateButton = page.getByRole("button", { name: "業務質問を生成" });
  await expect(generateButton).toBeDisabled();
  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(generateButton).toBeEnabled();

  await page.goto("/admin-sql");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await expectRequiredTextarea(adminSql, "admin-sql-input", "管理 SQL");
  const adminExecuteButton = adminSql.getByRole("button", { name: "SQL 実行" });
  await expect(adminExecuteButton).toBeDisabled();
  await adminSqlInput(adminSql).fill("SELECT 1 FROM DUAL");
  await expect(adminExecuteButton).toBeEnabled();
});

test("スキーマ参照から連続挿入すると各項目が改行区切りになる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = nl2sqlQuestionInput(page);
  await openSchemaPicker(page);
  const tableToggle = page.getByRole("button", { name: "請求 を開閉" });
  const tableChevron = tableToggle.locator('svg[data-state]');
  await expect(tableChevron).toHaveAttribute("data-state", "collapsed");
  await expect(tableChevron).toHaveClass(/rotate-90/);
  await tableToggle.click();
  await expect(tableChevron).toHaveAttribute("data-state", "expanded");
  await expect(tableChevron).toHaveClass(/rotate-0/);
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
  const question = nl2sqlQuestionInput(page);

  // 「テンプレート:」行が textarea の上に見える
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

  // 別テンプレートも全置換
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
  await expect(nl2sqlQuestionInput(page)).toHaveValue("\"請求\".\"請求金額\"");
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
  const question = nl2sqlQuestionInput(page);
  await expect(question).toHaveValue("");
  await page.getByRole("button", { name: /^請求 INVOICES/ }).click();
  await expect(question).toHaveValue("\"請求\"");
  await secondToggle.click();
  await expect(question).toHaveValue("\"請求\""); // chevron では変化しない
});

test("検索クエリとスキーマ参照は desktop で左右並置、mobile で縦積みになる", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  const question = nl2sqlQuestionInput(page);
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
  const question = nl2sqlQuestionInput(page);

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

test("標準プロファイル削除後は最初の利用可能なプロファイルを選択する", async ({ page }) => {
  await mockNl2SqlApi(page);
  const alternateProfile = {
    ...profiles[0],
    id: "alternate",
    name: "代替プロファイル",
    category: "代替",
  };
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: alternateProfile.id,
          name: alternateProfile.name,
          category: alternateProfile.category,
          description: alternateProfile.description,
          archived: false,
          allowed_table_count: alternateProfile.allowed_tables.length,
          allowed_view_count: alternateProfile.allowed_views.length,
          glossary_count: Object.keys(alternateProfile.glossary).length,
          few_shot_count: alternateProfile.few_shot_examples.length,
          version: 1,
          etag: "etag-alternate",
          updated_at: "2026-06-21T10:00:00.000Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 2,
    })
  );
  await page.unroute("**/api/nl2sql/profiles/default");
  await page.route("**/api/nl2sql/profiles/default", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "指定された profile が見つかりません。" }),
    })
  );
  await page.route("**/api/nl2sql/profiles/alternate", (route) =>
    fulfillJson(route, alternateProfile)
  );

  await page.goto("/query");

  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue(
    "alternate"
  );
  await expect(page.getByText("業務プロファイルがありません")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("最後のプロファイル削除後は作成案内を表示して検索操作を無効化する", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [],
      next_cursor: null,
      total: 0,
      change_token: 2,
    })
  );
  await page.unroute("**/api/nl2sql/profiles/default");
  await page.route("**/api/nl2sql/profiles/default", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "指定された profile が見つかりません。" }),
    })
  );

  await page.goto("/query");
  await expect(page.getByText("業務プロファイルがありません")).toBeVisible();
  await expect(
    page.getByText("検索を実行するには、先に業務プロファイルを作成してください。")
  ).toBeVisible();

  const workspace = page.getByTestId("nl2sql-workspace-shell");
  await nl2sqlQuestionInput(page).fill("未入金の請求を確認したい");
  await expect(
    workspace.getByRole("button", { name: "プロファイルを自動判定" })
  ).toBeDisabled();
  await expect(workspace.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(workspace.getByRole("button", { name: "質問を解釈" })).toHaveCount(0);
  await expect(workspace.getByRole("button", { name: "検索を実行" })).toBeDisabled();

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalScroll(page);
  await page.getByRole("button", { name: "業務プロファイルを作成" }).click();
  await expect(page).toHaveURL(/\/profiles\?profile=new$/);
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
      recommended_profile_category: "入金管理",
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
  await nl2sqlQuestionInput(page).fill("未入金の請求を確認したい");
  await expect(detect).toBeEnabled();
  await detect.click();

  await expect(profileSelect).toHaveValue("payment");
  await expect(page.getByText(/入金管理（入金管理） を選択しました/)).toBeVisible();
});

test("低信頼度の業務プロファイル自動判定は選択を変更しない", async ({ page }) => {
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
      recommended_profile_category: "入金管理",
      confidence: 0.2,
      reason: "入金関連の語彙に少し一致しました。",
      rewritten_question: "",
      recommended_allowed_objects: { table_names: ["INVOICES"], columns: {} },
      candidates: [],
      recommendation_source: "deterministic",
    })
  );

  await page.goto("/query");
  const profileSelect = page.locator("#nl2sql-profile-select");
  await expect(profileSelect).toHaveValue("default");

  const detect = page.getByRole("button", { name: "プロファイルを自動判定" });
  await nl2sqlQuestionInput(page).fill("未入金の請求を確認したい");
  await expect(detect).toBeEnabled();
  await detect.click();

  await expect(profileSelect).toHaveValue("default");
  await expect(page.getByTestId("nl2sql-recommend-low-confidence")).toContainText(
    "十分な信頼度で自動判定できませんでした"
  );
  // 固定面の warning Banner に一本化(同文の Toast と二重表示しない: messaging spec §0.6)。
  await expect(page.getByText(/十分な信頼度で自動判定できませんでした/)).toHaveCount(1);
  await expect(
    page.getByRole("region", { name: "通知" }).getByRole("status")
  ).toHaveCount(0);
  await expect(page.getByText(/入金管理（入金管理） を選択しました/)).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

for (const statusCode of [404, 410, 501, 503]) {
  test(`schema 更新の開始失敗(${statusCode})は旧同期 API へ fallback せず固定面に表示する`, async ({
    page,
  }) => {
    await mockNl2SqlApi(page);
    const refreshError = "DB 構造の再取得を開始できませんでした。";
    let legacyRefreshRequests = 0;
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.method() === "POST" && url.pathname === "/api/schema/refresh") {
        legacyRefreshRequests += 1;
      }
    });
    await page.route("**/api/schema/refresh", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "旧同期 API は呼び出されない想定です。" }),
      })
    );
    await page.route("**/api/schema/refresh-jobs", (route) =>
      route.fulfill({
        status: statusCode,
        contentType: "application/json",
        body: JSON.stringify({ detail: refreshError }),
      })
    );

    await page.goto("/query");
    await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();
    // mobile 幅ではヘッダー操作が「その他の操作」メニューへ収納される。
    const refreshButton = page.getByRole("button", { name: "DB 構造を再取得" });
    if (await refreshButton.isVisible()) {
      await refreshButton.click();
    } else {
      await page.getByRole("button", { name: "その他の操作", exact: true }).click();
      await page.getByRole("menuitem", { name: "DB 構造を再取得", exact: true }).click();
    }

    // 固定面(PageNotice)が正本。永続 Toast との二重表示はしない(messaging spec §0.6)。
    await expect(page.getByText(refreshError)).toHaveCount(1);
    await expect(
      page.getByRole("region", { name: "通知" }).getByRole("status")
    ).toHaveCount(0);
    expect(legacyRefreshRequests).toBe(0);
  });
}

test("catalog 空のときスキーマ参照からスキーマを更新して表を取得できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  // 初回 GET は空、更新 job 完了後に実表を返す。
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

test("query workbench generates SQL through the job flow and shows results", async ({ page }) => {
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

  const question = nl2sqlQuestionInput(page);
  const placeholder = (await question.getAttribute("placeholder")) ?? "";
  for (const sample of removedSamples) expect(placeholder).not.toContain(sample);
  await openSchemaPicker(page);
  await page.getByRole("button", { name: "請求 を開閉" }).click();
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"");

  await question.fill("請求金額を一覧で見たい");
  // 推薦は現在の profile（default）と同一のため、progressive-disclosure によりヒントは出さない。
  await expect(page.getByTestId("nl2sql-recommend-hint")).toHaveCount(0);

  await page.getByRole("button", { name: "検索を実行" }).click();

  const generatedSqlStep = page.getByTestId("nl2sql-job-step-generate_sql");
  await expect(generatedSqlStep).toContainText("SQL を生成");
  await expect(generatedSqlStep.getByRole("code")).toContainText(
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
  );

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1200000" })).toBeVisible();
  // 生成 SQL の pre(role=region「生成 SQL コード」)と区別するため textbox を明示する。
  const feedbackResponse = page.getByRole("textbox", { name: "生成 SQL" });
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();
  await expect(
    page.getByText(
      "この SQL 生成結果に対する利用者からの評価をアプリ内 DB に保存します。参考履歴には管理者レビュー結果が良い履歴だけを使用します。"
    )
  ).toBeVisible();
  await expect(feedbackResponse).toHaveValue("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  // 結果フィードバックカードは廃止され、良い/違うボタンへ統合された。
  await expect(page.getByRole("heading", { name: "結果フィードバック" })).toHaveCount(0);
  await expect(feedbackResponse).toHaveJSProperty("readOnly", true);

  // 「良い」は利用者からの評価としてアプリ DB にだけ保存する。
  await page.getByLabel("利用者コメント（feedback_content）").fill("期待どおりの SQL です");
  await page.getByRole("button", { name: "良い", exact: true }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "good",
    feedback_content: "期待どおりの SQL です",
    comment: "期待どおりの SQL です",
  });
  expect(api.selectAiFeedbackAddPayload).toBeNull();

  // 「違う」= negative。利用者コメント未入力なら送信をブロックする。
  await page.getByLabel("利用者コメント（feedback_content）").fill("");
  await page.getByRole("button", { name: "違う", exact: true }).click();
  await expect(page.getByText("「違う」の場合は利用者コメントの入力が必須です。")).toBeVisible();

  await page.getByLabel("利用者コメント（feedback_content）").fill("列を請求金額だけに修正");
  await page.getByRole("button", { name: "違う", exact: true }).click();
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "bad",
    feedback_content: "列を請求金額だけに修正",
    comment: "列を請求金額だけに修正",
  });
  // checkbox 廃止後、スコープは profile / 推薦適用が決める（手動選択なしは空）。
  expect(api.jobPayload?.allowed_objects).toEqual({
    table_names: [],
    columns: {},
  });
  expect(api.executePayload).toBeNull();
  await expectNoHorizontalScroll(page);
});

test("query workbench shows job action errors below the execution buttons", async ({ page }) => {
  await mockNl2SqlApi(page);
  const agentError =
    "Oracle Select AI Agent 実行に失敗しました: Select AI Agent conversation_id を作成できませんでした。";
  await page.route("**/api/nl2sql/jobs", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: agentError }),
    })
  );

  await page.goto("/query");
  await page.getByRole("button", { name: /Select AI Agent/ }).click();
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const runButton = page.getByRole("button", { name: "検索を実行" });
  await runButton.click();

  const actionError = page.getByTestId("nl2sql-action-feedback-error");
  await expect(actionError).toContainText(agentError);
  await expect(actionError).toContainText("入力内容と接続状態を確認して再試行してください。");
  await expect(page.getByText(agentError)).toHaveCount(1);
  // 参考履歴の遅延挿入や自動スクロールで 2 回の boundingBox が別レイアウトを見るレースを避け、
  // 単一 evaluate で原子的に上下関係を検証する。
  await expect
    .poll(() =>
      page.evaluate(() => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const run = buttons.find((el) => el.textContent?.includes("検索を実行"));
        const error = document.querySelector('[data-testid="nl2sql-action-feedback-error"]');
        if (!run || !error) return "missing";
        const runBox = run.getBoundingClientRect();
        const errorBox = error.getBoundingClientRect();
        return errorBox.top >= runBox.bottom - 1 ? "below" : `above:${errorBox.top}<${runBox.bottom}`;
      })
    )
    .toBe("below");
});

test("実行中の job は「実行を中止」でキャンセルでき、警告トーンで表示して UI を解放する", async ({ page }) => {
  await mockNl2SqlApi(page);
  let cancelRequested = false;
  await page.route("**/api/nl2sql/jobs/job-default-001/cancel", (route) => {
    cancelRequested = true;
    return fulfillJson(route, {
      job_id: "job-default-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [],
    });
  });
  await page.unroute("**/api/nl2sql/jobs/job-default-001");
  await page.route("**/api/nl2sql/jobs/job-default-001", (route) =>
    fulfillJson(
      route,
      cancelRequested
        ? {
            job_id: "job-default-001",
            status: "error",
            created_at: "2026-06-21T10:00:00.000Z",
            error_message: "利用者の要求によりジョブをキャンセルしました。",
            error_code: "JOB_CANCELLED",
            steps: [],
          }
        : {
            job_id: "job-default-001",
            status: "running",
            created_at: "2026-06-21T10:00:00.000Z",
            steps: [
              { stage: "prepare_context", status: "done", elapsed_ms: 8 },
              { stage: "generate_sql", status: "running", elapsed_ms: null },
              { stage: "safety_check", status: "pending", elapsed_ms: null },
              { stage: "execute_sql", status: "pending", elapsed_ms: null },
              { stage: "format_results", status: "pending", elapsed_ms: null },
            ],
          }
    )
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const runButton = page.getByRole("button", { name: "検索を実行" });
  await runButton.click();

  const cancelButton = page.getByRole("button", { name: "実行を中止" });
  await expect(cancelButton).toBeVisible();
  await cancelButton.click();
  await expect.poll(() => cancelRequested).toBe(true);

  // キャンセルは失敗ではなく警告トーンの固定面で表示し、UI ロックを解除する。
  await expect(page.getByTestId("nl2sql-job-cancelled")).toContainText(
    "利用者の要求によりジョブをキャンセルしました。"
  );
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveAttribute("data-job-status", "error");
  await expect(runButton).toBeEnabled();
  await expect(cancelButton).toHaveCount(0);
});

test("job ポーリングの通信断が続くと追跡を停止しエラー表示と UI ロック解除を行う", async ({ page }) => {
  test.slow();
  await mockNl2SqlApi(page);
  // 作成は成功するが、以後の状態確認がネットワーク断で失敗し続けるケース。
  await page.route("**/api/nl2sql/jobs/job-default-001", (route) => route.abort("failed"));

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const runButton = page.getByRole("button", { name: "検索を実行" });
  const resetButton = page.getByRole("button", { name: "リセット" });
  await runButton.click();

  // 2.5s 間隔 × 連続 3 回失敗(即時 tick 含む)で追跡を断念する。
  const actionError = page.getByTestId("nl2sql-action-feedback-error");
  await expect(actionError).toContainText("ジョブの状態確認に連続して失敗したため", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  await expect(runButton).toBeEnabled();
  await expect(resetButton).toBeEnabled();
  // 追跡解除後は localStorage の snapshot も消え、リロードで復元されない。
  expect(await page.evaluate(() => window.localStorage.getItem("nl2sql.activeJobId"))).toBeNull();
});

test("job が 404 のときは即座に追跡を解除して案内を表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/jobs/job-default-001", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "job not found" }),
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const runButton = page.getByRole("button", { name: "検索を実行" });
  await runButton.click();

  const actionError = page.getByTestId("nl2sql-action-feedback-error");
  await expect(actionError).toContainText("実行中のジョブが見つかりませんでした");
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  await expect(runButton).toBeEnabled();
});

test("失効した job スナップショットは復元時の 404 で破棄されリクエストが打ち止めになる", async ({ page }) => {
  await mockNl2SqlApi(page);
  let staleJobRequests = 0;
  await page.route("**/api/nl2sql/jobs/job-stale-001", (route) => {
    staleJobRequests += 1;
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "job not found" }),
    });
  });
  await page.addInitScript(() => {
    window.localStorage.setItem("nl2sql.activeJobId", "job-stale-001");
    window.localStorage.setItem("nl2sql.activeJobStartedAt", String(Date.now() - 1_000));
  });

  await page.goto("/query");

  const actionError = page.getByTestId("nl2sql-action-feedback-error");
  await expect(actionError).toContainText("実行中のジョブが見つかりませんでした");
  expect(staleJobRequests).toBe(1);
  expect(await page.evaluate(() => window.localStorage.getItem("nl2sql.activeJobId"))).toBeNull();
  expect(
    await page.evaluate(() => window.localStorage.getItem("nl2sql.activeJobStartedAt"))
  ).toBeNull();
});

test("TTL を超えた job スナップショットは復元ポーリング自体を行わない", async ({ page }) => {
  await mockNl2SqlApi(page);
  let staleJobRequests = 0;
  await page.route("**/api/nl2sql/jobs/job-expired-001", (route) => {
    staleJobRequests += 1;
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "job not found" }),
    });
  });
  await page.addInitScript(() => {
    window.localStorage.setItem("nl2sql.activeJobId", "job-expired-001");
    // 66 分前開始 = longRunningJob(65 分)の TTL 超過。
    window.localStorage.setItem(
      "nl2sql.activeJobStartedAt",
      String(Date.now() - 66 * 60_000)
    );
  });

  await page.goto("/query");
  await expect(page.getByTestId("nl2sql-schema-reference")).toContainText("参照可能な表");

  expect(staleJobRequests).toBe(0);
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  expect(await page.evaluate(() => window.localStorage.getItem("nl2sql.activeJobId"))).toBeNull();
});

test("履歴更新の失敗は warning に留め、成功した検索結果を失敗表示に変えない", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/history", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "履歴の読込に失敗しました。" }),
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  // 結果領域が正本: job は成功しているので結果表と進捗はそのまま表示される。
  await expect(page.getByTestId("nl2sql-job-progress")).toContainText(
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES"
  );
  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByTestId("nl2sql-action-feedback-error")).toHaveCount(0);
  // 履歴の失敗は warning toast 1 回のみ(messaging spec: 固定面の正本と二重にしない)。
  const toastRegion = page.getByRole("region", { name: "通知" });
  await expect(toastRegion.getByRole("status")).toContainText("履歴の更新に失敗しました");
});

test("query workbench keeps schema detail errors inside the schema panel when schema list is populated", async ({ page }) => {
  await mockNl2SqlApi(page);
  const schemaEmptyError =
    "Schema catalog が空です。Oracle schema を refresh するか、Data Tools から sample data を明示的に import してください。";
  await page.unroute("**/api/schema/objects/*/*");
  await page.route("**/api/schema/objects/*/*", (route) =>
    route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ detail: schemaEmptyError }),
    })
  );

  await page.goto("/query");
  await page.getByRole("button", { name: /Enterprise AI Direct/ }).click();
  await page.getByLabel("表・項目検索").fill("請求");

  const schemaDetailError = page.getByTestId("nl2sql-schema-detail-error");
  await expect(schemaDetailError).toContainText(schemaEmptyError);
  await expect(page.getByText("Schema catalog が空です")).toHaveCount(1);
  await expect(page.getByTestId("nl2sql-schema-reference")).toContainText("請求");
  await expectNoHorizontalScroll(page);
});

test("Enterprise AI Direct job does not show schema-empty when schema reference is populated", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByTestId("nl2sql-schema-reference")).toContainText("参照可能な表");
  await page.getByRole("button", { name: /Enterprise AI Direct/ }).click();
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect.poll(() => api.jobPayload?.engine).toBe("enterprise_ai_direct");
  await expect(page.getByTestId("nl2sql-job-progress")).toContainText(
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES"
  );
  await expect(page.getByText("Schema catalog が空です")).toHaveCount(0);
  await expect(page.getByTestId("nl2sql-action-feedback-error")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("検索実行開始時に前回の生成 SQL・実行結果を先に消す", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  const question = nl2sqlQuestionInput(page);
  await question.fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect(page.getByTestId("nl2sql-job-progress")).toContainText(
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES"
  );
  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();

  const jobsGate = createRequestGate();
  await page.route("**/api/nl2sql/jobs", async (route) => {
    await jobsGate.promise;
    return fulfillJson(route, {
      job_id: "job-preview-reset-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-preview-reset-001", (route) =>
    fulfillJson(route, {
      job_id: "job-preview-reset-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.040Z",
      elapsed_ms: 40,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 18 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 6 },
        { stage: "format_results", status: "done", elapsed_ms: 4 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        original_question: "請求金額だけを確認したい",
        rewritten_question: "請求金額だけを確認したい",
        generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
        explanation: "請求金額だけを取得します。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: { columns: ["TOTAL_AMOUNT"], rows: [{ TOTAL_AMOUNT: 1200000 }], total: 1 },
        timing,
      },
    })
  );

  await question.fill("請求金額だけを確認したい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  await expect(page.getByText("検索結果（1件）")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toHaveCount(0);
  await expect(question).toHaveValue("請求金額だけを確認したい");

  jobsGate.release();
  await expect(page.getByTestId("nl2sql-job-progress")).toContainText(
    "SELECT TOTAL_AMOUNT FROM INVOICES"
  );
  await expect(page.getByText("検索結果（1件）")).toBeVisible();
});

test("検索実行開始時に前回の結果表を先に消す", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  const question = nl2sqlQuestionInput(page);
  await question.fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();

  const createdAt = "2026-06-21T10:00:00.000Z";
  const jobsGate = createRequestGate();
  let jobPayload: Record<string, unknown> | null = null;
  await page.route("**/api/nl2sql/jobs", async (route) => {
    jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    await jobsGate.promise;
    return fulfillJson(route, {
      job_id: "job-reset-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-reset-001", (route) =>
    fulfillJson(route, {
      job_id: "job-reset-001",
      status: "done",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 40,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 6 },
        { stage: "format_results", status: "done", elapsed_ms: 2 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        engine_meta: {},
        fallback_reason: "",
        original_question: "請求件数を数えたい",
        rewritten_question: "請求件数を数えたい",
        generated_sql: "SELECT COUNT(*) AS INVOICE_COUNT FROM INVOICES",
        executable_sql: "SELECT COUNT(*) AS INVOICE_COUNT FROM INVOICES",
        explanation: "請求件数を集計します。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: ["INVOICE_COUNT"],
          rows: [{ INVOICE_COUNT: 2 }],
          total: 1,
        },
        timing: null,
      },
    })
  );

  await question.fill("請求件数を数えたい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  // 用語・同義語は既定 off なので、入力そのままの質問で job を作る。
  await expect.poll(() => jobPayload?.question).toBe("請求件数を数えたい");
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  await expect(page.getByText("検索結果（1件）")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toHaveCount(0);
  await expect(question).toHaveValue("請求件数を数えたい");

  jobsGate.release();
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveAttribute("data-job-status", "done");
  await expect(page.getByTestId("nl2sql-job-progress")).toContainText(
    "SELECT COUNT(*) AS INVOICE_COUNT FROM INVOICES"
  );
  await expect(page.getByRole("cell", { name: "2" })).toBeVisible();
});

test("検索実行開始時に前回の生成結果を先に消し、現在の質問で job を作成できる", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  const question = nl2sqlQuestionInput(page);
  await question.fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();

  const jobsGate = createRequestGate();
  let jobPayload: Record<string, unknown> | null = null;
  await page.route("**/api/nl2sql/jobs", async (route) => {
    jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    await jobsGate.promise;
    return fulfillJson(route, {
      job_id: "job-current-question-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-current-question-001", (route) =>
    fulfillJson(route, {
      job_id: "job-current-question-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.040Z",
      elapsed_ms: 40,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 18 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 6 },
        { stage: "format_results", status: "done", elapsed_ms: 4 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        original_question: "請求金額を業務用語で解釈したい",
        rewritten_question: "請求金額を業務用語で解釈したい",
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
        timing,
      },
    })
  );

  await question.fill("請求金額を業務用語で解釈したい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect(page.getByTestId("nl2sql-job-progress")).toHaveCount(0);
  await expect(page.getByText("検索結果（1件）")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toHaveCount(0);
  await expect(question).toHaveValue("請求金額を業務用語で解釈したい");
  // 用語・同義語は既定 off なので、入力そのままの質問で job を作る。
  await expect.poll(() => jobPayload?.question).toBe("請求金額を業務用語で解釈したい");

  jobsGate.release();
  await expect(page.getByTestId("nl2sql-job-progress")).toHaveAttribute("data-job-status", "done");
  expect(jobPayload).toMatchObject({
    question: "請求金額を業務用語で解釈したい",
    profile_id: "default",
    engine: "select_ai",
  });
});

test("参考履歴は既定で折りたたまれ、ヘッダークリックで過去 SQL を展開できる", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();

  // 4 文字以上の質問を入力すると参考履歴を取得する（debounce 650ms）。
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");

  const header = page.getByRole("button", { name: /参考履歴/ });
  await expect(header).toBeVisible();
  await expect(header).toContainText("管理者レビュー結果: 良いのみ");
  // 既定は折りたたみ: 中身（類似度・過去 SQL）は表示されない。
  // aria-controls の参照先(#nl2sql-similar-history)は常時レンダされ、閉時は hidden。
  await expect(header).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("nl2sql-similar-history")).toBeAttached();
  await expect(page.getByTestId("nl2sql-similar-history")).toBeHidden();
  await expect(page.getByText("類似度 90%")).toBeHidden();

  await header.click();
  await expect(header).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("類似度 90%")).toBeVisible();
  await expect(page.getByText("請求金額の履歴と近い質問です。")).toBeVisible();
});

test("参考履歴は API が空の場合も表示し、空状態を展開できる", async ({ page }) => {
  await mockNl2SqlApi(page);
  let requested = false;
  await page.unroute("**/api/nl2sql/similar-history");
  await page.route("**/api/nl2sql/similar-history", (route) => {
    requested = true;
    return fulfillJson(route, { items: [] });
  });

  await page.goto("/query");
  await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();

  await nl2sqlQuestionInput(page).fill('対象テーブル："PROJECT"\n抽出項目：PROJECT_ID\n抽出条件：');

  await expect.poll(() => requested).toBe(true);
  const header = page.getByRole("button", { name: /参考履歴/ });
  await expect(header).toBeVisible();
  await expect(header).toContainText("管理者レビュー結果: 良いのみ");
  await expect(header).toHaveAttribute("aria-expanded", "false");

  const panel = page.getByTestId("nl2sql-similar-history");
  await expect(panel).toBeAttached();
  await expect(panel).toBeHidden();
  await expect(panel.getByTestId("nl2sql-similar-history-item")).toHaveCount(0);

  await header.click();
  await expect(header).toHaveAttribute("aria-expanded", "true");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("参考履歴はありません");
  await expect(panel).toContainText("管理者レビュー結果が良い履歴は見つかりませんでした。");
  await expect(panel.getByTestId("nl2sql-similar-history-item")).toHaveCount(0);
});

test("参考履歴の「検索中」は SQL 生成の実行中に固まって残らない", async ({ page }) => {
  await mockNl2SqlApi(page);
  // 参考履歴の応答を握って in-flight のまま実行へ進める（abort で .finally が走らない経路）。
  const similarGate = createRequestGate();
  await page.unroute("**/api/nl2sql/similar-history");
  await page.route("**/api/nl2sql/similar-history", async (route) => {
    await similarGate.promise;
    return fulfillJson(route, { items: [] });
  });
  const jobsGate = createRequestGate();
  await page.route("**/api/nl2sql/jobs", async (route) => {
    await jobsGate.promise;
    return fulfillJson(route, { job_id: "job-similar-stuck", status: "running", steps: [] });
  });

  await page.goto("/query");
  await expect(page.getByRole("region", { name: "SQL 生成ワークスペース" })).toBeVisible();

  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const header = page.getByRole("button", { name: /参考履歴/ });
  await expect(header).toContainText("参考履歴を検索中");

  await page.getByRole("button", { name: "検索を実行" }).click();
  // 実行中は入力系が無効化される。参考履歴は「検索中」のまま残らず消える。
  await expect(nl2sqlQuestionInput(page)).toBeDisabled();
  await expect(page.getByText("参考履歴を検索中")).toHaveCount(0);
  await expect(header).toHaveCount(0);

  similarGate.release();
  jobsGate.release();
});

test("参考履歴は管理者レビュー結果が良い履歴だけを表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/similar-history");
  await page.route("**/api/nl2sql/similar-history", (route) =>
    fulfillJson(route, {
      items: [
        {
          item: {
            ...longHistoryItem,
            id: "hist-good",
            feedback_rating: "good",
            admin_feedback_rating: "good",
            admin_feedback_content: "管理者が確認しました。",
          },
          score: 0.92,
          reason: "請求金額が一致し、管理者レビュー結果が良い履歴です。",
        },
        {
          item: {
            ...historyItem,
            feedback_rating: "good",
            admin_feedback_rating: null,
            admin_feedback_content: "",
            id: "hist-user-good",
            question: "利用者だけ良い評価の請求金額",
          },
          score: 0.98,
          reason: "利用者評価のみのため表示しません。",
        },
        {
          item: {
            ...historyItem,
            id: "hist-bad",
            question: "違う評価の請求金額",
            feedback_rating: "bad",
            admin_feedback_rating: "bad",
          },
          score: 0.96,
          reason: "高スコアでも bad のため表示しません。",
        },
        {
          item: {
            ...historyItem,
            id: "hist-unrated",
            question: "未評価の請求金額",
            feedback_rating: null,
          },
          score: 0.94,
          reason: "未評価のため表示しません。",
        },
      ],
    })
  );

  await page.goto("/query");
  const workspace = page.getByRole("region", { name: "SQL 生成ワークスペース" });
  await expect(workspace).toBeVisible();

  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  const header = page.getByRole("button", { name: /参考履歴/ });
  await expect(header).toBeVisible();
  await expect(header).toContainText("管理者レビュー結果: 良いのみ");
  await header.click();

  const panel = page.getByTestId("nl2sql-similar-history");
  const rows = panel.getByTestId("nl2sql-similar-history-item");
  const similarQuestion = rows.first().getByTestId("nl2sql-similar-history-question");
  await expect(rows).toHaveCount(1);
  await expect(similarQuestion).toContainText("対象テーブル");
  await expectQuestionClamp(similarQuestion, longHistoryItem.question, 1);
  await expect(panel).toContainText("類似度 92%");
  await expect(panel).not.toContainText("利用者だけ良い評価の請求金額");
  await expect(panel).not.toContainText("違う評価の請求金額");
  await expect(panel).not.toContainText("未評価の請求金額");
  await expectNoHorizontalScroll(page);
  await expectNoElementHorizontalOverflow(header);
  await expectNoElementHorizontalOverflow(panel);
  await expectNoElementHorizontalOverflow(rows.first());
  await expectNoElementHorizontalOverflow(similarQuestion);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(header).toBeVisible();
  await expect(panel).toBeVisible();
  await expectNoHorizontalScroll(page);
  await expectNoElementHorizontalOverflow(header);
  await expectNoElementHorizontalOverflow(panel);
  await expectNoElementHorizontalOverflow(rows.first());
  await expectNoElementHorizontalOverflow(similarQuestion);
});

test("検索結果は 10 件ごとにページングする", async ({ page }) => {
  await mockNl2SqlApi(page);
  // 実行結果を 12 件へ上書き（後勝ちルートで mockNl2SqlApi の job result を差し替える）
  const rows = Array.from({ length: 12 }, (_, index) => ({
    CUSTOMER_NAME: `顧客${String(index + 1).padStart(2, "0")}`,
    TOTAL_AMOUNT: (index + 1) * 1000,
  }));
  await page.route("**/api/nl2sql/jobs/job-default-001", (route) =>
    fulfillJson(route, {
      job_id: "job-default-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.050Z",
      elapsed_ms: 50,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 12 },
        { stage: "format_results", status: "done", elapsed_ms: 6 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        original_question: "請求金額を一覧で見たい",
        rewritten_question: "請求金額を一覧で見たい",
        generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
        explanation: "請求情報を取得します。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: { columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"], rows, total: rows.length },
        timing,
      },
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();

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
        history_id: "hist-run-001",
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
  await nl2sqlQuestionInput(page).fill(questionText);
  // 用語・同義語は既定 off のため、書き換え後の質問を検証する本テストでは明示 ON にする。
  await openNl2SqlExecutionOptions(page);
  await page.getByLabel("用語・同義語を使う").check();
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
    await expect(progress.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expect(progress.getByRole("timer")).toHaveAttribute("aria-live", "off");
    const runningIcon = generate.locator("svg.animate-spin");
    await expect(runningIcon).toBeVisible();
    expect(await runningIcon.evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
    await expect(safetyStep).toHaveAttribute("data-step-status", "pending");
    expect(jobPayload).toMatchObject({
      // 用語・同義語を ON にしたため job には辞書適用後の質問が渡る。
      question: `${questionText}（請求金額=INVOICES.TOTAL_AMOUNT）`,
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
  await expect(progress.getByRole("timer")).toHaveAccessibleName("処理時間 00:00");
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
  await expectContentActionsRightAligned(generate.getByTestId("generated-sql-content-actions"));
  await copySql.click();
  // i18n.ts の "common.action.copied" は「コピーしました」(base 定義を上書き)。
  await expect(page.getByRole("status").filter({ hasText: "コピーしました" })).toBeVisible();
  await page.getByRole("status").filter({ hasText: "コピーしました" }).getByRole("button", { name: "閉じる" }).click();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: () => Promise.reject(new Error("clipboard denied")),
    });
  });
  await copySql.click();
  const copyFailedToast = page.getByRole("alert").filter({ hasText: "コピーできませんでした。" });
  await expect(copyFailedToast).toBeVisible();
  // danger toast は自動消滅しない(duration 0)。mobile 幅で後続の「良い」クリックを遮るため閉じる。
  await copyFailedToast.getByRole("button", { name: "閉じる" }).click();
  await expect(copyFailedToast).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();

  // 実行結果の「良い」は利用者からの評価としてアプリ DB に保存する。
  await expect(page.getByRole("heading", { name: "アプリ内フィードバック" })).toBeVisible();
  await page.getByLabel("利用者コメント（feedback_content）").fill("想定どおりの SQL です");
  await page.getByRole("button", { name: "良い", exact: true }).click();
  await expect(page.getByText("フィードバックを保存しました。")).toBeVisible();
  expect(api.feedbackPayload).toEqual({
    history_id: "hist-run-001",
    rating: "good",
    feedback_content: "想定どおりの SQL です",
    comment: "想定どおりの SQL です",
  });
  expect(api.selectAiFeedbackAddPayload).toBeNull();

  await expectNoHorizontalScroll(page);
});

test("保存警告がある完了 job は結果を表示し、赤エラーではなく黄色 warning を出す", async ({ page }) => {
  await page.unroute("**/api/auth/me").catch(() => undefined);
  await page.route("**/api/auth/me**", (route) => fulfillJson(route, systemAdminMe));
  await page.route("**/api/auth/login**", (route) => fulfillJson(route, systemAdminMe));
  await mockNl2SqlApi(page);
  const questionText = "請求金額を確認したい";
  const createdAt = "2026-06-21T10:00:00.000Z";
  const warningMessage = "結果は生成されましたが、履歴/ジョブ保存に失敗しました。";
  const doneSteps = [
    { stage: "prepare_context", status: "done", elapsed_ms: 8 },
    { stage: "generate_sql", status: "done", elapsed_ms: 20 },
    { stage: "safety_check", status: "done", elapsed_ms: 4 },
    { stage: "execute_sql", status: "done", elapsed_ms: 12 },
    { stage: "format_results", status: "done", elapsed_ms: 6 },
  ];

  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-warning-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  await page.route("**/api/nl2sql/jobs/job-warning-001", (route) =>
    fulfillJson(route, {
      job_id: "job-warning-001",
      status: "done",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 50,
      error_message: null,
      warning_message: warningMessage,
      steps: doneSteps,
      timing,
      result: {
        history_id: "hist-warning-001",
        engine: "select_ai",
        engine_meta: { profile: "mock_profile" },
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
        timing,
      },
    })
  );

  await page.goto("/query");
  const loginHeading = page.getByRole("heading", { name: "システムにログイン" });
  if (await loginHeading.isVisible({ timeout: 3000 }).catch(() => false)) {
    await page.getByLabel("ログインユーザーID").fill("SYSTEM");
    await page.getByLabel("パスワード").fill("password");
    await page.getByRole("button", { name: "ログイン" }).click();
  }
  await expect(nl2sqlQuestionInput(page)).toBeVisible();
  await nl2sqlQuestionInput(page).fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  await expect(progress).toHaveAttribute("data-job-status", "done");
  await expect(progress.getByRole("status").filter({ hasText: warningMessage })).toBeVisible();
  await expect(progress).not.toContainText("処理を完了できませんでした");
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
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
  const question = nl2sqlQuestionInput(page);
  await question.fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  await expect(progress).toHaveAttribute("data-job-status", "error");
  await expect(progress).not.toHaveAttribute("role", "alert");
  await expect(progress.getByRole("alert")).toContainText("SQL 生成サービスに接続できませんでした。");
  await expect(page.getByTestId("nl2sql-job-step-generate_sql")).toHaveAttribute(
    "data-step-status",
    "error"
  );
  await expect(progress).toContainText("SQL 生成サービスに接続できませんでした。");
  await expect(question).toHaveValue(questionText);
  await expect(page.getByRole("button", { name: "検索を実行" })).toBeEnabled();
  await expectNoHorizontalScroll(page);
});

test("Query Rewrite の用語・同義語は既定 off で、Schema オプションを表示しない", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();

  await expect(page.getByLabel("用語・同義語を使う")).not.toBeChecked();
  await expect(page.getByLabel("Schema を使う")).toHaveCount(0);

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
  await nl2sqlQuestionInput(page).fill(questionText);
  await openNl2SqlExecutionOptions(page);
  await page.getByLabel("用語・同義語を使う").check();
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect(page.getByTestId("nl2sql-job-progress")).toHaveAttribute("data-job-status", "done");
  // ジョブへ渡す question が書き換え後の文になっている
  expect(jobPayload).toMatchObject({ question: rewrittenText, engine: "select_ai" });
  // 入力欄は書き換えずユーザー入力のまま保持する
  await expect(nl2sqlQuestionInput(page)).toHaveValue(questionText);
});

test("空の抽出条件では rewrite カードを出さず、条件を増やさずジョブ投入する", async ({ page }) => {
  await mockNl2SqlApi(page);
  const questionText = '対象テーブル："部署情報を管理するテーブル"\n抽出項目：\n抽出条件：';
  const warning = "抽出条件が空欄のため条件追加を抑止しました。";
  const blockedReason = "抽出条件が空欄の質問に対して WHERE 条件が生成されたため、SQL を実行しません。";
  const generatedWhere = 'UPPER("DEPARTMENT_NAME") LIKE \'%管理部門%\'';
  const blockedSafety = {
    ...safety,
    is_safe: false,
    blocked_reason: blockedReason,
    warnings: [blockedReason],
    referenced_tables: ["ADMIN.DEPARTMENT"],
    referenced_columns: ["ADMIN.DEPARTMENT.DEPARTMENT_ID", "ADMIN.DEPARTMENT.DEPARTMENT_NAME"],
  };
  const createdAt = "2026-06-21T10:00:00.000Z";

  let jobPayload: Record<string, unknown> | null = null;
  await page.unroute("**/api/nl2sql/rewrite");
  await page.route("**/api/nl2sql/rewrite", (route) =>
    fulfillJson(route, {
      original_question: questionText,
      rewritten_question: questionText,
      source: "deterministic",
      model: "",
      warnings: [warning],
    })
  );
  await page.route("**/api/nl2sql/jobs", (route) => {
    jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      job_id: "job-empty-filter-rewrite-001",
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
  await page.route("**/api/nl2sql/jobs/job-empty-filter-rewrite-001", (route) =>
    fulfillJson(route, {
      job_id: "job-empty-filter-rewrite-001",
      status: "error",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 20,
      error_message: blockedReason,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "done", elapsed_ms: 5 },
        { stage: "safety_check", status: "error", elapsed_ms: 2 },
        { stage: "execute_sql", status: "skipped", elapsed_ms: 0 },
        { stage: "format_results", status: "done", elapsed_ms: 1 },
      ],
      timing: null,
      result: {
        engine: "select_ai",
        original_question: questionText,
        rewritten_question: questionText,
        generated_sql: `SELECT "DEPARTMENT_ID", "DEPARTMENT_NAME" FROM "ADMIN"."DEPARTMENT" WHERE ${generatedWhere}`,
        executable_sql: "",
        explanation: blockedReason,
        safety: blockedSafety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: { columns: ["DEPARTMENT_ID", "DEPARTMENT_NAME"], rows: [], total: 0 },
        timing: null,
        interpretation: {
          available: true,
          warnings: [blockedReason],
          question: {
            available: true,
            source: "deterministic",
            original_question: questionText,
            rewritten_question: questionText,
            profile_id: "default",
            profile_name: "PROFILE_DEPT",
            target_objects: ["ADMIN.DEPARTMENT"],
            filters: [],
            group_by: [],
            order_by: [],
            aggregations: [],
            row_limit: null,
            confidence: 0.4,
            warnings: [blockedReason],
          },
          sql: {
            available: true,
            source: "sql_semantics",
            summary: "ADMIN.DEPARTMENT を参照し、SELECT 操作を行います。",
            statement_type: "SELECT",
            tables: ["ADMIN.DEPARTMENT"],
            columns: ["ADMIN.DEPARTMENT.DEPARTMENT_ID", "ADMIN.DEPARTMENT.DEPARTMENT_NAME"],
            joins: [],
            filters: [generatedWhere],
            aggregations: [],
            group_by: [],
            order_by: [],
            limit: null,
            semantic_graph: {},
            warnings: [blockedReason],
          },
        },
        show_prompt: null,
      },
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill(questionText);
  await openNl2SqlExecutionOptions(page);
  await page.getByLabel("用語・同義語を使う").check();
  await page.getByRole("button", { name: "検索を実行" }).click();

  // 質問が無変換のときは rewrite カード自体を出さない（内部処理の warning も表に出さない）。
  await expect(page.getByText("生成に使用される質問")).toHaveCount(0);
  await expect(page.getByText(warning)).toHaveCount(0);
  await expect(page.getByText("deterministic", { exact: true })).toHaveCount(0);
  expect(jobPayload).toMatchObject({ question: questionText, engine: "select_ai" });
  const submittedJobPayload = jobPayload as Record<string, unknown> | null;
  expect(String(submittedJobPayload?.question ?? "")).not.toContain("管理部門");
  const interpretation = page.getByTestId("nl2sql-interpretation-panel");
  await expect(interpretation.getByText("入力と SQL が一致していません")).toBeVisible();
  await expect(interpretation.getByText("入力の抽出条件は空欄ですが")).toBeVisible();
  await expect(page.getByText("入力と生成 SQL の対応")).toHaveCount(0);
  await expect(page.getByText("入力テンプレート")).toHaveCount(0);
  await expect(page.getByText("生成 SQL の意味")).toHaveCount(0);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(page.getByText("生成に使用される質問")).toHaveCount(0);
  await expect(page.getByText(warning)).toHaveCount(0);
  await expect(interpretation.getByText("入力と SQL が一致していません")).toBeVisible();
  await expect(interpretation.getByText("入力の抽出条件は空欄ですが")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("生成 SQL を読み取り専用 Ontology グラフへ接地して確認できる", async ({ page }, testInfo) => {
  await mockNl2SqlApi(page);
  const questionText = "部署ごとの従業員氏名を表示";
  const generatedSql =
    'SELECT "d"."DEPARTMENT_NAME" AS "部署名", "e"."EMPLOYEE_NAME" AS "従業員氏名", "p"."PROJECT_NAME" AS "プロジェクト名" FROM "ADMIN"."DEPARTMENT" "d" JOIN "ADMIN"."EMPLOYEE" "e" ON "e"."DEPARTMENT_ID"="d"."DEPARTMENT_ID" JOIN "ADMIN"."PROJECT" "p" ON "p"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"';
  const sqlGraph = {
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: generatedSql,
    ctes: [],
    tables: [
      {
        id: "table-department",
        owner: "ADMIN",
        name: "DEPARTMENT",
        alias: "d",
        qualified_name: "ADMIN.DEPARTMENT",
        source_sql: '"ADMIN"."DEPARTMENT" "d"',
      },
      {
        id: "table-employee",
        owner: "ADMIN",
        name: "EMPLOYEE",
        alias: "e",
        qualified_name: "ADMIN.EMPLOYEE",
        source_sql: '"ADMIN"."EMPLOYEE" "e"',
      },
      {
        id: "table-project",
        owner: "ADMIN",
        name: "PROJECT",
        alias: "p",
        qualified_name: "ADMIN.PROJECT",
        source_sql: '"ADMIN"."PROJECT" "p"',
      },
    ],
    columns: [
      {
        id: "column-department-name",
        table: "d",
        name: "DEPARTMENT_NAME",
        expression_sql: '"d"."DEPARTMENT_NAME"',
      },
      {
        id: "column-employee-name",
        table: "e",
        name: "EMPLOYEE_NAME",
        expression_sql: '"e"."EMPLOYEE_NAME"',
      },
      {
        id: "column-employee-department-id",
        table: "e",
        name: "DEPARTMENT_ID",
        expression_sql: '"e"."DEPARTMENT_ID"',
      },
      {
        id: "column-department-id",
        table: "d",
        name: "DEPARTMENT_ID",
        expression_sql: '"d"."DEPARTMENT_ID"',
      },
      {
        id: "column-project-name",
        table: "p",
        name: "PROJECT_NAME",
        expression_sql: '"p"."PROJECT_NAME"',
      },
      {
        id: "column-project-department-id",
        table: "p",
        name: "DEPARTMENT_ID",
        expression_sql: '"p"."DEPARTMENT_ID"',
      },
    ],
    joins: [
      {
        id: "join-department-employee",
        left_source: '"ADMIN"."DEPARTMENT" "d"',
        right_source: '"ADMIN"."EMPLOYEE" "e"',
        join_type: "inner",
        condition_sql: '"e"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
      },
      {
        // star join。過去 artifact 互換のため位置ベースの誤った左端点を保持する。
        id: "join-department-project",
        left_source: '"ADMIN"."EMPLOYEE" "e"',
        right_source: '"ADMIN"."PROJECT" "p"',
        join_type: "inner",
        condition_sql: '"p"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
      },
    ],
    projections: [
      {
        id: "projection-dept",
        output_name: "部署名",
        expression_sql: '"d"."DEPARTMENT_NAME" AS "部署名"',
        referenced_columns: ["d.DEPARTMENT_NAME"],
      },
      {
        id: "projection-employee",
        output_name: "従業員氏名",
        expression_sql: '"e"."EMPLOYEE_NAME" AS "従業員氏名"',
        referenced_columns: ["e.EMPLOYEE_NAME"],
      },
      {
        id: "projection-project",
        output_name: "プロジェクト名",
        expression_sql: '"p"."PROJECT_NAME" AS "プロジェクト名"',
        referenced_columns: ["p.PROJECT_NAME"],
      },
    ],
    filters: [],
    aggregates: [],
    groups: [],
    having: [],
    orders: [],
    windows: [],
    parse_warnings: [],
  };
  const ontologyGraph = {
    revision: {
      id: "revision-admin-hr",
      version: 1,
      status: "published",
      schema_fingerprint: "hr-schema",
      etag: "revision-admin-hr-etag",
    },
    nodes: [
      {
        id: "department-business",
        kind: "business_entity",
        business_name_ja: "部署",
        technical_name: "department",
        review_status: "approved",
        physical_mappings: [
          {
            object_ref: {
              node_id: "department-table",
              owner: "ADMIN",
              object_name: "DEPARTMENT",
              object_type: "table",
            },
          },
        ],
      },
      {
        id: "employee-business",
        kind: "business_entity",
        business_name_ja: "従業員",
        technical_name: "employee",
        review_status: "approved",
        physical_mappings: [
          {
            object_ref: {
              node_id: "employee-table",
              owner: "ADMIN",
              object_name: "EMPLOYEE",
              object_type: "table",
            },
          },
        ],
      },
      {
        id: "department-table",
        kind: "table",
        technical_name: "ADMIN.DEPARTMENT",
        business_name_ja: "部署情報",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "DEPARTMENT" },
      },
      {
        id: "employee-table",
        kind: "table",
        technical_name: "ADMIN.EMPLOYEE",
        business_name_ja: "従業員情報",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE" },
      },
      {
        id: "project-table",
        kind: "table",
        technical_name: "ADMIN.PROJECT",
        business_name_ja: "プロジェクト情報",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "PROJECT" },
      },
      {
        id: "department-name",
        kind: "property",
        technical_name: "ADMIN.DEPARTMENT.DEPARTMENT_NAME",
        business_name_ja: "部署名",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_NAME" },
      },
      {
        id: "employee-name",
        kind: "property",
        technical_name: "ADMIN.EMPLOYEE.EMPLOYEE_NAME",
        business_name_ja: "従業員氏名",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "EMPLOYEE_NAME" },
      },
      {
        id: "department-id",
        kind: "column",
        technical_name: "ADMIN.DEPARTMENT.DEPARTMENT_ID",
        business_name_ja: "部署ID",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_ID" },
      },
      {
        id: "employee-department-id",
        kind: "column",
        technical_name: "ADMIN.EMPLOYEE.DEPARTMENT_ID",
        business_name_ja: "従業員部署ID",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "DEPARTMENT_ID" },
      },
      {
        id: "project-name",
        kind: "property",
        technical_name: "ADMIN.PROJECT.PROJECT_NAME",
        business_name_ja: "プロジェクト名",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "PROJECT", column_name: "PROJECT_NAME" },
      },
      {
        id: "project-department-id",
        kind: "column",
        technical_name: "ADMIN.PROJECT.DEPARTMENT_ID",
        business_name_ja: "部門ID",
        review_status: "approved",
        metadata: { owner: "ADMIN", object_name: "PROJECT", column_name: "DEPARTMENT_ID" },
      },
    ],
    edges: [
      {
        id: "department-business-map",
        kind: "physical_mapping",
        source_node_id: "department-business",
        target_node_id: "department-table",
        relationship_name_ja: "物理マッピング",
        review_status: "approved",
      },
      {
        id: "employee-business-map",
        kind: "physical_mapping",
        source_node_id: "employee-business",
        target_node_id: "employee-table",
        relationship_name_ja: "物理マッピング",
        review_status: "approved",
      },
      {
        id: "department-name-column",
        kind: "column",
        source_node_id: "department-table",
        target_node_id: "department-name",
        relationship_name_ja: "列",
        review_status: "approved",
      },
      {
        id: "employee-name-column",
        kind: "column",
        source_node_id: "employee-table",
        target_node_id: "employee-name",
        relationship_name_ja: "列",
        review_status: "approved",
      },
      {
        id: "department-employee-join",
        kind: "foreign_key",
        source_node_id: "department-table",
        target_node_id: "employee-table",
        relationship_name_ja: "部署と従業員の Join",
        review_status: "approved",
        join_conditions: [
          {
            left: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_ID" },
            right: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "DEPARTMENT_ID" },
            operator: "=",
            ordinal: 1,
          },
        ],
      },
      {
        id: "project-name-column",
        kind: "column",
        source_node_id: "project-table",
        target_node_id: "project-name",
        relationship_name_ja: "列",
        review_status: "approved",
      },
      {
        id: "department-project-join",
        kind: "foreign_key",
        source_node_id: "department-table",
        target_node_id: "project-table",
        relationship_name_ja: "部署とプロジェクトの Join",
        review_status: "approved",
        join_conditions: [
          {
            left: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_ID" },
            right: { owner: "ADMIN", object_name: "PROJECT", column_name: "DEPARTMENT_ID" },
            operator: "=",
            ordinal: 1,
          },
        ],
      },
    ],
  };

  await page.unroute("**/api/nl2sql/jobs");
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-ontology-grounding-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  const groundingJobDetail = {
      job_id: "job-ontology-grounding-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.050Z",
      elapsed_ms: 50,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 12 },
        { stage: "format_results", status: "done", elapsed_ms: 6 },
      ],
      timing: null,
      result: {
        history_id: "hist-ontology-grounding-001",
        engine: "select_ai",
        engine_meta: { profile: "mock_agent_profile" },
        fallback_reason: "",
        original_question: questionText,
        rewritten_question: questionText,
        generated_sql: generatedSql,
        executable_sql: generatedSql,
        explanation: "部署と従業員を結合して氏名を取得します。",
        safety: {
          ...safety,
          referenced_tables: ["ADMIN.DEPARTMENT", "ADMIN.EMPLOYEE"],
          referenced_columns: [
            "ADMIN.DEPARTMENT.DEPARTMENT_NAME",
            "ADMIN.EMPLOYEE.EMPLOYEE_NAME",
            "ADMIN.EMPLOYEE.DEPARTMENT_ID",
            "ADMIN.DEPARTMENT.DEPARTMENT_ID",
          ],
        },
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: ["部署名", "従業員氏名"],
          rows: [{ 部署名: "開発部", 従業員氏名: "山田太郎" }],
          total: 1,
        },
        timing,
        interpretation: {
          available: true,
          question: {
            available: true,
            source: "deterministic",
            original_question: questionText,
            rewritten_question: questionText,
            profile_id: "default",
            profile_name: "PROFILE_ALL",
            profile_category: "HR_ALL",
            target_objects: ["ADMIN.DEPARTMENT", "ADMIN.EMPLOYEE"],
            filters: [],
            group_by: [],
            order_by: [],
            aggregations: [],
            row_limit: null,
            confidence: 0.9,
            warnings: [],
          },
          sql: {
            available: true,
            source: "sql_semantics",
            summary: "ADMIN.DEPARTMENT と ADMIN.EMPLOYEE を参照し、SELECT 操作を行います。",
            statement_type: "SELECT",
            tables: ["ADMIN.DEPARTMENT", "ADMIN.EMPLOYEE", "ADMIN.PROJECT"],
            columns: [
              "ADMIN.DEPARTMENT.DEPARTMENT_NAME",
              "ADMIN.EMPLOYEE.EMPLOYEE_NAME",
              "ADMIN.PROJECT.PROJECT_NAME",
            ],
            joins: [
              '"e"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
              '"p"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
            ],
            filters: [],
            aggregations: [],
            group_by: [],
            order_by: [],
            limit: null,
            logical_steps: [
              "ADMIN.DEPARTMENT と ADMIN.EMPLOYEE を参照し、SELECT 操作を行います。",
              '結合: "e"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
              '結合: "p"."DEPARTMENT_ID"="d"."DEPARTMENT_ID"',
            ],
            semantic_graph: sqlGraph,
            warnings: [],
          },
          ontology_graph: ontologyGraph,
          warnings: [],
        },
        show_prompt: null,
      },
  };
  await page.route("**/api/nl2sql/jobs/job-ontology-grounding-001", (route) =>
    fulfillJson(route, groundingJobDetail)
  );
  await page.route("**/api/nl2sql/profiles/default/ontology-view", (route) =>
    fulfillJson(route, {
      profile_ontology_view: {
        id: "profile-view-admin-hr",
        profile_id: "default",
        ontology_revision_id: "revision-admin-hr",
        node_ids: ontologyGraph.nodes.map((node) => node.id),
        edge_ids: ontologyGraph.edges.map((edge) => edge.id),
      },
      ontology_graph: ontologyGraph,
      materialized: true,
      stale: false,
      warnings_ja: [],
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const panel = page.getByTestId("nl2sql-sql-grounding-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("SQL 全要素を接地");
  // 処理手順パネルは接地確認の下に独立表示し、番号付きで手順を示す。
  const stepsPanel = page.getByTestId("nl2sql-logical-steps-panel");
  await expect(stepsPanel).toBeVisible();
  await expect(stepsPanel).toContainText("SQL の処理手順");
  await expect(stepsPanel).toContainText(
    "ADMIN.DEPARTMENT と ADMIN.EMPLOYEE を参照し、SELECT 操作を行います。"
  );
  await expect(stepsPanel.locator("ol > li")).toHaveCount(3);
  await stepsPanel.screenshot({ path: testInfo.outputPath("sql-logical-steps.png") });
  await expect(page.getByText("入力と生成 SQL の対応")).toHaveCount(0);
  await expect(page.getByText("入力テンプレート")).toHaveCount(0);
  await expect(page.getByText("生成 SQL の意味")).toHaveCount(0);
  await expect(page.getByTestId("ontology-node-card-department-table")).toContainText("部署情報");
  await expect(page.getByTestId("ontology-node-card-employee-table")).toContainText("従業員情報");
  await expect(panel.getByTestId("nl2sql-sql-grounding-list")).toContainText("ADMIN.DEPARTMENT");
  await expect(panel.getByTestId("nl2sql-sql-grounding-list")).toContainText("部署と従業員の Join");
  // star join の 2 本目も ON 句の列対で接地する(left_source は誤ったまま)。
  await expect(page.getByTestId("ontology-node-card-project-table")).toContainText(
    "プロジェクト情報"
  );
  await expect(panel.getByTestId("nl2sql-sql-grounding-list")).toContainText(
    "部署とプロジェクトの Join"
  );

  const graphSearch = panel.getByTestId("ontology-graph-search");
  await graphSearch.focus();
  await expect(graphSearch).toBeFocused();
  const zoomIn = panel.getByLabel("グラフを拡大");
  await zoomIn.focus();
  await expect(zoomIn).toBeFocused();

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("SQL 全要素を接地")).toBeVisible();
  await expect(stepsPanel).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: testInfo.outputPath("sql-ontology-grounding.png"), fullPage: true });

  // 「Ontology を使う」OFF(backend echo)のときは接地確認を出さず、処理手順は残す。
  await page.unroute("**/api/nl2sql/jobs/job-ontology-grounding-001");
  await page.route("**/api/nl2sql/jobs/job-ontology-grounding-001", (route) =>
    fulfillJson(route, {
      ...groundingJobDetail,
      result: {
        ...groundingJobDetail.result,
        interpretation: {
          ...groundingJobDetail.result.interpretation,
          ontology_grounding_enabled: false,
          ontology_graph: null,
        },
      },
    })
  );
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect(page.getByTestId("nl2sql-sql-grounding-panel")).toHaveCount(0);
  await expect(stepsPanel).toBeVisible();
  await expect(stepsPanel).toContainText("SQL の処理手順");
});

test("未修飾列の単一表 SELECT は FROM 句の表だけを接地する", async ({ page }, testInfo) => {
  await mockNl2SqlApi(page);
  const questionText = "従業員の一覧を表示";
  // 実 SQL 相当: 別名 EMP を付けながら SELECT 列は未修飾。列名だけで ontology 全体を
  // 横断一致させると DEPARTMENT / PROJECT の同名列まで接地扱いになる回帰を防ぐ。
  const generatedSql =
    'SELECT "EMPLOYEE_ID" AS "EMPLOYEE_ID","DEPARTMENT_ID" AS "DEPARTMENT_ID","EMPLOYEE_NAME" AS "EMPLOYEE_NAME","SALARY" AS "SALARY" FROM "ADMIN"."EMPLOYEE" "EMP"';
  const columnNames = ["EMPLOYEE_ID", "DEPARTMENT_ID", "EMPLOYEE_NAME", "SALARY"];
  const sqlGraph = {
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: generatedSql,
    ctes: [],
    tables: [
      {
        id: "table-employee",
        scope_id: "scope_1",
        owner: "ADMIN",
        name: "EMPLOYEE",
        alias: "EMP",
        qualified_name: "ADMIN.EMPLOYEE",
        source_sql: '"ADMIN"."EMPLOYEE" "EMP"',
      },
    ],
    // backend の parse_oracle_sql は未修飾列を table:"" で返し、projections に同じ列を再掲する。
    columns: columnNames.map((name) => ({
      id: `column-${name}`,
      scope_id: "scope_1",
      owner: "",
      table: "",
      name,
      clause: "select",
      expression_sql: `"${name}"`,
    })),
    projections: columnNames.map((name) => ({
      id: `projection-${name}`,
      scope_id: "scope_1",
      output_name: name,
      expression_sql: `"${name}" AS "${name}"`,
      referenced_columns: [name],
    })),
    joins: [],
    filters: [],
    aggregates: [],
    groups: [],
    having: [],
    orders: [],
    windows: [],
    limit: null,
  };

  const businessNode = (id: string, label: string, objectName: string, nodeId: string) => ({
    id,
    kind: "business_entity",
    business_name_ja: label,
    review_status: "approved",
    physical_mappings: [
      {
        object_ref: { node_id: nodeId, owner: "ADMIN", object_name: objectName, object_type: "table" },
      },
    ],
  });
  const tableNode = (id: string, objectName: string, label: string) => ({
    id,
    kind: "table",
    technical_name: `ADMIN.${objectName}`,
    business_name_ja: label,
    review_status: "approved",
    metadata: { owner: "ADMIN", object_name: objectName },
  });
  const columnNode = (id: string, objectName: string, columnName: string, label: string) => ({
    id,
    kind: "column",
    technical_name: `ADMIN.${objectName}.${columnName}`,
    business_name_ja: label,
    review_status: "approved",
    metadata: { owner: "ADMIN", object_name: objectName, column_name: columnName },
  });
  const ontologyGraph = {
    id: "revision-admin-hr",
    nodes: [
      businessNode("employee-business", "従業員", "EMPLOYEE", "employee-table"),
      businessNode("department-business", "部署", "DEPARTMENT", "department-table"),
      businessNode("project-business", "プロジェクト", "PROJECT", "project-table"),
      tableNode("employee-table", "EMPLOYEE", "従業員情報"),
      tableNode("department-table", "DEPARTMENT", "部署情報"),
      tableNode("project-table", "PROJECT", "プロジェクト情報"),
      columnNode("employee-id", "EMPLOYEE", "EMPLOYEE_ID", "従業員ID(主キー)"),
      columnNode("employee-department-id", "EMPLOYEE", "DEPARTMENT_ID", "所属部署ID(外部キー)"),
      columnNode("employee-name", "EMPLOYEE", "EMPLOYEE_NAME", "従業員氏名"),
      columnNode("employee-salary", "EMPLOYEE", "SALARY", "給与"),
      // 同名 DEPARTMENT_ID を持つだけの無関係な列。接地してはならない。
      columnNode("department-id", "DEPARTMENT", "DEPARTMENT_ID", "部署ID(主キー)"),
      columnNode("project-department-id", "PROJECT", "DEPARTMENT_ID", "部門ID"),
    ],
    edges: [
      {
        id: "fk-employee-department",
        source_node_id: "employee-table",
        target_node_id: "department-table",
        kind: "foreign_key",
        relationship_name_ja: "所属部署",
        cardinality: "many_to_one",
        review_status: "approved",
        join_conditions: [
          {
            left: { owner: "ADMIN", object_name: "EMPLOYEE", column_name: "DEPARTMENT_ID" },
            right: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_ID" },
            operator: "=",
            ordinal: 1,
          },
        ],
      },
      {
        id: "fk-project-department",
        source_node_id: "project-table",
        target_node_id: "department-table",
        kind: "foreign_key",
        relationship_name_ja: "担当部署",
        cardinality: "many_to_one",
        review_status: "approved",
        join_conditions: [
          {
            left: { owner: "ADMIN", object_name: "PROJECT", column_name: "DEPARTMENT_ID" },
            right: { owner: "ADMIN", object_name: "DEPARTMENT", column_name: "DEPARTMENT_ID" },
            operator: "=",
            ordinal: 1,
          },
        ],
      },
    ],
  };

  await page.unroute("**/api/nl2sql/jobs");
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfillJson(route, {
      job_id: "job-unqualified-grounding-001",
      status: "running",
      created_at: "2026-06-21T10:00:00.000Z",
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  await page.route("**/api/nl2sql/jobs/job-unqualified-grounding-001", (route) =>
    fulfillJson(route, {
      job_id: "job-unqualified-grounding-001",
      status: "done",
      created_at: "2026-06-21T10:00:00.000Z",
      started_at: "2026-06-21T10:00:00.000Z",
      finished_at: "2026-06-21T10:00:00.050Z",
      elapsed_ms: 50,
      error_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 8 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 12 },
        { stage: "format_results", status: "done", elapsed_ms: 6 },
      ],
      timing: null,
      result: {
        history_id: "hist-unqualified-grounding-001",
        engine: "select_ai",
        engine_meta: { profile: "mock_agent_profile" },
        fallback_reason: "",
        original_question: questionText,
        rewritten_question: questionText,
        generated_sql: generatedSql,
        executable_sql: generatedSql,
        explanation: "従業員テーブルの一覧を取得します。",
        safety: {
          ...safety,
          referenced_tables: ["ADMIN.EMPLOYEE"],
          referenced_columns: columnNames.map((name) => `ADMIN.EMPLOYEE.${name}`),
        },
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: columnNames,
          rows: [
            {
              EMPLOYEE_ID: 1,
              DEPARTMENT_ID: 10,
              EMPLOYEE_NAME: "山田太郎",
              SALARY: 500000,
            },
          ],
          total: 1,
        },
        timing,
        interpretation: {
          available: true,
          question: {
            available: true,
            source: "deterministic",
            original_question: questionText,
            rewritten_question: questionText,
            profile_id: "default",
            profile_name: "PROFILE_ALL",
            profile_category: "HR_ALL",
            target_objects: ["ADMIN.EMPLOYEE"],
            filters: [],
            group_by: [],
            order_by: [],
            aggregations: [],
            row_limit: null,
            confidence: 0.9,
            warnings: [],
          },
          sql: {
            available: true,
            source: "sql_semantics",
            summary: "ADMIN.EMPLOYEE を参照し、SELECT 操作を行います。",
            statement_type: "SELECT",
            tables: ["ADMIN.EMPLOYEE"],
            columns: columnNames.map((name) => `ADMIN.EMPLOYEE.${name}`),
            joins: [],
            filters: [],
            aggregations: [],
            group_by: [],
            order_by: [],
            limit: null,
            logical_steps: ["ADMIN.EMPLOYEE を参照し、SELECT 操作を行います。"],
            semantic_graph: sqlGraph,
            warnings: [],
          },
          ontology_graph: ontologyGraph,
          warnings: [],
        },
        show_prompt: null,
      },
    })
  );
  await page.route("**/api/nl2sql/profiles/default/ontology-view", (route) =>
    fulfillJson(route, {
      profile_ontology_view: {
        id: "profile-view-admin-hr",
        profile_id: "default",
        ontology_revision_id: "revision-admin-hr",
        node_ids: ontologyGraph.nodes.map((node) => node.id),
        edge_ids: ontologyGraph.edges.map((edge) => edge.id),
      },
      ontology_graph: ontologyGraph,
      materialized: true,
      stale: false,
      warnings_ja: [],
    })
  );

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill(questionText);
  await page.getByRole("button", { name: "検索を実行" }).click();

  const panel = page.getByTestId("nl2sql-sql-grounding-panel");
  await expect(panel).toBeVisible();
  // 表 1 + 列 4 = 5 件。projections[] の再掲を二重に数えない。
  await expect(panel).toContainText("SQL 全要素を接地");
  await expect(panel).toContainText("SQL 要素 5 件を接地");

  const grounded = (nodeId: string) =>
    panel.getByTestId(`ontology-node-card-${nodeId}`).getAttribute("data-ontology-node-grounded");
  for (const nodeId of [
    "employee-business",
    "employee-table",
    "employee-id",
    "employee-department-id",
    "employee-name",
    "employee-salary",
  ]) {
    await expect
      .poll(() => grounded(nodeId), { message: `接地すべきノード: ${nodeId}` })
      .toBe("true");
  }
  // FK で隣接するだけの表は文脈ノードとして残るが、接地はしない。
  await expect(panel.getByTestId("ontology-node-card-department-table")).toBeVisible();
  for (const nodeId of ["department-table", "department-id", "project-table", "project-department-id"]) {
    const card = panel.getByTestId(`ontology-node-card-${nodeId}`);
    if ((await card.count()) === 0) continue;
    await expect
      .poll(() => grounded(nodeId), { message: `接地してはならないノード: ${nodeId}` })
      .toBe("false");
  }

  await panel.screenshot({
    path: testInfo.outputPath("sql-grounding-unqualified-columns.png"),
  });

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(panel).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({
    path: testInfo.outputPath("sql-grounding-unqualified-columns-375.png"),
    fullPage: true,
  });
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

  await nl2sqlQuestionInput(page).fill("すべてプロジェクトを教えてください。");
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

test("Select AI の今回だけの生成条件を job に渡し、reset で消去できる", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/query");

  await page.getByRole("button", { name: /Select AI DBMS_CLOUD_AI profile/ }).click();
  const disclosure = page.getByRole("button", { name: "今回だけの生成条件" });
  await expectButtonBelowInput(nl2sqlQuestionInput(page), disclosure);
  await expect(disclosure).toHaveAttribute("aria-expanded", "false");
  await disclosure.click();
  await expect(disclosure).toHaveAttribute("aria-expanded", "true");

  await page.getByLabel("今回の追加条件").fill("現在日付を基準に四半期を計算する。");
  const roleDisclosure = page.getByRole("button", { name: "ロールを上書き" });
  await expect(roleDisclosure).toHaveAttribute("aria-expanded", "false");
  await roleDisclosure.click();
  await expect(roleDisclosure).toHaveAttribute("aria-expanded", "true");
  await page.getByLabel("アシスタントロール").fill("CFO 向け財務 SQL アシスタント");
  await expect(disclosure.getByText("条件あり")).toBeVisible();
  await nl2sqlQuestionInput(page).fill("前四半期の売上を確認したい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  // 用語・同義語は既定 off なので、入力そのままの質問で job を作る。
  await expect.poll(() => api.jobPayload?.question).toBe("前四半期の売上を確認したい");
  expect(api.jobPayload).toMatchObject({
    engine: "select_ai",
    select_ai_overrides: {
      role: "CFO 向け財務 SQL アシスタント",
      additional_instructions: "現在日付を基準に四半期を計算する。",
    },
  });

  await page.getByRole("button", { name: "リセット" }).click();
  await expect(disclosure).toHaveAttribute("aria-expanded", "false");
  await disclosure.click();
  await expect(page.getByLabel("今回の追加条件")).toHaveValue("");
  await expect(page.getByRole("button", { name: "ロールを上書き" })).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "ロールを上書き" }).click();
  await expect(page.getByLabel("アシスタントロール")).toHaveValue("");

  await page.getByRole("button", { name: /Enterprise AI Direct/ }).click();
  await nl2sqlQuestionInput(page).fill("請求金額を一覧で見たい");
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect.poll(() => api.jobPayload?.engine).toBe("enterprise_ai_direct");
  expect(api.jobPayload).toMatchObject({
    engine: "enterprise_ai_direct",
    select_ai_overrides: null,
  });
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
    if (/\bEMPLOYEE\b/i.test(sql)) {
      return route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "SELECT の実行に失敗しました: ORA-01031: insufficient privileges",
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

  const sqlInput = directSqlInput(page);
  const rowLimitInput = directSql.getByLabel("取得件数上限");
  const rowLimitHelper = directSql.getByText("1〜100000 の整数。取得上限なしは指定できません。");
  await expect(rowLimitInput).toHaveValue("100");
  await expectOneLineWithoutOverflow(rowLimitHelper);
  await expectButtonBelowInput(rowLimitInput, directSql.getByRole("button", { name: "SQL 実行" }));
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL 実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(directSql.getByTestId("query-result-summary")).toContainText("取得件数 1 件");
  await expect(directSql.getByTestId("query-result-summary")).toContainText("取得上限 100 件");
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  expect(api.executePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    allowed_objects: { table_names: [], columns: {} },
    row_limit: 100,
  });
  expect(api.adminExecutePayload).toBeNull();

  await page.getByRole("button", { name: "クリア" }).click();
  await expect(sqlInput).toHaveValue("");
  await expect(rowLimitInput).toHaveValue("100");
  await expect(page.getByText("検索結果（1件）")).toHaveCount(0);

  // /api/nl2sql/execute は 1..100000 のみ受理(0=無制限 fetch は db-admin 専用で、この画面では送信不可)。
  await rowLimitInput.fill("-1");
  await expect(directSql.getByRole("alert")).toContainText("1〜100000 の整数で入力してください。");
  await expect(directSql.getByRole("button", { name: "SQL 実行" })).toBeDisabled();
  await rowLimitInput.fill("0");
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expect(directSql.getByRole("alert")).toContainText("1〜100000 の整数で入力してください。");
  await expect(directSql.getByRole("button", { name: "SQL 実行" })).toBeDisabled();
  await rowLimitInput.fill("100001");
  await expect(directSql.getByRole("alert")).toContainText("1〜100000 の整数で入力してください。");
  await expect(directSql.getByRole("button", { name: "SQL 実行" })).toBeDisabled();

  await page.getByRole("button", { name: "クリア" }).click();
  await rowLimitInput.fill("1");
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "SQL 実行" }).click();
  await expect(directSql.getByTestId("query-result-summary")).toContainText("上限到達");

  await page.getByRole("button", { name: "クリア" }).click();
  await sqlInput.fill("UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1");
  await page.getByRole("button", { name: "SQL 実行" }).click();
  await expect(page.getByRole("alert")).toContainText("SELECT/WITH のみ実行できます。");
  await expect(directSql.getByTestId("direct-sql-processing-error")).toContainText(
    "SELECT/WITH のみ実行できます。"
  );
  expect(api.adminExecutePayload).toBeNull();

  await page.getByRole("button", { name: "クリア" }).click();
  await sqlInput.fill("select * from employee");
  await page.getByRole("button", { name: "SQL 実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await expect(directSql.getByTestId("direct-sql-execution-activity")).toContainText("失敗");
  await expect(directSql.getByTestId("direct-sql-processing-error")).toContainText(
    "SELECT の実行に失敗しました: ORA-01031"
  );
  await expect(page).not.toHaveURL(/\/forbidden$/);

  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectOneLineWithoutOverflow(rowLimitHelper);
  await expectNoHorizontalScroll(page);
});

test("SQL 実行中は主ボタンだけが動的 spinner を表示する", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.unroute("**/api/nl2sql/execute");
  const directGate = createRequestGate();
  await page.route("**/api/nl2sql/execute", async (route) => {
    await directGate.promise;
    return fulfillJson(route, {
      columns: ["CUSTOMER_NAME"],
      rows: [{ CUSTOMER_NAME: "青山商事" }],
      total: 1,
    });
  });

  await page.goto("/direct-sql");
  const directSql = page.getByTestId("nl2sql-direct-sql");
  await directSqlInput(directSql).fill("SELECT CUSTOMER_NAME FROM INVOICES");
  const directExecuteButton = directSql.getByRole("button", { name: "SQL 実行" });
  const directRequest = page.waitForRequest("**/api/nl2sql/execute");
  await directExecuteButton.click();
  await directRequest;
  try {
    const directProcessing = directSql.getByTestId("direct-sql-execution-activity");
    await expect(directExecuteButton.locator("svg.animate-spin")).toHaveCount(1);
    await expect(directProcessing).toContainText("SQL を実行しています");
    await expect(directProcessing.locator("svg.animate-spin")).toHaveCount(0);
    await expect(directProcessing.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expectCompactExecutionActivity(directProcessing);
    await expect(directSql.getByTestId("direct-sql-processing-region")).toHaveCount(0);
    await expectNoHorizontalScroll(page);
  } finally {
    directGate.release();
  }
  await expect(directSql.getByText("検索結果（1件）")).toBeVisible();
  await expect(directSql.getByTestId("direct-sql-execution-activity").getByRole("timer")).toHaveAccessibleName(
    /処理時間 \d{2}:\d{2}/
  );
  await expectCompactExecutionActivity(directSql.getByTestId("direct-sql-execution-activity"));

  await page.unroute("**/api/nl2sql/db-admin/execute");
  const adminGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/execute", async (route) => {
    await adminGate.promise;
    return fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: {
        columns: ["CUSTOMER_NAME"],
        rows: [{ CUSTOMER_NAME: "青山商事" }],
        total: 1,
      },
      statements: [
        {
          index: 1,
          statement_type: "SELECT",
          status: "executed",
          sql: "SELECT CUSTOMER_NAME FROM INVOICES",
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

  await page.goto("/admin-sql");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await adminSqlInput(adminSql).fill("SELECT CUSTOMER_NAME FROM INVOICES");
  const adminExecuteButton = adminSql.getByRole("button", { name: "SQL 実行" });
  const adminRequest = page.waitForRequest("**/api/nl2sql/db-admin/execute");
  await adminExecuteButton.focus();
  await adminExecuteButton.press("Enter");
  await adminRequest;
  try {
    const adminProcessing = adminSql.getByTestId("admin-sql-execution-activity");
    await expect(adminExecuteButton.locator("svg.animate-spin")).toHaveCount(1);
    await expect(adminProcessing).toContainText("SQL を実行しています");
    await expect(adminProcessing.locator("svg.animate-spin")).toHaveCount(0);
    await expect(adminProcessing.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expectCompactExecutionActivity(adminProcessing);
    await expect(adminSql.getByTestId("admin-sql-processing-region")).toHaveCount(0);
    await expectNoHorizontalScroll(page);
  } finally {
    adminGate.release();
  }
  await expect(adminSql.getByTestId("query-results-table")).toBeVisible();
  await expect(adminSql.getByTestId("admin-sql-execution-activity").getByRole("timer")).toHaveAccessibleName(
    /処理時間 \d{2}:\d{2}/
  );
  await expectCompactExecutionActivity(adminSql.getByTestId("admin-sql-execution-activity"));
});

test("SQL 再実行は main スクロールを先頭へ戻さず結果領域を更新する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 430 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockNl2SqlApi(page);

  const rows = Array.from({ length: 16 }, (_, index) => ({
    CUSTOMER_NAME: `青山商事 ${String(index + 1).padStart(2, "0")}`,
    TOTAL_AMOUNT: 1_200_000 + index,
  }));

  await page.unroute("**/api/nl2sql/execute");
  let directRequestCount = 0;
  const directGate = createRequestGate();
  await page.route("**/api/nl2sql/execute", async (route) => {
    directRequestCount += 1;
    if (directRequestCount === 2) await directGate.promise;
    return fulfillJson(route, {
      columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      rows,
      total: rows.length,
    });
  });

  await page.goto("/direct-sql");
  const directSql = page.getByTestId("nl2sql-direct-sql");
  await directSqlInput(directSql).fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  const directExecuteButton = directSql.getByRole("button", { name: "SQL 実行" });
  await directExecuteButton.click();
  await expect(directSql.getByText("検索結果（16件）")).toBeVisible();

  await directExecuteButton.scrollIntoViewIfNeeded();
  await expectMainScrolledBelowTop(page);
  const directRequest = page.waitForRequest("**/api/nl2sql/execute");
  await directExecuteButton.click();
  await directRequest;
  try {
    await expect(directSql.getByTestId("direct-sql-execution-activity")).toBeVisible();
    await expectMainScrolledBelowTop(page);
  } finally {
    directGate.release();
  }
  await expect(directSql.getByText("検索結果（16件）")).toBeVisible();
  await expectMainScrolledBelowTop(page);

  await page.unroute("**/api/nl2sql/db-admin/execute");
  let adminRequestCount = 0;
  const adminGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/execute", async (route) => {
    adminRequestCount += 1;
    if (adminRequestCount === 2) await adminGate.promise;
    return fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: {
        columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
        rows,
        total: rows.length,
      },
      statements: [
        {
          index: 1,
          statement_type: "SELECT",
          status: "executed",
          sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
          row_count: rows.length,
          message: `${rows.length} rows`,
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

  await page.goto("/admin-sql");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await adminSqlInput(adminSql).fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  const adminExecuteButton = adminSql.getByRole("button", { name: "SQL 実行" });
  await adminExecuteButton.click();
  await expect(adminSql.getByTestId("query-results-table")).toBeVisible();

  await adminExecuteButton.scrollIntoViewIfNeeded();
  await expectMainScrolledBelowTop(page);
  const adminRequest = page.waitForRequest("**/api/nl2sql/db-admin/execute");
  await adminExecuteButton.click();
  await adminRequest;
  try {
    await expect(adminSql.getByTestId("admin-sql-execution-activity")).toBeVisible();
    await expectMainScrolledBelowTop(page);
  } finally {
    adminGate.release();
  }
  await expect(adminSql.getByTestId("query-results-table")).toBeVisible();
  await expectMainScrolledBelowTop(page);
});

test("データ準備の管理 SQL 画面は SELECT と確認済み更新 SQL を実行する", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await openSidebarLink(page, "管理 SQL を実行");
  await expect(page).toHaveURL(/\/admin-sql$/);
  await expect(page.getByRole("heading", { level: 1, name: "管理 SQL を実行" })).toBeVisible();

  const adminSql = page.getByTestId("nl2sql-admin-sql");
  const sqlInput = adminSqlInput(adminSql);
  const rowLimitInput = adminSql.getByLabel("取得件数上限");
  await expect(rowLimitInput).toHaveValue("100");
  await expectButtonBelowInput(rowLimitInput, adminSql.getByRole("button", { name: "SQL 実行" }));
  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await expect(
    adminSql.getByText("単一 SELECT/WITH は、ログインユーザーの DeepSec context を設定した data plane で実行します。")
  ).toHaveCount(0);
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await expect(adminSql.getByTestId("query-results-table")).toBeVisible();
  await expect(adminSql.getByTestId("query-result-summary")).toContainText("取得件数 1 件");
  await expect(adminSql.getByTestId("query-result-summary")).toContainText("取得上限 100 件");
  await expect(adminSql.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(
    adminSql.locator("code").filter({ hasText: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES" })
  ).toHaveCount(0);
  expect(api.adminExecutePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    row_limit: 100,
    confirmation: "",
    reason: "admin-sql-select",
  });

  await adminSql.getByRole("button", { name: "クリア" }).click();
  await expect(sqlInput).toHaveValue("");
  await expect(rowLimitInput).toHaveValue("100");
  await expect(adminSql.getByTestId("query-results-table")).toHaveCount(0);

  await sqlInput.fill("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES");
  await rowLimitInput.fill("0");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await expect(adminSql.getByTestId("query-result-summary")).toContainText("取得上限なし");
  expect(api.adminExecutePayload).toEqual({
    sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
    row_limit: 0,
    confirmation: "",
    reason: "admin-sql-select",
  });

  await adminSql.getByRole("button", { name: "クリア" }).click();
  const literalSelectSql =
    "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES " +
    "WHERE MEMO = 'a;b' AND STATUS = 'delete'";
  await sqlInput.fill(literalSelectSql);
  await expect(adminSql.getByLabel("実行確認語")).toHaveCount(0);
  await expect(rowLimitInput).toBeVisible();
  await rowLimitInput.fill("250");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await expect(adminSql.getByTestId("query-result-summary")).toContainText("取得上限 250 件");
  expect(api.adminExecutePayload).toEqual({
    sql: literalSelectSql,
    row_limit: 250,
    confirmation: "",
    reason: "admin-sql-select",
  });

  await adminSql.getByRole("button", { name: "クリア" }).click();
  await expect(rowLimitInput).toHaveValue("100");
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
    adminSql.getByText(
      "非 SELECT / 複数 statement は ADMIN_EXECUTE を入力すると実行できます。INSERT / UPDATE / DELETE / MERGE / TRUNCATE のみの場合は、成功した SQL をコミットします。"
    )
  ).toBeVisible();
  await expect(adminSql.getByLabel("取得件数上限")).toHaveCount(0);
  for (const label of ["INSERT(単一行)", "INSERT(複数行)", "UPDATE", "DELETE", "MERGE"]) {
    await expect(adminSql.getByRole("button", { name: label, exact: true })).toHaveCount(0);
  }
  await expect(adminSql.getByLabel("実行確認語")).toBeVisible();
  const executeButton = adminSql.getByRole("button", { name: "SQL 実行" });
  await expect(executeButton).toBeDisabled();
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await executeButton.focus();
  await expect(executeButton).toBeFocused();
  await executeButton.press("Enter");

  await expect(adminSql.getByText("コミット済み")).toBeVisible();
  await expect(adminSql.getByText("影響件数 2 件")).toBeVisible();
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

test("管理 SQL の純 DML バッチは部分成功を警告し、成功文だけコミットする", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.goto("/admin-sql");

  const adminSql = page.getByTestId("nl2sql-admin-sql");
  const sqlInput = adminSqlInput(adminSql);
  const partialSql =
    "INSERT INTO INVOICES (CUSTOMER_NAME) VALUES ('青山商事'); " +
    "UPDATE MISSING_TABLE SET STATUS = 'REVIEWED' WHERE ID = 1";
  await sqlInput.fill(partialSql);
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");

  const executeButton = adminSql.getByRole("button", { name: "SQL 実行" });
  await executeButton.focus();
  await executeButton.press("Enter");

  await expect(adminSql.getByText("一部実行", { exact: true })).toBeVisible();
  await expect(adminSql.getByText("コミット済み", { exact: true })).toBeVisible();
  const partialWarning = adminSql.getByRole("status").filter({
    hasText: "部分的に成功しました（1/2 件）。成功した SQL はコミット済みです。",
  });
  await expect(partialWarning).toBeVisible();
  await expect(partialWarning.locator("svg")).toHaveCount(1);
  await expect(adminSql.getByText("成功", { exact: true })).toBeVisible();
  await expect(adminSql.getByText("エラー", { exact: true })).toBeVisible();
  await expect(
    adminSql.getByText("ORA-00942: table or view does not exist", { exact: true }).first()
  ).toBeVisible();
  expect(api.adminExecutePayload).toEqual({
    sql: partialSql,
    row_limit: 100,
    confirmation: "ADMIN_EXECUTE",
    reason: "admin-sql-admin",
  });

  await expectNoHorizontalScroll(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("管理 SQL のコミット後はデータ管理のオブジェクト一覧を再取得する", async ({ page }) => {
  let objectRequestCount = 0;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/nl2sql/db-admin/objects") {
      objectRequestCount += 1;
    }
  });
  await mockNl2SqlApi(page);
  await page.goto("/data-management");
  await expect(page.getByTestId("data-preview-object-list")).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(objectRequestCount).toBeGreaterThan(0);
  const initialObjectRequestCount = objectRequestCount;

  await openSidebarLink(page, "管理 SQL を実行");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await adminSqlInput(adminSql).fill("UPDATE INVOICES SET STATUS = 'REVIEWED' WHERE INVOICE_ID = 1");
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();
  await expect(adminSql.getByText("コミット済み", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "データの管理" }).click();
  await expect(page).toHaveURL(/\/data-management$/);
  await expect.poll(() => objectRequestCount).toBeGreaterThan(initialObjectRequestCount);
});

test("管理 SQL の CREATE TABLE 後はDB構造差分同期を表示し完了後に消す", async ({ page }) => {
  await mockNl2SqlApi(page);
  let schemaRefreshPolls = 0;
  await page.unroute("**/api/nl2sql/db-admin/execute");
  await page.route("**/api/nl2sql/db-admin/execute", async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(payload.sql ?? "CREATE TABLE ADMIN_SQL_TABLE (ID NUMBER)");
    await fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "CREATE",
          status: "success",
          sql,
          row_count: null,
          message: "OK",
          elapsed_ms: 1,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      schema_refresh_job_id: "admin-sql-schema-refresh-done",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/admin-sql-schema-refresh-done", async (route) => {
    schemaRefreshPolls += 1;
    const done = schemaRefreshPolls >= 2;
    await fulfillJson(route, {
      job_id: "admin-sql-schema-refresh-done",
      status: done ? "done" : "running",
      mode: "targeted",
      source: "db_admin_execute",
      target_objects: [
        {
          owner: "APP",
          object_name: "ADMIN_SQL_TABLE",
          object_type: "table",
          expected_state: "present",
        },
      ],
      requires_full_refresh: false,
      phase: done ? "done" : "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: done ? 1 : 0,
      changed_objects: done ? 1 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : 0,
      error_code: "",
    });
  });

  await page.goto("/admin-sql");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await adminSqlInput(adminSql).fill("CREATE TABLE ADMIN_SQL_TABLE (ID NUMBER)");
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();

  await expect(adminSql.getByText("コミット済み", { exact: true })).toBeVisible();
  await expect(
    adminSql.getByTestId("admin-sql-processing-region").getByText("CREATE TABLE ADMIN_SQL_TABLE")
  ).toBeVisible();
  const schemaSync = adminSql.getByTestId("admin-sql-schema-refresh-processing");
  await expect(schemaSync).toContainText("DB 構造の差分を同期しています");
  await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
  await expect(schemaSync).toHaveCount(0);
});

test("管理 SQL の差分同期不整合はDB構造再取得CTAを表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  let manualRefreshStarted = 0;
  let manualRefreshPolls = 0;
  const manualRefreshGate = createRequestGate();
  await page.unroute("**/api/nl2sql/db-admin/execute");
  await page.route("**/api/nl2sql/db-admin/execute", async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(payload.sql ?? "CREATE TABLE ADMIN_SQL_TABLE (ID NUMBER)");
    await fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "CREATE",
          status: "success",
          sql,
          row_count: null,
          message: "OK",
          elapsed_ms: 1,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      schema_refresh_job_id: "admin-sql-schema-refresh-error",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/admin-sql-schema-refresh-error", async (route) => {
    await fulfillJson(route, {
      job_id: "admin-sql-schema-refresh-error",
      status: "error",
      mode: "targeted",
      source: "db_admin_execute",
      target_objects: [
        {
          owner: "APP",
          object_name: "ADMIN_SQL_TABLE",
          object_type: "table",
          expected_state: "present",
        },
      ],
      requires_full_refresh: true,
      phase: "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: 1,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: 0,
      error_code: "schema_refresh_full_required",
    });
  });
  await page.route("**/api/schema/refresh-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    manualRefreshStarted += 1;
    await fulfillJson(route, {
      job_id: "admin-sql-manual-full-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: null,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/admin-sql-manual-full-refresh", async (route) => {
    manualRefreshPolls += 1;
    if (manualRefreshPolls === 1) {
      await fulfillJson(route, {
        job_id: "admin-sql-manual-full-refresh",
        status: "running",
        mode: "full",
        source: "manual",
        target_objects: [],
        requires_full_refresh: false,
        phase: "fetching",
        created_at: "2026-07-22T00:00:01.000Z",
        scanned_objects: 1,
        changed_objects: 0,
        deleted_objects: 0,
        catalog_version: null,
        error_code: "",
      });
      return;
    }
    await manualRefreshGate.promise;
    await fulfillJson(route, {
      job_id: "admin-sql-manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });

  await page.goto("/admin-sql");
  const adminSql = page.getByTestId("nl2sql-admin-sql");
  await adminSqlInput(adminSql).fill("CREATE TABLE ADMIN_SQL_TABLE (ID NUMBER)");
  await adminSql.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await adminSql.getByRole("button", { name: "SQL 実行" }).click();

  await expect(adminSql.getByText("コミット済み", { exact: true })).toBeVisible();
  await expect(adminSql).toContainText("DB 構造の差分同期で不整合を検出しました。");
  const refreshButton = adminSql.getByRole("button", { name: "DB 構造を再取得" });
  await expect(refreshButton).toBeVisible();
  await refreshButton.click();
  await expect.poll(() => manualRefreshStarted).toBe(1);
  await expect(refreshButton).toBeDisabled();
  await expect(refreshButton.locator("svg.animate-spin")).toBeVisible();
  const schemaSync = adminSql.getByTestId("admin-sql-schema-refresh-processing");
  await expect(schemaSync).toContainText("DB 構造を再取得しています");
  await expectNoHorizontalScroll(page);

  manualRefreshGate.release();
  await expect.poll(() => manualRefreshPolls).toBeGreaterThanOrEqual(2);
  await expect(schemaSync).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("SQL ファイル入力は 44px のまま選択とドラッグ＆ドロップで読み込める", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await openSidebarLink(page, "管理 SQL を実行");

  const adminSql = page.getByTestId("nl2sql-admin-sql");
  const sqlInput = adminSqlInput(adminSql);
  const fileInput = adminSql.getByLabel("SQL ファイル読込 (.sql/.txt)");
  const dropzone = adminSql.getByTestId("sql-file-input-dropzone");

  await expect(dropzone).toHaveClass(/\bborder-dashed\b/);
  await expect(dropzone).toHaveAttribute("data-drag-active", "false");
  await expect(dropzone.getByText(".SQL / .TXT", { exact: true })).toBeVisible();
  await expect
    .poll(() =>
      dropzone.evaluate((element) => {
        const duration = getComputedStyle(element).transitionDuration;
        return duration.endsWith("ms")
          ? Number.parseFloat(duration)
          : Number.parseFloat(duration) * 1_000;
      })
    )
    .toBeLessThan(1);
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
  await expect(fileInput).toHaveAttribute("aria-invalid", "true");
  await expect(fileInput).toHaveAttribute("aria-describedby", /-error/);
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
  await expect(fileInput).toHaveAttribute("aria-invalid", "false");
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
  await expect(dropzone.getByText(".SQL / .TXT", { exact: true })).toBeHidden();
  await expectNoHorizontalScroll(page);

  const lightBackground = await dropzone.evaluate(
    (element) => getComputedStyle(element).backgroundColor
  );
  await page.locator("html").evaluate((element) => element.classList.add("dark"));
  await expect
    .poll(() => dropzone.evaluate((element) => getComputedStyle(element).backgroundColor))
    .not.toBe(lightBackground);
});

test("AI 活用の 4 画面はナビ切替で入力を保持し、リセットで消える", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");

  // SQL 生成に検索クエリを入力する。
  const question = nl2sqlQuestionInput(page);
  await question.fill("保持テスト: 未入金の請求金額を確認したい");

  // SPA ナビで SELECT SQL 実行へ移動し、そちらにも入力する。
  await page.getByRole("link", { name: "SELECT SQL を実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  const directSql = directSqlInput(page);
  await directSql.fill("SELECT CUSTOMER_NAME FROM INVOICES");

  // SQL 生成へ戻ると入力が残っている(unmount で破棄されない = keep-alive)。
  await page.getByRole("link", { name: /SQL 生成/ }).first().click();
  await expect(page).toHaveURL(/\/query$/);
  await expect(nl2sqlQuestionInput(page)).toHaveValue("保持テスト: 未入金の請求金額を確認したい");

  // SELECT SQL 実行へ再び移動しても入力が残っている。
  await page.getByRole("link", { name: "SELECT SQL を実行" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await expect(directSqlInput(page)).toHaveValue("SELECT CUSTOMER_NAME FROM INVOICES");

  // クリアは明示ボタンでのみ行われる(ナビ切替では消えない)。
  await page.getByRole("button", { name: "クリア" }).click();
  await expect(directSqlInput(page)).toHaveValue("");

  await page.getByRole("link", { name: /SQL 生成/ }).first().click();
  await page.getByRole("button", { name: "リセット" }).click();
  await expect(nl2sqlQuestionInput(page)).toHaveValue("");
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
  await expect(nl2sqlQuestionInput(page)).toHaveValue("履歴から再実行したい請求金額");
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
  // データ準備 / 改善・運用 は初期折りたたみ(initiallyCollapsed)のため、リンク断言前に展開する。
  // mobile 幅ではサイドバーが icon-only(セクション開閉なし・全リンク表示)になるため、展開ボタンがある時だけ押す。
  const prepareExpand = page.getByRole("button", { name: "データ準備 を展開" });
  if (await prepareExpand.isVisible()) {
    await prepareExpand.click();
    await page.getByRole("button", { name: "改善・運用 を展開" }).click();
  }
  await expect(page.getByRole("link", { name: /ダッシュボード/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /テーブルの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /ビューの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /データの管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /サンプルデータ管理/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /検証用サンプルデータ/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /業務プロファイル/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /用語・同義語/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 生成/ }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /SQL 確認・修復/ })).toHaveCount(0);
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

test("dark theme keeps the SQL workbench text, controls and active states legible", async ({ page }, testInfo) => {
  await mockNl2SqlApi(page);
  await page.goto("/query");
  await page.locator("html").evaluate((element) => element.classList.add("dark"));

  const heading = page.getByRole("heading", { name: "NL2SQL 検索ワークベンチ" });
  const question = nl2sqlQuestionInput(page);
  const selectedEngine = page.getByRole("button", {
    name: /Select AI DBMS_CLOUD_AI profile/u,
  });
  const executeButton = page.getByRole("button", { name: "検索を実行" });
  const activeQueryLink = page
    .getByRole("complementary", { name: "サイドナビゲーション" })
    .getByRole("link", { name: "SQL 生成" });

  await expect(heading).toHaveCSS("color", "rgb(242, 244, 247)");
  await expect(question).toHaveCSS("border-color", "rgb(93, 104, 120)");
  // エンジン選択は primary 塗りではなく「primary 枠線 + 前景色維持」の選択スタイル
  // (1 画面 primary 1 つ: button spec §0.2)。dark でも枠線が primary 色で判別できる。
  await expect(selectedEngine).toHaveAttribute("aria-pressed", "true");
  await expect(selectedEngine).toHaveCSS("border-color", "rgb(105, 173, 255)");
  await expect(selectedEngine).toHaveCSS("color", "rgb(242, 244, 247)");
  await expect(activeQueryLink).toHaveCSS("background-color", "rgb(40, 106, 189)");
  await expect(activeQueryLink).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(executeButton).toBeDisabled();
  await expect(executeButton).toHaveCSS("background-color", "rgb(37, 43, 52)");
  await expect(executeButton).toHaveCSS("color", "rgb(116, 127, 142)");

  await question.focus();
  await expect(question).toHaveCSS("border-color", "rgb(124, 187, 255)");
  await expectNoHorizontalScroll(page);
  await page.screenshot({
    path: testInfo.outputPath(`nl2sql-dark-workbench-${testInfo.project.name}.png`),
    fullPage: true,
  });
});

test("SQL 確認・修復 surface is removed and legacy URL falls back", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/");
  await expect(page.getByRole("link", { name: /SQL 確認・修復/ })).toHaveCount(0);

  await page.goto("/sql-analysis");
  await expect(page).toHaveURL(/\/query$/);
  await expect(page.getByRole("heading", { name: "NL2SQL 検索ワークベンチ" })).toBeVisible();
  await expect(page.locator("#sql-analysis-panel-analysis")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "SQL を分析" })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql to question page reverse-generates a business question with one primary action", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/sql-to-question");
  await expect(page.getByRole("heading", { name: "SQL から質問を生成" })).toBeVisible();
  await expect(page.locator("#sql-to-question-panel-input")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "業務プロファイル" })).toHaveValue("default");
  await expect(page.getByText("請求情報")).toBeVisible();
  await expect(page.getByTestId("sql-to-question-table-count")).toHaveText("参照表 1");

  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  // 用語・同義語は既定 off。本テストは適用時の payload を検証するので明示 ON にする。
  await expect(page.getByLabel("用語・同義語を使う")).not.toBeChecked();
  await page.getByLabel("用語・同義語を使う").check();
  const generateButton = page.getByRole("button", { name: "業務質問を生成" });
  await expect(generateButton).toBeEnabled();
  await expect(page.getByRole("button", { name: "SQL 構造を分析" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deep 逆生成" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "質問を生成", exact: true })).toHaveCount(0);
  await expect(sqlToQuestionInput(page)).toHaveValue("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByLabel("用語・同義語を使う")).toBeChecked();
  await generateButton.click();
  await expect(page.locator("#sql-to-question-panel-result")).toBeVisible();
  await expect(page.getByText("請求金額を条件付きで一覧確認したい")).toBeVisible();

  await expect(page.getByText("SQL 論理構造").first()).toBeVisible();
  // SQL 論理構造は「見出し + 業務者向け説明 + 技術詳細」で併記する。
  const structureList = page.getByTestId("sql-to-question-structure-list");
  await expect(structureList).toBeVisible();
  await expect(structureList).toContainText("SQL 種別");
  await expect(structureList).toContainText("データを取り出すだけの参照 SQL です");
  await expect(structureList.getByText("SELECT").first()).toBeVisible();
  // 処理手順は「SQL の処理手順」ラベルの番号付きリストで、業務文と技術行を併記する。
  const resultPanel = page.locator("#sql-to-question-panel-result");
  await expect(resultPanel.getByText("SQL の処理手順", { exact: true })).toBeVisible();
  await expect(resultPanel.locator("ol > li")).toHaveCount(2);
  await expect(resultPanel).toContainText("請求情報を対象に、一覧の取得を行います。");
  await expect(resultPanel).toContainText("合計を計算します");
  await expect(resultPanel.getByText("INVOICES を参照")).toBeVisible();
  await expect(resultPanel.getByText("集計: SUM")).toBeVisible();
  await expect.poll(() => api.reverseDeepPayload).toEqual({
    sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    profile_id: "default",
    use_glossary: true,
  });
  expect(api.reversePayload).toBeNull();
  expect(api.analyzePayload).toBeNull();
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
  await expect(page.getByRole("button", { name: "業務質問を生成" })).toBeDisabled();
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

  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await page.getByRole("button", { name: "業務質問を生成" }).click();
  await expect(page.getByText("請求金額を条件付きで一覧確認したい")).toBeVisible();

  // 入力を変えると生成済み結果は無効化され、質問セクションは空状態へ戻る。
  await page.getByRole("combobox", { name: "業務プロファイル" }).selectOption("alternate");
  await expect(page.getByText("質問候補は未生成です")).toBeVisible();

  await page.getByRole("button", { name: "業務質問を生成" }).click();
  await expect(page.getByText("請求金額を条件付きで一覧確認したい")).toBeVisible();
  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES WHERE TOTAL_AMOUNT > 0");
  await expect(page.getByRole("button", { name: "業務質問を生成" })).toBeEnabled();
  await expect(page.getByText("SQL 構造は未分析です")).toBeVisible();
  await expect(page.getByText("質問候補は未生成です")).toBeVisible();
});

test("sql to question page keeps controls usable without page overflow at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockNl2SqlApi(page);
  await page.goto("/sql-to-question");

  await expect(page.getByTestId("sql-to-question-steps")).toBeVisible();
  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByLabel("用語・同義語を使う")).toBeVisible();
  const generateButton = page.getByRole("button", { name: "業務質問を生成" });
  await expect(generateButton).toBeVisible();
  const box = await generateButton.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThan(250);
  await expect(page.getByRole("button", { name: "SQL 構造を分析" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deep 逆生成" })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql to question page remains usable at 150 percent zoom", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockNl2SqlApi(page);
  await page.goto("/sql-to-question");
  await page.evaluate(() => {
    document.documentElement.style.zoom = "1.5";
  });

  await sqlToQuestionInput(page).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await expect(page.getByLabel("用語・同義語を使う")).toBeVisible();
  const steps = page.getByTestId("sql-to-question-steps");
  for (const label of ["SQL入力・生成", "SQL論理構造", "質問候補"]) {
    await expect(steps.getByText(label)).toBeVisible();
  }
  const generateButton = page.getByRole("button", { name: "業務質問を生成" });
  await expect(generateButton).toBeVisible();
  await expect(generateButton).toHaveCSS("white-space", "nowrap");
  await expect(page.getByRole("button", { name: "SQL 構造を分析" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deep 逆生成" })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("sql to question page reports generation errors beside the action bar", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/reverse/deep");
  let releaseReverse: (() => void) | undefined;
  const reverseGate = new Promise<void>((resolve) => {
    releaseReverse = resolve;
  });
  await page.route("**/api/nl2sql/reverse/deep", async (route) => {
    await reverseGate;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "SQL reverse unavailable" }),
    });
  });
  await page.goto("/sql-to-question");

  const inputPanel = page.locator("#sql-to-question-panel-input");
  await sqlToQuestionInput(inputPanel).fill("SELECT TOTAL_AMOUNT FROM INVOICES");
  await inputPanel.getByRole("button", { name: "業務質問を生成" }).click();
  await expect(sqlToQuestionInput(inputPanel)).toBeDisabled();
  await expect(inputPanel.getByRole("combobox", { name: "業務プロファイル" })).toBeDisabled();
  await expect(inputPanel.getByLabel("用語・同義語を使う")).toBeDisabled();
  releaseReverse?.();

  await expect(inputPanel.getByRole("alert")).toContainText("接続状態と入力内容を確認して再試行してください。");
  await expect(sqlToQuestionInput(inputPanel)).toBeEnabled();
});

test("feedback management defaults to app feedback and keeps explicit tab deep links", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.goto("/feedback-management");
  await expect(page.getByRole("heading", { name: "フィードバック管理" })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveText([
    "アプリ内フィードバック",
    "Select AI feedback",
    "Select AI ベクトルインデックス",
    "類似検索インデックス",
  ]);
  await expect(page.getByRole("tab", { name: "アプリ内フィードバック" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Select AI feedback" })).toHaveAttribute("aria-selected", "false");
  await expect(page.getByTestId("feedback-history-pane")).toBeVisible();

  await page.evaluate(() => {
    window.history.pushState(null, "", "/feedback-management?tab=entries");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByRole("tab", { name: "Select AI feedback" })).toHaveAttribute("aria-selected", "true");

  await page.evaluate(() => {
    window.history.pushState(null, "", "/feedback-management?tab=unknown");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByRole("tab", { name: "アプリ内フィードバック" })).toHaveAttribute("aria-selected", "true");
});

test("feedback management refresh replaces the active workspace with the shared skeleton", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockNl2SqlApi(page);
  const refreshGate = createRequestGate();
  let holdNextRefresh = false;
  let markRefreshStarted: () => void = () => undefined;
  const refreshStarted = new Promise<void>((resolve) => {
    markRefreshStarted = resolve;
  });

  await page.route(/\/api\/nl2sql\/feedback(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET" && holdNextRefresh) {
      holdNextRefresh = false;
      markRefreshStarted();
      await refreshGate.promise;
    }
    await route.fallback();
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/feedback-management?tab=appFeedback");
  const panel = page.locator("#feedback-management-panel-appFeedback");
  await expect(panel.getByTestId("feedback-history-pane")).toBeVisible();
  await expect(panel.getByTestId("app-feedback-editor-pane")).toBeVisible();
  await expect(panel.getByText("生成 SQL", { exact: true })).toBeVisible();

  holdNextRefresh = true;
  await clickPageHeaderAction(page, "feedback-management-actions", "表示を更新");
  await refreshStarted;

  const skeleton = page.getByTestId("feedback-management-workspace-refresh-skeleton");
  await expect(skeleton).toBeVisible();
  await expect(skeleton).toHaveAttribute("aria-busy", "true");
  await expect(skeleton).toHaveAttribute("data-processing-placement", "workspace");
  await expect(skeleton).toContainText("表示を更新しています");
  await expect(skeleton.getByTestId("feedback-management-workspace-refresh-skeleton-processing")).toHaveAttribute(
    "data-processing-activity-icon",
    "none"
  );
  await expect(skeleton.getByTestId("db-management-skeleton-block")).toHaveCount(3);
  await expect(skeleton.getByTestId("db-management-skeleton-block").first()).toHaveCSS(
    "animation-name",
    "none"
  );
  await expect(panel.getByTestId("feedback-history-pane")).toHaveCount(0);
  await expect(panel.getByTestId("app-feedback-editor-pane")).toHaveCount(0);
  await expect(panel.getByText("生成 SQL", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("履歴から再実行したい請求金額")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(skeleton).toBeVisible();
  await expectNoHorizontalScroll(page);

  refreshGate.release();
  await expect(skeleton).toHaveCount(0);
  await expect(panel.getByTestId("feedback-history-pane")).toBeVisible();
  await expect(panel.getByTestId("app-feedback-editor-pane")).toBeVisible();
});

test("admin good feedback is available as similar history without manual index rebuild", async ({ page }) => {
  await mockNl2SqlApi(page);
  let rebuildRequested = false;
  await page.route("**/api/nl2sql/feedback-index/rebuild", async (route) => {
    rebuildRequested = true;
    await route.fallback();
  });

  await page.goto("/feedback-management?tab=appFeedback");
  await page.getByRole("combobox", { name: "管理者レビュー結果", exact: true }).selectOption("good");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("管理者レビューを保存し、類似検索に公開しました。")).toBeVisible();
  expect(rebuildRequested).toBe(false);

  await page.goto("/query");
  await nl2sqlQuestionInput(page).fill("履歴から再実行したい請求金額");
  const similarHistoryHeader = page.getByRole("button", { name: /参考履歴/ });
  await expect(similarHistoryHeader).toBeVisible();
  await similarHistoryHeader.click();
  await expect(page.getByTestId("nl2sql-similar-history-item")).toContainText(historySql);
  expect(rebuildRequested).toBe(false);
});

test("feedback management page mirrors Select AI feedback operations", async ({ page }, testInfo) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/feedback-management?tab=entries");
  await expect(page.getByRole("heading", { name: "フィードバック管理" })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveText([
    "アプリ内フィードバック",
    "Select AI feedback",
    "Select AI ベクトルインデックス",
    "類似検索インデックス",
  ]);
  await expect(page.getByRole("tab", { name: "Select AI feedback" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Select AI ベクトルインデックス" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "アプリ内フィードバック" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "類似検索インデックス" })).toBeVisible();
  const profileSelect = page.getByLabel("DBMS_CLOUD_AI profile");
  await expect(profileSelect).toHaveValue("NL2SQL_DEFAULT_PROFILE");
  await expect(profileSelect.locator("option")).toHaveCount(1);
  await expect(profileSelect.locator("option", { hasText: "NL2SQL_MANUAL_AGENT_V2_PROFILE" })).toHaveCount(0);
  await expect(page.getByTestId("feedback-management-entry-count")).toContainText("30");
  await expect(page.getByTestId("feedback-management-entries-toolbar")).toBeVisible();
  await expect(page.getByTestId("feedback-management-entries-runtime-info")).toContainText(
    "Runtime: oracle"
  );
  await expect(page.getByTestId("feedback-management-entries-runtime-info")).toContainText(
    "Vector Index:"
  );
  await expect(page.getByText("NL2SQL_DEFAULT_PROFILE_FEEDBACK_VECINDEX").first()).toBeVisible();
  const entriesScrollRegion = page.getByTestId("feedback-management-entries-scroll-region");
  await expect(entriesScrollRegion.getByRole("columnheader")).toHaveText(["CONTENT", "SQL_TEXT"]);
  await expect(page.getByRole("columnheader", { name: "SQL_ID" })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "ATTRIBUTES" })).toHaveCount(0);
  await expect(entriesScrollRegion.locator("tbody tr")).toHaveCount(30);
  await expectInformationTableRowLimit(
    entriesScrollRegion,
    "tbody tr",
    expectedInformationRows(testInfo)
  );
  const pageRefreshButton = page.getByRole("button", { name: "表示を更新", exact: true });
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await expect(pageRefreshButton).toHaveCSS("height", "44px");
  } else {
    await expect(pageRefreshButton).toHaveClass(/\bh-8\b/);
  }
  const entryRefreshButtons = page.getByRole("button", { name: "最新エントリを取得" });
  await expect(entryRefreshButtons).toHaveCount(1);
  await expect(entryRefreshButtons).toHaveCSS("height", "44px");

  const selectedEntryButton = page.getByRole("button", {
    name: "select ai showsql 請求金額を確認したい の feedback を選択",
  });
  await selectedEntryButton.click();
  await expect(selectedEntryButton.locator("xpath=ancestor::tr")).toHaveAttribute("aria-current", "true");
  const selectedEntryDetail = page.getByTestId("feedback-management-entry-detail");
  await expect(selectedEntryDetail).toContainText("select ai showsql 請求金額を確認したい");
  await expect(page.getByTestId("feedback-management-entry-sql")).toContainText(
    "SELECT TOTAL_AMOUNT FROM INVOICES"
  );
  await page.getByTestId("feedback-selected-sql-actions").getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "選択したフィードバックを削除" }).click();
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
  await expect(page.getByTestId("app-feedback-selected-question")).toContainText("履歴から再実行したい請求金額");
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
  const appFeedbackMoreButton = appFeedbackActions.getByRole("button", { name: "その他の操作" });
  await expect(appFeedbackActions).toHaveClass(/\bborder-t\b/);
  await expect(saveAppFeedbackButton).toHaveClass(/\bh-10\b/);
  await expect(saveAppFeedbackButton).toHaveClass(/\bbg-primary\b/);
  await expect(openCandidateLink).toHaveClass(/\bh-10\b/);
  await expect(openCandidateLink).toHaveClass(/\bbg-card\b/);
  await expect(appFeedbackMoreButton).toHaveClass(/\bh-10\b/);
  await expect(appFeedbackMoreButton).toHaveClass(/\bbg-card\b/);
  await expect(appFeedbackActions.getByRole("button", { name: "フィードバックを解除" })).toHaveCount(0);
  await expect(openCandidateLink).toHaveAttribute(
    "href",
    /question-classifier-models\?tab=candidates&history_id=hist-001/
  );
  const appFeedbackEditorPane = page.getByTestId("app-feedback-editor-pane");
  await expect(appFeedbackEditorPane.getByText("利用者からの評価", { exact: true })).toBeVisible();
  await expect(appFeedbackEditorPane.getByText("管理者レビュー結果", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("利用者評価: 良い", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("管理者レビュー: 良い", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("利用者コメント（feedback_content）")).toHaveValue("SQL は期待通りです");
  await page.getByRole("button", { name: "利用者コメントを反映" }).click();
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveValue("SQL は期待通りです");
  const registerSelectAiCheckbox = page.getByRole("checkbox", {
    name: "Select AI feedback に登録する",
  });
  await expect(registerSelectAiCheckbox).not.toBeChecked();
  await page.getByRole("combobox", { name: "管理者レビュー結果", exact: true }).selectOption("good");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("管理者レビューを保存し、類似検索に公開しました。")).toBeVisible();
  expect(api.adminFeedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "good",
    feedback_content: "SQL は期待通りです",
    register_select_ai_feedback: false,
    select_ai_response: historySql,
    select_ai_profile_name: "NL2SQL_DEFAULT_PROFILE",
  });
  expect(api.selectAiFeedbackAddPayload).toBeNull();
  await expect(registerSelectAiCheckbox).not.toBeChecked();
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveValue("SQL は期待通りです");

  await page.getByRole("combobox", { name: "管理者レビュー結果", exact: true }).selectOption("bad");
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveAttribute("required", "");
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveAttribute("aria-required", "true");
  await page.getByLabel("管理者レビューコメント（feedback_content）").fill("");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("「違う」の場合は管理者レビューコメントの入力が必須です。")).toBeVisible();
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toBeFocused();
  expect(api.adminFeedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "good",
    feedback_content: "SQL は期待通りです",
    register_select_ai_feedback: false,
    select_ai_response: historySql,
    select_ai_profile_name: "NL2SQL_DEFAULT_PROFILE",
  });

  await registerSelectAiCheckbox.check();
  await expect(page.getByLabel("Select AI response SQL")).toHaveValue(historySql);
  await page.getByLabel("管理者レビューコメント（feedback_content）").fill("Select AI 登録用の管理者確認メモ");
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("管理者レビューを保存し、Select AI feedback に登録しました。")).toBeVisible();
  expect(api.adminFeedbackPayload).toEqual({
    history_id: "hist-001",
    rating: "bad",
    feedback_content: "Select AI 登録用の管理者確認メモ",
    register_select_ai_feedback: true,
    select_ai_response: historySql,
    select_ai_profile_name: "NL2SQL_DEFAULT_PROFILE",
  });
  await expect(page.getByRole("combobox", { name: "管理者レビュー結果", exact: true })).toHaveValue("bad");
  await expect(registerSelectAiCheckbox).toBeChecked();
  await expect(page.getByLabel("Select AI response SQL")).toHaveValue(historySql);
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveValue("Select AI 登録用の管理者確認メモ");
  await appFeedbackMoreButton.click();
  const clearAppFeedbackMenuItem = page.getByRole("menuitem", { name: "フィードバックを解除" });
  await expect(clearAppFeedbackMenuItem).toHaveAttribute("data-form-action-tone", "danger");
  await clearAppFeedbackMenuItem.click();
  const clearAppFeedbackDialog = page.getByRole("alertdialog", {
    name: "フィードバックを解除しますか",
  });
  await expect(clearAppFeedbackDialog).toBeVisible();
  await expectNeutralDangerConfirmationSurface(clearAppFeedbackDialog);
  await expect(clearAppFeedbackDialog.locator(".bg-danger-bg")).toHaveCount(0);
  await clearAppFeedbackDialog.getByRole("button", { name: "フィードバックを解除" }).click();
  await expect(page.getByText("フィードバックを解除しました。")).toBeVisible();
  await expect(registerSelectAiCheckbox).not.toBeChecked();
  await expect(page.getByLabel("Select AI response SQL")).toHaveCount(0);
  await expect(page.getByLabel("管理者レビューコメント（feedback_content）")).toHaveValue("");
  const feedbackFilterOptions = page.getByLabel("利用者評価フィルター").locator("option");
  await expect(feedbackFilterOptions).toHaveText(["すべて", "良い", "違う", "未評価"]);
  await expect(feedbackFilterOptions.filter({ hasText: "要確認" })).toHaveCount(0);
  await page.getByLabel("利用者評価フィルター").selectOption("good");
  await expect(
    page.getByTestId("feedback-history-row").filter({ hasText: "履歴から再実行したい請求金額" }).filter({ hasText: "良い" })
  ).toBeVisible();
  await page.getByLabel("利用者評価フィルター").selectOption("bad");
  await expect(
    page.getByTestId("feedback-history-row").filter({ hasText: "別プロファイルの請求確認" }).filter({ hasText: "違う" })
  ).toBeVisible();
  await expect(page.getByTestId("app-feedback-selected-question")).toContainText("別プロファイルの請求確認");
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
  await expect(page.getByText("SQL 実行画面に出す類似履歴候補の絞り込み条件を管理します。")).toBeVisible();
  await expect(page.getByLabel("Oracle 26ai DDL plan")).toHaveCount(0);
  await expect(page.getByText("CREATE TABLE NL2SQL_FEEDBACK_VECTORS", { exact: false })).toHaveCount(0);
  await expect(page.getByText("CREATE VECTOR INDEX NL2SQL_FEEDBACK_VEC_IDX", { exact: false })).toHaveCount(0);
  await expect(page.getByText("現在の状態", { exact: true })).toHaveCount(0);
  await expect(page.getByText("索引候補", { exact: true })).toHaveCount(0);
  await expect(page.getByText("対象外履歴", { exact: true })).toHaveCount(0);
  await expect(page.getByText("更新待ち", { exact: true })).toHaveCount(0);
  await expect(page.getByText("技術詳細", { exact: true })).toHaveCount(0);
  const similarityConfigSave = page.getByRole("button", { name: "設定保存" });
  const similarityIndexActions = page.getByTestId("feedback-similarity-index-actions");
  await expect(similarityConfigSave).toHaveClass(/\bh-10\b/);
  await expect(similarityConfigSave).toHaveClass(/\bbg-primary\b/);
  await expect(similarityIndexActions).toHaveClass(/\bborder-t\b/);
  await expect(similarityIndexActions.getByRole("button", { name: "インデックスを更新" })).toHaveCount(0);
  await expect(similarityIndexActions.getByRole("button", { name: "インデックスを削除" })).toHaveCount(0);
  await expect(similarityIndexActions.getByRole("button", { name: "その他の操作" })).toHaveCount(0);
  await page.getByLabel("最低スコア", { exact: true }).fill("0.85");
  await page.getByLabel("最大候補数", { exact: true }).fill("4");
  await similarityConfigSave.click();
  await expect(page.getByText("Feedback 類似検索設定を保存しました。")).toBeVisible();
  expect(api.feedbackConfigPayload).toEqual({
    similarity_threshold: 0.85,
    match_limit: 4,
  });

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("feedback management keeps long query text contained in app feedback and similarity settings", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop project covers split-pane resizing and mobile stacking");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNl2SqlApi(page);
  await page.route(/\/api\/nl2sql\/feedback(?:\?.*)?$/, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return fulfillJson(route, {
      items: [
        {
          ...longHistoryItem,
          training_status: "pending",
          training_example_id: "",
        },
      ],
      total: 1,
      next_cursor: "",
    });
  });

  await page.goto("/feedback-management?tab=appFeedback");
  const historyPane = page.getByTestId("feedback-history-pane");
  const editorPane = page.getByTestId("app-feedback-editor-pane");
  const row = page.getByTestId("feedback-history-row").first();
  const rowQuestion = row.getByTestId("feedback-history-question");
  const selectedQuestion = page.getByTestId("app-feedback-selected-question");
  await expect(rowQuestion).toContainText("対象テーブル");
  await expect(selectedQuestion).toContainText("対象テーブル");
  await expectQuestionClamp(rowQuestion, longHistoryItem.question, 1);
  await expectQuestionClamp(selectedQuestion, longHistoryItem.question, 3);
  await expectHorizontallyContained(row, historyPane);
  await expectHorizontallyContained(rowQuestion, row);
  await expectHorizontallyContained(selectedQuestion, editorPane);
  const historySelect = page.getByRole("combobox", { name: "対象履歴" });
  await historySelect.click();
  const listbox = page.getByRole("listbox", { name: "対象履歴" });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option")).toContainText("対象テーブル");
  await expectHorizontallyContained(listbox, editorPane);
  await page.keyboard.press("Escape");
  const questionExpandButton = page.getByRole("button", { name: "全文表示" });
  await expect(questionExpandButton.locator('svg[data-state]')).toHaveAttribute("data-state", "collapsed");
  await questionExpandButton.click();
  const questionCollapseButton = page.getByRole("button", { name: "閉じる" });
  await expect(questionCollapseButton).toBeVisible();
  await expect(questionCollapseButton.locator('svg[data-state]')).toHaveAttribute("data-state", "expanded");
  await expectNoHorizontalScroll(page);

  await page.getByRole("tab", { name: "類似検索インデックス" }).click();
  const similarityPanel = page.locator("#feedback-management-panel-similarityIndex");
  await expect(similarityPanel.getByLabel("最低スコア", { exact: true })).toBeVisible();
  await expect(similarityPanel.getByLabel("最大候補数", { exact: true })).toBeVisible();
  await expect(similarityPanel.getByText("索引候補", { exact: true })).toHaveCount(0);
  await expect(similarityPanel.getByTestId("feedback-vector-entry")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(similarityPanel.getByLabel("最低スコア", { exact: true })).toBeVisible();
  await expect(similarityPanel.getByLabel("最大候補数", { exact: true })).toBeVisible();
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

  await page.goto("/feedback-management?tab=entries");

  const warning = page.getByText(missingTableWarning, { exact: true });
  await expect(warning).toBeVisible();
  await expect(warning).not.toContainText(physicalTableName);
  await expect(page.getByText(physicalTableName, { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("feedback-management-entry-detail-empty")).toBeVisible();
  await expect(page.getByTestId("feedback-management-entry-sql")).toHaveCount(0);
  await expect(page.getByTestId("feedback-management-entry-detail").locator(".bg-code")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("Select AI feedback reserves the master-detail workspace during initial loading", async ({ page }) => {
  await mockNl2SqlApi(page);
  let releaseConfig: (() => void) | undefined;
  const configGate = new Promise<void>((resolve) => {
    releaseConfig = resolve;
  });
  await page.route("**/api/nl2sql/feedback-config", async (route) => {
    await configGate;
    await fulfillJson(route, {
      similarity_threshold: 0,
      match_limit: 3,
    });
  });

  await page.goto("/feedback-management?tab=entries");

  const skeleton = page.getByTestId("feedback-management-workspace-refresh-skeleton");
  await expect(skeleton).toBeVisible();
  await expect(skeleton).toHaveAttribute("aria-busy", "true");
  releaseConfig?.();
  await expect(page.getByTestId("feedback-management-entries-table")).toBeVisible();
  await expect(skeleton).toHaveCount(0);
});

test("Select AI feedback stacks its workspace and keeps controls usable at 375px", async ({ page }, testInfo) => {
  await page.setViewportSize(
    testInfo.project.name === "desktop"
      ? { width: 2048, height: 1000 }
      : { width: 375, height: 900 }
  );
  await mockNl2SqlApi(page);
  await page.goto("/feedback-management?tab=entries");

  const workspace = page.getByTestId("feedback-management-entries-workspace");
  const toolbar = page.getByTestId("feedback-management-entries-toolbar");
  const refresh = toolbar.getByRole("button", { name: "最新エントリを取得" });
  const pane = page.getByTestId("fixed-split-pane-feedback-management-entries-split");

  if (testInfo.project.name === "desktop") {
    const divider = page.getByTestId("fixed-split-pane-feedback-management-entries-split-divider");
    await expect(pane).toHaveAttribute("data-split-layout", "split");
    await expect(divider).toBeVisible();
    await expect(divider).toHaveAttribute("role", "separator");
    await divider.press("Home");
    await expect(pane).toHaveAttribute("data-split-ratio", "equal");
    const equalFraction = Number(await pane.getAttribute("data-split-left-fraction"));
    await divider.press("ArrowRight");
    await expect
      .poll(async () => Number(await pane.getAttribute("data-split-left-fraction")))
      .toBeGreaterThan(equalFraction);
    await page.setViewportSize({ width: 375, height: 900 });
  }

  await expectSplitPaneStacked(pane);
  await expect(refresh).toHaveCSS("height", "44px");
  await expectHorizontallyContained(toolbar, workspace);
  await expect(page.getByTestId("feedback-management-entry-detail")).toBeVisible();
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

  await page.goto("/feedback-management?tab=entries");

  await expect(page.getByRole("alert")).toContainText("Feedback 類似検索設定を取得できません。");
  await expect(page.getByText("Select AI feedback はありません")).toBeVisible();
  await expect(page.getByTestId("feedback-management-entry-detail-empty")).toBeVisible();
  await expect(page.getByTestId("feedback-management-entry-sql")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "再読込", exact: true })).toBeVisible();
  const reloadButton = page.getByRole("button", { name: "表示を更新", exact: true });
  if ((page.viewportSize()?.width ?? 0) < 1024) {
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
    mobileActionBar.getByRole("button", { name: "その他の操作" }),
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
  await mobileActionBar.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "フィードバックを解除" })).toBeVisible();
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
    { path: "/sql-to-question", splitIds: ["sql-to-question-input"] },
    { path: "/feedback-management?tab=entries", splitIds: ["feedback-management-entries-split"] },
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
      await expect(pane).toHaveAttribute("data-split-layout", "split", {
        timeout: SPLIT_PANE_RENDER_TIMEOUT_MS,
      });
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
  await expect(trainingWorkspace.getByTestId("qcm-training-file-field-input")).toHaveAttribute(
    "accept",
    ".xlsx"
  );
  await expect(trainingWorkspace.getByText(".XLSX", { exact: true })).toBeVisible();

  await dropFiles(page, trainingWorkspace.getByTestId("qcm-training-file-field-dropzone"), [
    {
      name: "training_data.csv",
      type: "text/csv",
      content: "CATEGORY,TEXT\n監査,監査ログを確認したい\n",
    },
  ]);
  await expect(
    trainingWorkspace.getByText(
      "このファイル形式は使用できません。.XLSX ファイルを選択してください。"
    )
  ).toBeVisible();
  await dropFiles(page, trainingWorkspace.getByTestId("qcm-training-file-field-dropzone"), [
    {
      name: "training_data.xlsx",
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      content: "mock xlsx",
    },
  ]);
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

test("question classifier model management page trains classifier and finds learning candidates", async ({ page }, testInfo) => {
  const api = await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/classifier/predict");
  await page.route("**/api/nl2sql/classifier/predict", (route) =>
    fulfillJson(route, {
      recommendation_source: "classifier",
      classifier_version: "classifier-002",
      predicted_category: "既定プロファイル",
      confidence: 0.92,
      candidates: Array.from({ length: 30 }, (_, index) => ({
        category: index === 0 ? "既定プロファイル" : `候補カテゴリ ${index + 1}`,
        score: index === 0 ? 0.92 : Math.max(0.1, 0.9 - index * 0.02),
        profile_id: index === 0 ? "default" : `profile-${index + 1}`,
        profile_name: index === 0 ? "既定プロファイル" : `候補プロファイル ${index + 1}`,
      })),
      warnings: [],
    })
  );

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
  // PageHeaderStatusBadge は可視ラベル + sr-only 通知文の 2 重構造(意図的な a11y 設計)。
  await expect(classifierStatus.locator('[aria-hidden="true"]')).toHaveText("学習済み");
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
  const testCandidates = page.getByTestId("qcm-test-candidates-scroll-region");
  await expect(testCandidates.locator("tbody tr")).toHaveCount(30);
  await expectInformationTableRowLimit(
    testCandidates,
    "tbody tr",
    expectedInformationRows(testInfo)
  );

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
  await expect(classifierStatus.locator('[aria-hidden="true"]')).toHaveText("学習済み・再学習待ち");
  await expect(page.getByRole("button", { name: "推薦・書き換え" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "類似履歴検索" })).toHaveCount(0);

  await page.getByRole("tab", { name: "訓練データ" }).click();
  await page.getByPlaceholder("CATEGORY / TEXT / SOURCE で絞り込み").fill("履歴から再実行");
  await expect(page.getByText("SQL feedback", { exact: true })).toBeVisible();
  await expect(page.getByText("feedback:hist-001", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "モデル学習" }).click();
  await page.getByRole("button", { name: "Classifier 学習" }).click();
  await expect(page.getByText("LogisticRegression classifier を学習しました。")).toBeVisible();
  await expect(classifierStatus.locator('[aria-hidden="true"]')).toHaveText("学習済み");

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

  const firstTrainingRow = page
    .getByTestId("qcm-training-data-table")
    .locator("tbody tr")
    .filter({ hasText: "請求金額が大きい取引先を見たい" });
  await expect(firstTrainingRow).toBeVisible();
  await expect(firstTrainingRow.getByRole("button", { name: "編集", exact: true })).toHaveCount(0);
  await expect(firstTrainingRow.getByRole("button", { name: "削除", exact: true })).toHaveCount(0);
  await firstTrainingRow.getByTestId("qcm-training-row-actions-example-001-trigger").click();
  const editTrainingMenuItem = page.getByRole("menuitem", { name: "編集", exact: true });
  const deleteTrainingMenuItem = page.getByRole("menuitem", { name: "削除", exact: true });
  await expect(editTrainingMenuItem).toBeVisible();
  await expect(deleteTrainingMenuItem).toBeVisible();
  await expect(deleteTrainingMenuItem).toHaveAttribute("data-entity-action-tone", "danger");
  await editTrainingMenuItem.click();
  await expect(firstTrainingRow.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await firstTrainingRow.getByRole("button", { name: "キャンセル", exact: true }).click();
  await clickRowAction(page, "qcm-training-row-actions-example-001", "削除");
  const deleteTrainingDialog = page.getByRole("alertdialog", { name: "訓練データを削除しますか" });
  await expect(deleteTrainingDialog).toBeVisible();
  await deleteTrainingDialog.getByRole("button", { name: "削除", exact: true }).click();
  await expect(page.getByText("訓練データを削除しました。モデルは再学習待ちです。")).toBeVisible();
  expect(api.classifierTrainingDeleteId).toBe("example-001");
  await expect(page.getByText("請求金額が大きい取引先を見たい")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("learning candidates keep long query text clear of selection controls", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop project covers desktop and 375px geometry");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNl2SqlApi(page);
  await page.route("**/api/nl2sql/classifier/training-candidates*", (route) =>
    fulfillJson(route, {
      items: [
        {
          history_id: longHistoryItem.id,
          question: longHistoryItem.question,
          profile_id: "default",
          profile_name: "既定プロファイル",
          profile_category: "既定プロファイル",
          feedback_rating: "good",
          feedback_comment: longHistoryItem.feedback_comment,
          created_at: longHistoryItem.created_at,
          status: "pending",
          training_example_id: "",
          conflict_profile_ids: [],
        },
      ],
      total: 1,
      next_cursor: "",
      pending_count: 1,
      added_count: 0,
      attention_count: 0,
    })
  );

  await page.goto("/question-classifier-models?tab=candidates");
  const candidate = page.getByTestId("qcm-training-candidate").first();
  const question = candidate.getByTestId("qcm-candidate-question");
  const checkbox = candidate.getByRole("checkbox");
  const profile = candidate.getByRole("combobox", { name: "追加する Profile" });
  const candidateBulkBar = page.getByTestId("qcm-candidate-bulk-actions");
  const candidateBulkActions = page.getByTestId("qcm-candidate-page-selection-actions");
  const addSelectedButton = page.getByRole("button", { name: "選択した 0 件を追加" });
  const expectCandidateBulkLayout = async (expectTrailingAction: boolean) => {
    const [barBox, actionsBox, addButtonBox] = await Promise.all([
      candidateBulkBar.boundingBox(),
      candidateBulkActions.boundingBox(),
      addSelectedButton.boundingBox(),
    ]);
    expect(barBox).not.toBeNull();
    expect(actionsBox).not.toBeNull();
    expect(addButtonBox).not.toBeNull();
    expect(Math.abs(actionsBox!.x - (barBox!.x + 12))).toBeLessThanOrEqual(1);
    if (expectTrailingAction) {
      expect(
        Math.abs(addButtonBox!.x + addButtonBox!.width - (barBox!.x + barBox!.width - 12))
      ).toBeLessThanOrEqual(1);
    }
  };
  await expect(question).toContainText("対象テーブル");
  await expectQuestionClamp(question, longHistoryItem.question, 1);
  await expect(checkbox).toBeVisible();
  await expect(profile).toBeVisible();
  await expectHorizontallyContained(question, candidate);
  await expectHorizontallyContained(profile, candidate);
  await expectCandidateBulkLayout(true);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(question).toBeVisible();
  await expect(checkbox).toBeVisible();
  await expect(profile).toBeVisible();
  await expectHorizontallyContained(question, candidate);
  await expectHorizontallyContained(profile, candidate);
  await expectCandidateBulkLayout(false);
  await expectNoHorizontalScroll(page);
});

test("learning candidates use the shared responsive list, filters, paging, and recovery states", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop context covers both 1440px and 375px geometry");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mockNl2SqlApi(page);

  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) =>
    fulfillJson(route, [
      { ...profiles[0], category: "請求カテゴリ" },
      {
        ...profiles[0],
        id: "payment",
        name: "入金管理",
        category: "入金カテゴリ",
      },
    ])
  );
  // 候補の Profile picker は増分 API(/profiles/search)から一覧を取るため、こちらも同じ内容で上書きする。
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        { ...profiles[0], category: "請求カテゴリ" },
        { ...profiles[0], id: "payment", name: "入金管理", category: "入金カテゴリ" },
      ].map((profile, index) => ({
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
        etag: `etag-search-${index}`,
        updated_at: "2026-06-21T10:00:00.000Z",
      })),
      next_cursor: null,
      total: 2,
      change_token: 1,
    })
  );

  const initialCandidate = {
    history_id: "hist-001",
    question: "履歴から再実行したい請求金額",
    profile_id: "default",
    profile_name: "既定プロファイル",
    profile_category: "請求カテゴリ",
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
  let holdNoResultsRequest = false;
  let failRecoveryRequest = true;
  const candidateRequests: URL[] = [];
  const noResultsGate = createRequestGate();

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
      if (holdNoResultsRequest) {
        await noResultsGate.promise;
      }
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
  const candidateBulkBar = page.getByTestId("qcm-candidate-bulk-actions");
  const candidateBulkActions = page.getByTestId("qcm-candidate-page-selection-actions");
  const addSelectedButton = page.getByRole("button", { name: "選択した 0 件を追加" });
  const [candidateBarBox, candidateActionsBox, addSelectedBox] = await Promise.all([
    candidateBulkBar.boundingBox(),
    candidateBulkActions.boundingBox(),
    addSelectedButton.boundingBox(),
  ]);
  expect(candidateBarBox).not.toBeNull();
  expect(candidateActionsBox).not.toBeNull();
  expect(addSelectedBox).not.toBeNull();
  expect(Math.abs(candidateActionsBox!.x - (candidateBarBox!.x + 12))).toBeLessThanOrEqual(1);
  if ((page.viewportSize()?.width ?? 0) >= 640) {
    expect(
      Math.abs(
        addSelectedBox!.x + addSelectedBox!.width - (candidateBarBox!.x + candidateBarBox!.width - 12)
      )
    ).toBeLessThanOrEqual(1);
  }
  await expect(candidateBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeEnabled();
  await expect(candidateBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeDisabled();
  await candidateBulkActions.getByRole("button", { name: "表示中をすべて選択" }).click();
  await expect(page.getByRole("button", { name: "選択した 1 件を追加" })).toBeEnabled();
  await expect(candidateBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeDisabled();
  await expect(candidateBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeEnabled();
  await candidateBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" }).click();
  await expect(page.getByRole("button", { name: "選択した 0 件を追加" })).toBeDisabled();

  const candidateProfile = firstCandidate.getByRole("combobox", { name: "追加する Profile" });
  await expect(candidateProfile).toHaveText("既定プロファイル");
  await expect(candidateProfile).not.toContainText("default");
  await candidateProfile.focus();
  await page.keyboard.press("ArrowDown");
  await expect(candidateProfile).toHaveAttribute("aria-expanded", "true");
  const profileListbox = firstCandidate.getByRole("listbox");
  await expect(profileListbox.getByText("default", { exact: true })).toHaveCount(0);
  await expect(profileListbox.getByText("payment", { exact: true })).toHaveCount(0);
  await expect(profileListbox.getByText("請求カテゴリ", { exact: true })).toBeVisible();
  await expect(profileListbox.getByText("入金カテゴリ", { exact: true })).toBeVisible();
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
  holdNoResultsRequest = true;
  await search.fill("一致なし");
  await search.press("Enter");
  const applyFiltersButton = page.getByRole("button", { name: "絞り込み" });
  const filteredSkeleton = page.getByTestId("qcm-candidates-list-skeleton");
  await expect(filteredSkeleton).toBeVisible();
  await expect(applyFiltersButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(filteredSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  noResultsGate.release();
  await expect(page.getByText("条件に一致する学習候補がありません", { exact: true })).toBeVisible();
  holdNoResultsRequest = false;
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
  await expect(classifierStatus.locator('[aria-hidden="true"]')).toHaveText("未学習");
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
  await expect(page.getByTestId("glossary-rules-panel-heading-file-input")).toHaveAttribute(
    "accept",
    ".xlsx"
  );
  await expect(page.getByText(".XLSX", { exact: true })).toBeVisible();

  await dropFiles(page, page.getByTestId("glossary-rules-panel-heading-file-dropzone"), [
    {
      name: "terms.csv",
      type: "text/csv",
      content: "TERM,DEFINITION\n粗利,INVOICES.PROFIT\n",
    },
  ]);
  await expect(page.getByText(".XLSX ファイルを選択してください")).toBeVisible();
  await dropFiles(page, page.getByTestId("glossary-rules-panel-heading-file-dropzone"), [
    {
      name: "terms.xlsx",
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      content: "mock",
    },
  ]);
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
    sql: 'SELECT * FROM "INVOICES" FETCH FIRST 10 ROWS ONLY',
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
  const invoicesSelectButton = page.getByRole("button", { name: "APP.INVOICES を選択" });
  const viewSelectButton = page.getByRole("button", { name: "APP.V_EMP_DEPT を選択" });
  const showPreviewButton = resultsPanel.getByRole("button", { name: "データを表示", exact: true });
  const resultsActions = resultsPanel.getByTestId("data-preview-results-actions");
  await invoicesSelectButton.click();
  await showPreviewButton.click();
  const dataSkeleton = page.getByTestId("data-preview-results-detail-skeleton");
  await expect(dataSkeleton).toBeVisible();
  await expect(showPreviewButton).toBeDisabled();
  await expect(invoicesSelectButton).toBeEnabled();
  await expect(viewSelectButton).toBeEnabled();
  await expect(resultsActions.getByRole("button", { name: "XLSX ダウンロード" })).toBeDisabled();
  await expect(resultsActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await resultsActions.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "APP.INVOICES のデータを空にする" })).toBeDisabled();
  await resultsActions.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByTestId("data-preview-object-list").getByRole("button", { name: /^操作: / })).toHaveCount(0);
  await expect(resultsPanel.getByRole("button", { name: "APP.V_EMP_DEPT のデータを空にする" })).toHaveCount(0);
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

  firstPreviewGate.release();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toBeVisible();

  await showPreviewButton.click();
  await expect(resultsPanel.getByRole("cell", { name: "スケルトン確認顧客" })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  await showPreviewButton.click();
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
  await page.getByRole("button", { name: "APP.INVOICES を表示" }).click();
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
  const commentTargetToolbar = page.getByTestId("comment-management-target-toolbar");
  await expect(commentTargetToolbar).toBeVisible();
  await expect(page.getByTestId("comment-management-target-footer")).toContainText("選択 0 件");
  await commentTargetToolbar.getByRole("searchbox", { name: "検索" }).fill("INVO");
  await expect(page.getByRole("checkbox", { name: /INVOICES/ })).toBeVisible();
  const commentBulkActions = page.getByTestId("comment-management-target-selection-actions");
  const commentTargetList = page.getByTestId("db-admin-object-list");
  await expect
    .poll(async () => {
      const [actionsBox, listBox] = await Promise.all([
        commentBulkActions.boundingBox(),
        commentTargetList.boundingBox(),
      ]);
      if (!actionsBox || !listBox) return Number.POSITIVE_INFINITY;
      return Math.abs(actionsBox.x - listBox.x);
    })
    .toBeLessThanOrEqual(1);
  await expect(commentBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeEnabled();
  await expect(commentBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeDisabled();
  await commentBulkActions.getByRole("button", { name: "表示中をすべて選択" }).click();
  await expect(page.getByTestId("comment-management-target-footer")).toContainText("選択 1 件");
  await expect(commentBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeDisabled();
  await expect(commentBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeEnabled();
  await commentBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" }).click();
  await expect(page.getByTestId("comment-management-target-footer")).toContainText("選択 0 件");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await expect(page.getByTestId("comment-management-target-footer")).toContainText("選択 1 件");
  const commentFetchButton = page.getByRole("button", { name: "情報を取得" });
  await expectButtonBelowInput(page.getByTestId("comment-management-target-footer"), commentFetchButton);
  await commentFetchButton.click();
  const commentInputSkeleton = page.getByTestId("comment-management-input-detail-skeleton");
  await expect(commentInputSkeleton).toBeVisible();
  await expect(commentFetchButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(commentInputSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expect(page.locator("#comment-management-panel-input").getByText("対象情報が未取得です")).toHaveCount(0);
  commentDetailGate.release();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.INVOICES/);
  await expect(page.getByRole("region", { name: "通知" })).toContainText("対象情報を取得しました。1 件を確認できます。");

  const commentGenerateGate = createRequestGate();
  await page.route("**/api/nl2sql/metadata-samples", async (route) => {
    await commentGenerateGate.promise;
    return fulfillJson(route, {
      sample_text: "OBJECT: APP.INVOICES\nCUSTOMER_NAME: 青山商事",
      sample_count: 1,
      runtime: "oracle",
      warnings: [],
    });
  });
  const commentGenerateButton = page.getByRole("button", { name: "SQL 生成" });
  await expectButtonBelowInput(page.getByLabel("追加入力"), commentGenerateButton);
  await commentGenerateButton.click();
  const commentExecuteSkeleton = page.getByTestId("comment-management-execute-result-detail-skeleton");
  await expect(commentExecuteSkeleton).toBeVisible();
  await expect(commentGenerateButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(commentExecuteSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expect(page.locator("#comment-management-panel-execute").getByText("生成済み SQL がありません")).toHaveCount(0);
  commentGenerateGate.release();
  await expect(page.locator("#comment-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/COMMENT ON COLUMN/);
  await expect(page.getByRole("region", { name: "通知" })).toContainText("SQL 生成が完了しました。");

  await page.goto("/annotation-management");
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  const annotationFetchButton = page.getByRole("button", { name: "情報を取得" });
  await expectButtonBelowInput(page.getByTestId("annotation-management-target-footer"), annotationFetchButton);
  await annotationFetchButton.click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.INVOICES/);
  await expect(page.getByRole("region", { name: "通知" })).toContainText("対象情報を取得しました。1 件を確認できます。");
  const annotationGenerateGate = createRequestGate();
  await page.route("**/api/nl2sql/annotations/generate-sql", async (route) => {
    await annotationGenerateGate.promise;
    return fulfillJson(route, {
      sql: "ALTER TABLE \"APP\".\"INVOICES\" MODIFY (\"TOTAL_AMOUNT\" ANNOTATIONS (ADD IF NOT EXISTS UI_Display '税込請求金額'));",
      source: "deterministic",
      warnings: [],
      timing,
    });
  });
  const annotationGenerateButton = page.getByRole("button", { name: "SQL 生成" });
  await expectButtonBelowInput(page.getByLabel("追加入力"), annotationGenerateButton);
  await annotationGenerateButton.click();
  const annotationExecuteSkeleton = page.getByTestId("annotation-management-execute-result-detail-skeleton");
  await expect(annotationExecuteSkeleton).toBeVisible();
  await expect(annotationGenerateButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(annotationExecuteSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  annotationGenerateGate.release();
  await expect(page.locator("#annotation-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/ALTER TABLE/);
  await expect(page.getByRole("region", { name: "通知" })).toContainText("SQL 生成が完了しました。");

  await page.goto("/view-management");
  await expect(page.getByTestId("view-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "APP.V_EMP_DEPT を表示" }).click();
  await expect(page.getByRole("button", { name: /XLSX ダウンロード/ })).toBeVisible();
  const viewColumnsDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /XLSX ダウンロード/ }).click();
  const viewColumnsDownload = await viewColumnsDownloadPromise;
  expect(viewColumnsDownload.suggestedFilename()).toBe("app_v_emp_dept_columns.xlsx");
  const autoDdlResponse = page.waitForResponse((response) =>
    response.url().includes("/api/nl2sql/db-admin/views/V_EMP_DEPT?include_ddl=1") && response.ok()
  );
  await clickPageHeaderAction(page, "view-management-actions", "JOIN/WHERE 条件抽出");
  await autoDdlResponse;
  await expect(page.getByTestId("view-join-where-actions")).toContainText("DDL 取得済み");
  let joinWhereGate = createRequestGate();
  await page.route("**/api/nl2sql/db-admin/extract-join-where", async (route) => {
    await joinWhereGate.promise;
    return fulfillJson(route, {
      join_text:
        "JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\nON: EMPLOYEE(e).DEPARTMENT_ID = DEPARTMENT(d).DEPARTMENT_ID",
      where_text: "EMPLOYEE(e).STATUS = 'A'",
      source: "deterministic",
      warnings: [],
      prompt_profile: "sql_structure",
      structure_markdown:
        "## SQL構造分析\n\n### JOIN句\n- JOIN: EMPLOYEE(e) JOIN DEPARTMENT(d)\n\n### WHERE句\n- EMPLOYEE(e).STATUS = 'A'",
    });
  });
  const joinWhereButton = page.getByRole("button", { name: "AI で抽出" });
  await expectButtonBelowInput(page.getByTestId("view-join-where-advanced-settings"), joinWhereButton);
  await joinWhereButton.click();
  const joinWhereSkeleton = page.getByTestId("view-join-where-result-detail-skeleton");
  await expect(joinWhereSkeleton).toBeVisible();
  await expect(joinWhereButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(joinWhereSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveCount(0);
  joinWhereGate.release();
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE/);
  await page.setViewportSize({ width: 375, height: 900 });
  joinWhereGate = createRequestGate();
  await joinWhereButton.click();
  const mobileJoinWhereSkeleton = page.getByTestId("view-join-where-result-detail-skeleton");
  await expect(mobileJoinWhereSkeleton).toBeVisible();
  await expect(joinWhereButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(mobileJoinWhereSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expectTopToBottomOrder(page.getByTestId("view-join-where-actions"), mobileJoinWhereSkeleton);
  await expectNoHorizontalScroll(page);
  joinWhereGate.release();
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE/);
  await expectNoHorizontalScroll(page);
});

test("metadata management target lists load more tables and views before SQL generation", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  const columns = [
    {
      column_name: "ID",
      logical_name: "識別子",
      data_type: "NUMBER",
      nullable: false,
      comment: "識別子",
      sample_values: ["1"],
    },
  ];
  const baseItems = Array.from({ length: 100 }, (_, index) => {
    const suffix = String(index + 1).padStart(3, "0");
    return {
      name: `META_TABLE_${suffix}`,
      owner: "APP",
      object_type: "table",
      row_count: 1,
      comment: `メタデータ確認 ${suffix}`,
    };
  });
  const routePagedObjects = async (secondItem: {
    name: string;
    owner: string;
    object_type: "table" | "view";
    row_count: number | null;
    comment: string;
  }) => {
    await page.unroute("**/api/nl2sql/db-admin/objects?*");
    await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
      const url = new URL(route.request().url());
      const objectType = url.searchParams.get("type") ?? "all";
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      const cursor = url.searchParams.get("cursor") ?? "";
      const allItems = [...baseItems, secondItem];
      const items = allItems.filter((item) => {
        if (objectType !== "all" && item.object_type !== objectType) return false;
        return !query || `${item.name} ${item.owner} ${item.comment}`.toLowerCase().includes(query);
      });
      const pagedItems = query
        ? items
        : cursor === "metadata-page-2"
          ? items.slice(100)
          : items.slice(0, 100);
      return fulfillJson(route, {
        runtime: "deterministic",
        owner: "APP",
        items: pagedItems,
        total: items.length,
        table_count: items.filter((item) => item.object_type === "table").length,
        view_count: items.filter((item) => item.object_type === "view").length,
        next_cursor: !query && !cursor && items.length > 100 ? "metadata-page-2" : null,
        refreshed_at: schemaCatalog.refreshed_at,
        catalog_version: 1,
        warnings: [],
      });
    });
  };

  await page.route("**/api/nl2sql/db-admin/tables/PAGE_101_TABLE*", (route) =>
    fulfillJson(route, {
      name: "PAGE_101_TABLE",
      owner: "APP",
      object_type: "table",
      row_count: 1,
      comment: "101件目のテーブル",
      columns,
      ddl: "",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/views/V_PAGE_101_VIEW*", (route) =>
    fulfillJson(route, {
      name: "V_PAGE_101_VIEW",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "101件目のビュー",
      columns,
      ddl: "",
      warnings: [],
    })
  );

  await routePagedObjects({
    name: "PAGE_101_TABLE",
    owner: "APP",
    object_type: "table",
    row_count: 1,
    comment: "101件目のテーブル",
  });
  await page.goto("/comment-management");
  const commentFooter = page.getByTestId("comment-management-target-footer");
  await expect(commentFooter).toContainText("100 / 101 件を表示");
  await commentFooter.getByRole("button", { name: "さらに読み込む" }).click();
  await expect(commentFooter).toContainText("101 / 101 件を表示");
  await page.getByRole("checkbox", { name: /PAGE_101_TABLE/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.PAGE_101_TABLE/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.locator("#comment-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/COMMENT ON COLUMN/);
  expect(api.metadataSamplesPayload?.targets).toEqual([
    {
      object_name: "PAGE_101_TABLE",
      owner: "APP",
      object_type: "table",
      columns: ["ID"],
    },
  ]);

  await routePagedObjects({
    name: "V_PAGE_101_VIEW",
    owner: "APP",
    object_type: "view",
    row_count: null,
    comment: "101件目のビュー",
  });
  await page.goto("/annotation-management");
  const annotationToolbar = page.getByTestId("annotation-management-target-toolbar");
  const annotationFooter = page.getByTestId("annotation-management-target-footer");
  await expect(annotationFooter).toContainText("100 / 101 件を表示");
  await annotationToolbar.getByRole("searchbox", { name: "検索" }).fill("V_PAGE_101_VIEW");
  await expect(annotationFooter).toContainText("1 / 1 件を表示");
  await page.getByRole("checkbox", { name: /V_PAGE_101_VIEW/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.V_PAGE_101_VIEW/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect(page.locator("#annotation-management-panel-execute").getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(/ALTER TABLE/);
});

test("table and view management object lists load more and find unloaded objects", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  type PagedObject = {
    name: string;
    owner: string;
    object_type: "table" | "view";
    row_count: number | null;
    comment: string;
  };
  const columns = [
    {
      column_name: "ID",
      logical_name: "識別子",
      data_type: "NUMBER",
      nullable: false,
      comment: "識別子",
      sample_values: ["1"],
    },
  ];
  const tableObjects: PagedObject[] = Array.from({ length: 101 }, (_, index) => {
    const suffix = String(index + 1).padStart(3, "0");
    return {
      name: `PAGE_TABLE_${suffix}`,
      owner: "APP",
      object_type: "table",
      row_count: index + 1,
      comment: `${index + 1}件目のテーブル`,
    };
  });
  const viewObjects: PagedObject[] = Array.from({ length: 101 }, (_, index) => {
    const suffix = String(index + 1).padStart(3, "0");
    return {
      name: `V_PAGE_VIEW_${suffix}`,
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: `${index + 1}件目のビュー`,
    };
  });

  const routePagedObjects = async (
    objects: PagedObject[],
    expectedType: "table" | "view",
    nextCursor: string
  ) => {
    await page.unroute("**/api/nl2sql/db-admin/objects?*");
    await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
      const url = new URL(route.request().url());
      const objectType = url.searchParams.get("type") ?? "all";
      const rowState = url.searchParams.get("row_state") ?? "all";
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      const cursor = url.searchParams.get("cursor") ?? "";
      const items = objects.filter((item) => {
        if (objectType !== "all" && item.object_type !== objectType) return false;
        if (rowState === "with_rows" && !(typeof item.row_count === "number" && item.row_count > 0)) return false;
        if (rowState === "empty_rows" && item.row_count !== 0) return false;
        return !query || `${item.name} ${item.owner} ${item.comment}`.toLowerCase().includes(query);
      });
      const pageItems = query ? items : cursor === nextCursor ? items.slice(100) : items.slice(0, 100);
      return fulfillJson(route, {
        runtime: "deterministic",
        owner: "APP",
        items: pageItems,
        total: items.length,
        table_count: expectedType === "table" ? items.length : 0,
        view_count: expectedType === "view" ? items.length : 0,
        next_cursor: !query && !cursor && items.length > 100 ? nextCursor : null,
        refreshed_at: schemaCatalog.refreshed_at,
        catalog_version: 1,
        warnings: [],
      });
    });
  };
  const routeObjectDetail = async (kind: "table" | "view") => {
    const collection = kind === "table" ? "tables" : "views";
    const namePattern = kind === "table" ? "PAGE_*" : "V_PAGE_*";
    await page.route(`**/api/nl2sql/db-admin/${collection}/${namePattern}`, (route) => {
      const url = new URL(route.request().url());
      const name = decodeURIComponent(url.pathname.split("/").pop() ?? "");
      const includeDdl = url.searchParams.get("include_ddl") === "1";
      return fulfillJson(route, {
        name,
        owner: "APP",
        object_type: kind,
        row_count: kind === "table" ? 1 : null,
        comment: name.endsWith("_101") ? "101件目の対象" : "ページング対象",
        columns,
        ddl: includeDdl
          ? kind === "table"
            ? `CREATE TABLE "${name}" ("ID" NUMBER);`
            : `CREATE OR REPLACE VIEW "${name}" AS SELECT ID FROM PAGE_TABLE_001;`
          : "",
        warnings: [],
      });
    });
  };
  await routeObjectDetail("table");
  await routeObjectDetail("view");

  await routePagedObjects(tableObjects, "table", "table-management-page-2");
  await page.goto("/table-management");
  const tableFooter = page.getByTestId("table-management-footer");
  const tableSearch = page.getByRole("searchbox", { name: "検索" });
  await expect(tableFooter).toContainText("100 / 101 件を表示");
  await tableSearch.fill("PAGE_TABLE_101");
  await expect(tableFooter).toContainText("1 / 1 件を表示");
  await page.getByRole("button", { name: "PAGE_TABLE_101 を表示" }).click();
  await expect(page.getByTestId("table-management-detail-header")).toContainText("PAGE_TABLE_101");
  await expect(page.getByTestId("db-admin-detail-columns").getByRole("cell", { name: "ID" })).toBeVisible();
  await tableSearch.fill("");
  await expect(tableFooter).toContainText("100 / 101 件を表示");
  await tableFooter.getByRole("button", { name: "さらに読み込む" }).click();
  await expect(tableFooter).toContainText("101 / 101 件を表示");
  await expect(page.getByRole("button", { name: "PAGE_TABLE_101 を表示" })).toBeVisible();

  await routePagedObjects(viewObjects, "view", "view-management-page-2");
  await page.goto("/view-management");
  const viewFooter = page.getByTestId("view-management-footer");
  const viewSearch = page.getByRole("searchbox", { name: "検索" });
  await expect(viewFooter).toContainText("100 / 101 件を表示");
  await viewFooter.getByRole("button", { name: "さらに読み込む" }).click();
  await expect(viewFooter).toContainText("101 / 101 件を表示");
  await page.getByRole("button", { name: "V_PAGE_VIEW_101 を表示" }).click();
  await expect(page.getByTestId("view-management-detail-header")).toContainText("V_PAGE_VIEW_101");
  await expect(page.getByTestId("db-admin-detail-columns").getByRole("cell", { name: "ID" })).toBeVisible();
  await page.goto("/view-management");
  await expect(viewFooter).toContainText("100 / 101 件を表示");
  await viewSearch.fill("V_PAGE_VIEW_101");
  await expect(viewFooter).toContainText("1 / 1 件を表示");
  await expect(page.getByRole("button", { name: "V_PAGE_VIEW_101 を表示" })).toBeVisible();
});

test("synthetic data table bulk selection and results use the shared skeleton preset", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await expect(syntheticPanel.getByRole("heading", { name: "対象選択" })).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "進捗と状態" })).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("オペレーションID")).toHaveCount(0);
  await expect(syntheticPanel.getByRole("button", { name: "ステータスを更新" })).toHaveCount(0);
  const refreshTablesActions = syntheticPanel.getByTestId("data-synthetic-refresh-tables-actions");
  await expect(refreshTablesActions.getByText("対象テーブル一覧の取得")).toBeVisible();
  await expect(refreshTablesActions.getByText("選択中の Profile から対象テーブル一覧を取得します。")).toBeVisible();
  await expectTopToBottomOrder(
    syntheticPanel.getByLabel("Profile"),
    refreshTablesActions,
    syntheticPanel.getByTestId("data-synthetic-table-toolbar")
  );

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
  const refreshTablesButton = syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" });
  await refreshTablesButton.click();
  const syntheticTablesSkeleton = page.getByTestId("data-synthetic-tables-list-skeleton");
  await expect(syntheticTablesSkeleton).toBeVisible();
  await expect(refreshTablesButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(syntheticTablesSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expect(syntheticPanel.getByText("対象テーブルが未取得です")).toHaveCount(0);
  tablesGate.release();
  await expect(syntheticPanel.getByLabel("APP.INVOICES を選択")).toBeVisible();
  await expect(page.getByRole("region", { name: "通知" })).toContainText("対象テーブル一覧を取得しました。1 件を確認できます。");
  const syntheticBulkActions = syntheticPanel.getByTestId("data-synthetic-table-selection-actions");
  const syntheticTableList = syntheticPanel.getByTestId("data-synthetic-table-list");
  await expect
    .poll(async () => {
      const [actionsBox, listBox] = await Promise.all([
        syntheticBulkActions.boundingBox(),
        syntheticTableList.boundingBox(),
      ]);
      if (!actionsBox || !listBox) return Number.POSITIVE_INFINITY;
      return Math.abs(actionsBox.x - listBox.x);
    })
    .toBeLessThanOrEqual(1);
  await expect(syntheticBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeEnabled();
  await expect(syntheticBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeDisabled();
  await syntheticBulkActions.getByRole("button", { name: "表示中をすべて選択" }).click();
  await expect(syntheticPanel.getByText("選択 1 件", { exact: true })).toBeVisible();
  await expect(syntheticBulkActions.getByRole("button", { name: "表示中をすべて選択" })).toBeDisabled();
  await expect(syntheticBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" })).toBeEnabled();
  await syntheticBulkActions.getByRole("button", { name: "表示中の選択をすべて解除" }).click();
  await expect(syntheticPanel.getByText("選択 0 件", { exact: true })).toBeVisible();
  await syntheticPanel.getByLabel("APP.INVOICES を選択").check();
  await syntheticPanel.getByLabel("実行確認語").fill("APP.INVOICES");
  const generateGate = createRequestGate();
  await page.unroute("**/api/nl2sql/synthetic-data/generate");
  await page.route("**/api/nl2sql/synthetic-data/generate", async (route) => {
    await generateGate.promise;
    return fulfillJson(route, {
      table_name: "APP.INVOICES",
      object_list: ["APP.INVOICES"],
      row_count: 1,
      runtime: "oracle",
      status: "executed",
      message: "DBMS_CLOUD_AI synthetic data generation を実行しました。",
      warnings: [],
      engine_meta: {},
      timing,
    });
  });
  await syntheticPanel.getByRole("button", { name: "生成開始" }).click();
  await expect(page.getByRole("region", { name: "通知" })).toContainText("Synthetic data 生成を開始しました。");
  await expectToastStackBottomRight(page);
  generateGate.release();
  await expect(syntheticPanel.getByText("operation-001")).toHaveCount(0);
  const syntheticResultsSection = syntheticPanel.locator("section[aria-labelledby='synthetic-results-heading']");
  await expect(syntheticResultsSection.getByRole("heading", { name: "生成結果データの表示" })).toBeVisible();
  await expect(syntheticResultsSection.getByText("生成後に結果テーブルを選択すると表示できます。").first()).toBeVisible();
  await expect(page.getByRole("region", { name: "通知" })).toContainText("Synthetic data 生成が完了しました。");
  await expectToastStackBottomRight(page);

  const resultsGate = createRequestGate();
  const syntheticResultsRequests: URL[] = [];
  await page.route("**/api/nl2sql/synthetic-data/results**", async (route) => {
    syntheticResultsRequests.push(new URL(route.request().url()));
    await resultsGate.promise;
    return fulfillJson(route, {
      runtime: "deterministic",
      table_name: "APP.INVOICES",
      results: {
        columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
        rows: [{ CUSTOMER_NAME: "synthetic-loading-customer", TOTAL_AMOUNT: 12345 }],
        total: 1,
      },
      warnings: [],
    });
  });
  const resultTableSelect = syntheticPanel.getByTestId("synthetic-result-table-select");
  const resultLimitInput = syntheticResultsSection.getByLabel("取得件数上限");
  const resultsActions = syntheticPanel.getByTestId("data-synthetic-results-actions");
  await expect(resultTableSelect).toHaveValue("APP.INVOICES");
  await expect(resultLimitInput).toHaveValue("100");
  await expect(resultLimitInput).toHaveAttribute("max", "10000");
  await expect(syntheticResultsSection.getByText("表示するデータはまだありません")).toBeVisible();
  await expectTopToBottomOrder(resultTableSelect, resultLimitInput, resultsActions);
  const showDataButton = syntheticResultsSection.getByRole("button", { name: "データを表示" });
  await resultLimitInput.fill("-1");
  await expect(syntheticResultsSection.getByRole("alert")).toContainText("1〜10000 の整数で入力してください。");
  await expect(showDataButton).toBeDisabled();
  expect(syntheticResultsRequests).toHaveLength(0);
  await resultLimitInput.fill("100");
  await expect(syntheticResultsSection.getByText("1〜10000 の整数で入力してください。")).toHaveCount(0);
  await expect(showDataButton).toBeEnabled();
  await showDataButton.click();
  const syntheticResultsSkeleton = page.getByTestId("data-synthetic-results-detail-skeleton");
  await expect(syntheticResultsSkeleton).toBeVisible();
  await expect(showDataButton.locator("svg.animate-spin")).toHaveCount(1);
  await expect(syntheticResultsSkeleton.locator("svg.animate-spin")).toHaveCount(0);
  await expect(syntheticPanel.getByText("表示するデータはまだありません")).toHaveCount(0);
  await expect.poll(() => syntheticResultsRequests.length).toBe(1);
  expect(syntheticResultsRequests[0].searchParams.get("table_name")).toBe("APP.INVOICES");
  expect(syntheticResultsRequests[0].searchParams.get("limit")).toBe("100");
  resultsGate.release();
  await expect(syntheticPanel.getByRole("cell", { name: "synthetic-loading-customer" })).toBeVisible();
  await expect(syntheticPanel.getByTestId("query-result-summary")).toContainText("取得件数 1 件");
  await expect(syntheticPanel.getByTestId("query-result-summary")).toContainText("取得上限 100 件");
  await expect(page.getByRole("region", { name: "通知" })).toContainText("「APP.INVOICES」の生成結果データを表示しました。");
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(refreshTablesActions.getByRole("button", { name: "テーブル一覧を取得" })).toBeVisible();
  await expect(syntheticPanel.getByRole("button", { name: "ステータスを更新" })).toHaveCount(0);
  await expect(showDataButton).toBeVisible();
  await showDataButton.click();
  await expect.poll(() => syntheticResultsRequests.length).toBe(2);
  expect(syntheticResultsRequests[1].searchParams.get("limit")).toBe("100");
  await expect(page.getByRole("region", { name: "通知" })).toContainText("「APP.INVOICES」の生成結果データを表示しました。");
  await expectToastStackBottomRight(page);
  await expectNoHorizontalScroll(page);
});

test("synthetic data reports non-executed 200 responses beside the generate action", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.unroute("**/api/nl2sql/synthetic-data/generate");
  await page.route("**/api/nl2sql/synthetic-data/generate", (route) =>
    fulfillJson(route, {
      table_name: "APP.INVOICES",
      object_list: ["APP.INVOICES"],
      row_count: 1,
      executed: false,
      runtime: "deterministic",
      status: "requires_oracle",
      message: "INVOICES に 1 行/表の synthetic data を生成する plan です。",
      warnings: [
        "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。",
      ],
      engine_meta: {},
      timing,
    })
  );

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" }).click();
  await expect(syntheticPanel.getByLabel("APP.INVOICES を選択")).toBeVisible();
  await syntheticPanel.getByLabel("APP.INVOICES を選択").check();
  await syntheticPanel.getByLabel("実行確認語").fill("APP.INVOICES");

  await syntheticPanel.getByRole("button", { name: "生成開始" }).click();

  await expect(page.getByRole("region", { name: "通知" })).toContainText("Synthetic data 生成を開始しました。");
  await expect(page.getByRole("region", { name: "通知" })).not.toContainText("Synthetic data 生成が完了しました。");
  await expect(
    syntheticPanel.getByText(
      "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"
    )
  ).toBeVisible();
  await expectToastStackBottomRight(page);
  await expectNoHorizontalScroll(page);
});

test("synthetic data guides DB Profile read model refresh from the target step", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  let refreshed = false;
  let refreshSubmits = 0;
  let jobPolls = 0;

  await page.unroute(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true"
  );
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true",
    (route) =>
      fulfillJson(
        route,
        refreshed
          ? {
              runtime: "deterministic",
              profiles: [
                {
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
              ],
              warnings: [],
              profile_list_refresh_required: false,
              profile_list_refresh_reason_code: "",
            }
          : {
              runtime: "deterministic",
              profiles: [],
              warnings: [
                "DB Profile 一覧 read model が未初期化です。DB Profile 一覧を再取得してください。",
              ],
              profile_list_refresh_required: true,
              profile_list_refresh_reason_code: "profile_list_read_model_uninitialized",
            }
      )
  );
  await page.unroute("**/api/nl2sql/select-ai/db-profiles/refresh-jobs");
  await page.route("**/api/nl2sql/select-ai/db-profiles/refresh-jobs", (route) => {
    refreshSubmits += 1;
    return fulfillJson(route, {
      job_id: "synthetic-db-profile-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-08-13T00:00:00.000Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });
  await page.unroute("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*");
  await page.route("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*", (route) => {
    jobPolls += 1;
    const done = jobPolls >= 2;
    if (done) refreshed = true;
    return fulfillJson(route, {
      job_id: "synthetic-db-profile-refresh",
      status: done ? "done" : "running",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: done ? "done" : "fetching",
      created_at: "2026-08-13T00:00:00.000Z",
      started_at: "2026-08-13T00:00:00.000Z",
      finished_at: done ? "2026-08-13T00:00:01.000Z" : null,
      total_profiles: 1,
      processed_profiles: done ? 1 : 0,
      scanned_profiles: done ? 1 : 0,
      changed_profiles: done ? 1 : 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  const notice = syntheticPanel.getByTestId("data-synthetic-db-profile-refresh-notice");
  await expect(notice).toBeVisible();
  await expect(notice.getByText("DB Profile 一覧の再取得が必要です")).toBeVisible();
  await expect(notice.getByRole("button", { name: "DB Profile 一覧を再取得" })).toBeVisible();
  await expect(notice.getByRole("link", { name: "プロファイル管理を開く" })).toHaveAttribute(
    "href",
    "/profiles"
  );
  await expect(syntheticPanel.getByLabel("Profile")).toBeDisabled();
  await expect(syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" })).toBeDisabled();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(notice.getByRole("button", { name: "DB Profile 一覧を再取得" })).toBeVisible();
  await expect(notice.getByRole("link", { name: "プロファイル管理を開く" })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await notice.getByRole("button", { name: "DB Profile 一覧を再取得" }).click();
  await expect(page.getByTestId("data-synthetic-db-profile-refresh-processing")).toBeVisible();
  await expect(notice.getByRole("button", { name: "DB Profile 一覧を再取得" })).toBeDisabled();
  await expect(notice).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("Profile")).toHaveValue("NL2SQL_DEFAULT_PROFILE");
  await expect(syntheticPanel.getByLabel("Profile")).toBeEnabled();
  await expect(syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" })).toBeEnabled();
  await expect.poll(() => refreshSubmits).toBe(1);
});

test("synthetic data keeps DB Profile refresh recovery visible when the job fails", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.unroute(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true"
  );
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true",
    (route) =>
      fulfillJson(route, {
        runtime: "deterministic",
        profiles: [],
        warnings: [
          "DB Profile 一覧 read model が未初期化です。DB Profile 一覧を再取得してください。",
        ],
        profile_list_refresh_required: true,
        profile_list_refresh_reason_code: "profile_list_read_model_uninitialized",
      })
  );
  await page.unroute("**/api/nl2sql/select-ai/db-profiles/refresh-jobs");
  await page.route("**/api/nl2sql/select-ai/db-profiles/refresh-jobs", (route) =>
    fulfillJson(route, {
      job_id: "synthetic-db-profile-refresh-error",
      status: "pending",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-08-13T00:00:00.000Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    })
  );
  await page.unroute("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*");
  await page.route("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*", (route) =>
    fulfillJson(route, {
      job_id: "synthetic-db-profile-refresh-error",
      status: "error",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: true,
      phase: "fetching",
      created_at: "2026-08-13T00:00:00.000Z",
      started_at: "2026-08-13T00:00:00.000Z",
      finished_at: "2026-08-13T00:00:01.000Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "profile_list_refresh_full_required",
      error_message: "DB Profile 一覧の差分同期で不整合を検出しました。",
    })
  );

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  const notice = page
    .locator("#data-management-panel-synthetic")
    .getByTestId("data-synthetic-db-profile-refresh-notice");
  await notice.getByRole("button", { name: "DB Profile 一覧を再取得" }).click();

  await expect(
    notice.getByText("DB Profile 一覧の差分同期で不整合を検出しました。DB Profile 一覧を再取得してください。")
  ).toBeVisible();
  await expect(notice.getByRole("button", { name: "DB Profile 一覧を再取得" })).toBeEnabled();
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
  await expect(page.getByTestId("global-rules-file-input")).toHaveAttribute(
    "accept",
    ".xlsx"
  );
  await expect(page.getByText(".XLSX", { exact: true })).toBeVisible();
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
  await dropFiles(page, page.getByTestId("global-rules-file-dropzone"), [
    {
      name: "rules.csv",
      type: "text/csv",
      content: "RULE\n集計時は NULL を除外する\n",
    },
  ]);
  await expect(page.getByText(".XLSX ファイルを選択してください")).toBeVisible();
  await dropFiles(page, page.getByTestId("global-rules-file-dropzone"), [
    {
      name: "rules.xlsx",
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      content: "mock rules",
    },
  ]);
  await expect(page.getByText(/共通ルールを取り込みました/)).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("data management CSV upload layout keeps table, file, mode, and execution in full-width rows", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/data-management");
  await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
  const desktopCsvPanel = page.locator("#data-management-panel-csv");
  await expect(desktopCsvPanel).toBeVisible();
  await expectCsvUploadLayout(desktopCsvPanel);
  await expect(desktopCsvPanel.getByText("CSV / XLSX / XLS ファイルを選択してください。")).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/data-management");
  await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
  const mobileCsvPanel = page.locator("#data-management-panel-csv");
  await expect(mobileCsvPanel).toBeVisible();
  await expectCsvUploadLayout(mobileCsvPanel);
  await expect(mobileCsvPanel.getByText("CSV / XLSX / XLS ファイルを選択してください。")).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("data management CSV upload hides unmatched columns when Oracle reports none", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/db-admin/upload-csv");
  await page.route("**/api/nl2sql/db-admin/upload-csv", (route) => {
    return fulfillJson(route, {
      table_name: "INVOICES",
      filename: "book2.csv",
      mode: "insert",
      matched_columns: ["ID", "NAME"],
      unmatched_csv_columns: [],
      row_count: 2,
      success_count: 2,
      error_count: 0,
      row_errors: [],
      hint: "",
      executed: true,
      runtime: "oracle",
      sample_rows: [
        { ID: "123", NAME: "456" },
        { ID: "666", NAME: "777" },
      ],
      warnings: [],
      timing,
    });
  });

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
  const csvPanel = page.locator("#data-management-panel-csv");
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.INVOICES", { exact: true })).toBeVisible();
  await csvPanel.getByLabel("実行確認語").fill("APP.INVOICES");
  await dropFiles(page, page.getByTestId("data-csv-file-field-dropzone"), [
    {
      name: "book2.csv",
      type: "text/csv",
      content: "ID,NAME\n123,456\n666,777\n",
    },
  ]);

  await csvPanel.getByRole("button", { name: "アップロード実行" }).click();

  await expect(csvPanel.getByText(/一致列:\s*ID, NAME/)).toBeVisible();
  await expect(csvPanel.getByText("不一致列", { exact: false })).toHaveCount(0);
});

test("sample data and data management run imported workflows", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  const currentPreviewDataPayload = () => api.previewDataPayload;
  const dbAdminObjectOwnerPrefixRequests: string[] = [];
  const dbAdminObjectExactOwnerRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/nl2sql/db-admin/objects") {
      dbAdminObjectOwnerPrefixRequests.push(url.searchParams.get("owner_prefix") ?? "");
      if (url.searchParams.has("owner")) {
        dbAdminObjectExactOwnerRequests.push(url.searchParams.get("owner") ?? "");
      }
    }
  });

  await page.goto("/sample-data");
  await expect(page.getByText("検証用サンプルデータ管理")).toBeVisible();
  await expect(page.getByRole("heading", { name: "取り込み実行", exact: true })).toBeVisible();
  await expect(page.getByText("DEPARTMENT").first()).toBeVisible();
  await expect(page.getByLabel("実行する", { exact: true })).toHaveCount(0);

  const importPanel = page.locator("#sample-data-panel-import");
  const importConfirmationField = importPanel.getByTestId("execution-confirmation-field");
  await expect(importConfirmationField).toHaveClass(/border-border/);
  await expect(importConfirmationField).not.toHaveClass(/border-l-danger/);
  await expect(importConfirmationField).not.toHaveClass(/bg-danger-bg/);
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
  const deleteConfirmationField = deletePanel.getByTestId("execution-confirmation-field");
  await expectExecutionConfirmationFieldNoLeftAccent(deleteConfirmationField);
  await expect(deleteConfirmationField.getByText("確認済み", { exact: true })).toBeVisible();
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

  const dataPreviewObjectRequests: Array<{
    ownerPrefix: string | null;
    exactOwner: string | null;
    queryScope: string | null;
  }> = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname !== "/api/nl2sql/db-admin/objects") return;
    dataPreviewObjectRequests.push({
      ownerPrefix: url.searchParams.get("owner_prefix"),
      exactOwner: url.searchParams.get("owner"),
      queryScope: url.searchParams.get("query_scope"),
    });
  });
  await page.goto("/data-management");
  const dataPreviewTab = page.getByRole("tab", { name: "テーブル・ビューデータの表示" });
  const dataCsvTab = page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" });
  const dataSyntheticTab = page.getByRole("tab", { name: "合成データ生成" });
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "SQL 一括実行" })).toHaveCount(0);
  for (const label of ["INSERT(単一行)", "INSERT(複数行)", "UPDATE", "DELETE", "MERGE"]) {
    await expect(page.getByRole("button", { name: label, exact: true })).toHaveCount(0);
  }
  await expect(dataPreviewTab).toHaveAttribute("aria-selected", "true");
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "false");
  await expect(page.getByRole("tab", { name: "Synthetic NL2SQL ケース" })).toHaveCount(0);
  await dataPreviewTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(dataSyntheticTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(dataPreviewTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "テーブル・ビューデータの表示" })).toBeVisible();
  const dataPreviewPanel = page.locator("#data-management-panel-preview");
  await expect(page.getByText("Excel/CSV 取込(新規テーブル)", { exact: true })).toHaveCount(0);
  await expect(dataPreviewPanel.getByText("サンプルデータ管理", { exact: true })).toHaveCount(0);

  await expect(dataPreviewPanel.getByText("全4件")).toBeVisible();
  await expect(dataPreviewPanel.getByText("テーブル3")).toBeVisible();
  await expect(dataPreviewPanel.getByText("ビュー1")).toBeVisible();
  const previewSearch = dataPreviewPanel.getByRole("searchbox", { name: "検索" });
  const previewOwnerFilter = dataPreviewPanel.getByRole("searchbox", { name: "所有者" });
  await expect(previewSearch).toHaveAttribute("placeholder", "名前・コメントを入力");
  await expect(previewOwnerFilter).toBeVisible();
  await expect(previewOwnerFilter).toHaveAttribute("placeholder", "所有者の先頭を入力（例：ADM）");
  await expectEqualFilterWidths(previewSearch, previewOwnerFilter);
  await expect(dataPreviewPanel.getByRole("combobox", { name: "所有者" })).toHaveCount(0);
  await previewSearch.focus();
  await page.keyboard.press("Tab");
  await expect(previewOwnerFilter).toBeFocused();
  await expect
    .poll(() => dataPreviewObjectRequests.some((request) => request.queryScope === "name_comment"))
    .toBe(true);
  await previewOwnerFilter.fill("app");
  await expect(previewOwnerFilter).toHaveValue("APP");
  await expect
    .poll(() => dataPreviewObjectRequests.some((request) => request.ownerPrefix === "APP"))
    .toBe(true);
  expect(dataPreviewObjectRequests.some((request) => request.exactOwner !== null)).toBe(false);
  await previewOwnerFilter.clear();
  await expect(dataPreviewPanel.getByLabel("種別フィルタ")).toBeVisible();
  await expect(dataPreviewPanel.getByLabel("行数フィルタ")).toHaveCount(0);
  const previewRowLimitInput = dataPreviewPanel.getByLabel("取得件数上限");
  await expect(previewRowLimitInput).toHaveValue("100");
  await expect(dataPreviewPanel.getByText("0 は取得上限なし。")).toBeVisible();
  await expect(dataPreviewPanel.getByLabel("WHERE 条件(任意)")).toHaveCount(0);
  await expect(dataPreviewPanel.getByText("選択中", { exact: true })).toHaveCount(0);
  await expect(dataPreviewPanel.getByText("統計未取得")).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: /^操作: / })).toHaveCount(0);
  await expect(dataPreviewPanel.getByRole("button", { name: / を選択$/ })).toHaveCount(4);
  await expect(dataPreviewPanel.getByRole("button", { name: / のデータを表示$/ })).toHaveCount(0);
  const previewShowButton = dataPreviewPanel.getByRole("button", { name: "データを表示", exact: true });
  const previewClearButton = dataPreviewPanel.getByRole("button", { name: "クリア", exact: true });
  const previewResultsActions = dataPreviewPanel.getByTestId("data-preview-results-actions");
  const previewExportButton = previewResultsActions.getByRole("button", { name: "XLSX ダウンロード" });
  const previewMoreButton = previewResultsActions.getByRole("button", { name: "その他の操作" });
  const previewSteps = dataPreviewPanel.getByTestId("data-preview-steps");
  const previewResultsStep = previewSteps.getByRole("listitem").filter({ hasText: "結果確認" });
  await expect(previewSteps).toHaveAttribute("aria-label", "テーブル・ビューデータ表示ステップ");
  await expect(previewSteps).toContainText("対象選択");
  await expect(previewSteps).toContainText("結果確認");
  await expect(previewShowButton).toBeEnabled();
  await expect(previewResultsStep).toHaveAttribute("aria-current", "step");
  await expect(previewClearButton).toBeDisabled();
  api.previewDataPayload = null;
  await dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" }).click();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toHaveAttribute("aria-current", "true");
  await expect(previewResultsStep).toHaveAttribute("aria-current", "step");
  expect(api.previewDataPayload).toBeNull();
  await previewRowLimitInput.fill("-1");
  await expect(dataPreviewPanel.getByText("0 以上の整数で入力してください。")).toBeVisible();
  await expect(previewShowButton).toBeDisabled();
  await expect(previewExportButton).toBeDisabled();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toBeEnabled();
  await previewRowLimitInput.fill("100");
  await expect(dataPreviewPanel.getByText("0 以上の整数で入力してください。")).toHaveCount(0);
  await previewShowButton.click();
  await expect(previewExportButton).toBeVisible();
  await expect(previewMoreButton).toBeVisible();
  await expect(dataPreviewPanel.getByTestId("query-result-summary")).toContainText("取得件数 100 件");
  await expect(dataPreviewPanel.getByTestId("query-result-summary")).toContainText("取得上限 100 件");
  await expect(dataPreviewPanel.getByTestId("query-results-pagination")).toContainText("1-10 / 100 件");
  await expect(dataPreviewPanel.getByRole("cell", { name: "顧客11" })).toHaveCount(0);
  await dataPreviewPanel.getByRole("button", { name: "次へ" }).click();
  await expect(dataPreviewPanel.getByRole("cell", { name: "顧客11" })).toBeVisible();
  await dataPreviewPanel.getByRole("button", { name: "前へ" }).click();
  await previewClearButton.click();
  await expect(previewRowLimitInput).toHaveValue("100");
  await expect(dataPreviewPanel.getByText("データ未表示")).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toHaveAttribute("aria-current", "true");
  await expect(previewClearButton).toBeDisabled();
  await expect(previewShowButton).toBeEnabled();
  api.previewDataPayload = null;
  await previewShowButton.click();
  await expect.poll(() => currentPreviewDataPayload()?.object_name).toBe("INVOICES");
  expect(currentPreviewDataPayload()?.owner).toBe("APP");
  expect(currentPreviewDataPayload()?.limit).toBe(100);
  expect(currentPreviewDataPayload()?.where_clause).toBe("");
  const tableDownloadPromise = page.waitForEvent("download");
  await previewExportButton.click();
  const tableDownload = await tableDownloadPromise;
  expect(tableDownload.suggestedFilename()).toBe("app_invoices_preview.xlsx");
  await expect.poll(() => api.previewDataExportPayload?.object_name).toBe("INVOICES");
  expect(api.previewDataExportPayload?.owner).toBe("APP");
  expect(api.previewDataExportPayload?.limit).toBe(100);
  expect(api.previewDataExportPayload?.where_clause).toBe("");
  const [exportBox, moreBox] = await Promise.all([
    previewExportButton.boundingBox(),
    previewMoreButton.boundingBox(),
  ]);
  expect(exportBox).not.toBeNull();
  expect(moreBox).not.toBeNull();
  if (Math.abs(moreBox!.y - exportBox!.y) <= 2) {
    expect(moreBox!.x).toBeGreaterThan(exportBox!.x);
  } else {
    expect(moreBox!.y).toBeGreaterThan(exportBox!.y);
  }
  await clickObjectDetailAction(page, "data-preview-results-actions", "APP.INVOICES のデータを空にする");
  const truncateDialog = page.getByRole("dialog", { name: "TRUNCATE TABLE の確認" });
  await expect(truncateDialog).toBeVisible();
  await mainScroller(page).evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expectAppDialogOverlayCoversViewport(page);
  await expectOnlyConfirmationFieldHasNoLeftAccent(truncateDialog);
  const truncateButton = truncateDialog.getByRole("button", { name: "データを空にする" });
  await expect(truncateButton).toBeDisabled();
  await truncateDialog.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(truncateButton).toBeDisabled();
  await truncateDialog.getByLabel("実行確認語").fill("APP.INVOICES");
  await truncateButton.click();
  await expect.poll(() => api.truncateTablePayload?.confirmation).toBe("APP.INVOICES");
  expect(api.truncateTablePayload?.table_name).toBe("INVOICES");
  expect(api.truncateTablePayload?.owner).toBe("APP");
  expect(api.truncateTablePayload?.reason).toBe("ui-data-management-truncate");
  await expect(page.getByRole("dialog", { name: "TRUNCATE TABLE の確認" })).toHaveCount(0);

  await previewSearch.fill("EMP");
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.V_EMP_DEPT を選択" })).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toHaveCount(0);
  await previewSearch.fill("ZZZ");
  await expect(dataPreviewPanel.getByText("条件に一致する対象がありません")).toBeVisible();
  await previewSearch.fill("");
  await previewOwnerFilter.fill("ap");
  await expect(previewOwnerFilter).toHaveValue("AP");
  await expect.poll(() => dbAdminObjectOwnerPrefixRequests.includes("AP")).toBe(true);
  expect(dbAdminObjectExactOwnerRequests).toEqual([]);
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toBeVisible();
  await previewOwnerFilter.fill("ZZZ");
  await expect(dataPreviewPanel.getByText("条件に一致する対象がありません")).toBeVisible();
  await previewOwnerFilter.fill("");
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toBeVisible();
  await dataPreviewPanel.getByLabel("種別フィルタ").selectOption("view");
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.V_EMP_DEPT を選択" })).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.INVOICES を選択" })).toHaveCount(0);
  await previewRowLimitInput.fill("0");
  await expect(dataPreviewPanel.getByText("データ未表示")).toBeVisible();
  await expect(dataPreviewPanel.getByRole("button", { name: "APP.V_EMP_DEPT のデータを空にする" })).toHaveCount(0);
  await expect(dataPreviewPanel.getByRole("button", { name: "データを空にする", exact: true })).toHaveCount(0);
  await expect(previewResultsActions.getByRole("button", { name: "その他の操作" })).toHaveCount(0);
  const viewSelectTarget = dataPreviewPanel.getByRole("button", { name: "APP.V_EMP_DEPT を選択" });
  await expect(viewSelectTarget).toBeEnabled();
  await viewSelectTarget.focus();
  await page.keyboard.press("Enter");
  await expect(viewSelectTarget).toHaveAttribute("aria-current", "true");
  await expect(previewShowButton).toBeEnabled();

  api.previewDataPayload = null;
  await previewShowButton.click();
  await expect(page.getByRole("cell", { name: "顧客01" })).toBeVisible();
  await expect(dataPreviewPanel.getByTestId("query-result-summary")).toContainText("取得上限なし");
  await expect(page.getByTestId("query-results-pagination")).toContainText("1-10 / 25 件");
  await expect(page.getByRole("cell", { name: "顧客11" })).toHaveCount(0);
  await expect.poll(() => currentPreviewDataPayload()?.object_name).toBe("V_EMP_DEPT");
  expect(currentPreviewDataPayload()?.owner).toBe("APP");
  expect(currentPreviewDataPayload()?.limit).toBe(0);
  expect(currentPreviewDataPayload()?.where_clause).toBe("");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "XLSX ダウンロード" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("app_v_emp_dept_preview.xlsx");
  await expect.poll(() => api.previewDataExportPayload?.object_name).toBe("V_EMP_DEPT");
  expect(api.previewDataExportPayload?.owner).toBe("APP");
  expect(api.previewDataExportPayload?.limit).toBe(0);
  expect(api.previewDataExportPayload?.where_clause).toBe("");
  await expectNoHorizontalScroll(page);

  await dataCsvTab.click();
  await expect(dataCsvTab).toHaveAttribute("aria-selected", "true");
  const csvPanel = page.locator("#data-management-panel-csv");
  await expect(csvPanel).toBeVisible();
  await expectCsvUploadLayout(csvPanel);
  const csvTableSearch = csvPanel.getByRole("searchbox", { name: "検索" });
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.INVOICES", { exact: true })).toBeVisible();
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.AUDIT_LOG", { exact: true })).toHaveCount(0);
  await csvTableSearch.fill("PAY");
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.PAYMENTS", { exact: true })).toBeVisible();
  await csvPanel.getByRole("button", { name: "APP.PAYMENTS を選択" }).click();
  await expect(csvPanel.getByText("選択中")).toBeVisible();
  await expect(csvPanel.getByText("APP.PAYMENTS", { exact: true }).first()).toBeVisible();
  await csvTableSearch.clear();
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.INVOICES", { exact: true })).toBeVisible();
  await csvPanel.getByLabel("実行確認語").fill("APP.PAYMENTS");
  await expect(csvPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await csvPanel.getByTestId("data-csv-table-footer").getByRole("button", { name: "さらに読み込む" }).click();
  await expect(csvPanel.getByTestId("data-csv-table-list").getByText("APP.AUDIT_LOG", { exact: true })).toBeVisible();
  await expect(csvPanel.getByText("APP.PAYMENTS", { exact: true }).first()).toBeVisible();
  await expect(csvPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  const dataTabularInput = page.getByTestId("data-csv-file-field-input");
  await expect(dataTabularInput).toHaveAttribute("accept", ".csv,.xlsx,.xls");
  await expect(page.getByText(".CSV / .XLSX / .XLS", { exact: true })).toBeVisible();
  await dropFiles(page, page.getByTestId("data-csv-file-field-dropzone"), [
    {
      name: "invoices.XLS",
      type: "application/vnd.ms-excel",
      content: "legacy-xls",
    },
  ]);
  await expect(page.getByText("選択中: invoices.XLS")).toBeVisible();
  const csvUploadButton = page.getByRole("button", { name: "アップロード実行" });
  await expect(csvUploadButton).toBeEnabled();
  await csvUploadButton.click();
  await expect(page.getByText("UNKNOWN_COLUMN", { exact: false }).first()).toBeVisible();
  await expect.poll(() => api.csvUploadPayload?.table_name).toBe("PAYMENTS");
  expect(api.csvUploadPayload?.owner).toBe("APP");
  expect(api.csvUploadPayload?.mode).toBe("insert");
  expect(api.csvUploadPayload?.confirmation).toBe("APP.PAYMENTS");
  expect(api.csvUploadPayload?.filename).toBe("invoices.XLS");

  await dataSyntheticTab.click();
  await expect(dataSyntheticTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#data-management-panel-synthetic")).toBeVisible();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await expect(syntheticPanel.getByText("Synthetic NL2SQL ケース")).toHaveCount(0);
  await expect(syntheticPanel.getByRole("button", { name: "ケース生成" })).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("Oracle に synthetic data を生成")).toHaveCount(0);
  await expect(syntheticPanel.getByText("Oracle への synthetic data 生成")).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "対象選択" })).toBeVisible();
  await expect(syntheticPanel.getByRole("heading", { name: "進捗と状態" })).toHaveCount(0);
  await expect(syntheticPanel.getByRole("heading", { name: "生成結果データの表示" })).toBeVisible();
  const syntheticGenerateButton = syntheticPanel.getByRole("button", { name: "生成開始" });
  await expect(syntheticGenerateButton).toBeDisabled();
  const syntheticProfileSelect = syntheticPanel.getByLabel("Profile");
  await expect(syntheticProfileSelect).toHaveValue("NL2SQL_DEFAULT_PROFILE");
  await expect(syntheticProfileSelect.locator("option")).toHaveCount(1);
  await expect(syntheticProfileSelect.locator("option", { hasText: "NL2SQL_MANUAL_AGENT_V2_PROFILE" })).toHaveCount(0);
  await syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" }).click();
  await expect(syntheticPanel.getByLabel("APP.INVOICES を選択")).toBeVisible();
  await expect(syntheticPanel.getByLabel("PAYMENTS を選択")).toHaveCount(0);
  await expect(syntheticPanel.getByLabel("AUDIT_LOG を選択")).toHaveCount(0);
  await syntheticPanel.getByLabel("APP.INVOICES を選択").check();
  await expect(syntheticPanel.getByText("選択 1 件", { exact: true })).toBeVisible();
  await expect(syntheticGenerateButton).toBeDisabled();
  await syntheticPanel.getByLabel("実行確認語").fill("APP.INVOICES");
  await expect(syntheticPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(syntheticGenerateButton).toBeEnabled();
  await syntheticGenerateButton.click();
  await expect(syntheticPanel.getByText("operation-001")).toHaveCount(0);
  await expect(syntheticPanel.getByTestId("synthetic-result-table-select")).toHaveValue("APP.INVOICES");
  await expect(syntheticPanel.getByLabel("取得件数上限")).toHaveValue("100");
  await expect(syntheticPanel.getByRole("option", { name: "AUDIT_LOG" })).toHaveCount(0);
  await expect(syntheticPanel.getByRole("button", { name: "ステータスを更新" })).toHaveCount(0);
  await syntheticPanel.getByRole("button", { name: "データを表示" }).click();
  await expect(syntheticPanel.getByRole("cell", { name: "synthetic-customer" })).toBeVisible();
  await expect.poll(() => api.syntheticDataPayload?.table_name).toBe("APP.INVOICES");
  expect(api.syntheticDataPayload?.confirmation).toBe("APP.INVOICES");
  expect(api.syntheticDataPayload?.profile_name).toBe("NL2SQL_DEFAULT_PROFILE");
  expect(api.syntheticDataPayload?.object_list).toEqual([]);
  expect(api.syntheticDataPayload?.rows_per_table).toBe(1);
  expect(api.syntheticDataPayload?.sample_rows).toBe(5);
  expect(api.syntheticDataPayload?.use_comments).toBe(true);
  await expectNoHorizontalScroll(page);
});

test("sample data refresh replaces the whole workspace with the shared skeleton", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockNl2SqlApi(page);
  const refreshGate = createRequestGate();
  let holdNextRefresh = false;
  let markRefreshStarted: () => void = () => undefined;
  const refreshStarted = new Promise<void>((resolve) => {
    markRefreshStarted = resolve;
  });
  const sampleDataResponse = {
    runtime: "deterministic",
    profile_id: "",
    confirmation: "SQL_ASSIST_SAMPLE",
    objects: ["DEPARTMENT", "EMPLOYEE", "PROJECT"],
    imported_objects: ["DEPARTMENT"],
    sql: {
      tables: ["CREATE TABLE DEPARTMENT (DEPARTMENT_ID NUMBER PRIMARY KEY)"],
      views: ["CREATE OR REPLACE VIEW V_EMP_DEPT AS SELECT 1 AS ID FROM DUAL"],
      data: ["INSERT INTO DEPARTMENT (DEPARTMENT_ID) VALUES (10)"],
      delete: ["DROP TABLE DEPARTMENT PURGE"],
    },
    warnings: [],
  };

  await page.route("**/api/nl2sql/sample-data", async (route) => {
    if (holdNextRefresh) {
      holdNextRefresh = false;
      markRefreshStarted();
      await refreshGate.promise;
    }
    await fulfillJson(route, sampleDataResponse);
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/sample-data");
  const panel = page.locator("#sample-data-panel-import");
  await expect(page.getByRole("heading", { name: "検証用サンプルデータ管理" })).toBeVisible();
  await expect(panel.getByText("対象オブジェクト", { exact: true })).toBeVisible();
  await expect(panel.getByText("SQL プレビュー", { exact: true })).toBeVisible();

  holdNextRefresh = true;
  await clickPageHeaderAction(page, "sample-data-actions", "表示を更新");
  await refreshStarted;

  const skeleton = page.getByTestId("sample-data-workspace-refresh-skeleton");
  await expect(skeleton).toBeVisible();
  await expect(skeleton).toHaveAttribute("aria-busy", "true");
  await expect(skeleton).toHaveAttribute("data-processing-placement", "workspace");
  await expect(skeleton).toContainText("表示を更新しています");
  await expect(skeleton.getByTestId("sample-data-workspace-refresh-skeleton-processing")).toHaveAttribute(
    "data-processing-activity-icon",
    "none"
  );
  await expect(skeleton.getByTestId("db-management-skeleton-block")).toHaveCount(3);
  await expect(skeleton.getByTestId("db-management-skeleton-block").first()).toHaveCSS(
    "animation-name",
    "none"
  );
  await expect(panel.getByText("対象オブジェクト", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("SQL プレビュー", { exact: true })).toHaveCount(0);
  await expect(panel.getByText("DEPARTMENT", { exact: true })).toHaveCount(0);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect(skeleton).toBeVisible();
  await expectNoHorizontalScroll(page);

  refreshGate.release();
  await expect(skeleton).toHaveCount(0);
  await expect(panel.getByText("対象オブジェクト", { exact: true })).toBeVisible();
  await expect(panel.getByText("SQL プレビュー", { exact: true })).toBeVisible();
});

test("SampleData schema refresh recovery disables CTA while workspace processing is visible", async ({ page }) => {
  await mockNl2SqlApi(page);
  let manualRefreshStarted = 0;
  let manualRefreshPolls = 0;
  const manualRefreshGate = createRequestGate();

  await page.unroute("**/api/nl2sql/sample-data/import");
  await page.route("**/api/nl2sql/sample-data/import", (route) =>
    fulfillJson(route, {
      operation: "import",
      step: "all",
      runtime: "oracle",
      executed: false,
      objects: [],
      statements: [],
      warnings: [],
      profile_id: "",
      schema_refresh_required: true,
      schema_refresh_reason_code: "schema_refresh_full_required",
      timing,
    })
  );
  await page.route("**/api/schema/refresh-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    manualRefreshStarted += 1;
    await fulfillJson(route, {
      job_id: "sample-data-manual-full-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: null,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/sample-data-manual-full-refresh", async (route) => {
    manualRefreshPolls += 1;
    if (manualRefreshPolls === 1) {
      await fulfillJson(route, {
        job_id: "sample-data-manual-full-refresh",
        status: "running",
        mode: "full",
        source: "manual",
        target_objects: [],
        requires_full_refresh: false,
        phase: "fetching",
        created_at: "2026-07-22T00:00:01.000Z",
        scanned_objects: 1,
        changed_objects: 0,
        deleted_objects: 0,
        catalog_version: null,
        error_code: "",
      });
      return;
    }
    await manualRefreshGate.promise;
    await fulfillJson(route, {
      job_id: "sample-data-manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });

  await page.goto("/sample-data");
  await page.getByLabel("実行確認語").fill("SQL_ASSIST_SAMPLE");
  await page.getByRole("button", { name: "取り込み実行" }).last().click();

  await expect(page.getByText("DB 構造の差分同期で不整合を検出しました。")).toBeVisible();
  const refreshButton = page.getByRole("button", { name: "DB 構造を再取得" });
  await expect(refreshButton).toBeVisible();
  await refreshButton.click();
  await expect.poll(() => manualRefreshStarted).toBe(1);
  await expect(refreshButton).toBeDisabled();
  await expect(refreshButton.locator("svg.animate-spin")).toBeVisible();
  const processing = page.getByTestId("sample-data-workspace-processing");
  await expect(processing).toContainText("DB 構造を再取得しています");
  await expectNoHorizontalScroll(page);

  manualRefreshGate.release();
  await expect.poll(() => manualRefreshPolls).toBeGreaterThanOrEqual(2);
  await expect(processing).toHaveCount(0);
  await expect(page.getByText("DB 構造を再取得しました。")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("DB 構造再取得は旧30秒上限を超えても三つの管理画面で追跡を続ける", async ({
  page,
}) => {
  await page.unroute("**/api/auth/me").catch(() => undefined);
  await page.route("**/api/auth/me**", (route) => fulfillJson(route, systemAdminMe));
  await page.route("**/api/auth/login**", (route) => fulfillJson(route, systemAdminMe));
  await mockNl2SqlApi(page);

  let currentJobId = "";
  let completed = false;
  let failNextPoll = false;
  let submitted = 0;
  let objectRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/nl2sql/db-admin/objects") {
      objectRequests += 1;
    }
  });
  await page.route("**/api/schema/refresh-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    submitted += 1;
    await fulfillJson(route, {
      job_id: currentJobId,
      status: "pending",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-08-29T00:00:00.000Z",
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: null,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/schema-refresh-long-*", async (route) => {
    const jobId = new URL(route.request().url()).pathname.split("/").at(-1) ?? currentJobId;
    if (failNextPoll) {
      failNextPoll = false;
      await route.fulfill({ status: 504, body: "temporary poll failure" });
      return;
    }
    await fulfillJson(route, {
      job_id: jobId,
      status: completed ? "done" : "running",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: completed ? "done" : "persisting",
      created_at: "2026-08-29T00:00:00.000Z",
      started_at: "2026-08-29T00:00:01.000Z",
      finished_at: completed ? "2026-08-29T00:00:32.000Z" : null,
      processed_objects: 218,
      total_objects: 218,
      scanned_objects: completed ? 218 : 0,
      changed_objects: completed ? 3 : 0,
      deleted_objects: 0,
      catalog_version: completed ? 2 : null,
      error_code: "",
    });
  });

  const targets = [
    {
      id: "table",
      path: "/table-management",
      ready: "table-management-grid",
      processing: "table-management-workspace-processing",
    },
    {
      id: "view",
      path: "/view-management",
      ready: "view-management-grid",
      processing: "view-management-workspace-processing",
    },
    {
      id: "metadata",
      path: "/comment-management",
      ready: "comment-management-steps",
      processing: "comment-management-workspace-processing",
    },
  ] as const;

  for (const [index, target] of targets.entries()) {
    currentJobId = `schema-refresh-long-${target.id}`;
    completed = false;
    if (index === 0) {
      await page.goto(target.path);
      const loginHeading = page.getByRole("heading", { name: "システムにログイン" });
      if (await loginHeading.isVisible({ timeout: 1_000 }).catch(() => false)) {
        await page.getByLabel("ログインユーザーID").fill("SYSTEM");
        await page.getByLabel("パスワード").fill("password");
        await page.getByRole("button", { name: "ログイン", exact: true }).click();
      }
    } else {
      await page.evaluate((path) => {
        window.history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, target.path);
    }
    await expect(page.getByTestId(target.ready)).toBeVisible();
    if (index === 0) {
      await page.waitForLoadState("networkidle");
      await page.clock.install({ time: new Date("2026-08-29T00:00:00.000Z") });
    }

    const header = page.locator("header");
    const directAction = header.getByRole("button", {
      name: "DB 構造を再取得",
      exact: true,
    });
    if (await directAction.isVisible()) {
      await directAction.click();
    } else {
      await header.getByRole("button", { name: "その他の操作", exact: true }).click();
      await page.getByRole("menuitem", { name: "DB 構造を再取得", exact: true }).click();
    }
    await expect.poll(() => submitted).toBe(index + 1);

    const processing = page.getByTestId(target.processing);
    await expect(processing).toBeVisible();
    await expect(processing).toContainText("DB 構造を再取得しています");
    await expect(page.getByText("DB 構造再取得: 保存中 218/218", { exact: true })).toBeVisible();

    await page.clock.fastForward(31_000);
    await expect(processing).toBeVisible();
    await expect(processing).toContainText("通常より時間がかかっています");
    await expect(
      page.getByText("DB 構造の再取得に時間がかかっています。完了後に一覧を自動更新します。", {
        exact: false,
      }),
    ).toHaveCount(0);

    if (await directAction.isVisible()) {
      await expect(directAction).toBeDisabled();
    } else {
      await header.getByRole("button", { name: "その他の操作", exact: true }).click();
      await expect(
        page.getByRole("menuitem", { name: "DB 構造を再取得", exact: true }),
      ).toBeDisabled();
      await page.keyboard.press("Escape");
    }
    await expectNoHorizontalScroll(page);

    failNextPoll = true;
    await page.clock.fastForward(1_100);
    await expect(processing).toBeVisible();
    await expect(page.getByText("temporary poll failure", { exact: false })).toHaveCount(0);

    const objectRequestsBeforeCompletion = objectRequests;
    completed = true;
    await page.clock.fastForward(1_100);
    await expect(processing).toHaveCount(0);
    await expect(page.getByText("DB 構造を再取得しました。", { exact: true })).toHaveCount(1);
    await expect.poll(() => objectRequests).toBeGreaterThan(objectRequestsBeforeCompletion);
    await page.clock.fastForward(5_000);
  }
});

test("実行中 DB 構造再取得を全10ルートと再読込後に復元して完了時に一度だけ通知する", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await mockNl2SqlApi(page);
  await page.unroute("**/api/schema/refresh-jobs/active");

  const jobId = "schema-refresh-route-recovery";
  const startedAt = new Date(Date.now() - 12_000).toISOString();
  let completed = false;
  const responseJob = () => ({
    job_id: jobId,
    status: completed ? "done" : "running",
    mode: "full",
    source: "manual",
    target_objects: [],
    requires_full_refresh: false,
    phase: completed ? "done" : "persisting",
    created_at: startedAt,
    started_at: startedAt,
    finished_at: completed ? new Date().toISOString() : null,
    processed_objects: 218,
    total_objects: 218,
    scanned_objects: completed ? 218 : 0,
    changed_objects: completed ? 3 : 0,
    deleted_objects: 0,
    catalog_version: completed ? 2 : null,
    error_code: "",
  });

  await page.route("**/api/schema/refresh-jobs/active", (route) =>
    fulfillJson(route, { active_job: completed ? null : responseJob() })
  );
  await page.route(`**/api/schema/refresh-jobs/${jobId}`, (route) =>
    fulfillJson(route, responseJob())
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) =>
    fulfillJson(route, {
      profile_ontology_view: null,
      ontology_graph: { nodes: [], edges: [] },
      materialized: false,
      stale: false,
      warnings_ja: [],
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: null,
      draft_etag: "",
      published_at: null,
    })
  );

  const targets = [
    ["/query", "query-schema-refresh-status", "schema-reference-refreshing", true],
    ["/table-management", "table-schema-refresh-status", "table-management-workspace-processing", true],
    ["/view-management", "view-schema-refresh-status", "view-management-workspace-processing", true],
    ["/data-management", "data-management-schema-refresh-status", "data-management-workspace-processing", true],
    ["/sample-data", "sample-data-schema-refresh-status", "sample-data-workspace-processing", false],
    ["/comment-management", "comment-management-schema-refresh-status", "comment-management-workspace-processing", true],
    ["/annotation-management", "annotation-management-schema-refresh-status", "annotation-management-workspace-processing", true],
    ["/admin-sql", "admin-sql-schema-refresh-status", "admin-sql-schema-refresh-processing", false],
    ["/profiles", "profile-management-schema-refresh-status", "profile-management-workspace-processing", true],
    ["/ontology-build", "ontology-build-schema-refresh-status", "ontology-build-schema-refresh-processing", false],
  ] as const;

  for (const [index, [path, statusTestId, processingTestId, hasHeaderRefreshAction]] of targets.entries()) {
    if (index === 0) {
      await page.goto(path);
    } else {
      await page.evaluate((nextPath) => {
        window.history.pushState({}, "", nextPath);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, path);
    }
    const status = page.getByTestId(statusTestId);
    await expect(status).toContainText("DB 構造再取得: 保存中 218/218");
    const processing = page.getByTestId(processingTestId);
    await expect(processing).toContainText("DB 構造を再取得しています");
    await expect(processing).toContainText("通常より時間がかかっています");
    await expect(processing.getByRole("timer")).toHaveAttribute("aria-live", "off");

    if (hasHeaderRefreshAction) {
      const header = page.locator("header").filter({ has: status }).first();
      const directAction = header.getByRole("button", {
        name: "DB 構造を再取得",
        exact: true,
      });
      if (await directAction.isVisible()) {
        await expect(directAction).toBeDisabled();
      } else {
        await header.getByRole("button", { name: "その他の操作", exact: true }).click();
        await expect(
          page.getByRole("menuitem", { name: "DB 構造を再取得", exact: true }),
        ).toBeDisabled();
        await page.keyboard.press("Escape");
      }
    }
    await expectNoHorizontalScroll(page);
  }

  await page.evaluate(() => {
    window.history.pushState({}, "", "/query");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByTestId("query-schema-refresh-status")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("query-schema-refresh-status")).toContainText(
    "DB 構造再取得: 保存中 218/218",
  );
  await expect(page.getByTestId("schema-reference-refreshing")).toBeVisible();

  completed = true;
  await expect(page.getByTestId("query-schema-refresh-status")).toHaveCount(0);
  await expect(page.getByTestId("schema-reference-refreshing")).toHaveCount(0);
  await expect(page.getByText("DB 構造を再取得しました。", { exact: true })).toHaveCount(1);
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
  await expect(page.getByText("DB 構造を再取得しました。")).toBeVisible();
  expect(jobPolls).toBeGreaterThanOrEqual(2);
  expect(catalogRequests).toBe(0);

  submitErrorJob = true;
  await clickPageHeaderAction(
    page,
    "data-management-actions",
    "DB 構造を再取得"
  );
  await expect(
    mainScroller(page).getByText("DB 構造の再取得に失敗しました。再試行してください。", {
      exact: false,
    }),
  ).toBeVisible();

  await page.getByRole("tab", { name: "合成データ生成" }).click();
  await expect.poll(() => profileRequests).toBeGreaterThan(0);
  await expectNoHorizontalScroll(page);
});

test("data management keeps loaded object rows when load more times out and retries in place", async ({ page }) => {
  await page.addInitScript(() => {
    const nativeTimeout = AbortSignal.timeout.bind(AbortSignal);
    Object.defineProperty(AbortSignal, "timeout", {
      configurable: true,
      value: (delay: number) => nativeTimeout(delay === 60_000 ? 150 : delay),
    });
  });
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/db-admin/objects?*");
  const timeoutGate = createRequestGate();
  let cursorAttempts = 0;
  let allowSuccess = false;
  await page.route("**/api/nl2sql/db-admin/objects?*", async (route) => {
    const url = new URL(route.request().url());
    const cursor = url.searchParams.get("cursor");
    if (cursor) {
      expect(url.searchParams.get("include_counts")).toBe("false");
      cursorAttempts += 1;
      if (!allowSuccess) {
        await timeoutGate.promise;
      }
      try {
        await fulfillJson(route, {
          runtime: "deterministic",
          owner: "APP",
          items: [
            { name: "PAYMENTS", owner: "APP", object_type: "table", row_count: 3, comment: "入金情報" },
          ],
          total: 0,
          table_count: 0,
          view_count: 0,
          counts_included: false,
          next_cursor: null,
          refreshed_at: schemaCatalog.refreshed_at,
          catalog_version: 0,
          warnings: [],
        });
      } catch {
        // timeout で browser が最初の cursor request を破棄した場合は retry request を待つ。
      }
      return;
    }
    await fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: [
        { name: "INVOICES", owner: "APP", object_type: "table", row_count: 2, comment: "請求情報" },
      ],
      total: 2,
      table_count: 2,
      view_count: 0,
      counts_included: true,
      next_cursor: "page-2",
      refreshed_at: schemaCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });

  await page.goto("/data-management");
  await expect(page.getByRole("button", { name: "APP.INVOICES を選択" })).toBeVisible();
  await page.getByTestId("data-preview-object-footer").getByRole("button", { name: "さらに読み込む" }).click();
  await expect(page.getByRole("button", { name: "APP.INVOICES を選択" })).toBeVisible();
  await expect(page.getByText(/追加読み込みが60秒以内に完了しませんでした/)).toBeVisible();
  allowSuccess = true;
  timeoutGate.release();
  const retry = page.getByTestId("data-preview-object-footer").getByRole("button", { name: "再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "APP.PAYMENTS を選択" })).toBeVisible();
  expect(cursorAttempts).toBe(2);
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

test("Excel/CSV取込の列幅エラーは取込フォーム内に表示して入力を保持する", async ({
  page,
}) => {
  await mockNl2SqlApi(page);
  const errorMessage =
    "TEST_TABLE.NAME: ファイル2行目は16バイトで、取込先列の上限6バイトを超えています。" +
    "値を短くするか列定義を拡張して再試行してください。";
  let submittedMode = "";
  await page.unroute("**/api/nl2sql/db-admin/import-tabular");
  await page.route("**/api/nl2sql/db-admin/import-tabular", async (route) => {
    const payload = route.request().postDataJSON() as { mode?: string };
    submittedMode = payload.mode ?? "";
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: [errorMessage],
        warning_messages: [],
      }),
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );
  const importPanel = page.locator("#table-management-panel-import");
  await importPanel.getByLabel("Oracle 表名").fill("TEST_TABLE");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    {
      name: "oversized.csv",
      type: "text/csv",
      content: "ID,NAME\n1,株式会社青山\n",
    },
  ]);
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");

  const executeButton = importPanel.getByRole("button", { name: "取込を実行" });
  await executeButton.focus();
  await executeButton.press("Enter");

  const errorStatus = importPanel.getByRole("alert").filter({ hasText: "16バイト" });
  await expect(errorStatus).toBeVisible();
  await expect(errorStatus).toContainText("列定義を拡張して再試行してください");
  await expect(errorStatus.locator("svg")).toHaveCount(1);
  await expect(page.getByText(errorMessage, { exact: true })).toHaveCount(1);
  expect(submittedMode).toBe("create");
  await expect(importPanel.getByLabel("Oracle 表名")).toHaveValue("TEST_TABLE");
  await expect(importPanel.getByText("取込方法", { exact: true })).toHaveCount(0);
  await expect(importPanel.locator("select")).toHaveCount(0);
  await expect(importPanel.getByText("選択中: oversized.csv")).toBeVisible();
  await expect(importPanel.getByLabel("実行確認語")).toHaveValue("ADMIN_EXECUTE");
  await expect(executeButton).toBeEnabled();

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
  await importPanel.getByLabel("Oracle 表名").fill("TEST_TABLE_FIXED");
  await expect(errorStatus).toHaveCount(0);
});

test("Excel/CSV取込の重複名は対象と安全な復旧手順を表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  await page.unroute("**/api/nl2sql/db-admin/import-tabular");
  await page.route("**/api/nl2sql/db-admin/import-tabular", async (route) => {
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: ["「TEST_TABLE」は既に存在するため、新規作成できません。"],
        error_code: "ORA-00955",
        error_details: {
          summary: "「TEST_TABLE」は既に存在するため、新規作成できません。",
          cause: "既存のTABLE と名前が重複しています。",
          actions: ["表名を変更して再実行してください。", "一覧へ戻り、同名の既存オブジェクトを確認してください。"],
          target_name: "TEST_TABLE",
          target_type: "TABLE",
          operation: "tabular_import",
          raw_message: "ORA-00955: name is already used by an existing object",
        },
      }),
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(page, "table-management-actions", "Excel/CSV 取込(新規テーブル)");
  const importPanel = page.locator("#table-management-panel-import");
  await importPanel.getByLabel("Oracle 表名").fill("TEST_TABLE");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    { name: "test.csv", type: "text/csv", content: "ID\n1\n" },
  ]);
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await importPanel.getByRole("button", { name: "取込を実行" }).click();

  const alert = importPanel.getByRole("alert");
  await expect(alert).toContainText("TEST_TABLE");
  await expect(alert).toContainText("表名を変更して再実行してください");
  await expect(importPanel.getByLabel("Oracle 表名")).toHaveValue("TEST_TABLE");
  await alert.getByText("詳細ログ").click();
  // 詳細ログはサニタイズ済みのエラーコードを表示する(生の Oracle 文言は expose しない)。
  await expect(alert).toContainText("ORA-00955");
  await expect(alert.getByRole("button", { name: "一覧へ戻る" })).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalScroll(page);
});

test("Excel/CSV取込はSheet不一致をエラーで止め旧結果を残さない", async ({ page }) => {
  await mockNl2SqlApi(page);
  let schemaRefreshRequests = 0;
  page.on("request", (request) => {
    // SchemaRefreshCoordinator の active-job discovery(GET /active)は refresh 開始ではない。
    const url = request.url();
    if (url.includes("/api/schema/refresh-jobs/") && !url.includes("/refresh-jobs/active")) {
      schemaRefreshRequests += 1;
    }
  });
  await page.unroute("**/api/nl2sql/db-admin/import-tabular");
  await page.route("**/api/nl2sql/db-admin/import-tabular", async (route) => {
    const payload = route.request().postDataJSON() as { table_name?: string; sheet_name?: string };
    const tableName = payload.table_name ?? "TABLE_2026081301";
    if (payload.sheet_name === "Sheet1") {
      await fulfillJson(route, {
        table_name: tableName,
        filename: "Book1.xlsx",
        sheet_name: "Sheet1",
        mode: "create",
        columns: [
          { source_name: "A", column_name: "A", data_type: "NUMBER", nullable: true },
          { source_name: "B", column_name: "B", data_type: "NUMBER", nullable: true },
        ],
        row_count: 2,
        executed: true,
        ddl: `CREATE TABLE "${tableName}" ("A" NUMBER, "B" NUMBER)`,
        insert_sql: `INSERT INTO "${tableName}" ("A", "B") VALUES (:c0, :c1)`,
        schema_refresh_job_id: "",
        warnings: [],
        sample_rows: [
          { A: "123", B: "456" },
          { A: "666", B: "777" },
        ],
        timing,
      });
      return;
    }
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: [
          "Shell1: Sheet が見つかりません。Sheet 名を修正するか、ファイル内の Sheet 名を確認して再試行してください。利用可能な Sheet: Sheet1。",
        ],
        warning_messages: [],
      }),
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(page, "table-management-actions", "Excel/CSV 取込(新規テーブル)");
  const importPanel = page.locator("#table-management-panel-import");
  await importPanel.getByLabel("Oracle 表名").fill("TABLE_2026081301");
  await importPanel.getByLabel("Sheet 名").fill("Sheet1");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    {
      name: "Book1.xlsx",
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      content: "mock workbook",
    },
  ]);
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await importPanel.getByRole("button", { name: "取込を実行" }).click();
  await expect(importPanel.getByTestId("table-import-result-panel")).toContainText(
    'CREATE TABLE "TABLE_2026081301"'
  );

  await importPanel.getByLabel("Sheet 名").fill("Shell1");
  await importPanel.getByRole("button", { name: "取込を実行" }).click();

  const alert = importPanel.getByRole("alert").filter({ hasText: "Sheet が見つかりません" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Sheet 名を修正するか");
  await expect(importPanel.getByTestId("table-import-result-panel")).toHaveCount(0);
  await expect(importPanel.getByTestId("table-import-schema-refresh-processing")).toHaveCount(0);
  await expect(importPanel.getByText(/active または先頭 Sheet/)).toHaveCount(0);
  await expect(importPanel.getByLabel("Oracle 表名")).toHaveValue("TABLE_2026081301");
  await expect(importPanel.getByLabel("Sheet 名")).toHaveValue("Shell1");
  await expect(importPanel.getByText("選択中: Book1.xlsx")).toBeVisible();
  await expect(importPanel.getByLabel("実行確認語")).toHaveValue("ADMIN_EXECUTE");
  expect(schemaRefreshRequests).toBe(0);
  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("Excel/CSV取込はDB構造再取得を待たず結果を実行枠下に表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  let schemaRefreshPolls = 0;
  let dbAdminObjectRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/nl2sql/db-admin/objects?")) dbAdminObjectRequests += 1;
  });
  await page.unroute("**/api/nl2sql/db-admin/import-tabular");
  await page.route("**/api/nl2sql/db-admin/import-tabular", async (route) => {
    const payload = route.request().postDataJSON() as { table_name?: string; filename?: string; mode?: string };
    const tableName = payload.table_name ?? "IMPORTED_ORDERS";
    await fulfillJson(route, {
      table_name: tableName,
      filename: payload.filename ?? "orders.csv",
      sheet_name: "",
      mode: payload.mode ?? "create",
      columns: [
        { source_name: "ORDER_ID", column_name: "ORDER_ID", data_type: "NUMBER", nullable: false },
        { source_name: "ORDER_NAME", column_name: "ORDER_NAME", data_type: "VARCHAR2(4 CHAR)", nullable: true },
      ],
      row_count: 1,
      executed: true,
      ddl: `CREATE TABLE "${tableName}" ("ORDER_ID" NUMBER, "ORDER_NAME" VARCHAR2(4 CHAR))`,
      insert_sql: `INSERT INTO "${tableName}" ("ORDER_ID", "ORDER_NAME") VALUES (:c0, :c1)`,
      schema_refresh_job_id: "table-import-refresh-pending",
      warnings: [],
      sample_rows: [{ ORDER_ID: "1", ORDER_NAME: "青山商事" }],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/table-import-refresh-pending", async (route) => {
    schemaRefreshPolls += 1;
    const done = schemaRefreshPolls >= 2;
    await fulfillJson(route, {
      job_id: "table-import-refresh-pending",
      status: done ? "done" : "running",
      mode: "targeted",
      source: "db_admin_import_tabular",
      target_objects: [
        {
          owner: "APP",
          object_name: "IMPORTED_ORDERS",
          object_type: "table",
          expected_state: "present",
        },
      ],
      phase: done ? "done" : "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: done ? 1 : 0,
      changed_objects: done ? 1 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : 0,
      error_code: "",
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(page, "table-management-actions", "Excel/CSV 取込(新規テーブル)");
  const importPanel = page.locator("#table-management-panel-import");
  const executionFieldset = importPanel.getByTestId("table-import-execution-fieldset");
  const executeButton = importPanel.getByRole("button", { name: "取込を実行" });
  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    { name: "orders.csv", type: "text/csv", content: "ORDER_ID,ORDER_NAME\n1,青山商事\n" },
  ]);
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await executeButton.click();

  const resultPanel = executionFieldset.getByTestId("table-import-result-panel");
  await expect(resultPanel).toBeVisible();
  await expect(resultPanel).toContainText('CREATE TABLE "IMPORTED_ORDERS"');
  await expect(importPanel.getByTestId("table-import-schema-refresh-processing")).toBeVisible();
  await expect(importPanel.getByTestId("table-import-schema-refresh-processing")).toContainText(
    "DB 構造の差分を同期しています",
  );
  await expect(executeButton).toBeEnabled();
  await expect.poll(() => schemaRefreshPolls).toBeGreaterThan(0);
  await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
  await expect(importPanel.getByTestId("table-import-schema-refresh-processing")).toHaveCount(0);
  await page.waitForTimeout(200);
  const dbAdminObjectRequestsAfterDone = dbAdminObjectRequests;
  await page.waitForTimeout(1_500);
  expect(dbAdminObjectRequests).toBe(dbAdminObjectRequestsAfterDone);

  const fieldsetBox = await executionFieldset.boundingBox();
  const buttonBox = await executeButton.boundingBox();
  const resultBox = await resultPanel.boundingBox();
  expect(fieldsetBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(resultBox).not.toBeNull();
  expect(resultBox!.y).toBeGreaterThan(buttonBox!.y + buttonBox!.height);
  expect(resultBox!.y).toBeGreaterThanOrEqual(fieldsetBox!.y);
  expect(resultBox!.y + resultBox!.height).toBeLessThanOrEqual(fieldsetBox!.y + fieldsetBox!.height + 1);

  await page.setViewportSize({ width: 375, height: 900 });
  await expectNoHorizontalScroll(page);
});

test("Excel/CSV取込後の差分同期不整合はDB構造再取得CTAを表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  let manualRefreshStarted = 0;
  await page.unroute("**/api/nl2sql/db-admin/import-tabular");
  await page.route("**/api/nl2sql/db-admin/import-tabular", async (route) => {
    const payload = route.request().postDataJSON() as { table_name?: string; filename?: string; mode?: string };
    const tableName = payload.table_name ?? "IMPORTED_ORDERS";
    await fulfillJson(route, {
      table_name: tableName,
      filename: payload.filename ?? "orders.csv",
      sheet_name: "",
      mode: payload.mode ?? "create",
      columns: [
        { source_name: "ORDER_ID", column_name: "ORDER_ID", data_type: "NUMBER", nullable: false },
      ],
      row_count: 1,
      executed: true,
      ddl: `CREATE TABLE "${tableName}" ("ORDER_ID" NUMBER)`,
      insert_sql: `INSERT INTO "${tableName}" ("ORDER_ID") VALUES (:c0)`,
      schema_refresh_job_id: "table-import-refresh-error",
      schema_refresh_required: false,
      schema_refresh_reason_code: "",
      warnings: [],
      sample_rows: [{ ORDER_ID: "1" }],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/table-import-refresh-error", async (route) => {
    await fulfillJson(route, {
      job_id: "table-import-refresh-error",
      status: "error",
      mode: "targeted",
      source: "db_admin_import_tabular",
      target_objects: [
        {
          owner: "APP",
          object_name: "IMPORTED_ORDERS",
          object_type: "table",
          expected_state: "present",
        },
      ],
      requires_full_refresh: true,
      phase: "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: 1,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: 0,
      error_code: "schema_refresh_full_required",
    });
  });
  await page.route("**/api/schema/refresh-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    manualRefreshStarted += 1;
    await fulfillJson(route, {
      job_id: "manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/manual-full-refresh", async (route) => {
    await fulfillJson(route, {
      job_id: "manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(page, "table-management-actions", "Excel/CSV 取込(新規テーブル)");
  const importPanel = page.locator("#table-management-panel-import");
  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    { name: "orders.csv", type: "text/csv", content: "ORDER_ID\n1\n" },
  ]);
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await importPanel.getByRole("button", { name: "取込を実行" }).click();

  await expect(importPanel).toContainText("DB 構造の差分同期で不整合を検出しました。");
  const refreshButton = importPanel.getByRole("button", { name: "DB 構造を再取得" });
  await expect(refreshButton).toBeVisible();
  await refreshButton.click();
  await expect.poll(() => manualRefreshStarted).toBe(1);
});

test("テーブル作成はDB構造再取得を待たずSQL結果と操作可能状態を戻す", async ({ page }) => {
  await mockNl2SqlApi(page);
  let schemaRefreshPolls = 0;
  const statementsGate = createRequestGate();
  await page.unroute("**/api/nl2sql/db-admin/statements");
  await page.route("**/api/nl2sql/db-admin/statements", async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(payload.sql ?? "CREATE TABLE T_PENDING (ID NUMBER)");
    await statementsGate.promise;
    await fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "CREATE",
          status: "success",
          sql,
          row_count: null,
          message: "OK",
          elapsed_ms: 1,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      schema_refresh_job_id: "table-create-refresh-running",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/table-create-refresh-running", async (route) => {
    schemaRefreshPolls += 1;
    await fulfillJson(route, {
      job_id: "table-create-refresh-running",
      status: "running",
      mode: "targeted",
      source: "db_admin_statements",
      target_objects: [
        {
          owner: "APP",
          object_name: "T_PENDING",
          object_type: "table",
          expected_state: "present",
        },
      ],
      phase: "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      started_at: "2026-07-22T00:00:01.000Z",
      heartbeat_at: "2026-07-22T00:00:02.000Z",
      lease_expires_at: "2026-07-22T00:15:02.000Z",
      worker_id: "api:test",
      attempt: 1,
      processed_objects: 1,
      total_objects: 2,
      scanned_objects: 0,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: 0,
      error_code: "",
    });
  });

  await page.goto("/table-management");
  await clickPageHeaderAction(page, "table-management-actions", "テーブル作成");
  const createPanel = page.locator("#table-management-panel-create");
  const executeButton = createPanel.getByRole("button", { name: "SQL 実行" });
  await createPanel
    .getByLabel("SQL(セミコロン区切りで複数文を入力可能)")
    .fill("CREATE TABLE T_PENDING (ID NUMBER)");
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  const statementsRequest = page.waitForRequest("**/api/nl2sql/db-admin/statements");
  await executeButton.click();
  await statementsRequest;
  try {
    const executionActivity = createPanel.getByTestId("statement-runner-table_ddl-execution-activity");
    await expect(executionActivity).toContainText("SQL を実行しています");
    await expect(executionActivity.locator("svg.animate-spin")).toHaveCount(0);
    await expect(executionActivity.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expectCompactExecutionActivity(executionActivity);
    await expect(createPanel.getByTestId("statement-runner-table_ddl-processing-region")).toHaveCount(0);
    await expectNoHorizontalScroll(page);
  } finally {
    statementsGate.release();
  }

  await expect(
    createPanel.getByTestId("statement-runner-table_ddl-processing-region").getByText("CREATE TABLE T_PENDING")
  ).toBeVisible();
  await expect(createPanel.getByText("コミット済み")).toBeVisible();
  const completedExecutionActivity = createPanel.getByTestId("statement-runner-table_ddl-execution-activity");
  await expect(completedExecutionActivity.getByRole("timer")).toHaveAccessibleName(/処理時間 \d{2}:\d{2}/);
  await expectCompactExecutionActivity(completedExecutionActivity);
  await expect(executeButton).toBeEnabled();
  await expect.poll(() => schemaRefreshPolls).toBeGreaterThan(0);
  await expect(page.getByTestId("table-schema-refresh-status")).toContainText(
    "DB 構造差分同期: 構造取得中",
  );
  await expectNoHorizontalScroll(page);
});

test("ビュー作成後はDB構造差分同期を作成パネル内に表示し完了後に消す", async ({ page }) => {
  await mockNl2SqlApi(page);
  let schemaRefreshPolls = 0;
  await page.unroute("**/api/nl2sql/db-admin/statements");
  await page.route("**/api/nl2sql/db-admin/statements", async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(
      payload.sql ?? "CREATE OR REPLACE VIEW V_PENDING AS SELECT 1 AS ID FROM DUAL"
    );
    await fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "CREATE",
          status: "success",
          sql,
          row_count: null,
          message: "OK",
          elapsed_ms: 1,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      schema_refresh_job_id: "view-create-refresh-done",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/view-create-refresh-done", async (route) => {
    schemaRefreshPolls += 1;
    const done = schemaRefreshPolls >= 2;
    await fulfillJson(route, {
      job_id: "view-create-refresh-done",
      status: done ? "done" : "running",
      mode: "targeted",
      source: "db_admin_statements",
      target_objects: [
        {
          owner: "APP",
          object_name: "V_PENDING",
          object_type: "view",
          expected_state: "present",
        },
      ],
      requires_full_refresh: false,
      phase: done ? "done" : "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: done ? 1 : 0,
      changed_objects: done ? 1 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : 0,
      error_code: "",
    });
  });

  await page.goto("/view-management");
  await clickPageHeaderAction(page, "view-management-actions", "ビュー作成");
  const createPanel = page.locator("#view-management-panel-create");
  await createPanel
    .getByLabel("SQL(セミコロン区切りで複数文を入力可能)")
    .fill("CREATE OR REPLACE VIEW V_PENDING AS SELECT 1 AS ID FROM DUAL");
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await createPanel.getByRole("button", { name: "SQL 実行" }).click();

  const resultRegion = createPanel.getByTestId("statement-runner-view_ddl-processing-region");
  await expect(resultRegion.getByText("CREATE OR REPLACE VIEW V_PENDING")).toBeVisible();
  await expect(createPanel.getByText("コミット済み", { exact: true })).toBeVisible();
  const schemaSync = createPanel.getByTestId("view-create-schema-refresh-processing");
  await expect(schemaSync).toContainText("DB 構造の差分を同期しています");
  await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
  await expect(schemaSync).toHaveCount(0);
  await expect(resultRegion.getByText("CREATE OR REPLACE VIEW V_PENDING")).toBeVisible();
  await expect(createPanel.getByText("コミット済み", { exact: true })).toBeVisible();
});

test("ビュー作成の差分同期不整合はDB構造再取得CTAを表示する", async ({ page }) => {
  await mockNl2SqlApi(page);
  let manualRefreshStarted = 0;
  await page.unroute("**/api/nl2sql/db-admin/statements");
  await page.route("**/api/nl2sql/db-admin/statements", async (route) => {
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const sql = String(
      payload.sql ?? "CREATE OR REPLACE VIEW V_NEEDS_REFRESH AS SELECT 1 AS ID FROM DUAL"
    );
    await fulfillJson(route, {
      executed: true,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "CREATE",
          status: "success",
          sql,
          row_count: null,
          message: "OK",
          elapsed_ms: 1,
          error_message: "",
        },
      ],
      committed: true,
      rolled_back: false,
      schema_refresh_job_id: "view-create-refresh-error",
      warnings: [],
      timing,
    });
  });
  await page.route("**/api/schema/refresh-jobs/view-create-refresh-error", async (route) => {
    await fulfillJson(route, {
      job_id: "view-create-refresh-error",
      status: "error",
      mode: "targeted",
      source: "db_admin_statements",
      target_objects: [
        {
          owner: "APP",
          object_name: "V_NEEDS_REFRESH",
          object_type: "view",
          expected_state: "present",
        },
      ],
      requires_full_refresh: true,
      phase: "fetching",
      created_at: "2026-07-22T00:00:00.000Z",
      scanned_objects: 1,
      changed_objects: 0,
      deleted_objects: 0,
      catalog_version: 0,
      error_code: "schema_refresh_full_required",
    });
  });
  await page.route("**/api/schema/refresh-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    manualRefreshStarted += 1;
    await fulfillJson(route, {
      job_id: "view-create-manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });
  await page.route("**/api/schema/refresh-jobs/view-create-manual-full-refresh", async (route) => {
    await fulfillJson(route, {
      job_id: "view-create-manual-full-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_objects: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-07-22T00:00:01.000Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });

  await page.goto("/view-management");
  await clickPageHeaderAction(page, "view-management-actions", "ビュー作成");
  const createPanel = page.locator("#view-management-panel-create");
  await createPanel
    .getByLabel("SQL(セミコロン区切りで複数文を入力可能)")
    .fill("CREATE OR REPLACE VIEW V_NEEDS_REFRESH AS SELECT 1 AS ID FROM DUAL");
  await createPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await createPanel.getByRole("button", { name: "SQL 実行" }).click();

  const resultRegion = createPanel.getByTestId("statement-runner-view_ddl-processing-region");
  await expect(resultRegion.getByText("CREATE OR REPLACE VIEW V_NEEDS_REFRESH")).toBeVisible();
  await expect(createPanel.getByText("コミット済み", { exact: true })).toBeVisible();
  await expect(page.locator("body")).toContainText("DB 構造の差分同期で不整合を検出しました。");
  const refreshButton = page.getByRole("button", { name: "DB 構造を再取得" });
  await expect(refreshButton).toBeVisible();
  await refreshButton.click();
  await expect.poll(() => manualRefreshStarted).toBe(1);
});

test("table and view management pages run guarded DDL and AI workflows", async ({ page }) => {
  test.slow();
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
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await tablePageActions.getByRole("button", { name: "その他の操作" }).click();
    await expect(page.getByRole("menuitem", { name: "表示を更新" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeVisible();
    await page.keyboard.press("Escape");
  } else {
    await expect(tablePageActions.getByRole("button", { name: "表示を更新" })).toBeVisible();
    await expect(tablePageActions.getByRole("button", { name: "DB 構造を再取得" })).toBeVisible();
  }
  await page.getByLabel("検索").fill("請求");
  await expect(page.getByTestId("table-management-grid").getByText("APP.INVOICES")).toBeVisible();
  await page.getByRole("button", { name: "APP.INVOICES を表示" }).click();
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
  expect(columnsDownload.suggestedFilename()).toBe("app_invoices_columns.xlsx");
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
  await expectContentActionsRightAligned(page.getByTestId("table-management-ddl-actions"));
  const ddlDownloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "SQL 出力" }).click();
  const ddlDownload = await ddlDownloadPromise;
  expect(ddlDownload.suggestedFilename()).toBe("app_invoices_ddl.sql");

  await expect(page.getByTestId("table-management-grid").getByRole("button", { name: /^操作: / })).toHaveCount(0);
  await clickObjectDetailAction(page, "table-management-detail-actions", "削除");
  const dropTableDialog = page.getByRole("dialog", { name: "DROP TABLE の確認" });
  await expect(dropTableDialog).toBeVisible();
  await mainScroller(page).evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expectAppDialogOverlayCoversViewport(page);
  await expectOnlyConfirmationFieldHasNoLeftAccent(dropTableDialog);
  await dropTableDialog.getByLabel("実行確認語").fill("APP.INVOICES");
  await expect(dropTableDialog.getByText("確認済み", { exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropTablePayload?.confirmation).toBe("APP.INVOICES");
  expect(api.dropTablePayload?.table_name).toBe("INVOICES");
  expect(api.dropTablePayload?.owner).toBe("APP");
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
  await expect(importPanel.getByText(/必須入力項目です。/)).toBeVisible();
  const importExecuteButton = importPanel.getByRole("button", { name: "取込を実行" });
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(importPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(importExecuteButton).toBeDisabled();
  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  await importPanel.getByLabel("Sheet 名").fill("Sheet1");
  await expect(importPanel.locator('label[for="table-import-table-name"] span[aria-hidden="true"]')).toHaveText("*");
  await expect(importPanel.locator('label[for="table-import-sheet-name"] span[aria-hidden="true"]')).toHaveText("*");
  const importFileClearButton = importPanel.getByRole("button", { name: "取込ファイルをクリア" });
  const importFileInput = importPanel.getByTestId("table-import-file-field-input");
  await expect(importFileClearButton).toBeDisabled();
  await expect(importFileInput).toHaveAttribute("accept", ".csv,.xlsx,.xls");
  await expect(importFileInput).toHaveAttribute("required", "");
  await expect(importFileInput).toHaveAttribute("aria-required", "true");
  await expect(importPanel.locator("#table-import-table-name")).toHaveAttribute("required", "");
  await expect(importPanel.locator("#table-import-sheet-name")).toHaveAttribute("required", "");
  await expect(importPanel.getByText(".CSV / .XLSX / .XLS", { exact: true })).toHaveCount(1);
  await importPanel.getByLabel("CSV/XLSX/XLS 選択", { exact: true }).setInputFiles({
    name: "orders.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("ORDER_ID,ORDER_NAME\n1,青山商事\n"),
  });
  await expect(importPanel.getByText("選択中: orders.csv")).toBeVisible();
  await expect(importPanel.locator("#table-import-sheet-name")).not.toHaveAttribute("required", "");
  await expect(importFileClearButton).toBeEnabled();
  await importFileClearButton.click();
  await expect(importPanel.getByText("ドラッグ＆ドロップまたは選択")).toBeVisible();
  await expect(importFileClearButton).toBeDisabled();
  await importPanel.getByLabel("CSV/XLSX/XLS 選択").setInputFiles({
    name: "orders.XLS",
    mimeType: "application/vnd.ms-excel",
    buffer: Buffer.from("legacy-xls"),
  });
  await expect(importPanel.getByText("選択中: orders.XLS")).toBeVisible();
  await expect(importPanel.locator("#table-import-sheet-name")).toHaveAttribute("required", "");
  await dropFiles(page, importPanel.getByTestId("table-import-file-field-dropzone"), [
    {
      name: "orders.exe",
      type: "application/x-msdownload",
      content: "unsupported",
    },
  ]);
  await expect(importPanel.getByRole("alert")).toContainText(
    ".CSV / .XLSX / .XLS ファイルを選択してください"
  );
  await expect(importPanel.getByText("選択中: orders.XLS")).toBeVisible();
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
  expect(api.importTabularPayload?.filename).toBe("orders.XLS");
  expect(api.importTabularPayload?.mode).toBe("create");
  expect(api.importTabularPayload?.confirmation).toBe("ADMIN_EXECUTE");

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toHaveCount(0);
  await page.getByRole("tab", { name: "DDL" }).click();
  await expect(page.getByText('CREATE TABLE "INVOICES"')).toBeVisible();
  await expectContentActionsRightAligned(page.getByTestId("table-management-ddl-actions"));
  await expectNoHorizontalScroll(page);

  await page.goto("/comment-management");
  await expect(page.getByRole("heading", { name: "コメント管理" })).toBeVisible();
  await expect(page.getByTestId("comment-management-steps")).toBeVisible();
  await page.getByRole("checkbox", { name: /INVOICES/ }).check();
  await page.getByRole("button", { name: "情報を取得" }).click();
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.INVOICES/);
  await page.getByLabel("サンプル件数").fill("10");
  await page.getByRole("button", { name: "SQL 生成" }).click();
  await expect.poll(() => api.metadataSamplesPayload?.sample_limit).toBe(10);
  await expect.poll(() => api.commentGeneratePayload?.sample_text).toContain(
    "CUSTOMER_NAME: 青山商事, 鈴木商店"
  );
  const commentExecutePanel = page.locator("#comment-management-panel-execute");
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /COMMENT ON COLUMN "APP"\."INVOICES"\."TOTAL_AMOUNT" IS '税込請求金額';/
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
  await expect(page.getByLabel("構造情報")).toHaveValue(/OBJECT: APP\.INVOICES/);
  await page.getByRole("button", { name: "SQL 生成" }).click();
  const annotationExecutePanel = page.locator("#annotation-management-panel-execute");
  await expect(page.getByLabel("SQL(セミコロン区切りで複数文を入力可能)")).toHaveValue(
    /ALTER TABLE "APP"\."INVOICES" MODIFY/
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
  await page.getByRole("button", { name: "APP.V_EMP_DEPT を表示" }).click();
  const viewColumnsTab = page.getByRole("tab", { name: "列情報" });
  const viewDdlTab = page.getByRole("tab", { name: "DDL" });
  await expect(viewColumnsTab).toHaveAttribute("aria-selected", "true");
  await viewColumnsTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(viewDdlTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText('CREATE OR REPLACE VIEW "V_EMP_DEPT"')).toBeVisible();
  await expectContentActionsRightAligned(page.getByTestId("view-management-ddl-actions"));

  await clickPageHeaderAction(page, "view-management-actions", "JOIN/WHERE 条件抽出");
  const stepIndicatorBox = await page.getByTestId("view-join-where-steps").boundingBox();
  const selectedViewBox = await page.getByTestId("view-join-where-selected-view").boundingBox();
  const advancedSettings = page.getByTestId("view-join-where-advanced-settings");
  const advancedSettingsBox = await advancedSettings.boundingBox();
  const joinWhereActionsBox = await page.getByTestId("view-join-where-actions").boundingBox();
  expect(stepIndicatorBox).not.toBeNull();
  expect(selectedViewBox).not.toBeNull();
  expect(advancedSettingsBox).not.toBeNull();
  expect(joinWhereActionsBox).not.toBeNull();
  expect(stepIndicatorBox!.y).toBeLessThan(advancedSettingsBox!.y);
  expect(selectedViewBox!.y).toBeLessThan(advancedSettingsBox!.y);
  expect(advancedSettingsBox!.y).toBeLessThan(joinWhereActionsBox!.y);
  await expect(page.getByTestId("view-join-where-actions")).toContainText("DDL 取得済み");
  await expect(page.getByText("提示詞プロファイル")).toHaveCount(0);
  await expect(page.getByText("標準: JOIN/WHERE のみ抽出")).toHaveCount(0);
  await expect(advancedSettings.getByText("解析設定")).toBeVisible();
  await expect(advancedSettings.getByText("詳細解析: SQL構造も解析")).toBeVisible();
  await expect(advancedSettings.getByRole("list", { name: "出力範囲" })).toContainText("JOIN");
  await expect(advancedSettings.getByRole("list", { name: "出力範囲" })).toContainText("WHERE");
  await expect(advancedSettings.getByRole("list", { name: "出力範囲" })).toContainText("SQL構造");
  await expect(page.getByRole("combobox", { name: "抽出モード" })).toHaveCount(0);
  await page.getByRole("button", { name: "AI で抽出" }).click();
  await expect.poll(() => api.extractJoinWherePayload?.prompt_profile).toBe("sql_structure");
  await expect(page.getByLabel("結合条件 (JOIN)")).toHaveValue(/EMPLOYEE.*DEPARTMENT/);
  await expect(page.getByLabel("抽出条件 (WHERE)")).toHaveValue("EMPLOYEE(e).STATUS = 'A'");
  await page.getByText("SQL構造解析結果").click();
  await expect(page.getByText("## SQL構造分析")).toBeVisible();

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.getByTestId("view-management-grid").getByRole("button", { name: /^操作: / })).toHaveCount(0);
  await clickObjectDetailAction(page, "view-management-detail-actions", "削除");
  const dropViewDialog = page.getByRole("dialog", { name: "DROP VIEW の確認" });
  await expect(dropViewDialog).toBeVisible();
  await mainScroller(page).evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });
  await expectAppDialogOverlayCoversViewport(page);
  await expectOnlyConfirmationFieldHasNoLeftAccent(dropViewDialog);
  await expect(page.getByRole("button", { name: "Drop 実行" })).toBeDisabled();
  await page.getByLabel("実行確認語").fill("APP.V_EMP_DEPT");
  await page.getByRole("button", { name: "Drop 実行" }).click();
  await expect.poll(() => api.dropViewPayload?.confirmation).toBe("APP.V_EMP_DEPT");
  expect(api.dropViewPayload?.view_name).toBe("V_EMP_DEPT");
  expect(api.dropViewPayload?.owner).toBe("APP");

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

test("table and view management show DROP failures in the confirmation dialog", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const objectType = new URL(route.request().url()).searchParams.get("type") ?? "table";
    const items =
      objectType === "view"
        ? [
            {
              name: "V_EMP_DEPT",
              owner: "APP",
              qualified_name: "APP.V_EMP_DEPT",
              object_type: "view",
              row_count: null,
              comment: "社員と部署",
            },
          ]
        : [
            {
              name: "lower",
              owner: "APP",
              qualified_name: 'APP."lower"',
              object_type: "table",
              row_count: 2,
              comment: "小文字テーブル",
            },
          ];
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: objectType === "view" ? 0 : items.length,
      view_count: objectType === "view" ? items.length : 0,
      next_cursor: null,
      refreshed_at: schemaCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/%22lower%22**", (route) =>
    fulfillJson(route, {
      name: "lower",
      owner: "APP",
      qualified_name: 'APP."lower"',
      object_type: "table",
      row_count: 2,
      comment: "小文字テーブル",
      columns: schemaCatalog.tables[0].columns,
      ddl: 'CREATE TABLE "APP"."lower" ("CUSTOMER_NAME" VARCHAR2(120), "TOTAL_AMOUNT" NUMBER);',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/drop-table", (route) => {
    api.dropTablePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      executed: false,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: "error",
          sql: 'DROP TABLE "APP"."lower" PURGE',
          row_count: null,
          message: "",
          elapsed_ms: 0,
          error_message: "ORA-02449: 表の一意/主キーが外部キーに参照されています。",
        },
      ],
      committed: false,
      rolled_back: true,
      warnings: ["ORA-02449: 表の一意/主キーが外部キーに参照されています。"],
      timing,
    });
  });

  await page.goto("/table-management");
  await expect(page.getByTestId("table-management-grid")).toBeVisible();
  await expect(page.getByTestId("table-management-grid")).toContainText('APP."lower"');
  await page.getByRole("button", { name: 'APP."lower" を表示' }).click();
  await clickObjectDetailAction(page, "table-management-detail-actions", "削除");
  const dropTableDialog = page.getByRole("dialog", { name: "DROP TABLE の確認" });
  await dropTableDialog.getByLabel("実行確認語").fill('APP."lower"');
  await dropTableDialog.getByRole("button", { name: "Drop 実行" }).click();

  await expect.poll(() => api.dropTablePayload?.confirmation).toBe('APP."lower"');
  expect(api.dropTablePayload?.table_name).toBe('"lower"');
  expect(api.dropTablePayload?.owner).toBe("APP");
  await expect(dropTableDialog.getByText(/ORA-02449/)).toBeVisible();
  await expect(dropTableDialog).toBeVisible();

  await page.route("**/api/nl2sql/db-admin/drop-view", (route) => {
    api.dropViewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      executed: false,
      runtime: "oracle",
      select_result: null,
      statements: [
        {
          index: 1,
          statement_type: "DROP",
          status: "error",
          sql: 'DROP VIEW "APP"."V_EMP_DEPT"',
          row_count: null,
          message: "",
          elapsed_ms: 0,
          error_message: "ORA-04043: オブジェクト APP.V_EMP_DEPT は存在しません。",
        },
      ],
      committed: false,
      rolled_back: true,
      warnings: [],
      timing,
    });
  });

  await page.goto("/view-management");
  await expect(page.getByTestId("view-management-grid")).toBeVisible();
  await page.getByRole("button", { name: "APP.V_EMP_DEPT を表示" }).click();
  await clickObjectDetailAction(page, "view-management-detail-actions", "削除");
  const dropViewDialog = page.getByRole("dialog", { name: "DROP VIEW の確認" });
  await dropViewDialog.getByLabel("実行確認語").fill("APP.V_EMP_DEPT");
  await dropViewDialog.getByRole("button", { name: "Drop 実行" }).click();

  await expect.poll(() => api.dropViewPayload?.confirmation).toBe("APP.V_EMP_DEPT");
  await expect(dropViewDialog.getByText(/ORA-04043/)).toBeVisible();
  await expect(dropViewDialog).toBeVisible();
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
  const summary = details.locator("summary");
  await expect(summary).toContainText("詳細ログ");
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

test("legacy model-learning URL opens Select AI settings and preserves asset refresh", async ({ page }) => {
  const api = await mockNl2SqlApi(page);
  let legacySyncPhase:
    | "queued"
    | "syncing_oracle_profile"
    | "rebuilding_agent_assets"
    | "verifying"
    | "succeeded" = "queued";
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
  await page.route("**/api/nl2sql/oracle-sync-jobs/legacy-profile-sync", (route) => {
    const succeeded = legacySyncPhase === "succeeded";
    const oracleCompleted =
      legacySyncPhase === "rebuilding_agent_assets" ||
      legacySyncPhase === "verifying" ||
      succeeded;
    return fulfillJson(route, {
      job_id: "legacy-profile-sync",
      profile_id: "default",
      profile_etag: "etag-default",
      status: succeeded ? "succeeded" : legacySyncPhase === "queued" ? "queued" : "running",
      phase: legacySyncPhase,
      rebuild_agent_assets: true,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-23T00:00:00Z",
      started_at: legacySyncPhase === "queued" ? null : "2026-07-23T00:00:00Z",
      finished_at: succeeded ? "2026-07-23T00:00:01Z" : null,
      oracle_result: oracleCompleted
        ? {
            runtime: "oracle",
            executed: true,
            status: "saved",
            profile_name: "NL2SQL_DEFAULT_PROFILE",
            original_name: "",
            ddl: [],
            profile: null,
            warnings: [],
            engine_meta: {},
          }
        : null,
      agent_result: succeeded
        ? {
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
          }
        : null,
    });
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/settings/nl2sql-model");
  await expect(page).toHaveURL(/\/profiles\?profile=[^#]+#profile-select-ai$/);
  await expect(page.locator("#profile-select-ai")).toBeVisible();
  await expect(page.getByText("語彙・few-shot", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("few-shot 例")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await page.getByLabel("名称").fill("DEFAULT_PROFILE");
  await page
    .locator("#profile-select-ai-additional-instructions")
    .fill("粗利は INVOICES.PROFIT を使う。");
  // ADMIN_EXECUTE ゲート + Agent アセット再構築チェックを付けて保存 1 クリックに集約。
  await page.getByRole("checkbox", { name: /Select AI Agent アセット/ }).check();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  const profileSaveButton = page.getByRole("button", { name: "保存", exact: true });
  await profileSaveButton.click();
  expect(api.profilePatchPayload?.name).toBe("DEFAULT_PROFILE");
  expect(api.profilePatchPayload).not.toHaveProperty("glossary");
  expect(api.profilePatchPayload).not.toHaveProperty("few_shot_examples");
  expect(api.profilePatchPayload?.select_ai_config).toMatchObject({
    additional_instructions: "粗利は INVOICES.PROFIT を使う。",
  });
  const saveProgress = page.getByTestId("profile-save-progress");
  await expect(saveProgress).toHaveAttribute("data-job-status", "queued");
  await expect(page.getByTestId("profile-save-step-save_profile")).toHaveAttribute(
    "data-step-status",
    "done"
  );
  await expect(page.getByTestId("profile-save-step-sync_oracle_profile")).toHaveAttribute(
    "data-step-status",
    "pending"
  );

  legacySyncPhase = "syncing_oracle_profile";
  await expect(saveProgress).toHaveAttribute("data-job-status", "running");
  await expect(saveProgress).toHaveAttribute("role", "status");
  await expect(page.getByTestId("profile-save-step-sync_oracle_profile")).toHaveAttribute(
    "data-step-status",
    "running"
  );

  legacySyncPhase = "rebuilding_agent_assets";
  await expect(page.getByTestId("profile-save-step-rebuild_agent_assets")).toHaveAttribute(
    "data-step-status",
    "running"
  );

  legacySyncPhase = "verifying";
  await expect(page.getByTestId("profile-save-step-verify")).toHaveAttribute(
    "data-step-status",
    "running"
  );

  legacySyncPhase = "succeeded";
  await expect(page.getByText("NL2SQL_DEFAULT_AGENT_PROFILE")).toBeVisible();
  const saveResultRegion = page.getByTestId("profile-save-result-region");
  const agentAssetStatus = page.getByTestId("profile-asset-status-select_ai_agent");
  const resultFollowsConfirmation = () =>
    saveResultRegion.evaluate((region) => {
      const previous = region.previousElementSibling;
      if (!(region instanceof HTMLElement) || !(previous instanceof HTMLElement)) return false;
      return (
        previous.getAttribute("data-testid") === "execution-confirmation-field"
        && region.offsetTop >= previous.offsetTop + previous.offsetHeight - 1
      );
    });
  await expect(saveResultRegion).toBeVisible();
  await expect.poll(resultFollowsConfirmation).toBe(true);
  await expect(saveProgress).toHaveAttribute("data-job-status", "succeeded");
  await expect(saveProgress).not.toHaveAttribute("role", "status");
  await expect(saveProgress.getByRole("timer")).toHaveAccessibleName(/処理時間/u);
  await expect(agentAssetStatus).toBeVisible();
  const saveResultOrder = await saveResultRegion.evaluate((region) =>
    Array.from(
      region.querySelectorAll(
        '[data-testid="profile-save-progress"], [data-testid="profile-asset-status-select_ai_agent"]'
      )
    ).map((node) => node.getAttribute("data-testid"))
  );
  expect(saveResultOrder).toEqual([
    "profile-save-progress",
    "profile-asset-status-select_ai_agent",
  ]);
  await expectNoHorizontalScroll(page);

  await page.setViewportSize({ width: 375, height: 900 });
  await expect.poll(resultFollowsConfirmation).toBe(true);
  await expectNoHorizontalScroll(page);
  await page.goto("/settings/nl2sql-model");
  await expect(page).toHaveURL(/\/profiles\?profile=[^#]+#profile-select-ai$/);
  await expect(page.locator("#profile-select-ai")).toBeVisible();
  await expect(page.getByLabel("few-shot 例")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /モデル学習/ })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("profile sync keeps result visible while DB Profile targeted refresh asks for full refresh", async ({
  page,
}) => {
  const api = await mockNl2SqlApi(page);
  let targetedPolls = 0;
  let manualRefreshSubmits = 0;

  await page.route("**/api/nl2sql/profiles/default/oracle-sync-jobs", (route) =>
    fulfillJson(route, {
      job_id: "profile-sync-with-db-profile-refresh",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "queued",
      phase: "queued",
      rebuild_agent_assets: true,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-08-13T00:00:00Z",
    })
  );
  await page.route("**/api/nl2sql/oracle-sync-jobs/profile-sync-with-db-profile-refresh", (route) =>
    fulfillJson(route, {
      job_id: "profile-sync-with-db-profile-refresh",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "succeeded",
      phase: "succeeded",
      rebuild_agent_assets: true,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-08-13T00:00:00Z",
      finished_at: "2026-08-13T00:00:01Z",
      oracle_result: {
        runtime: "oracle",
        executed: true,
        status: "saved",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        original_name: "",
        ddl: [],
        profile: null,
        warnings: [],
        engine_meta: {},
        profile_list_refresh_job_id: "targeted-db-profile-refresh",
        profile_list_refresh_required: false,
        profile_list_refresh_reason_code: "",
      },
      agent_result: {
        engine: "select_ai_agent",
        refreshed: true,
        status: "ready",
        refreshed_at: "2026-08-13T00:00:01Z",
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
  await page.route("**/api/nl2sql/select-ai/db-profiles/refresh-jobs", (route) => {
    manualRefreshSubmits += 1;
    return fulfillJson(route, {
      job_id: "manual-db-profile-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-08-13T00:00:02Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });
  await page.route("**/api/nl2sql/select-ai/db-profile-refresh-jobs/*", (route) => {
    const url = route.request().url();
    if (url.endsWith("/targeted-db-profile-refresh")) {
      targetedPolls += 1;
      const stillRunning = targetedPolls <= 2;
      return fulfillJson(route, {
        job_id: "targeted-db-profile-refresh",
        status: stillRunning ? "running" : "error",
        mode: "targeted",
        source: "profile_sync",
        target_profiles: [
          { profile_name: "NL2SQL_DEFAULT_PROFILE", expected_state: "present" },
        ],
        requires_full_refresh: !stillRunning,
        phase: "fetching",
        created_at: "2026-08-13T00:00:01Z",
        started_at: "2026-08-13T00:00:01Z",
        finished_at: stillRunning ? null : "2026-08-13T00:00:02Z",
        total_profiles: 1,
        processed_profiles: stillRunning ? 1 : 0,
        scanned_profiles: 1,
        changed_profiles: 0,
        deleted_profiles: 0,
        error_code: stillRunning ? "" : "profile_list_refresh_full_required",
        error_message: stillRunning
          ? ""
          : "DB Profile 一覧の差分同期で不整合を検出しました。",
      });
    }
    return fulfillJson(route, {
      job_id: "manual-db-profile-refresh",
      status: "done",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "done",
      created_at: "2026-08-13T00:00:02Z",
      started_at: "2026-08-13T00:00:02Z",
      finished_at: "2026-08-13T00:00:03Z",
      total_profiles: 1,
      processed_profiles: 1,
      scanned_profiles: 1,
      changed_profiles: 1,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/profiles?profile=default");
  await page.getByLabel("名称").fill("DEFAULT_PROFILE");
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("checkbox", { name: /Select AI Agent アセット/ }).check();
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect.poll(() => api.profilePatchPayload?.name).toBe("DEFAULT_PROFILE");
  await expect(page.getByText("NL2SQL_DEFAULT_AGENT_PROFILE")).toBeVisible();
  await expect(page.getByTestId("profile-management-workspace-processing")).toContainText(
    "DB Profile 一覧の差分を同期しています"
  );
  await expect(page.locator("main").getByText(/DB Profile 一覧の差分同期で不整合/))
    .toBeVisible();

  await page
    .locator("main")
    .getByRole("button", { name: "DB Profile 一覧を再取得", exact: true })
    .click();
  await expect.poll(() => manualRefreshSubmits).toBe(1);
});

test("全 NL2SQL ルートのページヘッダーは 1440px / 375px で横方向に溢れない", async ({
  page,
}) => {
  test.setTimeout(120_000);
  // 未モック API を実バックエンド(proxy)へ漏らさないための fallback。実バックエンドが
  // 起動していると 401 が返り、AuthProvider がログイン画面へ戻してしまう。
  // Playwright の route は後勝ちなので、この後に登録する具体 mock が優先される。
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ data: null, error_messages: ["e2e unmocked"], warning_messages: [] }),
    })
  );
  await mockDatabaseGateReady(page);
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
