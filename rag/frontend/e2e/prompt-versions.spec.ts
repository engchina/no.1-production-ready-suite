import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

type Version = { version_id: string; name: string; active: boolean };

function promptsEnvelope(versions: Version[]) {
  return {
    data: {
      active_version_id: versions.find((v) => v.active)?.version_id ?? null,
      versions: versions.map((v) => ({
        version_id: v.version_id,
        name: v.name,
        system_prompt: "あなたは厳密なアシスタントです。",
        note: "",
        created_at: "2026-06-28T00:00:00Z",
        created_by: "",
        active: v.active,
      })),
    },
    error_messages: [],
    warning_messages: [],
  };
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答プロンプト画面は版一覧と作成フォームを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/prompts", async (route) => {
      await route.fulfill({
        json: promptsEnvelope([
          { version_id: "v1", name: "標準版", active: true },
          { version_id: "v2", name: "監査版", active: false },
        ]),
      });
    });

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト版" })).toBeVisible();
    await expect(page.getByText("標準版")).toBeVisible();
    await expect(page.getByText("監査版")).toBeVisible();
    await expect(page.getByRole("button", { name: "版を作成" })).toBeVisible();
    // 版の system prompt は回答エンジンが DocRAG の業務ビューでは使われない(#300)。
    await expect(page.getByTestId("docrag-unused-note")).toContainText(
      "回答エンジンが DocRAG の業務ビューでは使われません"
    );
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "回答プロンプト" })).toHaveAttribute("aria-current", "page");
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, maxHeightRem: 28 },
  { name: "mobile", width: 375, height: 812, maxHeightRem: 17.5 },
]) {
  test(`版が多いときは一覧の中で縦スクロールし、10 件ごとにページを送る (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const versions = Array.from({ length: 23 }, (_, index) => ({
      version_id: `v${index + 1}`,
      name: `版 ${String(index + 1).padStart(2, "0")}`,
      active: index === 0,
    }));
    await page.route("**/api/settings/prompts", (route) => route.fulfill({ json: promptsEnvelope(versions) }));

    await page.goto("/settings/prompts");
    const list = page.getByTestId("prompt-version-list");
    await expect(list.getByRole("listitem")).toHaveCount(10);
    // md 未満 5 行・md 以上 8 行ぶんの高さ（17.5rem / 28rem、ルート 14px）で止まり、中でスクロールする（#265）。
    const metrics = await list.evaluate((element) => ({
      maxHeight: getComputedStyle(element).maxHeight,
      scrolls: element.scrollHeight > element.clientHeight,
    }));
    expect(metrics.maxHeight).toBe(`${viewport.maxHeightRem * 14}px`);
    expect(metrics.scrolls).toBe(true);

    const pager = page.getByTestId("prompt-version-pagination");
    await expect(pager).toContainText("1 - 10 / 23 件");
    await pager.getByRole("button", { name: "次へ" }).click();
    await expect(pager).toContainText("11 - 20 / 23 件");
    await expect(list.getByText("版 11")).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("回答プロンプト版を作成できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let created: unknown = null;
  await page.route("**/api/settings/prompts", async (route) => {
    if (route.request().method() === "POST") {
      created = route.request().postDataJSON();
      await route.fulfill({
        json: promptsEnvelope([{ version_id: "v1", name: "新規版", active: true }]),
      });
      return;
    }
    await route.fulfill({ json: promptsEnvelope([]) });
  });

  await page.goto("/settings/prompts");
  await page.getByPlaceholder(/例:/).fill("新規版");
  await page.getByPlaceholder(/社内ナレッジ検索アシスタント/).fill("厳密に回答してください。");
  await page.getByRole("button", { name: "版を作成" }).click();

  await expect(page.getByText("回答プロンプト版を作成しました。")).toBeVisible();
  expect(created).toEqual({
    name: "新規版",
    system_prompt: "厳密に回答してください。",
    activate: true,
  });
  await expectNoPageOverflow(page);
});

test("別の版を有効化できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let activatedUrl: string | null = null;
  await page.route("**/api/settings/prompts/*/activate", async (route) => {
    activatedUrl = route.request().url();
    await route.fulfill({
      json: promptsEnvelope([
        { version_id: "v1", name: "標準版", active: false },
        { version_id: "v2", name: "監査版", active: true },
      ]),
    });
  });
  await page.route("**/api/settings/prompts", async (route) => {
    await route.fulfill({
      json: promptsEnvelope([
        { version_id: "v1", name: "標準版", active: true },
        { version_id: "v2", name: "監査版", active: false },
      ]),
    });
  });

  await page.goto("/settings/prompts");
  // 有効化は行の RowActionMenu に入る（#147）。有効な版は理由付きで無効。
  await page.getByRole("button", { name: "標準版 の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "この版はすでに有効です" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "標準版 の操作" })).toBeFocused();
  await page.getByRole("button", { name: "監査版 の操作" }).click();
  await page.getByRole("menuitem", { name: "有効化" }).click();

  await expect(page.getByText("回答プロンプト版を有効化しました。")).toBeVisible();
  expect(activatedUrl).toContain("/api/settings/prompts/v2/activate");
});

test("回答プロンプト取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/prompts", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["回答プロンプト版を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/prompts");

  await expect(page.getByRole("alert")).toContainText("回答プロンプト版を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
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

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`DocRAG の回答生成テンプレートを検証・保存・既定に戻せて、各段は読み取り専用で見られる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route("**/api/settings/prompts", (route) =>
      route.fulfill({ json: promptsEnvelope([{ version_id: "v1", name: "標準版", active: true }]) })
    );
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

// #541: 作成のボタンは押せる状態のまま、未入力は押したときに欄の直下へ出して最初の欄へフォーカスする。
test("回答プロンプト版の未入力は欄の下に出す", async ({ page }) => {
  let posts = 0;
  await page.route("**/api/settings/prompts", async (route) => {
    if (route.request().method() === "POST") posts += 1;
    await route.fulfill({ json: promptsEnvelope([]) });
  });

  await page.goto("/settings/prompts");
  const create = page.getByRole("button", { name: "版を作成" });
  await expect(create).toBeEnabled();
  await create.click();

  await expect(page.locator("#prompt-version-name")).toHaveAccessibleDescription(/版名を入力してください。/);
  await expect(page.locator("#prompt-version-system-prompt")).toHaveAccessibleDescription(
    /system prompt を入力してください。/
  );
  await expect(page.locator("#prompt-version-system-prompt")).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#prompt-version-name")).toBeFocused();
  await page.getByPlaceholder(/例:/).fill("新規版");
  await expect(page.locator("#prompt-version-name")).not.toHaveAttribute("aria-invalid", "true");
  expect(posts).toBe(0);
  await expectNoPageOverflow(page);
});
