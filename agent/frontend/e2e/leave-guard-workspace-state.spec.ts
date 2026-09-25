import type { Locator, Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

// 未保存変更の離脱ガードと、一覧の作業状態の保持（platform UX 契約 workspace-state.md。#87）。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
];

function sidebarLink(page: Page, href: string): Locator {
  return page.getByRole("complementary", { name: "サイドナビゲーション" }).locator(`a[href="${href}"]`);
}

/** beforeunload の handler が登録されていれば、cancelable な event が preventDefault される。 */
async function beforeUnloadBlocks(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test.describe(`離脱ガード (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("未保存の Agent 作成フォームはサイドナビの移動を確認し、破棄を選ぶと移動する", async ({ page }) => {
      await page.goto("/agents?id=new");
      await expect(page.getByRole("heading", { name: "業務 Agent を作成", level: 1 })).toBeVisible();
      expect(await beforeUnloadBlocks(page)).toBe(false);

      await page.locator("#new-agent-name").fill("下書きの Agent");
      await expect.poll(() => beforeUnloadBlocks(page)).toBe(true);

      await sidebarLink(page, "/runs").click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("変更を破棄しますか")).toBeVisible();
      await expectNoHorizontalOverflow(page);

      // キャンセルすると移動せず、入力も残る。
      await dialog.getByRole("button", { name: "キャンセル" }).click();
      await expect(page).toHaveURL(/\/agents\?id=new$/);
      await expect(page.locator("#new-agent-name")).toHaveValue("下書きの Agent");

      // パンくずの一覧リンクも同じ離脱ガードで止まる。
      await page.getByRole("navigation", { name: "パンくず" }).getByRole("link", { name: "業務 Agent" }).click();
      await expect(dialog.getByText("変更を破棄しますか")).toBeVisible();
      await dialog.getByRole("button", { name: "キャンセル" }).click();
      await expect(page).toHaveURL(/\/agents\?id=new$/);

      await sidebarLink(page, "/runs").click();
      await dialog.getByRole("button", { name: "破棄して移動" }).click();
      await expect(page).toHaveURL(/\/runs$/);
      await expect(page.getByRole("heading", { name: "Run", level: 1 })).toBeVisible();
      expect(await beforeUnloadBlocks(page)).toBe(false);
    });

    test("Agent を作成すると作成した Agent のエディタへ移り、確認なしで移動できる", async ({ page }) => {
      await page.goto("/agents");
      await page.getByRole("button", { name: "業務 Agent を作成" }).click();
      await page.locator("#new-agent-name").fill("作成する Agent");
      await expect.poll(() => beforeUnloadBlocks(page)).toBe(true);
      await page.getByRole("button", { name: "作成", exact: true }).first().click();
      await expect(page.getByText("Agent を作成しました")).toBeVisible();
      await expect(page).toHaveURL(/\/agents\?id=agent-2$/);
      await expect(page.getByRole("heading", { name: "作成する Agent", level: 1 })).toBeVisible();
      await expect.poll(() => beforeUnloadBlocks(page)).toBe(false);
      // 新規フォームは履歴に残さない（戻るで空の作成フォームへ戻らない）。
      await page.goBack();
      await expect(page).toHaveURL(/\/agents$/);
      await expect(page.getByRole("heading", { name: "業務 Agent", level: 1 })).toBeVisible();

      await sidebarLink(page, "/runs").click();
      await expect(page).toHaveURL(/\/runs$/);
    });

    test("設定を保存すると基準が更新され、確認なしで移動できる", async ({ page }) => {
      await page.goto("/settings/runtime-safety");
      await expect(page.getByRole("heading", { name: "Runtime Safety", level: 1 })).toBeVisible();
      await page.locator("#runtime-safety-max-tool-calls").fill("7");
      await expect.poll(() => beforeUnloadBlocks(page)).toBe(true);

      await page.getByRole("button", { name: "保存", exact: true }).click();
      await expect(page.getByText("設定を保存しました")).toBeVisible();
      await expect.poll(() => beforeUnloadBlocks(page)).toBe(false);

      await sidebarLink(page, "/settings/external-nl2sql").click();
      await expect(page).toHaveURL(/\/settings\/external-nl2sql$/);
      await expect(page.getByText("変更を破棄しますか")).toHaveCount(0);
    });

    test("変更していなければ確認なしで移動できる", async ({ page }) => {
      await page.goto("/settings/external-mcp");
      await expect(page.getByRole("heading", { name: "外部 MCP", level: 1 })).toBeVisible();
      expect(await beforeUnloadBlocks(page)).toBe(false);

      await sidebarLink(page, "/agents").click();
      await expect(page).toHaveURL(/\/agents$/);
      await expect(page.getByText("変更を破棄しますか")).toHaveCount(0);
    });

    test("Skill のエディタは画面内の「一覧に戻る」でも破棄を確認する", async ({ page }) => {
      await page.goto("/skills");
      await page.getByRole("button", { name: "スキルを追加" }).click();
      await page.locator("#skill-id").fill("draft_skill");

      // 狭い幅では PageHeader の補助操作が「その他の操作」に入る。
      const more = page.getByTestId("page-actions-more");
      if (await more.isVisible()) await more.click();
      await page.getByRole("button", { name: "一覧に戻る" }).or(page.getByRole("menuitem", { name: "一覧に戻る" })).click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("閉じると編集内容は破棄されます")).toBeVisible();
      await dialog.getByRole("button", { name: "破棄して閉じる" }).click();
      await expect(page).toHaveURL(/\/skills$/);
      await expect(page.locator("#skill-id")).toHaveCount(0);
      expect(await beforeUnloadBlocks(page)).toBe(false);
    });
  });

  test.describe(`作業状態の保持 (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("監査の絞り込み条件は移動して戻っても、再読込しても残る", async ({ page, mockApi }) => {
      await page.goto("/audit");
      await expect(page.getByRole("heading", { name: "監査", level: 1 })).toBeVisible();
      await page.locator("#audit-run-id").fill("run-e2e-1");
      await page.locator("#audit-tool-name").selectOption("echo");
      await page.locator("#audit-warning-filter").selectOption("true");
      await page.getByRole("button", { name: "フィルター適用" }).click();
      await expect
        .poll(() => mockApi.lastRequest("GET", "/api/audit/tool-calls")?.searchParams.get("run_id"))
        .toBe("run-e2e-1");
      // 適用していない入力も下書きとして残す。
      await page.locator("#audit-error-code").fill("E_DRAFT");

      await sidebarLink(page, "/runs").click();
      await expect(page).toHaveURL(/\/runs$/);
      await sidebarLink(page, "/audit").click();
      await expect(page.locator("#audit-run-id")).toHaveValue("run-e2e-1");
      await expect(page.locator("#audit-tool-name")).toHaveValue("echo");
      await expect(page.locator("#audit-warning-filter")).toHaveValue("true");
      await expect(page.locator("#audit-error-code")).toHaveValue("E_DRAFT");

      await page.reload();
      await expect(page.locator("#audit-run-id")).toHaveValue("run-e2e-1");
      await expect(page.locator("#audit-error-code")).toHaveValue("E_DRAFT");
      // 再読込後の一覧は、適用済みの条件（run_id / tool_name / 警告あり）で取り直す。
      await expect
        .poll(() => {
          const params = mockApi.lastRequest("GET", "/api/audit/tool-calls")?.searchParams;
          return params ? `${params.get("run_id")}|${params.get("tool_name")}|${params.get("has_guardrail_warnings")}|${params.get("error_code")}` : null;
        })
        .toBe("run-e2e-1|echo|true|null");
      await expectNoHorizontalOverflow(page);
    });

    test("Skill の編集対象は URL が唯一の情報源で、消えた対象は説明して一覧へ戻す", async ({ page }) => {
      await page.goto("/skills");
      await page.getByRole("button", { name: /業務 RAG 調査/ }).click();
      await expect(page).toHaveURL(/\/skills\?id=business_rag_research$/);
      // 詳細だけが出す読み取り専用の案内で、対象が開いていることを確かめる。
      const detail = page.getByText("このスキルは読み取り専用です", { exact: false });
      await expect(detail).toBeVisible();

      await page.reload();
      await expect(detail).toBeVisible();

      // 編集対象は sessionStorage ではなく URL に持つ（#137）。
      const stored = await page.evaluate(() =>
        Object.keys(window.sessionStorage).filter((key) => key.startsWith("production-ready-agent.workspace.v1:skills"))
      );
      expect(stored).toEqual([]);

      // URL の対象が一覧にない場合は、黙って別の対象に置き換えず説明する。
      await page.goto("/skills?id=deleted_skill");
      await expect(page.getByText("対象が見つかりません")).toBeVisible();
      await expect(page.getByText("「deleted_skill」は削除されたか、存在しません", { exact: false })).toBeVisible();
      await page.getByRole("button", { name: "一覧に戻る" }).last().click();
      await expect(page).toHaveURL(/\/skills$/);
      await expectNoHorizontalOverflow(page);
    });

    test("Run の目標とメモリの検索語は残り、確認語は移動で解除される", async ({ page }) => {
      await page.goto("/runs");
      await page.locator("#run-goal").fill("下書きの目標");
      await page.goto("/memory");
      await page.locator("#memory-search").fill("学習メモ");
      await sidebarLink(page, "/runs").click();
      await expect(page.locator("#run-goal")).toHaveValue("下書きの目標");
      await page.reload();
      await expect(page.locator("#run-goal")).toHaveValue("下書きの目標");
      await page.goto("/memory");
      await expect(page.locator("#memory-search")).toHaveValue("学習メモ");

      // 置換の確認語は保存も復元もしない。
      await page.goto("/settings/runtime-snapshot");
      await page.locator("#runtime-snapshot-confirm").fill("REPLACE");
      await sidebarLink(page, "/runs").click();
      await expect(page).toHaveURL(/\/runs$/);
      await sidebarLink(page, "/settings/runtime-snapshot").click();
      await expect(page.locator("#runtime-snapshot-confirm")).toHaveValue("");

      const stored = await page.evaluate(() =>
        Object.keys(window.sessionStorage).filter((key) => key.startsWith("production-ready-agent.workspace.v1:"))
      );
      expect(stored.sort()).toEqual([
        "production-ready-agent.workspace.v1:memory.query",
        "production-ready-agent.workspace.v1:runs.goal",
      ]);
    });
  });
}
