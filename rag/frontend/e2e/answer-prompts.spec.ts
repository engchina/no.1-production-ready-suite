import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

// 回答プロンプトの画面（#595）。回答を作る指示のテンプレートと、回答の各工程（読み取り専用）だけを持つ。
// 以前の system prompt の版（作成・有効化）は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

/** TanStack Query の window focus 再取得を起こす(タブへ戻った操作に相当)。 */
async function refetchOnFocus(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答プロンプトの画面はテンプレートだけを出し、版の一覧と作成は無い (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/docrag-prompts**", (route) =>
      route.fulfill({ json: docragPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) })
    );

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText("回答生成テンプレート", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "版を作成" })).toHaveCount(0);
    await expect(page.getByTestId("prompt-version-list")).toHaveCount(0);
    await expect(page.getByTestId("docrag-unused-note")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText("DocRAG");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "回答プロンプト" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoPageOverflow(page);
  });

  test(`回答プロンプトの読み込み中は経過時間と形の Skeleton を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/settings/docrag-prompts**", async (route) => {
      await gate;
      await route.fulfill({ json: docragPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) });
    });

    await page.goto("/settings/prompts");

    const loading = page.getByTestId("settings-prompts-loading");
    await expect(loading).toBeVisible();
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答生成テンプレートを検証・保存・既定に戻せて、各段は読み取り専用で見られる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: { method: string; body: unknown }[] = [];
    await page.route("**/api/settings/docrag-prompts**", async (route) => {
      const method = route.request().method();
      if (method === "GET") {
        await route.fulfill({ json: docragPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) });
        return;
      }
      const body = method === "PUT" ? route.request().postDataJSON() : null;
      requests.push({ method, body });
      if (method === "PUT" && !String((body as { content: string }).content).includes("{{images}}")) {
        await route.fulfill({
          status: 422,
          json: { data: null, error_messages: ["必須の placeholder がありません: {{images}}"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json:
          method === "PUT"
            ? docragPromptsEnvelope((body as { content: string }).content, true)
            : docragPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false),
      });
    });

    await page.goto("/settings/prompts");

    const field = page.getByLabel("テンプレート");
    const save = page.getByRole("button", { name: "プロンプトを保存" });
    await expect(page.getByText("既定値", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "既定に戻す" })).toBeDisabled();
    await field.fill("質問だけ {{question}}");
    await save.click();
    await expect(page.getByText("必須の placeholder がありません: {{images}}")).toBeVisible();

    await field.fill("独自 {{question}} {{images}}");
    await save.click();
    await expect(page.getByText("プロンプトを保存しました。")).toBeVisible();
    await expect(page.getByText(/^編集済み/)).toBeVisible();

    await page.getByRole("button", { name: "既定に戻す" }).click();
    await page.getByRole("alertdialog", { name: "既定のプロンプトに戻しますか？" }).getByRole("button", { name: "既定に戻す" }).click();
    await expect(page.getByText("既定のプロンプトに戻しました。")).toBeVisible();
    await expect(field).toHaveValue("既定のテンプレート {{question}} {{images}}");
    expect(requests.map((request) => request.method)).toEqual(["PUT", "PUT", "DELETE"]);

    await page.getByText("5. 回答の監査").click();
    await expect(page.getByText("監査の system")).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("回答生成テンプレートは編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let content = "保存版 A {{question}} {{images}}";
  await page.route("**/api/settings/docrag-prompts**", (route) =>
    route.fulfill({ json: docragPromptsEnvelope(content, true) })
  );

  await page.goto("/settings/prompts");
  const editor = page.getByRole("textbox", { name: "テンプレート", exact: true });
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。
  content = "保存版 B {{question}} {{images}}";
  await refetchOnFocus(page);
  await expect(editor).toHaveValue(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("編集中 {{question}} {{images}}");
  content = "保存版 C {{question}} {{images}}";
  const refetched = page.waitForResponse("**/api/settings/docrag-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("編集中 {{question}} {{images}}");
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

function docragPromptsEnvelope(content: string, customized: boolean) {
  return {
    data: {
      prompts: [
        {
          key: "vlm_answer",
          content,
          default_content: "既定のテンプレート {{question}} {{images}}",
          customized,
          required_placeholders: ["question", "images"],
          updated_at: customized ? "2026-09-26T01:00:00Z" : null,
        },
      ],
      stages: [
        { id: "routing", prompts: [{ id: "system", content: "ルーティングの system" }] },
        { id: "audit", prompts: [{ id: "system", content: "監査の system" }] },
      ],
    },
    error_messages: [],
    warning_messages: [],
  };
}
