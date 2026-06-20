import { expect, test, type Page } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function envelope(data: unknown) {
  return { json: { data, error_messages: [], warning_messages: [] } };
}

const generatedAllowed = {
  question: "部門ごとの平均給与",
  profile_name: "N2SPR_HR",
  generation_backend: "select_ai",
  router: {
    profile_selected: null,
    generation_backend: "select_ai_agent",
    complexity_score: 2,
    matched_signals: ["aggregate", "grouping"],
    reason: "router_off",
  },
  generated_sql: "SELECT department_name, AVG(salary) FROM employee GROUP BY department_name",
  narration: null,
  guardrail: {
    allowed: true,
    policy: "read_only",
    statement_type: "SELECT",
    violations: [],
    semantic_verify_required: false,
    max_rows: null,
    run_role: null,
  },
};

const executedResult = {
  sql: "SELECT department_name, AVG(salary) FROM employee GROUP BY department_name",
  executed: true,
  blocked_reason: null,
  guardrail: generatedAllowed.guardrail,
  result: {
    columns: ["DEPARTMENT_NAME", "AVG_SALARY"],
    rows: [
      ["開発", 610000],
      ["営業", 540000],
    ],
    row_count: 2,
    truncated: false,
  },
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await mockDatabaseReady(page);
});

test("NL2SQL コンソールは生成→確認→実行の 2 段ゲートで結果を表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("**/api/nl2sql/generate", (route) =>
    route.fulfill(envelope(generatedAllowed))
  );
  await page.route("**/api/nl2sql/execute", (route) => route.fulfill(envelope(executedResult)));

  await page.goto("/nl2sql");
  await expect(page.getByRole("heading", { name: "NL2SQL コンソール" }).first()).toBeVisible();

  // 生成フェーズ
  await page.getByLabel("質問(自然言語)").fill("部門ごとの平均給与を高い順に教えて");
  await page.getByRole("button", { name: "SQL 生成" }).click();

  // 生成 SQL とガードレール通過バッジ
  const sqlBox = page.getByLabel("生成された SQL(編集して承認できます)");
  await expect(sqlBox).toHaveValue(/SELECT department_name/);
  await expect(page.getByText("ガードレール通過")).toBeVisible();

  // 実行フェーズ(人手承認後)
  await page.getByRole("button", { name: "確認して実行" }).click();
  await expect(page.getByRole("heading", { name: "実行結果" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "DEPARTMENT_NAME" })).toBeVisible();
  await expect(page.getByText("開発")).toBeVisible();
  await expect(page.getByText("2 行")).toBeVisible();

  await expectNoPageOverflow(page);
});

test("NL2SQL コンソールはブロックされた SQL を実行せず違反理由を表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });

  const blockedGenerate = {
    ...generatedAllowed,
    generated_sql: "DROP TABLE employee",
    guardrail: {
      ...generatedAllowed.guardrail,
      allowed: false,
      statement_type: "DDL",
      violations: ["non_select_statement:DDL", "ddl_keyword_present"],
    },
  };
  const blockedExecute = {
    sql: "DROP TABLE employee",
    executed: false,
    blocked_reason: "non_select_statement:DDL;ddl_keyword_present",
    guardrail: blockedGenerate.guardrail,
    result: null,
  };
  await page.route("**/api/nl2sql/generate", (route) => route.fulfill(envelope(blockedGenerate)));
  await page.route("**/api/nl2sql/execute", (route) => route.fulfill(envelope(blockedExecute)));

  await page.goto("/nl2sql");
  await page.getByLabel("質問(自然言語)").fill("全部消して");
  await page.getByRole("button", { name: "SQL 生成" }).click();

  await expect(page.getByText("ガードレールによりブロック")).toBeVisible();
  await expect(page.getByText("ddl_keyword_present")).toBeVisible();

  await page.getByRole("button", { name: "確認して実行" }).click();
  await expect(page.getByText("この SQL は実行できません。")).toBeVisible();
});
