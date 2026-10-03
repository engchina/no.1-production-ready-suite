import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #770: 業務 Agent の下書き・公開・版の履歴・前の版に戻す。Run の「下書きで実行」。

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

function seedDraftAgent(mockApi: MockApi) {
  mockApi.state.agents.push({
    id: "agent-770",
    name: "経理の Agent",
    description: "経理の質問に答える",
    instructions: "v1 の指示",
    skill_ids: [],
    model_id: "",
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
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
]) {
  test(`下書きを公開し、変更して再公開し、前の版に戻せる (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    seedDraftAgent(mockApi);
    await page.goto("/agents?id=agent-770");

    await expect(page.getByRole("heading", { name: "経理の Agent", level: 1 })).toBeVisible();
    await expect(page.getByText("未公開", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("この業務 Agent はまだ公開していません。", { exact: false })).toBeVisible();
    await expect(page.getByText("まだ公開していません。", { exact: true })).toBeVisible();

    // 公開（対象への操作は概要の ObjectActionBar。確認してから公開する）。
    const actions = page.getByTestId("agent-object-actions");
    const publish = actions.getByRole("button", { name: "公開" });
    if (await publish.count()) {
      await publish.click();
    } else {
      await page.getByTestId("agent-object-actions-more").click();
      await page.getByRole("menuitem", { name: "公開" }).click();
    }
    const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await expect(dialog.getByText("v1 として公開しますか?")).toBeVisible();
    await dialog.getByRole("button", { name: "公開", exact: true }).click();
    await expect(page.getByText("v1 として公開しました")).toBeVisible();
    await expect(page.getByText("公開中 v1").first()).toBeVisible();
    const versions = page.getByRole("table", { name: "版の履歴" });
    await expect(versions.getByText("v1", { exact: true })).toBeVisible();

    // 下書きを変えて保存すると「公開していない変更あり」。再公開で v2。
    await page.getByLabel("指示").fill("v2 の指示");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("Agent を保存しました")).toBeVisible();
    await expect(page.getByText("公開していない変更あり").first()).toBeVisible();
    expect(mockApi.state.agents.find((agent) => agent.id === "agent-770")?.instructions).toBe("v2 の指示");

    // v1 に戻す（版の行メニュー → 確認）。
    await page.getByRole("button", { name: "v1 の操作", exact: true }).click();
    await page.getByRole("menuitem", { name: "この版に戻す" }).click();
    await expect(dialog.getByText("v1 に戻しますか?")).toBeVisible();
    await dialog.getByRole("button", { name: "この版に戻す", exact: true }).click();
    await expect(page.getByText("v1 に戻しました")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/agents/agent-770/versions/1/restore")).toBeDefined();
    await expect(page.getByLabel("指示")).toHaveValue("v1 の指示");
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`agent-versions-${viewport.name}.png`), fullPage: true });
  });
}

test("一覧に公開の状態を出す", async ({ page, mockApi }) => {
  seedDraftAgent(mockApi);
  await page.goto("/agents");
  const table = page.getByRole("table");
  await expect(table.getByRole("row", { name: /経理の Agent/ }).getByText("未公開")).toBeVisible();
  await expect(table.getByRole("row", { name: /汎用業務 Agent/ }).getByText("公開中 v1")).toBeVisible();
});

test("公開していない Agent は、管理者が「下書きで実行」にしたときだけ Run で選べる", async ({ page, mockApi }) => {
  seedDraftAgent(mockApi);
  const bodies: unknown[] = [];
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    bodies.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        data: {
          id: "run-draft-770",
          goal: "試す",
          agent_id: "agent-770",
          runtime_id: "builtin",
          status: "queued",
          steps: [],
          events: [],
          approvals: [],
          artifacts: [],
          pending_tool_calls: [],
          metadata: { agent_version: "draft" },
          created_at: MOCK_NOW,
          updated_at: MOCK_NOW,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.goto("/runs?id=new");
  const agentSelect = page.locator("#run-agent");
  await agentSelect.click();
  await expect(page.getByRole("option", { name: "経理の Agent" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await page.getByTestId("run-draft").click();
  await agentSelect.click();
  await page.getByRole("option", { name: "経理の Agent" }).click();
  await page.locator("#run-goal").fill("試す");
  await page.getByRole("button", { name: "実行を作成" }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).toEqual({ goal: "試す", agent_id: "agent-770", draft: true });
});
