import { expect, test, type Page } from "./fixtures/test";
import { mockEditableStage } from "./_feature-prompt-helpers";
import { DEFAULT_STAGES, FLOW_STAGES, type StageFixture } from "./_prompt-stage-fixtures";
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
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false) })
    );

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText("回答生成のプロンプト", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
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
      await route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false) });
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
  test(`回答生成のプロンプトは空を送る前に止め、変更を破棄で保存値へ戻せる（#1010, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: string[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      if (route.request().method() !== "GET") requests.push(route.request().method());
      await route.fulfill({ json: answerPromptsEnvelope("保存版 {{question}} {{answer_context}}", true) });
    });

    await page.goto("/settings/prompts");
    const field = page.getByRole("textbox", { name: "テンプレート", exact: true });
    const actions = page.getByRole("group", { name: "編集内容の操作" });
    const save = actions.getByRole("button", { name: "プロンプトを保存" });
    const discard = actions.getByRole("button", { name: "変更を破棄" });
    await expect(discard).toBeDisabled();

    await field.fill("   ");
    await expect(actions).toContainText("未保存の変更があります。");
    await save.click();
    await expect(page.getByText("テンプレートを入力してください。")).toBeVisible();
    await expect(field).toBeFocused();
    expect(requests).toEqual([]);

    await discard.click();
    await expect(field).toHaveValue("保存版 {{question}} {{answer_context}}");
    await expect(page.getByText("テンプレートを入力してください。")).toHaveCount(0);
    await expect(save).toBeDisabled();
    await expect(actions.getByText(/^編集済み/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("回答生成のプロンプトは編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let content = "保存版 A {{question}} {{answer_context}}";
  await page.route("**/api/settings/answer-prompts**", (route) =>
    route.fulfill({ json: answerPromptsEnvelope(content, true) })
  );

  await page.goto("/settings/prompts");
  const editor = page.getByRole("textbox", { name: "テンプレート", exact: true });
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。初回の取得（StrictMode の再購読による取り直しを含む）が
  // まだ途中だと、フォーカスの再取得はその取得（保存版 A）にまとめられるため、B が届くまで再取得を促す（#833）。
  content = "保存版 B {{question}} {{answer_context}}";
  await expect
    .poll(async () => {
      await refetchOnFocus(page);
      return editor.inputValue();
    })
    .toBe(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("編集中 {{question}} {{answer_context}}");
  content = "保存版 C {{question}} {{answer_context}}";
  const refetched = page.waitForResponse("**/api/settings/answer-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("編集中 {{question}} {{answer_context}}");
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

function answerPromptsEnvelope(content: string, customized: boolean, stages: StageFixture[] = DEFAULT_STAGES) {
  return {
    data: {
      prompts: [
        {
          key: "vlm_answer",
          content,
          default_content: "既定のテンプレート {{question}} {{answer_context}}",
          customized,
          required_placeholders: ["question", "answer_context"],
          updated_at: customized ? "2026-09-26T01:00:00Z" : null,
        },
      ],
      stages,
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
      : route.fulfill({ json: answerPromptsEnvelope("既定 {{question}} {{answer_context}}", false) }));
    await page.goto("/settings/prompts");
    await expect(page.getByText("プロンプトを読み込めませんでした。", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toHaveCount(0);
    failed = false;
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeEditable();
    await expectNoPageOverflow(page);
  });
}

// 新しい工程別の編集欄もテンプレートと同じ保存・破棄・復帰の契約を使う。


test("引用範囲の修復指示を保存・再読込でき、既定への復帰は保存まで取り消せる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const key = "generation.citationRepair";
  const label = "引用範囲の修復（同じ根拠・版を特定でき、行範囲が未指定・不正・範囲外で、是正の予算が残るとき）";
  const requests = await mockEditableStage(page, "generation", "generation", "citationRepair");
  await page.goto("/settings/prompts");
  const stage = page.getByTestId("answer-prompt-stage-generation");
  await stage.locator("summary").click();
  const field = stage.getByRole("textbox", { name: label, exact: true });
  const actions = stage.getByRole("group", { name: "編集内容の操作" });
  const save = actions.getByRole("button", { name: "プロンプトを保存", exact: true });
  const discard = actions.getByRole("button", { name: "変更を破棄", exact: true });
  const reset = actions.getByRole("button", { name: "既定に戻す", exact: true });
  const builtin = "## Step 1: 合成の既定指示";
  const custom = "## Step 1: 合成の引用範囲を確認する";
  await expect(field).toBeEditable();
  await expect(field).toHaveValue(builtin);
  await field.fill(custom);
  await save.click();
  await expect(save).toBeDisabled();
  expect(requests).toEqual([{ key, method: "PUT", content: custom }]);
  await page.reload();
  await stage.locator("summary").click();
  await expect(field).toHaveValue(custom);
  await field.fill("## Step 1: 破棄する変更");
  await discard.click();
  await expect(field).toHaveValue(custom);
  await reset.click();
  await expect(field).toHaveValue(builtin);
  expect(requests).toHaveLength(1);
  await discard.click();
  await expect(field).toHaveValue(custom);
  await reset.click();
  await save.click();
  await expect(save).toBeDisabled();
  expect(requests).toEqual([{ key, method: "PUT", content: custom }, { key, method: "DELETE", content: null }]);
  await page.reload();
  await stage.locator("summary").click();
  await expect(field).toHaveValue(builtin);
  await expect(actions.getByRole("button")).toHaveText(["プロンプトを保存", "変更を破棄", "既定に戻す"]);
  await expect(stage.getByText("既定値", { exact: true })).toHaveCount(0);
  await actions.scrollIntoViewIfNeeded();
  const boxes = await actions.getByRole("button").evaluateAll((buttons) => buttons.map((button) => {
    const box = button.getBoundingClientRect();
    return { x: box.x, y: box.y, right: box.right, bottom: box.bottom };
  }));
  const fieldBox = await field.boundingBox();
  expect(boxes[2].y).toBeGreaterThanOrEqual(fieldBox!.y + fieldBox!.height);
  if (testInfo.project.name === "mobile") {
    expect(boxes[0].bottom).toBeLessThanOrEqual(boxes[1].y);
    expect(boxes[1].bottom).toBeLessThanOrEqual(boxes[2].y);
  } else {
    expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].x);
    expect(boxes[1].right).toBeLessThanOrEqual(boxes[2].x);
  }
  await expectNoPageOverflow(page);
  await stage.screenshot({ path: testInfo.outputPath("citation-range-prompt-editor.png") });
});

// #3753 の受入: #4678 で増えた、該当の入力があるときだけ生成に足す段も同じ契約で編集できる。
for (const [part, label] of [
  ["evidenceReading", "根拠に該当の印・値があるときに入力へ足す読み方"],
  ["guideRules", "業務ガイドがあるときに入力へ足す扱い"],
] as const) {
  test(`入力へ足す段（${part}）は保存・破棄でき、既定への復帰は保存まで確定しない`, { tag: "@responsive" }, async ({ page }) => {
    const key = `generation.${part}`;
    const requests = await mockEditableStage(page, "generation", "generation", part);
    const rejected = "## 合成の読み方（キーの行がない内容）";
    await page.route("**/api/settings/answer-prompts/**", async (route) => {
      if (route.request().method() === "PUT" && route.request().postDataJSON().content === rejected) {
        await route.fulfill({ status: 422, json: { data: null, error_messages: ["「- キー: 説明」の行が使われます。"], warning_messages: [] } });
        return;
      }
      await route.fallback();
    });
    await page.goto("/settings/prompts");
    const stage = page.getByTestId("answer-prompt-stage-generation");
    await stage.locator("summary").click();
    const field = stage.getByRole("textbox", { name: label, exact: true });
    const actions = stage.getByRole("group", { name: "編集内容の操作" });
    const save = actions.getByRole("button", { name: "プロンプトを保存", exact: true });
    const discard = actions.getByRole("button", { name: "変更を破棄", exact: true });
    const reset = actions.getByRole("button", { name: "既定に戻す", exact: true });
    const builtin = "## Step 1: 合成の既定指示";
    const custom = "## 合成の読み方\n- 表の値: 合成の説明。";

    await field.fill(rejected);
    await save.click();
    await expect(actions.getByText("「- キー: 説明」の行が使われます。")).toBeVisible();
    expect(requests).toEqual([]);
    await expect(field).toHaveValue(rejected);

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
    await expect(stage.getByText("既定値", { exact: true })).toHaveCount(0);
    await expectNoPageOverflow(page);
  });
}
