import { expect, test, type Page, type Route } from "@playwright/test";

const now = "2026-06-28T00:00:00Z";

const agent = {
  id: "default",
  name: "汎用業務 Agent",
  description: "業務調査を行う",
  instructions: "根拠を示して回答する。",
  skill_ids: ["business_rag_research"],
  migration_required: false,
  enabled: true,
  source: "builtin",
  created_at: now,
  updated_at: now,
};

const skill = {
  id: "business_rag_research",
  name: "業務 RAG 調査",
  description: "根拠付き情報を検索する",
  instructions: "検索して根拠を返す",
  mcp_requirements: [
    { server_id: "control-plane", tool_names: ["external_rag_search"] },
  ],
  resource_ids: [],
  tool_calls: [],
  enabled: true,
  tags: ["rag"],
  source: "builtin",
  created_at: now,
  updated_at: now,
};

const runtime = {
  id: "hermes-default",
  name: "Hermes",
  kind: "hermes",
  base_url: "http://runtime-hermes:8642",
  auth_secret_ref: "HERMES_API_SERVER_KEY",
  managed_service_id: "runtime-hermes",
  capabilities: {
    stream_events: true,
    cancel: false,
    artifacts: false,
    approvals: false,
    skill_sync: true,
    mcp_sync: true,
  },
  enabled: true,
  status: "degraded",
  created_at: now,
  updated_at: now,
};

const binding = {
  id: "binding-default-hermes",
  agent_id: "default",
  runtime_id: "hermes-default",
  native_agent_ref: "default-profile",
  is_default: true,
  enabled: true,
  policy: {},
  sync_status: "ready",
  sync_error: null,
  created_at: now,
  updated_at: now,
};

function api(data: unknown) {
  return JSON.stringify({ data, error_messages: [], warning_messages: [] });
}

async function installControlPlaneApi(page: Page, options?: { unbound?: boolean }) {
  let runs: Record<string, unknown>[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const respond = (data: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: api(data) });

    if (path === "/api/agents") {
      if (method === "POST") return respond(agent);
      return respond({ agents: [agent] });
    }
    if (path.startsWith("/api/agents/")) return respond(agent);
    if (path === "/api/skills") return respond({ skills: [skill], metadata: {} });
    if (path === "/api/runtimes") return respond({ runtimes: [runtime] });
    if (path.endsWith("/status") && path.startsWith("/api/runtimes/")) {
      return respond({ ...runtime, status: "running" });
    }
    if (path.startsWith("/api/runtimes/services/")) return respond({ ok: true });
    if (path === "/api/runtime-bindings") {
      if (method === "POST") return respond(binding);
      return respond({ bindings: options?.unbound ? [] : [binding] });
    }
    if (path.startsWith("/api/runtime-bindings/")) return respond(binding);
    if (path === "/api/runs" && method === "POST") {
      const body = route.request().postDataJSON() as { goal: string };
      const run = {
        id: "run-control-plane-e2e",
        goal: body.goal,
        agent_id: "default",
        runtime_id: "hermes-default",
        binding_id: binding.id,
        external_run_id: "hermes-run-1",
        external_cursor: null,
        runtime_capabilities: runtime.capabilities,
        status: "running",
        steps: [],
        events: [
          {
            id: "event-1",
            run_id: "run-control-plane-e2e",
            type: "runtime.submitted",
            message: "外部 Runtime が Run を受理しました。",
            payload: { external_run_id: "hermes-run-1" },
            created_at: now,
          },
        ],
        approvals: [],
        artifacts: [],
        pending_tool_calls: [],
        metadata: {},
        created_at: now,
        updated_at: now,
      };
      runs = [run];
      return respond(run);
    }
    if (path === "/api/runs") return respond({ runs });
    if (path.includes("/audit")) return respond({ run_id: "run-control-plane-e2e", goal: "", status: "running", records: [] });
    if (path.includes("/artifacts")) return respond({ artifacts: [] });
    return respond({});
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
}

function runDetail(page: Page) {
  return page.locator("section").filter({ hasText: "実行詳細" }).first();
}

test.describe("AI Agent Control Plane", () => {
  test("業務 Agent は Skill だけを選択し実行先を別パネルで管理する", async ({ page }) => {
    await installControlPlaneApi(page);
    await page.goto("/agents");

    await expect(page.getByRole("heading", { name: "業務 Agent", level: 1 })).toBeVisible();
    await expect(page.getByText("業務 RAG 調査").first()).toBeVisible();
    await expect(page.getByText("実行先", { exact: true })).toBeVisible();
    await expect(page.getByText("default-profile")).toBeVisible();
    await expect(page.getByText("利用可能ツール")).toHaveCount(0);
    await expect(page.getByText("Command allowed prefixes")).toHaveCount(0);

    await page.keyboard.press("Tab");
    await expectNoHorizontalOverflow(page);
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByText("default-profile")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("未 Binding を明示し、Binding 指定で Run を作成できる", async ({ page }) => {
    await installControlPlaneApi(page, { unbound: true });
    await page.goto("/runs");
    await expect(page.getByText("この Agent には実行可能な Binding がありません。")).toBeVisible();

    await page.unroute("**/api/**");
    await installControlPlaneApi(page);
    await page.reload();
    const goal = "契約情報を確認する";
    await page.getByLabel("ゴール").fill(goal);
    await expect(page.getByLabel("実行先 Binding")).toContainText("default-profile");
    await page.getByRole("button", { name: "実行を作成" }).click();

    await expect(page.getByText("実行を作成しました", { exact: true })).toBeVisible();
    await expect(page.getByText(goal).first()).toBeVisible();
    await expect(runDetail(page).getByText(/Runtime: hermes-default/)).toBeVisible();
    await expect(page.getByText("この Runtime は取消に対応していません。")).toBeVisible();
    await expect(page.getByText("runtime.submitted")).toBeVisible();
  });

  test("Runtime の degraded 状態、capability、管理操作を表示する", async ({ page }) => {
    await installControlPlaneApi(page);
    await page.goto("/runtimes");

    await expect(page.getByRole("heading", { name: "Runtime", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Hermes" })).toBeVisible();
    await expect(page.getByText("degraded")).toBeVisible();
    await expect(page.getByText("cancel: off")).toBeVisible();
    await expect(page.getByRole("button", { name: "状態確認" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pull" })).toBeVisible();
    await expect(page.getByRole("button", { name: "起動", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "停止" })).toBeVisible();
    await expect(page.getByRole("button", { name: "再起動" })).toBeVisible();
    await expect(page.getByRole("button", { name: "削除" })).toBeVisible();

    await page.getByRole("button", { name: "状態確認" }).click();
    await page.setViewportSize({ width: 375, height: 812 });
    await expect(page.getByRole("heading", { name: "Hermes" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
