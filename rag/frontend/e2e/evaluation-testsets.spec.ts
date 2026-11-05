import { jumpToPage } from "../../../platform/packages/ui/e2e/layout-checks";
import { readFile } from "node:fs/promises";
import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow } from "./_helpers";
import { mockTestSetPage } from "./_testsets";
import { chooseEvaluationProfile, mockEvaluationImport } from "./_evaluation-jobs";

// ファイル自体の六列互換と位置の復元は backend の reader/writer 往復で検証する。
// ここでは明示選択・編集・保存再開・取得したファイルのアップロードを実画面で検証する。
test("本人の評価から一人で下書きを保存し、明示確認してダウンロードと再取り込みへ進む", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const state = await mockTestSetPage(page);
  await page.goto("/evaluation?tab=calibration");
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.getByRole("textbox", { name: "下書きの名前", exact: true }).fill("合成テストセット");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByTestId("testset-sources").getByRole("checkbox").last().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  const candidates = page.getByTestId("testset-candidates");
  await expect(candidates.getByRole("checkbox")).toHaveCount(2);
  await expect(candidates.getByRole("checkbox").first()).not.toBeChecked();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  await page.getByRole("button", { name: "選んだ項目で下書きを保存" }).click();
  await expect(page.getByRole("textbox", { name: "標準回答", exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "この項目の収録を確認する" })).toBeDisabled();
  await page.getByText("参考：生成された回答と根拠", { exact: true }).click();
  await page.getByRole("button", { name: "標準回答に使う", exact: true }).click();
  await page.getByRole("textbox", { name: "標準回答", exact: true }).fill("確認した合成の標準回答");
  await page.getByRole("button", { name: "下書きを保存", exact: true }).click();
  await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("確認した合成の標準回答");
  await page.reload();
  await page.getByRole("button", { name: "合成テストセット", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "標準回答", exact: true })).toHaveValue("確認した合成の標準回答");
  await page.getByRole("button", { name: "この項目の収録を確認する" }).click();
  await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
  expect(state.getSaved()?.items[1].confirmed).toBe(false);
  await expectNoPageOverflow(page);
  await page.getByRole("textbox", { name: "標準回答", exact: true }).fill("再確認した合成の標準回答");
  await expect(page.getByRole("button", { name: "テストデータをダウンロード", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "この項目の収録を確認する" }).click();
  await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
  await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("再確認した合成の標準回答");
  await page.locator("#testset-draft-name").scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("testset-editor-top.png"), animations: "disabled", style: "[data-toast-placement] { visibility: hidden !important; }" });
  await page.getByRole("button", { name: "テストデータをダウンロード", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("testset-editor-actions.png"), animations: "disabled", style: "[data-toast-placement] { visibility: hidden !important; }" });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "テストデータをダウンロード", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("evaluation_testset.xlsx");
  const path = await download.path();
  expect(path).toBeTruthy();
  const bytes = await readFile(path!);
  await page.getByRole("tab", { name: "評価の実行", exact: true }).click();
  await mockEvaluationImport(page);
  await chooseEvaluationProfile(page);
  await page.getByTestId("evaluation-request-file").setInputFiles({ name: download.suggestedFilename(), mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: bytes });
  await expect(page.getByTestId("evaluation-import-ready")).toBeVisible();
});

test("保存前の離脱に確認が出る", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page);
  await page.goto("/evaluation?tab=calibration");
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.getByRole("textbox", { name: "下書きの名前", exact: true }).fill("未保存の合成下書き");
  await page.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  test(`作成と編集の入力幅と局所操作を共通規約にそろえる (${theme})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await mockTestSetPage(page);
    await page.goto("/evaluation?tab=calibration");
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await page.getByRole("button", { name: "新しい下書きを作る" }).click();
    const assertWidth = async (selector: string) => {
      const box = await page.locator(selector).evaluate(el => ({ width: el.getBoundingClientRect().width, root: parseFloat(getComputedStyle(document.documentElement).fontSize), viewport: innerWidth, form: Boolean(el.closest("[data-form-grid]")) }));
      expect(box.form).toBe(true);
      if (box.viewport >= 640) expect(box.width).toBeCloseTo(box.root * 20, 0);
      else expect(box.width).toBeLessThan(box.viewport);
    };
    await assertWidth("#testset-name");
    await page.locator("#testset-name").focus();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("testset-sources").getByRole("checkbox").first()).toBeFocused();
    await page.locator("#testset-name").fill("合成テストセット");
    await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
    const show = page.getByRole("button", { name: "評価の項目を表示する" });
    if ((page.viewportSize()?.width ?? 0) >= 640) expect((await show.boundingBox())!.width).toBeLessThan(400);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath(`create-${theme}.png`), animations: "disabled" });
    await show.click();
    await page.getByRole("button", { name: "すべて選択", exact: true }).click();
    await page.getByRole("button", { name: "選んだ項目で下書きを保存" }).click();
    await assertWidth("#testset-draft-name");
    const text = await page.locator("#testset-standard").evaluate(el => ({ width: el.getBoundingClientRect().width, root: parseFloat(getComputedStyle(document.documentElement).fontSize), form: Boolean(el.closest("[data-form-grid]")) }));
    expect(text.form).toBe(true);
    expect(text.width).toBeLessThanOrEqual(text.root * 60);
    await expectNoPageOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: testInfo.outputPath(`edit-${theme}.png`), animations: "disabled" });
  });
}

test("作成と保存の失敗を確定行の直上に出し入力を保って再試行する", { tag: "@responsive" }, async ({ page }) => {
  const state = await mockTestSetPage(page);
  let failCreate = true;
  let failSave = true;
  await page.route("**/api/evaluation/testsets**", route => {
    const req = route.request();
    if ((req.method() === "POST" && new URL(req.url()).pathname.endsWith("/testsets") && failCreate) || (req.method() === "PUT" && failSave)) {
      if (req.method() === "POST") failCreate = false;
      else failSave = false;
      return route.fulfill({ status: 500, json: { data: null, error_messages: [{ message: "合成の保存失敗です。もう一度保存してください。" }], warning_messages: [] } });
    }
    return route.fallback();
  });
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.locator("#testset-name").fill("再試行する合成下書き");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  const create = page.getByRole("button", { name: "選んだ項目で下書きを保存" });
  await create.click();
  const createError = page.getByTestId("testset-create-error");
  await expect(createError.getByRole("alert")).toBeVisible();
  await expect(page.locator("#testset-name")).toHaveValue("再試行する合成下書き");
  expect((await createError.boundingBox())!.y).toBeLessThan((await create.boundingBox())!.y);
  await create.click();
  await page.locator("#testset-standard").fill("再試行後の合成標準回答");
  const save = page.getByRole("button", { name: "下書きを保存", exact: true });
  await save.click();
  await expect(page.getByTestId("testset-save-error").getByRole("alert")).toBeVisible();
  await expect(page.locator("#testset-standard")).toHaveValue("再試行後の合成標準回答");
  await expect(page.getByRole("button", { name: "テストデータをダウンロード" })).toBeDisabled();
  await save.click();
  await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("再試行後の合成標準回答");
  await expect(page.getByTestId("testset-save-error")).toHaveCount(0);
});

test("候補を25件でページングして選択をページ間で保つ", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page, 27);
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByTestId("testset-sources").getByRole("checkbox").last().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  const candidates = page.getByTestId("testset-candidates");
  await expect(candidates.getByRole("checkbox")).toHaveCount(25);
  await candidates.getByRole("checkbox").first().check();
  const pagination = page.getByRole("navigation", { name: "収録候補", exact: true });
  await jumpToPage(pagination, 2);
  await expect(candidates.getByRole("checkbox")).toHaveCount(2);
  await jumpToPage(pagination, 1);
  await expect(candidates.getByRole("checkbox").first()).toBeChecked();
  await expectNoPageOverflow(page);
});

test("作成要求の処理中は送信済みの名前と選択を固定する", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/evaluation/testsets", async route => {
    if (route.request().method() === "POST") await pending;
    await route.fallback();
  });
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.locator("#testset-name").fill("作成待ちの合成下書き");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  const requested = page.waitForRequest(req => req.method() === "POST" && new URL(req.url()).pathname.endsWith("/testsets"));
  await page.getByRole("button", { name: "選んだ項目で下書きを保存" }).click();
  await requested;
  try {
    await expect(page.locator("#testset-name")).toBeDisabled();
    for (const checkbox of await page.getByRole("checkbox").all()) await expect(checkbox).toBeDisabled();
    await expect(page.getByRole("button", { name: "すべて選択", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "選択を解除", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "キャンセル", exact: true })).toBeDisabled();
  } finally { release(); }
  await expect(page.locator("#testset-draft-name")).toHaveValue("作成待ちの合成下書き");
});

test("内容保存後に確認だけ失敗しても未確認を保ち再試行できる", { tag: "@responsive" }, async ({ page }) => {
  const state = await mockTestSetPage(page);
  let failConfirm = true;
  await page.route("**/api/evaluation/testsets/draft-a", route => {
    const request = route.request();
    if (request.method() === "PUT" && request.postDataJSON().items.some((item: { confirmed: boolean }) => item.confirmed) && failConfirm) {
      failConfirm = false;
      return route.fulfill({ status: 500, json: { data: null, error_messages: [{ message: "合成の確認保存失敗です。再試行してください。" }], warning_messages: [] } });
    }
    return route.fallback();
  });
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.locator("#testset-name").fill("確認を再試行する合成下書き");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  await page.getByRole("button", { name: "選んだ項目で下書きを保存" }).click();
  await page.locator("#testset-standard").fill("保存は成功する合成標準回答");
  const confirm = page.getByRole("button", { name: "この項目の収録を確認する" });
  await confirm.click();
  await expect(page.getByTestId("testset-save-error").getByRole("alert")).toBeVisible();
  expect(state.getSaved()?.items[0].standard_answer).toBe("保存は成功する合成標準回答");
  expect(state.getSaved()?.items[0].confirmed).toBe(false);
  await expect(page.getByTestId("testset-editor").getByRole("group",{name:"下書きを保存",exact:true}).getByRole("alert")).toHaveText("保存に失敗しました。");
  await expect(page.getByRole("button", { name: "テストデータをダウンロード" })).toBeDisabled();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
  await expect(page.getByRole("button", { name: "テストデータをダウンロード" })).toBeEnabled();
  await expect(page.getByTestId("testset-editor").getByRole("group",{name:"下書きを保存",exact:true}).getByRole("status")).toHaveText("保存済みです。");
});

for (const theme of ["light", "dark"] as const) {
  test(`下書きの保存状態を常設し失敗から再試行まで示す (${theme})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    const state = await mockTestSetPage(page);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release=resolve; });
    let failSave=true;
    await page.route("**/api/evaluation/testsets/draft-a", async route => {
      if (route.request().method()==="PUT" && failSave) {
        failSave=false;
        await pending;
        return route.fulfill({status:500,json:{data:null,error_messages:[{message:"合成の保存失敗です。再試行してください。"}],warning_messages:[]}});
      }
      return route.fallback();
    });
    await page.goto("/evaluation?tab=calibration");
    await page.evaluate(value => {document.documentElement.dataset.theme=value;},theme);
    await page.getByRole("button",{name:"新しい下書きを作る"}).click();
    await page.locator("#testset-name").fill("保存状態の合成下書き");
    await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
    await page.getByRole("button",{name:"評価の項目を表示する"}).click();
    await page.getByRole("button",{name:"すべて選択",exact:true}).click();
    await page.getByRole("button",{name:"選んだ項目で下書きを保存"}).click();
    const editor=page.getByTestId("testset-editor");
    const actions=editor.getByRole("group",{name:"下書きを保存",exact:true});
    const save=actions.getByRole("button",{name:"下書きを保存",exact:true});
    const capture=async (stage:string) => {
      await actions.evaluate(element => element.scrollIntoView({block:"center"}));
      await expectNoPageOverflow(page);
      await page.screenshot({path:testInfo.outputPath(`save-status-${stage}-${theme}.png`),animations:"disabled",style:"[data-toast-placement] { visibility: hidden !important; }"});
    };
    await expect(actions.getByRole("status")).toHaveText("保存済みです。");
    await expect(save).toBeDisabled();
    await capture("saved");
    await page.locator("#testset-standard").fill("保存状態を確認する合成回答");
    await expect(actions.getByRole("status")).toHaveText("未保存の変更があります。");
    await expect(page.getByRole("button",{name:"テストデータをダウンロード"})).toBeDisabled();
    await capture("unsaved");
    const requested=page.waitForRequest(req => req.method()==="PUT" && req.url().endsWith("/testsets/draft-a"));
    await save.click();await requested;
    try {
      await expect(actions.getByRole("status")).toHaveText("保存しています。");
      await expect(page.locator("#testset-standard")).toBeDisabled();
      await capture("saving");
    } finally {release();}
    await expect(actions.getByRole("alert")).toHaveText("保存に失敗しました。");
    await expect(page.getByTestId("testset-save-error").getByRole("alert")).toBeVisible();
    await expect(page.locator("#testset-standard")).toHaveValue("保存状態を確認する合成回答");
    expect(state.getSaved()?.items[0].standard_answer).toBe("");
    await capture("failed");
    await save.click();
    await expect(actions.getByRole("status")).toHaveText("保存済みです。");
    await expect(page.getByTestId("testset-save-error")).toHaveCount(0);
    expect(state.getSaved()?.items[0].standard_answer).toBe("保存状態を確認する合成回答");
    expect(state.getSaved()?.items[0].confirmed).toBe(false);
    await expect(save).toBeDisabled();
    await capture("retry-saved");
    await page.getByRole("button",{name:"この項目の収録を確認する"}).click();
    await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
    await expect(page.getByRole("button",{name:"テストデータをダウンロード"})).toBeEnabled();
    await page.reload();
    await page.getByRole("button",{name:"保存状態の合成下書き",exact:true}).click();
    await expect(actions.getByRole("status")).toHaveText("保存済みです。");
    await expect(page.locator("#testset-standard")).toHaveValue("保存状態を確認する合成回答");
    await capture("reopened");
  });
}
