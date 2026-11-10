import { expect, test } from "./fixtures/test";
import { findClippedPlaceholders } from "../../../platform/packages/ui/e2e/layout-checks";
import { expectNoPageOverflow } from "./_helpers";
import { envelope, faqList, faqRecords, mockFaqScopeApi } from "./_faq-scope";

// #4788: 登録済みQ&A の「当てはめない質問」（否定の質問）と、一致度が高いとき回答をそのまま返す設定。

const NEGATIVE = "合成の設定 A を消す方法は？";

test.describe("登録済みQ&A の当てはめない質問とそのまま返す設定", { tag: "@responsive" }, () => {
  test("そのまま返す設定は既定でオフ。切り替えた項目だけを保存し、類似問の提示がオフの間は切り替えられない", async ({ page }) => {
    await mockFaqScopeApi(page);
    const saved: Record<string, unknown>[] = [];
    let settings: Record<string, unknown> = {};
    await page.route("**/api/search-answer-profiles/bv-1/approved-faq/settings", async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      saved.push(body);
      settings = { ...settings, ...body };
      await route.fulfill({ json: envelope(faqList(faqRecords, settings)) });
    });

    await page.goto("/search-answer-profiles?id=bv-1");
    const direct = page.getByTestId("approved-faq-direct-enabled");
    await expect(direct).toHaveAttribute("aria-checked", "false");
    // 何が起きるかと、全体設定の下限を説明に出す。
    await expect(direct).toHaveAccessibleName("一致度が高いときは、Q&A の回答をそのまま返す");
    await expect(direct).toHaveAccessibleDescription(/LLM を使いません.*一致度の下限は 92% です（全体設定）。/);

    await direct.click();
    await expect(direct).toHaveAttribute("aria-checked", "true");
    expect(saved).toEqual([{ direct_answer_enabled: true }]);

    // 類似問の提示をオフにすると Q&A を使わないので、そのまま返す設定は切り替えられない（保存値は残す）。
    await page.getByTestId("approved-faq-enabled").click();
    await expect(direct).toBeDisabled();
    await expect(direct).toHaveAttribute("aria-checked", "true");
    expect(saved).toEqual([{ direct_answer_enabled: true }, { enabled: false }]);
    expect(await findClippedPlaceholders(page)).toEqual([]);
    await expectNoPageOverflow(page);
  });

  test("追加のときに当てはめない質問を 1 行に 1 つ送り、上限を超えると欄の下に出して送らない", async ({ page }) => {
    await mockFaqScopeApi(page, { records: [] });
    const added: Record<string, unknown>[] = [];
    await page.route("**/api/search-answer-profiles/bv-1/approved-faq", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      added.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: envelope({ ...faqList([]), inserted_count: 1, deleted_count: 0 }) });
    });

    await page.goto("/search-answer-profiles?id=bv-1");
    await page.locator("#approved-faq-question").fill("合成の質問");
    await page.locator("#approved-faq-answer").fill("合成の回答");
    const negative = page.locator("#approved-faq-negative");
    await expect(negative).toHaveAccessibleDescription(/この Q&A を当てたくない質問の例/);
    await negative.fill(Array.from({ length: 21 }, (_, index) => `合成の質問 ${index}`).join("\n"));
    await page.getByRole("button", { name: "追加", exact: true }).click();
    await expect(negative).toHaveAccessibleDescription(/当てはめない質問は 20 件までです。/);
    expect(added).toHaveLength(0);

    await negative.fill(`${NEGATIVE}\n\n  合成の設定 A を止めたい  \n`);
    await page.getByRole("button", { name: "追加", exact: true }).click();
    await expect.poll(() => added.length).toBe(1);
    expect(added[0].negative_questions).toEqual([NEGATIVE, "合成の設定 A を止めたい"]);
    await expect(negative).toHaveValue("");
    await expectNoPageOverflow(page);
  });

  test("行に当てはめない質問の件数を出し、行の操作から編集できる。保存できない理由は欄の下に出す", async ({ page }) => {
    const records = faqRecords.map((record) =>
      record.id === "faq-1" ? { ...record, negative_questions: [NEGATIVE] } : record,
    );
    await mockFaqScopeApi(page, { records });
    const saved: unknown[] = [];
    await page.route(
      "**/api/search-answer-profiles/bv-1/approved-faq/faq-1/negative-questions",
      async (route) => {
        const body = route.request().postDataJSON() as { negative_questions: string[] };
        saved.push(body);
        if (body.negative_questions.includes(faqRecords[0].question)) {
          return route.fulfill({
            status: 422,
            json: {
              data: null,
              error_messages: ["当てはめない質問に、この Q&A の質問と同じ文は登録できません。"],
              warning_messages: [],
            },
          });
        }
        await route.fulfill({
          json: envelope(
            faqList(
              records.map((record) =>
                record.id === "faq-1" ? { ...record, negative_questions: body.negative_questions } : record,
              ),
            ),
          ),
        });
      },
    );

    await page.goto("/search-answer-profiles?id=bv-1");
    await expect(page.getByTestId("approved-faq-negative-faq-1")).toHaveText("当てはめない質問: 1 件");
    await expect(page.getByTestId("approved-faq-negative-faq-2")).toHaveCount(0);

    await page.getByTestId("approved-faq-row-actions-faq-1").click();
    await page.getByRole("menuitem", { name: "当てはめない質問を編集" }).click();
    const editor = page.getByTestId("approved-faq-negative-editor");
    await expect(editor.getByText(faqRecords[0].question)).toBeVisible();
    const field = page.locator("#approved-faq-negative-edit");
    await expect(field).toHaveValue(NEGATIVE);

    await field.fill(`${NEGATIVE}\n${faqRecords[0].question}`);
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    await expect(field).toHaveAccessibleDescription(/この Q&A の質問と同じ文は登録できません。/);
    await expect(editor).toBeVisible();

    await field.fill(`${NEGATIVE}\n合成の設定 A を止めたい`);
    await editor.getByRole("button", { name: "保存", exact: true }).click();
    await expect(editor).toHaveCount(0);
    expect(saved.at(-1)).toEqual({ negative_questions: [NEGATIVE, "合成の設定 A を止めたい"] });
    await expect(page.getByTestId("approved-faq-negative-faq-1")).toHaveText("当てはめない質問: 2 件");
    await expectNoPageOverflow(page);
  });
});


test("Excel のプレビューに当てはめない質問を出す", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockFaqScopeApi(page);
  await page.route("**/approved-faq/import/preview", (route) => route.fulfill({ json: envelope({ total: 1, rows: [{ question: "合成の質問 A", answer: "合成の回答 A", row: 2, negative_questions: [NEGATIVE] }] }) }));
  await page.goto("/search-answer-profiles?id=bv-1");
  await page.getByLabel("取込む Excel ファイル").setInputFiles({ name: "synthetic.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mocked preview") });
  await expect(page.getByText(NEGATIVE, { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("faq-import-preview.png"), fullPage: true });
});
