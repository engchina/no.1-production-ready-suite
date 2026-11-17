import type { Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import { findClippedPlaceholders } from "../../../platform/packages/ui/e2e/layout-checks";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// #5107: 登録済みQ&Aは質問と回答だけ。プロファイルの権限で全員が見られる。選んだら回答をそのまま返す。
// 内容は合成。

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const summary = {
  id: "bv-1",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
  archived_at: null,
};

const detail = {
  ...summary,
  config: { version: 1, knowledge_base_ids: ["kb-1"], query: { answer_engine: "grounded" } },
  knowledge_bases: [{ id: "kb-1", name: "ナレッジベース A" }],
};

const records = [
  { id: "faq-1", question: "合成の設定 A はどこで変えますか？", answer: "合成の画面 A の設定タブで変えます。" },
  { id: "faq-2", question: "合成の手続き B の期限は？", answer: "合成の手続き B は申請から 5 日以内です。" },
];

function faqList(items: unknown[] = records, settings: Record<string, unknown> = {}) {
  return { search_answer_profile_id: "bv-1", records: items, enabled: true, min_score: 0.92, max_suggestions: 3, ...settings };
}

async function mockFaqApi(page: Page, items: unknown[] = records) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) }),
  );
  await page.route("**/api/search-answer-profiles**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq") && route.request().method() === "GET") {
      return route.fulfill({ json: envelope(faqList(items)) });
    }
    if (path.endsWith("/domain-keywords")) {
      return route.fulfill({ json: envelope({ search_answer_profile_id: "bv-1", keywords: [] }) });
    }
    if (path.endsWith("/runtime-knowledge")) {
      return route.fulfill({ json: envelope({ search_answer_profile_id: "bv-1", terms: [] }) });
    }
    if (path.endsWith("/bv-1")) {
      return route.fulfill({ json: envelope(detail) });
    }
    return route.fulfill({
      json: envelope({ items: [summary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  });
}

test.describe("登録済みQ&Aの管理（質問と回答だけ）", { tag: "@responsive" }, () => {
  test("見える範囲・提示の説明・2 列の取込を示し、そのまま返す設定・対象のナレッジベース・当てはめない質問は無い", async ({
    page,
  }, testInfo) => {
    await mockFaqApi(page);
    await page.goto("/search-answer-profiles?id=bv-1");

    await expect(page.getByTestId("approved-faq-visibility")).toHaveText(
      /この検索・回答プロファイルの権限を持つ人は、全員ここのQ&Aを見られます（ナレッジベースの権限では判定しません）。/,
    );
    await expect(page.getByTestId("approved-faq-visibility")).toContainText("文書としてナレッジベースに登録");
    await expect(page.locator("#approved-faq-enabled-help")).toContainText("一致度 92% 以上・最大 3 件");
    await expect(page.locator("#approved-faq-enabled-help")).toContainText("選んだQ&Aの回答をそのまま返し");
    await expect(page.locator("#approved-faq-enabled-help")).not.toContainText("LLM に渡します");
    await expect(page.getByTestId("approved-faq-direct-enabled")).toHaveCount(0);
    await expect(page.getByRole("rowheader", { name: records[0].question })).toBeVisible();

    // 追加の欄は質問と回答だけ。
    await expect(page.locator("#approved-faq-question")).toBeVisible();
    await expect(page.locator("#approved-faq-answer")).toBeVisible();
    await expect(page.locator("#approved-faq-negative")).toHaveCount(0);
    await expect(page.locator("#approved-faq-targets")).toHaveCount(0);
    await expect(page.locator("#approved-faq-import-targets")).toHaveCount(0);

    await expect(page.getByTestId("approved-faq-import-help")).toContainText("ほかの列は無視します");
    await expect(page.getByTestId("approved-faq-template")).toHaveText("テンプレートをダウンロード");

    expect(await findClippedPlaceholders(page)).toEqual([]);
    await expectNoPageOverflow(page);
    await page.getByRole("tabpanel").screenshot({ path: testInfo.outputPath("approved-faq-tab.png") });
  });
});

test("追加は質問と回答だけを送る", async ({ page }) => {
  await mockFaqApi(page, []);
  const added: unknown[] = [];
  await page.route("**/api/search-answer-profiles/bv-1/approved-faq", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    added.push(route.request().postDataJSON());
    await route.fulfill({ json: envelope({ ...faqList([records[0]]), inserted_count: 1, deleted_count: 0 }) });
  });

  await page.goto("/search-answer-profiles?id=bv-1");
  await page.locator("#approved-faq-question").fill(records[0].question);
  await page.locator("#approved-faq-answer").fill(records[0].answer);
  await page.getByRole("button", { name: "追加", exact: true }).click();
  await expect.poll(() => added.length).toBe(1);
  expect(added[0]).toEqual({ question: records[0].question, answer: records[0].answer });
  await expect(page.getByRole("rowheader", { name: records[0].question })).toBeVisible();
});

test("提示のオン / オフは enabled だけを保存する", async ({ page }) => {
  await mockFaqApi(page);
  const saved: unknown[] = [];
  await page.route("**/api/search-answer-profiles/bv-1/approved-faq/settings", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    saved.push(body);
    await route.fulfill({ json: envelope(faqList(records, body)) });
  });

  await page.goto("/search-answer-profiles?id=bv-1");
  const toggle = page.getByTestId("approved-faq-enabled");
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0]).toEqual({ enabled: false });
  await expect(toggle).not.toBeChecked();
});

test("テンプレートをダウンロードし、取込は取込モードだけを送る", async ({ page }) => {
  await mockFaqApi(page, []);
  let templateRequests = 0;
  await page.route("**/api/search-answer-profiles/bv-1/approved-faq/import/template", async (route) => {
    templateRequests += 1;
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-disposition": 'attachment; filename="approved_faq_template.xlsx"',
      },
      body: Buffer.from("synthetic template"),
    });
  });
  await page.route("**/api/search-answer-profiles/bv-1/approved-faq/import/preview", (route) =>
    route.fulfill({ json: envelope({ total: 1, rows: [{ question: "合成の質問 A", answer: "合成の回答 A", row: 2 }] }) }),
  );
  const imported: string[] = [];
  await page.route("**/api/search-answer-profiles/bv-1/approved-faq/import", async (route) => {
    imported.push(route.request().postData() ?? "");
    await route.fulfill({ json: envelope({ ...faqList([]), inserted_count: 1, deleted_count: 0 }) });
  });

  await page.goto("/search-answer-profiles?id=bv-1");
  const download = page.waitForEvent("download");
  await page.getByTestId("approved-faq-template").click();
  expect((await download).suggestedFilename()).toBe("approved_faq_template.xlsx");
  expect(templateRequests).toBe(1);

  await page.getByLabel("取込む Excel ファイル").setInputFiles({
    name: "synthetic.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mocked preview"),
  });
  await expect(page.getByText("合成の質問 A", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取込", exact: true }).click();
  await expect.poll(() => imported.length).toBe(1);
  expect(imported[0]).toContain('name="mode"');
  expect(imported[0]).not.toContain("target_knowledge_base_ids");
});
