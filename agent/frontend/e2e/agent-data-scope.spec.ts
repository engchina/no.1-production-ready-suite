import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { chooseSelectFieldOption, expectSelectFieldValue } from "./fixtures/select-field";

// #1378: 業務 Agent のデータの範囲。NL2SQL の業務プロファイル・RAG の検索・回答プロファイルを複数選び、
// 既定を決めて保存する。候補の読み込み中・失敗・空と、実行の step の範囲の記録を確かめる。

const AGENT_ID = "agent-1378";

function seedAgent(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.agents.push({
    id: AGENT_ID,
    name: "売上分析の Agent",
    description: "売上の質問に答える",
    instructions: "売上を集計する",
    skill_ids: [],
    model_id: "",
    data_scopes: {},
    migration_required: false,
    tool_names: [],
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [],
    published_version: null,
    unpublished_changes: true,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...overrides,
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function openAgent(page: Page) {
  await page.goto(`/agents?id=${AGENT_ID}`);
  await expect(page.getByRole("heading", { name: "売上分析の Agent", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "データの範囲", level: 2 })).toBeVisible();
}

/** 検索できる複数選択で候補を選ぶ（一覧は選んでも閉じない）。 */
async function pickProfiles(page: Page, fieldId: string, names: string[]) {
  const combobox = page.locator(`#${fieldId}`);
  await combobox.click();
  for (const name of names) {
    await page.getByRole("option", { name: new RegExp(name) }).click();
  }
  await page.keyboard.press("Escape");
}

test("業務プロファイルを複数選んで既定を決め、検索・回答プロファイルを 1 つに固定して保存できる", async ({
  page,
  mockApi,
}) => {
  seedAgent(mockApi);
  await openAgent(page);
  const nl2sql = page.getByTestId("agent-data-scope-nl2sql");
  const rag = page.getByTestId("agent-data-scope-rag");
  // 何も選ばないうちは「範囲なし」（今までどおり利用者が使えるすべてから選ぶ）。
  await expect(nl2sql).toContainText("範囲なし");
  await expect(nl2sql).toContainText("選ばないと、実行する利用者が使えるすべてのプロファイルから");
  await expectNoHorizontalOverflow(page);

  await pickProfiles(page, `${AGENT_ID}-data-scope-nl2sql`, ["売上", "原価"]);
  await expect(nl2sql).toContainText("2 件から選ぶ");
  const defaultField = page.getByTestId(`${AGENT_ID}-data-scope-nl2sql-default`);
  await expectSelectFieldValue(defaultField, "profile-sales");
  await chooseSelectFieldOption(defaultField, "profile-cost");

  await pickProfiles(page, `${AGENT_ID}-data-scope-rag`, ["営業の検索・回答プロファイル"]);
  await expect(rag).toContainText("1 件に固定");
  await expect(rag).toContainText("このプロファイルだけを使います");
  // 1 つだけなら既定の選択欄は出さない（それが既定）。
  await expect(page.getByTestId(`${AGENT_ID}-data-scope-rag-default`)).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("Agent を保存しました")).toBeVisible();
  const saved = mockApi.lastRequest("PATCH", `/api/agents/${AGENT_ID}`);
  expect(saved?.body).toMatchObject({
    data_scopes: {
      nl2sql: { profile_ids: ["profile-cost", "profile-sales"], default_profile_id: "profile-cost" },
      rag: { profile_ids: ["bv-sales"], default_profile_id: "bv-sales" },
    },
  });
  // 保存した内容が基準になり、変更を破棄は押せない。
  await expect(page.getByRole("button", { name: "変更を破棄" })).toBeDisabled();
});

test("候補の読み込み中は経過時間つきの表示を出し、読み込んだら選べる", async ({ page, mockApi }) => {
  seedAgent(mockApi);
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/agent-data-scopes/nl2sql/candidates", async (route) => {
    await gate;
    await route.fallback();
  });
  await openAgent(page);

  const loading = page.getByTestId("agent-data-scope-nl2sql-loading");
  await expect(loading).toContainText("業務プロファイルを読み込んでいます");
  // 読み込みの遅い接続があっても、ほかの接続は選べる。
  await expect(page.locator(`#${AGENT_ID}-data-scope-rag`)).toBeVisible();
  await expectNoHorizontalOverflow(page);

  release();
  await expect(loading).toHaveCount(0);
  await expect(page.locator(`#${AGENT_ID}-data-scope-nl2sql`)).toBeVisible();
});

test("候補を読み込めないときは理由と再試行を出し、範囲にある ID は外せる", async ({ page, mockApi }) => {
  seedAgent(mockApi, {
    data_scopes: { nl2sql: { profile_ids: ["profile-sales"], default_profile_id: "profile-sales" } },
  });
  mockApi.state.dataScopeCandidateErrors.nl2sql = {
    status: 409,
    message: "MCP 接続「データ問い合わせ（NL2SQL）」の URL が設定されていないため、プロファイルを選べません。",
  };
  await openAgent(page);

  const error = page.getByTestId("agent-data-scope-nl2sql-error");
  await expect(error).toContainText("URL が設定されていないため、プロファイルを選べません");
  await expectNoHorizontalOverflow(page);
  // 選んである ID は chip で出し、外せる。
  const nl2sql = page.getByTestId("agent-data-scope-nl2sql");
  await expect(nl2sql.getByRole("list", { name: /選択中/ })).toContainText("profile-sales");

  // 設定を直してから再試行すると候補が出る。
  delete mockApi.state.dataScopeCandidateErrors.nl2sql;
  await error.getByRole("button", { name: "再試行" }).click();
  await expect(error).toHaveCount(0);
  await expect(nl2sql.getByRole("list", { name: /選択中/ })).toContainText("売上");
});

test("使えるプロファイルが無いときは空の候補を出し、使えない ID は印を付ける", async ({ page, mockApi }) => {
  seedAgent(mockApi, {
    data_scopes: { rag: { profile_ids: ["bv-gone"], default_profile_id: "bv-gone" } },
  });
  mockApi.state.dataScopeCandidates.rag = [];
  await openAgent(page);

  const rag = page.getByTestId("agent-data-scope-rag");
  await expect(page.getByTestId("agent-data-scope-rag-missing")).toContainText("bv-gone");
  await expect(rag.getByRole("list", { name: /選択中/ })).toContainText("使えません");
  await page.locator(`#${AGENT_ID}-data-scope-rag`).click();
  await expect(page.getByText("あなたが使えるプロファイルがありません")).toBeVisible();
  await page.keyboard.press("Escape");
  await expectNoHorizontalOverflow(page);

  // 使えない ID を外して保存すると、範囲なしに戻る。
  await rag.getByRole("button", { name: /bv-gone/ }).click();
  await expect(rag).toContainText("範囲なし");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("Agent を保存しました")).toBeVisible();
  expect(mockApi.lastRequest("PATCH", `/api/agents/${AGENT_ID}`)?.body).toMatchObject({ data_scopes: {} });
});

test("実行の経過のステップに、使ったプロファイルと範囲の補完・拒否が出る", async ({ page, mockApi }) => {
  const step = (id: string, status: string, dataScope: Record<string, unknown>, error?: string) => ({
    id,
    run_id: "run-1378",
    kind: "tool",
    status,
    tool_call: { name: "nl2sql__nl2sql_query", arguments: { question: "売上" }, data_scope: dataScope },
    tool_result: {
      name: "nl2sql__nl2sql_query",
      success: !error,
      output: error ? null : { job_id: "job-1", status: "done", columns: [], rows: [] },
      error: error ?? null,
      error_code: error ? "agent_data_scope_violation" : null,
      error_details: {},
      started_at: MOCK_NOW,
      completed_at: MOCK_NOW,
      duration_ms: 10,
      policy_decision: "allow",
      approval_required: false,
      guardrail_warnings: [],
      audit_metadata: {},
    },
    started_at: MOCK_NOW,
    completed_at: MOCK_NOW,
  });
  const allowed = ["profile-sales", "profile-cost"];
  mockApi.state.runs.push({
    id: "run-1378",
    goal: "人事の数は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [
      step(
        "step-rejected",
        "failed",
        {
          connection: "nl2sql",
          allowed_profile_ids: allowed,
          argument: "profile_id",
          requested_profile_id: "profile-hr",
          action: "rejected",
        },
        "業務プロファイル「profile-hr」はこの業務 Agent では使えません。"
      ),
      step("step-filled", "completed", {
        connection: "nl2sql",
        allowed_profile_ids: allowed,
        argument: "profile_id",
        profile_id: "profile-cost",
        action: "filled",
      }),
    ],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: `thread_${"c".repeat(32)}`,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  await page.goto("/runs?id=run-1378");
  await page.getByRole("tab", { name: "実行の経過", exact: true }).click();

  await expect(page.getByTestId("run-step-data-scope-step-rejected")).toHaveText(
    "profile-hr は範囲外のため実行していません"
  );
  await expect(page.getByTestId("run-step-data-scope-step-filled")).toHaveText("profile-cost を範囲から補完");
  await expectNoHorizontalOverflow(page);
});
