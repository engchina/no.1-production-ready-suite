import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// ページの型（platform UX 契約 page-archetypes.md）の Agent への適用（#137）。
// A 型 = 一覧 → 全画面エディタ（?id= が唯一の情報源、パンくず、行メニュー / ObjectActionBar）、
// B 型 = 一覧 + 詳細を FixedSplitPane で並べる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800, layout: "split" },
  { name: "mobile-375", width: 375, height: 812, layout: "stacked" },
] as const;

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

function breadcrumbs(page: Page) {
  return page.getByRole("navigation", { name: "パンくず" });
}

function seedRun(mockApi: MockApi, id: string, goal: string, approvals: Record<string, unknown>[] = []) {
  mockApi.state.runs.push({
    id,
    goal,
    agent_id: "default",
    runtime_id: "legacy-native",
    binding_id: null,
    external_run_id: null,
    external_cursor: null,
    runtime_capabilities: {
      stream_events: true,
      cancel: true,
      artifacts: true,
      approvals: true,
      skill_sync: false,
      mcp_sync: false,
    },
    status: "completed",
    steps: [],
    events: [],
    approvals,
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

function approval(id: string, name: string, runId: string) {
  return {
    id,
    run_id: runId,
    step_id: `${id}-step`,
    tool_call: { name, arguments: { query: `${name} の引数` } },
    status: "pending",
    reason: "承認が必要です",
    decided_by: null,
    created_at: MOCK_NOW,
    decided_at: null,
  };
}

for (const viewport of VIEWPORTS) {
  test.describe(`A 型: 一覧 → 全画面エディタ (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("編集対象は URL で開き、再読込・戻る / 進む・パンくずで行き来できる", async ({ page }) => {
      await page.goto("/skills");
      await expect(page.getByRole("heading", { name: "スキル", level: 1 })).toBeVisible();
      await expect(breadcrumbs(page)).toHaveCount(0);

      // 先頭セルの対象名のボタンで開く。
      await page.getByRole("button", { name: /業務 RAG 調査/ }).click();
      await expect(page).toHaveURL(/\/skills\?id=business_rag_research$/);
      await expect(page.getByRole("heading", { name: "業務 RAG 調査", level: 1 })).toBeVisible();
      await expect(breadcrumbs(page).getByRole("link", { name: "スキル" })).toBeVisible();
      await expect(breadcrumbs(page).locator('[aria-current="page"]')).toHaveText("業務 RAG 調査");
      await expectNoHorizontalOverflow(page);

      await page.reload();
      await expect(page.getByRole("heading", { name: "業務 RAG 調査", level: 1 })).toBeVisible();

      await page.goBack();
      await expect(page).toHaveURL(/\/skills$/);
      await expect(page.getByRole("heading", { name: "スキル", level: 1 })).toBeVisible();
      await page.goForward();
      await expect(page).toHaveURL(/\/skills\?id=business_rag_research$/);
      await expect(page.getByRole("heading", { name: "業務 RAG 調査", level: 1 })).toBeVisible();

      await breadcrumbs(page).getByRole("link", { name: "スキル" }).click();
      await expect(page).toHaveURL(/\/skills$/);

      // 行の操作以外の領域（状態のセル）をクリックしても開く。
      await page.getByTestId("skill-row-structured_data_query").getByRole("cell").nth(2).click();
      await expect(page).toHaveURL(/\/skills\?id=structured_data_query$/);

      // ?id=new は新規作成のエディタ。
      await page.goto("/skills?id=new");
      await expect(page.getByRole("heading", { name: "スキルを追加", level: 1 })).toBeVisible();
      await expect(breadcrumbs(page).locator('[aria-current="page"]')).toHaveText("スキルを追加");
      await expect(page.locator("#skill-id")).toBeEditable();
    });

    test("一覧の行は操作メニュー 1 つだけを持ち、キーボードで開閉できる", async ({ page, mockApi }) => {
      mockApi.state.externalMcpServers.servers.push({
        server_id: "crm",
        label: "CRM Gateway",
        base_url: "http://mcp.example.test/jsonrpc",
        api_key_configured: false,
        oauth_configured: false,
        auth_mode: "none",
        session_configured: false,
        timeout_seconds: 10,
        default_limit: null,
        configured: true,
        is_default: false,
      });
      await page.goto("/settings/external-mcp");
      const table = page.getByRole("table", { name: "MCP サーバー" });
      await expect(table.getByRole("row")).toHaveCount(3);

      // 各行のボタンは「対象名」と「操作メニュー」だけ（文字ボタンを並べない）。
      for (const row of await table.getByRole("row").all()) {
        if ((await row.getByRole("columnheader").count()) > 0) continue;
        await expect(row.locator('[aria-haspopup="menu"]')).toHaveCount(1);
        expect(await row.getByRole("button").count()).toBeLessThanOrEqual(2);
      }

      // Enter で開いて先頭の項目へフォーカス、矢印で移動、Esc で閉じて trigger へ戻る。
      const trigger = page.getByRole("button", { name: "crm の操作" });
      await trigger.focus();
      await page.keyboard.press("Enter");
      const menu = page.getByRole("menu");
      await expect(menu).toBeVisible();
      await expect(menu.getByRole("menuitem", { name: "既定にする" })).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await expect(menu.getByRole("menuitem", { name: "削除" })).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(menu).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await expect(trigger).toHaveAttribute("aria-expanded", "false");

      // 既定のサーバーは既定にも削除にもできないため、行メニュー自体を使えない。
      await expect(page.getByRole("button", { name: "default の操作" })).toBeDisabled();

      // 破壊的な操作は確認する。キャンセルでは消えない。
      await trigger.click();
      await page.getByRole("menuitem", { name: "削除" }).click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("サーバーを削除しますか?")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await dialog.getByRole("button", { name: "キャンセル" }).click();
      await expect(trigger).toBeVisible();
      expect(mockApi.lastRequest("DELETE", "/api/settings/external-mcp-servers/crm")).toBeUndefined();

      await trigger.click();
      await page.getByRole("menuitem", { name: "削除" }).click();
      await dialog.getByRole("button", { name: "削除", exact: true }).click();
      await expect(page.getByText("サーバーを削除しました")).toBeVisible();
      await expect(trigger).toHaveCount(0);
    });

    test("エディタの対象操作は ObjectActionBar に出し、削除後は一覧へ戻る", async ({ page, mockApi }) => {
      mockApi.state.skills.push({
        id: "e2e_runtime",
        name: "E2E 実行時スキル",
        description: "削除できるスキル",
        instructions: "",
        mcp_requirements: [],
        resource_ids: [],
        tool_calls: [],
        enabled: true,
        tags: [],
        source: "runtime",
        created_at: MOCK_NOW,
        updated_at: MOCK_NOW,
      });
      await page.goto("/skills");
      await page.getByRole("button", { name: "E2E 実行時スキル e2e_runtime", exact: true }).click();
      await expect(page).toHaveURL(/\/skills\?id=e2e_runtime$/);

      // 行と同じ定義。危険な操作は「その他の操作」メニューに入る。
      const bar = page.getByTestId("skill-object-actions");
      await expect(bar.getByRole("button", { name: "削除" })).toHaveCount(0);
      await page.getByTestId("skill-object-actions-more").click();
      await page.getByRole("menuitem", { name: "削除" }).click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("スキルを削除しますか?")).toBeVisible();
      await dialog.getByRole("button", { name: "削除", exact: true }).click();
      await expect(page.getByText("スキルを削除しました")).toBeVisible();
      await expect(page).toHaveURL(/\/skills$/);
      await expect(page.getByRole("button", { name: "E2E 実行時スキル e2e_runtime", exact: true })).toHaveCount(0);
    });
  });

  test.describe(`B 型: 一覧 + 詳細の分割ペイン (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("Run / 承認 / メモリ / ツールは FixedSplitPane で一覧と詳細を並べる", async ({ page, mockApi }) => {
      seedRun(mockApi, "run-e2e-1", "一つ目の目標");
      seedRun(mockApi, "run-e2e-2", "二つ目の目標", [approval("approval-e2e-1", "external_rag_search", "run-e2e-2")]);
      mockApi.state.memory.push({
        id: "memory-e2e-1",
        kind: "note",
        content: "学習メモの内容",
        metadata: { source: "e2e" },
        created_at: MOCK_NOW,
      });

      for (const [path, splitId] of [
        ["/runs", "runs-list"],
        ["/approvals", "approvals-list"],
        ["/memory", "memory-list"],
        ["/tools", "tools-list"],
      ] as const) {
        await page.goto(path);
        const pane = page.getByTestId(`fixed-split-pane-${splitId}`);
        await expect(pane).toBeVisible();
        await expect(pane).toHaveAttribute("data-split-layout", viewport.layout);
        if (viewport.layout === "split") {
          await expect(pane.getByRole("separator", { name: "一覧と詳細の表示比率" })).toBeVisible();
        }
        await expectNoHorizontalOverflow(page);
      }

      if (viewport.layout === "split") {
        // divider のキー操作で比率が変わり、Agent の保存 key で残る。
        await page.goto("/tools");
        const divider = page.getByTestId("fixed-split-pane-tools-list-divider");
        const before = await divider.getAttribute("aria-valuenow");
        await divider.focus();
        await page.keyboard.press("ArrowRight");
        await expect(divider).not.toHaveAttribute("aria-valuenow", before ?? "");
        await expect
          .poll(() =>
            page.evaluate(() => window.localStorage.getItem("production-ready-agent.fixedSplitPane.tools-list"))
          )
          .not.toBeNull();
      }
    });

    test("Run の行を選ぶと詳細が変わり、行メニューと詳細の操作は同じ定義を使う", async ({ page, mockApi }) => {
      seedRun(mockApi, "run-e2e-1", "一つ目の目標");
      seedRun(mockApi, "run-e2e-2", "二つ目の目標");
      await page.goto("/runs");
      const detail = page.getByRole("region", { name: "実行詳細" });
      await expect(detail.getByText("run-e2e-1", { exact: true }).first()).toBeVisible();

      await page.getByRole("button", { name: /^二つ目の目標 default/ }).click();
      await expect(detail.getByText("run-e2e-2", { exact: true }).first()).toBeVisible();
      await expect(page.getByTestId("run-row-run-e2e-2")).toHaveAttribute("aria-current", "true");
      await expect(page.getByTestId("run-object-actions").getByRole("button", { name: "再実行" })).toBeVisible();

      await page.getByRole("button", { name: "run-e2e-1 の操作" }).click();
      await expect(page.getByRole("menuitem", { name: "再実行" })).toBeVisible();
      await expect(page.getByRole("menuitem", { name: "キャンセル" })).toHaveCount(0);
      await page.getByRole("menuitem", { name: "再実行" }).click();
      await expect(page.getByText("実行を作成しました", { exact: true })).toBeVisible();
      expect(mockApi.lastRequest("POST", "/api/runs/run-e2e-1/replay")).toBeDefined();
    });

    test("承認は行メニューから確認して判断し、詳細に引数を出す", async ({ page, mockApi }) => {
      seedRun(mockApi, "run-e2e-2", "二つ目の目標", [
        approval("approval-e2e-1", "external_rag_search", "run-e2e-2"),
        approval("approval-e2e-2", "external_nl2sql_query", "run-e2e-2"),
      ]);
      await page.goto("/approvals");
      const detail = page.getByRole("region", { name: "承認の詳細" });
      await expect(detail.getByText("external_rag_search の引数")).toBeVisible();

      await page.getByRole("button", { name: "external_nl2sql_query 二つ目の目標", exact: true }).click();
      await expect(detail.getByText("external_nl2sql_query の引数")).toBeVisible();

      await page.getByRole("button", { name: "external_rag_search の操作" }).click();
      await page.getByRole("menuitem", { name: "拒否" }).click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("ツール実行を拒否します")).toBeVisible();
      await dialog.getByRole("button", { name: "拒否", exact: true }).click();
      await expect
        .poll(() => mockApi.lastRequest("POST", "/api/approvals/approval-e2e-1/decision")?.body)
        .toMatchObject({ approved: false });
      // 判断済みの承認は行メニューを持たない。
      await expect(page.getByRole("button", { name: "external_rag_search の操作" })).toHaveCount(0);
      await expectNoHorizontalOverflow(page);
    });
  });
}
