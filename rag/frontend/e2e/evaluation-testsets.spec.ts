import { jumpToPage } from "../../../platform/packages/ui/e2e/layout-checks";
import { expect, test, type Page, type Locator } from "./fixtures/test";
import { expectNoPageOverflow } from "./_helpers";
import { mockTestSetPage } from "./_testsets";
import { chooseEvaluationProfile, mockEvaluationJobs } from "./_evaluation-jobs";
async function chooseSelectFieldOption(trigger: Locator, name: string | RegExp) { await trigger.click(); await trigger.page().getByRole("listbox").getByRole("option", { name }).click(); await trigger.press("Escape"); }

async function createFromResults(page: Page, name = "合成テストセット") {
  await page.goto("/evaluation?tab=testsets");
  await page.getByRole("button", { name: "テストセットを作成", exact: true }).click();
  await page.locator("#testset-name").fill(name);
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByTestId("testset-sources").getByRole("checkbox").last().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  await page.getByRole("button", { name: "テストセットを保存", exact: true }).click();
  await expect(page.getByTestId("testset-editor")).toBeVisible();
}

async function referenceTab(page: Page) {
  const tab = page.getByRole("tab", { name: "参考", exact: true });
  if (await tab.isVisible()) await tab.click();
}

test("長い質問の選択ラベルを裁切せず、全文と確認状態をキーボードで確認できる", { tag: "@responsive" }, async ({ page }, info) => {
  const state = await mockTestSetPage(page, 6);
  await createFromResults(page);
  const saved = state.getSaved()!;
  const items = saved.items.map((item, index) => ({ ...item,
    question: `合成の長い質問 ${index + 1}。${"複数の条件と確認手順を含む説明です。".repeat(12)}`,
    confirmed: index % 2 === 0, standard_answer: index % 2 === 0 ? "合成の標準回答" : "",
  }));
  state.setSaved({ ...saved, items });
  await page.reload();
  await expect(page.locator("#testset-question")).toHaveValue(items[1].question);
  if ((page.viewportSize()?.width ?? 1440) > 1024) {
    const nav = page.locator(".testset-item-nav");
    const button = nav.getByRole("button", { name: `1. ${items[0].question}`, exact: true });
    await button.focus();
    await expect(page.getByRole("tooltip")).toContainText(items[0].question);
    const bounds = await button.evaluate(element => {
      const range = document.createRange(); range.selectNodeContents(element.querySelector("span") ?? element);
      const text = range.getBoundingClientRect(); const box = element.getBoundingClientRect();
      return { textTop: text.top, textBottom: text.bottom, top: box.top, bottom: box.bottom };
    });
    expect(bounds.textTop).toBeGreaterThanOrEqual(bounds.top);
    expect(bounds.textBottom).toBeLessThanOrEqual(bounds.bottom);
    await button.press("Enter");
    await expect(button).toHaveAttribute("aria-current", "true");
    await expect(nav.getByText("確認済み", { exact: true })).toHaveCount(3);
    await expect(nav.getByText("未確認", { exact: true })).toHaveCount(3);
    expect(await nav.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  } else {
    await chooseSelectFieldOption(page.locator("#testset-item-picker"), new RegExp(`^1\\. ${items[0].question}`));
  }
  await expect(page.locator("#testset-question")).toHaveValue(items[0].question);
  await expect(page.locator("#testset-standard")).toHaveValue("合成の標準回答");
  await page.screenshot({ path: info.outputPath("long-question-navigation.png"), fullPage: true });
  await expectNoPageOverflow(page);
});

for (const theme of ["light", "dark"] as const) {
  test(`保存と確認を分けた三欄の編集・参考の切替・直接評価 (${theme})`, { tag: "@responsive" }, async ({ page }, info) => {
    const state = await mockTestSetPage(page);
    const jobs = await mockEvaluationJobs(page, {});
    await createFromResults(page);
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await expect(page.getByRole("heading", { name: "合成テストセット", level: 1 })).toBeVisible();
    await expect(page.locator("#testset-standard")).toHaveValue("");
    await expect(page.getByRole("button", { name: "保存して確認し、次へ" })).toBeDisabled();
    await referenceTab(page);
    await page.getByRole("button", { name: "標準回答に使う", exact: true }).click();
    await page.locator("#testset-standard").fill("人が確認した合成回答");
    await page.getByRole("button", { name: "下書きだけ保存", exact: true }).click();
    await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("人が確認した合成回答");
    expect(state.getSaved()?.items[0].confirmed).toBe(false);
    await expectNoPageOverflow(page);
    await expect(page.getByRole("button", { name: "下書きだけ保存", exact: true })).not.toHaveAttribute("aria-busy", "true");
    await page.getByRole("main").evaluate(element => { element.scrollTo(0, 0); element.querySelectorAll(".testset-review-workspace > *").forEach(pane => pane.scrollTo(0, 0)); });
    await page.screenshot({ path: info.outputPath("review-editor.png"), style: "[data-toast-placement] { visibility: hidden !important; }" });
    await page.getByRole("button", { name: "保存して確認し、次へ" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("review-actions.png"), style: "[data-toast-placement] { visibility: hidden !important; }" });
    await page.getByRole("button", { name: "保存して確認し、次へ" }).click();
    await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
    expect(state.getSaved()?.items[1].confirmed).toBe(false);
    await expect(page.locator("#testset-standard")).toHaveValue("");
    await page.getByRole("button", { name: "前の項目", exact: true }).click();
    await page.locator("#testset-standard").fill("変更した合成回答");
    await expect(page.getByRole("button", { name: "確認済み 0 問で評価" })).toBeDisabled();
    await page.getByRole("button", { name: "保存して確認し、次へ" }).click();
    await page.reload();
    await expect(page.getByTestId("testset-editor")).toBeVisible();
    await page.getByRole("button", { name: "確認済み 1 問で評価" }).click();
    await expect(page).toHaveURL(/testset=draft-a/);
    await chooseEvaluationProfile(page);
    await expect(page.getByText(/確認済み 1 問を使用します/)).toBeVisible();
    await chooseSelectFieldOption(page.getByRole("combobox", { name: "回答モデル", exact: true }), "合成モデル");
    await page.screenshot({ path: info.outputPath("saved-set-run-conditions.png"), fullPage: true });
    await page.getByRole("button", { name: "評価実行", exact: true }).click();
    await expect.poll(() => jobs.runPayloads.length).toBe(1);
    expect(jobs.runPayloads[0]).toMatchObject({ testset_id: "draft-a", testset_revision: state.getSaved()?.revision, cases: [], answer_model_id: "synthetic-model" });
  });
}

test("空のセットへ項目を追加し、変更を保持して参考と編集を切り替える", { tag: "@responsive" }, async ({ page }, info) => {
  const state = await mockTestSetPage(page);
  await page.goto("/evaluation?tab=testsets&set=new");
  await page.locator("#testset-name").fill("手入力の合成セット");
  await chooseSelectFieldOption(page.getByRole("combobox", { name: "作成方法" }), "空のテストセットから作る");
  await page.getByRole("button", { name: "テストセットを保存", exact: true }).click();
  await page.getByRole("button", { name: "質問を追加", exact: true }).click();
  await page.locator("#testset-question").fill("手入力した合成の質問");
  await page.locator("#testset-standard").fill("手入力した合成の回答");
  await referenceTab(page);
  const edit = page.getByRole("tab", { name: "編集", exact: true });
  if (await edit.isVisible()) await edit.click();
  await expect(page.locator("#testset-standard")).toHaveValue("手入力した合成の回答");
  await page.getByRole("button", { name: "保存して確認し、次へ" }).click();
  await expect.poll(() => state.getSaved()?.items[0]?.confirmed).toBe(true);
  await page.screenshot({ path: info.outputPath("manual-reviewed.png"), fullPage: true });
  await page.getByRole("button", { name: "この項目を削除", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "削除", exact: true }).click();
  await page.getByRole("button", { name: "下書きだけ保存", exact: true }).click();
  await expect.poll(() => state.getSaved()?.items.length).toBe(0);
});

test("Excel はプレビュー後も未確認で、明示選択した件数を確認して一括確認する", { tag: "@responsive" }, async ({ page }, info) => {
  const state = await mockTestSetPage(page);
  await page.goto("/evaluation?tab=testsets&set=new");
  await page.locator("#testset-name").fill("取込の合成セット");
  await chooseSelectFieldOption(page.getByRole("combobox", { name: "作成方法" }), "Excel から取り込む");
  await page.locator("#testset-import-file").setInputFiles({ name: "synthetic.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("synthetic fixture") });
  await expect(page.getByText("2 問を取り込みます。すべて未確認で保存されます。")).toBeVisible();
  await page.screenshot({ path: info.outputPath("excel-preview.png"), fullPage: true });
  await page.getByRole("button", { name: "テストセットを保存", exact: true }).click();
  await expect(page.getByTestId("testset-editor")).toBeVisible();
  expect(state.getSaved()?.items.every(value => !value.confirmed)).toBe(true);
  await chooseSelectFieldOption(page.getByRole("combobox", { name: "選択", exact: true }), /1\./);
  await page.getByRole("button", { name: "選んだ項目を確認済みにする", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("1 問を確認済みにしますか？");
  expect(state.getSaved()?.items.every(value => !value.confirmed)).toBe(true);
  await page.getByRole("alertdialog").getByRole("button", { name: "実行", exact: true }).click();
  await expect.poll(() => state.getSaved()?.items.filter(value => value.confirmed).length).toBe(1);
});

test("保存の失敗と競合でも入力を保ち、再試行できる", { tag: "@responsive" }, async ({ page }) => {
  const state = await mockTestSetPage(page);
  await createFromResults(page);
  let failing = true;
  await page.route("**/api/evaluation/testsets/draft-a", route => route.request().method() === "PUT" && failing ? route.fulfill({ status: 409, json: { data: null, error_messages: ["別の画面で更新されました。"] } }) : route.fallback());
  await page.locator("#testset-standard").fill("失敗しても残す合成回答");
  await page.getByRole("button", { name: "下書きだけ保存", exact: true }).click();
  await expect(page.getByTestId("testset-save-error")).toBeVisible();
  await expect(page.locator("#testset-standard")).toHaveValue("失敗しても残す合成回答");
  expect(state.getSaved()?.items[0].confirmed).toBe(false);
  failing = false;
  await page.getByRole("button", { name: "下書きだけ保存", exact: true }).click();
  await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("失敗しても残す合成回答");
});

test("未保存の編集の離脱に確認を出し、キャンセルで入力を保つ", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page);
  await createFromResults(page);
  await page.locator("#testset-standard").fill("未保存の合成回答");
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("alertdialog").getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(page.locator("#testset-standard")).toHaveValue("未保存の合成回答");
});

test("候補を25件でページングして選択をページ間で保つ", { tag: "@responsive" }, async ({ page }) => {
  const state = await mockTestSetPage(page, 28);
  await page.goto("/evaluation?tab=testsets&set=new");
  await page.locator("#testset-name").fill("合成の多数項目セット");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByTestId("testset-sources").getByRole("checkbox").last().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  const candidates = page.getByTestId("testset-candidates");
  await expect(candidates.getByRole("checkbox")).toHaveCount(25);
  await candidates.getByRole("checkbox").first().check();
  await jumpToPage(page.getByRole("navigation", { name: "収録候補" }), 2);
  await expect(candidates.getByRole("checkbox")).toHaveCount(3);
  await candidates.getByRole("checkbox").last().check();
  await page.getByRole("button", { name: "テストセットを保存", exact: true }).click();
  await expect(page.getByTestId("testset-editor")).toBeVisible();
  expect(state.getSaved()?.items.length).toBe(2);
});


test("携帯の編集と参考の読み取り位置を戻し、入力を保持する", { tag: "@responsive" }, async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1440) > 600, "携帯の切り替えを検証する");
  await mockTestSetPage(page);
  await createFromResults(page);
  await expect(page.getByRole("tabpanel", { name: "編集", exact: true })).toBeVisible();
  await page.locator("#testset-standard").fill("未保存の合成回答");
  await page.getByRole("main").evaluate(element => element.scrollTo(0, 300));
  const position = await page.getByRole("main").evaluate(element => element.scrollTop);
  expect(position).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "参考", exact: true }).evaluate(element => (element as HTMLElement).click());
  await expect(page.getByRole("tabpanel", { name: "参考", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "標準回答に使う", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "編集", exact: true }).evaluate(element => (element as HTMLElement).click());
  await expect(page.locator("#testset-standard")).toHaveValue("未保存の合成回答");
  await expect.poll(() => page.getByRole("main").evaluate(element => element.scrollTop)).toBe(position);
});


test("テストセットの取得失敗を空と見せず、再試行で復旧する", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page);
  let failing = true;
  await page.route("**/api/evaluation/testsets", route => failing ? route.fulfill({ status: 500, json: { data: null, error_messages: ["合成の取得失敗"], warning_messages: [] } }) : route.fallback());
  await page.goto("/evaluation?tab=testsets");
  const failure = page.getByRole("alert").filter({ hasText: "合成の取得失敗" });
  await expect(failure).toBeVisible();
  await expect(page.getByText("保存したテストセットはありません。")).toHaveCount(0);
  failing = false;
  await failure.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(failure).toHaveCount(0);
  await expect(page.getByText("保存したテストセットはありません。")).toBeVisible();
});
