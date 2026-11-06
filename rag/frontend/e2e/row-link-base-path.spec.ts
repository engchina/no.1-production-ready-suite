import { expect, type Page, test } from "./fixtures/test";
import { apiEnvelope, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// #4560: 一覧の題名のリンク（RowTitleButton の href）は、配信の base（`/rag/` の下の配備）を含む文書の URL にする。
// 画面内のクリックは React Router が base を補うが、新しいタブ・中クリック・URL のコピーは href をそのまま使う。
// 根パスの通常の e2e に加え、`RAG_E2E_BASE_PATH=/rag/` の実行で配備と同じ base を確かめる。
const BASE = (process.env.RAG_E2E_BASE_PATH ?? "").replace(/\/$/, "");

/** request の path から base を除いた `/api/...`。 */
function apiPath(url: string) {
  const { pathname } = new URL(url);
  return pathname.slice(pathname.indexOf("/api/"));
}

const RUN = {
  run_id: "run-a",
  surface: "search",
  search_answer_profile_id: "sap-a",
  search_answer_profile_name: "プロファイル A",
  question: "合成の質問 A",
  answer_generated: false,
  status: "success",
  answer_outcome: null,
  verification: null,
  confidence: null,
  needs_human_review: null,
  citation_count: 0,
  elapsed_ms: 1200,
  error_stage: null,
  model_id: null,
  conversation_id: null,
  message_id: null,
  created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
  executed_by_me: true,
  executor_display_name: "ローカル利用者",
};

const PROFILE = {
  id: "sap-a",
  name: "プロファイル A",
  description: "合成の説明",
  status: "ACTIVE",
  knowledge_base_count: 0,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  archived_at: null,
};

async function mockApis(page: Page) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/history/runs**", async (route) => {
    const path = apiPath(route.request().url());
    if (path === `/api/history/runs/${RUN.run_id}`) {
      await route.fulfill({
        json: apiEnvelope({
          ...RUN,
          answer: null,
          answer_available: false,
          answer_diagnostics: null,
          citations: [],
          withheld_evidence_count: 0,
          feedback: [],
          conversation_owner_display_name: null,
          owned_by_me: null,
          error_type: null,
          search_conditions: null,
        }),
      });
      return;
    }
    if (path.startsWith("/api/history/runs/")) {
      await route.fulfill({ status: 404, json: { data: null, error_messages: ["見つかりません。"], warning_messages: [] } });
      return;
    }
    await route.fulfill({ json: apiEnvelope({ items: [RUN], total: 1, limit: 25, offset: 0, has_next: false }) });
  });
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: apiEnvelope({ items: [], total: 0, limit: 100, offset: 0, has_next: false }) })
  );
  await page.route("**/api/search-answer-profiles**", async (route) => {
    const path = apiPath(route.request().url());
    if (path === `/api/search-answer-profiles/${PROFILE.id}`) {
      await route.fulfill({
        json: apiEnvelope({ ...PROFILE, config: { version: 1, knowledge_base_ids: [], query: {} }, knowledge_bases: [] }),
      });
      return;
    }
    if (path.startsWith(`/api/search-answer-profiles/${PROFILE.id}/`)) {
      await route.fulfill({ json: apiEnvelope({}) });
      return;
    }
    await route.fulfill({ json: apiEnvelope({ items: [PROFILE], total: 1, limit: 25, offset: 0, has_next: false }) });
  });
}

test.describe("一覧の題名のリンクの base（#4560）", { tag: "@responsive" }, () => {
  test.beforeEach(async ({ page }) => {
    await mockApis(page);
  });

  test("実行履歴の行の題名の href は base を含み、新しいタブでも詳細を開く", async ({ page }, testInfo) => {

    await page.goto(`${BASE}/history`);
    const link = page.getByRole("link", { name: /合成の質問 A/ });
    await expect(link).toHaveAttribute("href", `${BASE}/history/${RUN.run_id}`);
    await page.screenshot({ path: testInfo.outputPath("history-row-link.png") });

    // 画面内のクリックは今までどおり同じ詳細へ移る（base を二重に付けない）。
    const href = (await link.getAttribute("href"))!;
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${BASE}/history/${RUN.run_id}$`));
    await expect(page.getByTestId("run-history-detail")).toBeVisible();

    // 新しいタブ・URL のコピーと同じく、href をそのまま読み込んでも詳細を開く。
    await page.goto(href);
    await expect(page.getByTestId("run-history-detail")).toBeVisible();
    await expect(page.getByText("合成の質問 A").first()).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("history-row-link-opened.png") });
  });

  test("検索・回答プロファイルの行の題名の href は base を含み、新しいタブでもエディタを開く", async ({ page }, testInfo) => {
    await page.goto(`${BASE}/search-answer-profiles`);
    const link = page.getByRole("link", { name: /プロファイル A/ });
    await expect(link).toHaveAttribute("href", `${BASE}/search-answer-profiles?id=${PROFILE.id}`);
    await page.screenshot({ path: testInfo.outputPath("profiles-row-link.png") });

    const href = (await link.getAttribute("href"))!;
    await link.click();
    await expect(page).toHaveURL(new RegExp(`${BASE}/search-answer-profiles\\?id=${PROFILE.id}$`));
    await expect(page.getByRole("heading", { name: "プロファイル A", level: 1 })).toBeVisible();

    // 新しいタブ・URL のコピーと同じく、href をそのまま読み込んでもエディタを開く。
    await page.goto(href);
    await expect(page.getByRole("heading", { name: "プロファイル A", level: 1 })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("profiles-row-link-opened.png") });
  });
});
