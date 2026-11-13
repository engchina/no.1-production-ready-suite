import { expect, test, type Page } from "./fixtures/test";
import { mockEditableStage } from "./_feature-prompt-helpers";
import { DEFAULT_STAGES, FLOW_STAGES, type StageFixture } from "./_prompt-stage-fixtures";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

// 回答プロンプトの画面（#595）。回答の各工程の指示を出す。編集できるのは生成の「役割と回答の書き方」と
// 登録済みQ&Aの指示だけで、固定の部分と内部の段は読み取り専用（#4782）。
// 以前の system prompt の版（作成・有効化）は削除した。

const ROLE_LABEL = "役割と回答の書き方";

test("回答単位の文字修復の指示は日本語のラベルで読み取り専用に表示する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const stages = structuredClone(DEFAULT_STAGES);
  const generation = stages.find((stage) => stage.id === "generation");
  expect(generation).toBeDefined();
  generation!.prompts.push({ id: "partTextRepair", content: "## Step 1: 合成の回答単位を修復する" });
  await page.route("**/api/settings/answer-prompts**", (route) =>
    route.fulfill({ json: answerPromptsEnvelope(DEFAULT_ROLE, false, stages) })
  );
  await page.goto("/settings/prompts");
  await openRoleField(page);
  const field = page.getByRole("textbox", { name: "文字が破損した回答単位を修復する指示", exact: true });
  await expect(field).toHaveAttribute("readonly", "");
  await expect(field).toHaveValue("## Step 1: 合成の回答単位を修復する");
  await expectNoPageOverflow(page);
  await testInfo.attach("answer-unit-repair", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
});

/** 生成の段を開き、役割の文の編集欄を返す。 */
async function openRoleField(page: Page) {
  const stage = page.getByTestId("answer-prompt-stage-generation");
  await stage.locator("summary").click();
  return stage.getByRole("textbox", { name: ROLE_LABEL, exact: true });
}

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
  test(`回答プロンプトの画面は役割の文だけを編集欄にし、版の一覧と作成は無い (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope(DEFAULT_ROLE, false) })
    );

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText("回答生成のプロンプト", { exact: true })).toHaveCount(0);
    await expect(await openRoleField(page)).toBeEditable();
    // 固定の部分・入力テンプレートなど、ほかの部分は読み取り専用で、保存の操作は役割の文の 1 組だけ。
    const stage = page.getByTestId("answer-prompt-stage-generation");
    const fixed = stage.getByRole("textbox", { name: "必ず足す指示：信頼の境界・引用・出力の形（読み取り専用）", exact: true });
    await expect(fixed).toHaveAttribute("readonly", "");
    await expect(stage.getByRole("textbox", { name: "入力テンプレート", exact: true })).toHaveAttribute("readonly", "");
    await expect(stage.locator("textarea:not([readonly])")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "プロンプトを保存", exact: true })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "版を作成" })).toHaveCount(0);
    await expect(page.getByTestId("prompt-version-list")).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
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
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      await gate;
      await route.fulfill({ json: answerPromptsEnvelope(DEFAULT_ROLE, false) });
    });

    await page.goto("/settings/prompts");

    const loading = page.getByTestId("settings-prompts-loading");
    await expect(loading).toBeVisible();
    release();
    await expect(loading).toHaveCount(0);
    await expect(await openRoleField(page)).toBeVisible();
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`役割の文は空を送る前に止め、変更を破棄で保存値へ戻せる（#1010, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: string[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      if (route.request().method() !== "GET") requests.push(route.request().method());
      await route.fulfill({ json: answerPromptsEnvelope("## Step 1: 合成の保存版", true) });
    });

    await page.goto("/settings/prompts");
    const field = await openRoleField(page);
    const actions = page.getByRole("group", { name: "編集内容の操作" });
    const save = actions.getByRole("button", { name: "プロンプトを保存" });
    const discard = actions.getByRole("button", { name: "変更を破棄" });
    await expect(discard).toBeDisabled();

    await field.fill("   ");
    await expect(actions).toContainText("未保存の変更があります。");
    await save.click();
    await expect(page.getByText(`${ROLE_LABEL}を入力してください。`)).toBeVisible();
    await expect(field).toBeFocused();
    expect(requests).toEqual([]);

    await discard.click();
    await expect(field).toHaveValue("## Step 1: 合成の保存版");
    await expect(page.getByText(`${ROLE_LABEL}を入力してください。`)).toHaveCount(0);
    await expect(save).toBeDisabled();
    await expect(actions.getByText(/^編集済み/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("役割の文は編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let content = "## Step 1: 合成の保存版 A";
  await page.route("**/api/settings/answer-prompts**", (route) =>
    route.fulfill({ json: answerPromptsEnvelope(content, true) })
  );

  await page.goto("/settings/prompts");
  const editor = await openRoleField(page);
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。初回の取得（StrictMode の再購読による取り直しを含む）が
  // まだ途中だと、フォーカスの再取得はその取得（保存版 A）にまとめられるため、B が届くまで再取得を促す（#833）。
  content = "## Step 1: 合成の保存版 B";
  await expect
    .poll(async () => {
      await refetchOnFocus(page);
      return editor.inputValue();
    })
    .toBe(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("## Step 1: 合成の編集中");
  content = "## Step 1: 合成の保存版 C";
  const refetched = page.waitForResponse("**/api/settings/answer-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("## Step 1: 合成の編集中");
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

const DEFAULT_ROLE = "## Step 1: 合成の役割の文";

function answerPromptsEnvelope(content: string, customized: boolean, stages: StageFixture[] = DEFAULT_STAGES) {
  return {
    data: {
      prompts: [
        {
          key: "generation.role",
          content,
          default_content: DEFAULT_ROLE,
          customized,
          required_placeholders: [],
          updated_at: customized ? "2026-09-26T01:00:00Z" : null,
        },
      ],
      // 役割の文の部分は保存値を映す（backend と同じ）。
      stages: stages.map((stage) => ({
        ...stage,
        prompts: stage.prompts.map((part) => (part.prompt_key === "generation.role" ? { ...part, content } : part)),
      })),
    },
    error_messages: [],
    warning_messages: [],
  };
}


for (const width of [1280, 375]) {
  test(`回答プロンプトの取得失敗後は再試行で工程と編集欄へ戻れる (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let failed = true;
    await page.route("**/api/settings/answer-prompts**", (route) => failed
      ? route.fulfill({ status: 503, json: { data: null, error_messages: ["取得できませんでした"], warning_messages: [] } })
      : route.fulfill({ json: answerPromptsEnvelope(DEFAULT_ROLE, false) }));
    await page.goto("/settings/prompts");
    await expect(page.getByText("プロンプトを読み込めませんでした。", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("answer-prompt-stage-generation")).toHaveCount(0);
    failed = false;
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(await openRoleField(page)).toBeEditable();
    await expectNoPageOverflow(page);
  });
}

// 役割の文は、ほかの編集欄と同じ保存・破棄・復帰の契約を使う（#4782）。
test("役割の文は保存・破棄でき、既定への復帰は保存まで確定しない", { tag: "@responsive" }, async ({ page }) => {
  const key = "generation.role";
  const requests = await mockEditableStage(page, "generation", "generation", "role");
  await page.goto("/settings/prompts");
  const stage = page.getByTestId("answer-prompt-stage-generation");
  const field = await openRoleField(page);
  const actions = stage.getByRole("group", { name: "編集内容の操作" });
  const save = actions.getByRole("button", { name: "プロンプトを保存", exact: true });
  const discard = actions.getByRole("button", { name: "変更を破棄", exact: true });
  const reset = actions.getByRole("button", { name: "既定に戻す", exact: true });
  const builtin = "## Step 1: 合成の既定指示";
  const custom = "## Step 1: 合成の役割\n- 合成の書き方。";

  await field.fill(custom);
  await save.click();
  await expect(save).toBeDisabled();
  expect(requests).toEqual([{ key, method: "PUT", content: custom }]);

  await field.fill("破棄する指示");
  await discard.click();
  await expect(field).toHaveValue(custom);

  await reset.click();
  await expect(field).toHaveValue(builtin);
  await expect(actions.getByText("既定の内容に戻しました。保存すると反映されます。")).toBeVisible();
  expect(requests).toHaveLength(1);
  await discard.click();
  await expect(field).toHaveValue(custom);
  expect(requests).toHaveLength(1);

  await reset.click();
  await save.click();
  await expect(save).toBeDisabled();
  await expect(field).toHaveValue(builtin);
  expect(requests).toEqual([{ key, method: "PUT", content: custom }, { key, method: "DELETE", content: null }]);
  await expectNoPageOverflow(page);
});


test("編集・読み取り専用のプロンプトは所属区画の全幅を使う", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await page.route("**/api/settings/answer-prompts**", route => route.fulfill({ json: answerPromptsEnvelope(DEFAULT_ROLE, false) }));
  await page.goto("/settings/prompts");
  const role = await openRoleField(page);
  const fixed = page.getByRole("textbox", { name: "必ず足す指示：信頼の境界・引用・出力の形（読み取り専用）", exact: true });
  const widths = testInfo.project.name === "desktop" ? [1280, 1920] : [375];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
      const metrics = await role.evaluate(node => {
        const parent = node.closest("[data-form-grid]")!.parentElement!;
        const style = getComputedStyle(parent);
        return { field: node.getBoundingClientRect().width, available: parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) };
      });
      expect(Math.abs(metrics.field - metrics.available)).toBeLessThanOrEqual(2);
      expect(Math.abs((await fixed.boundingBox())!.width - metrics.field)).toBeLessThanOrEqual(2);
      await expectNoPageOverflow(page);
      await role.screenshot({ path: testInfo.outputPath(`rag-role-${width}-${theme}.png`) });
    }
  }
});


test("登録済み FAQ の編集プロンプトも全幅を使う", { tag: "@responsive" }, async ({ page }) => {
  await mockEditableStage(page, "faq_answer", "generation", "system");
  await page.goto("/settings/prompts");
  await page.getByTestId("answer-prompt-stage-faq_answer").locator("summary").click();
  const field = page.getByTestId("answer-prompt-stage-faq_answer").locator("textarea:not([readonly])");
  await expect(field).toBeEditable();
  const delta = await field.evaluate(node => {
    const parent = node.closest("[data-form-grid]")!.parentElement!;
    const style = getComputedStyle(parent);
    return node.getBoundingClientRect().width - (parent.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
  });
  expect(Math.abs(delta)).toBeLessThanOrEqual(2);
});
