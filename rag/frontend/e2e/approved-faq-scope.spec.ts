import { expect, test } from "./fixtures/test";
import { findClippedPlaceholders } from "../../../platform/packages/ui/e2e/layout-checks";
import { expectNoPageOverflow } from "./_helpers";
import { envelope, faqList, faqRecords, mockFaqScopeApi } from "./_faq-scope";

// #4621: 承認済み FAQ の対象のナレッジベース・根拠の状態（要確認 / 原文の更新）・承認者と、確かめ直し。

test.describe("承認済み FAQ の対象と根拠の状態", { tag: "@responsive" }, () => {
  test("要確認・原文の更新・対象・承認者を出し、対象を確かめ直せる", async ({ page }) => {
    await mockFaqScopeApi(page);
    const confirms: unknown[] = [];
    await page.route("**/api/search-answer-profiles/bv-1/approved-faq/faq-2/confirm", async (route) => {
      confirms.push(route.request().postDataJSON());
      const restored = faqRecords.map((record) =>
        record.id === "faq-2"
          ? {
              ...record,
              target_knowledge_base_ids: ["kb-1"],
              review_reasons: [],
              approved_by: "ローカル利用者",
              approved_at: "2026-11-08T01:00:00Z",
            }
          : record,
      );
      await route.fulfill({ json: envelope(faqList(restored)) });
    });

    await page.goto("/search-answer-profiles?id=bv-1");
    const reviewRow = page.getByTestId("approved-faq-row-faq-2");
    await expect(reviewRow.getByText("要確認", { exact: true })).toBeVisible();
    await expect(reviewRow).toContainText("対象のナレッジベースがプロファイルから外れました。");
    await expect(page.getByTestId("approved-faq-targets-faq-2")).toHaveText(
      "対象: ナレッジベース A、ナレッジベース C（プロファイルから外れた）",
    );
    await expect(page.getByTestId("approved-faq-approval-faq-2")).toHaveText("承認: 記録なし");
    await expect(page.getByTestId("approved-faq-approval-faq-1")).toContainText("承認: 管理者 A・");
    const updatedRow = page.getByTestId("approved-faq-row-faq-3");
    await expect(updatedRow.getByText("原文が更新されました", { exact: true })).toBeVisible();
    // 問題の無い行には状態の印と行の中の確かめ直しを出さない。
    await expect(page.getByTestId("approved-faq-row-faq-1").getByText("要確認")).toHaveCount(0);
    await expect(page.getByTestId("approved-faq-confirm-faq-1")).toHaveCount(0);

    await page.getByTestId("approved-faq-confirm-faq-2").click();
    const sheet = page.getByRole("dialog", { name: "対象を確かめ直す" });
    await expect(sheet).toBeVisible();
    // 初期値はプロファイルに残る今の対象（外れた KB は選択肢にも出さない）。
    await expect(sheet.getByLabel("ナレッジベース A を選択から外す")).toBeVisible();
    await expect(sheet.getByLabel("ナレッジベース C を選択から外す")).toHaveCount(0);
    await sheet.getByRole("button", { name: "確かめて承認" }).click();

    await expect(sheet).toBeHidden();
    expect(confirms).toEqual([{ target_knowledge_base_ids: ["kb-1"] }]);
    await expect(page.getByTestId("approved-faq-row-faq-2").getByText("要確認")).toHaveCount(0);
    await expect(page.getByTestId("approved-faq-approval-faq-2")).toContainText("承認: ローカル利用者・");
    // 対象の選択の placeholder は 375px の欄にも収める。
    expect(await findClippedPlaceholders(page)).toEqual([]);
    await expectNoPageOverflow(page);
  });

  test("追加の対象は既定でプロファイルの全ナレッジベース、空なら欄の下に出して送らない", async ({ page }) => {
    await mockFaqScopeApi(page, { records: [] });
    const added: unknown[] = [];
    await page.route("**/api/search-answer-profiles/bv-1/approved-faq", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      added.push(route.request().postDataJSON());
      await route.fulfill({ json: envelope({ ...faqList([]), inserted_count: 1, deleted_count: 0 }) });
    });

    await page.goto("/search-answer-profiles?id=bv-1");
    await page.locator("#approved-faq-question").fill("合成の質問");
    await page.locator("#approved-faq-answer").fill("合成の回答");
    // 全部外すと、追加を押したときに欄の下へ出して送らない。
    const addSection = page.getByRole("heading", { name: "Q&A を追加" }).locator("..");
    await expect(addSection.getByLabel("ナレッジベース A を選択から外す")).toBeVisible();
    await addSection.getByLabel("ナレッジベース A を選択から外す").click();
    await addSection.getByLabel("ナレッジベース B を選択から外す").click();
    await page.getByRole("button", { name: "追加", exact: true }).click();
    await expect(page.locator("#approved-faq-targets")).toHaveAccessibleDescription(
      /対象のナレッジベースを 1 つ以上選んでください。/,
    );
    expect(added).toHaveLength(0);
  });

  test("追加は選んだ対象（既定は全部）を送る", async ({ page }) => {
    await mockFaqScopeApi(page, { records: [] });
    const added: unknown[] = [];
    await page.route("**/api/search-answer-profiles/bv-1/approved-faq", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      added.push(route.request().postDataJSON());
      await route.fulfill({ json: envelope({ ...faqList([]), inserted_count: 1, deleted_count: 0 }) });
    });

    await page.goto("/search-answer-profiles?id=bv-1");
    await page.locator("#approved-faq-question").fill("合成の質問");
    await page.locator("#approved-faq-answer").fill("合成の回答");
    await expect(
      page.getByRole("heading", { name: "Q&A を追加" }).locator("..").getByLabel("ナレッジベース B を選択から外す"),
    ).toBeVisible();
    await page.getByRole("button", { name: "追加", exact: true }).click();
    await expect.poll(() => added.length).toBe(1);
    expect(added[0]).toEqual({
      question: "合成の質問",
      answer: "合成の回答",
      target_knowledge_base_ids: ["kb-1", "kb-2"],
      negative_questions: [],
    });
  });
});
