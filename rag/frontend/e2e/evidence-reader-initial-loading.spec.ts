import { expect, test } from "./fixtures/test";
import { mockDatabaseReady, mockLocalAuth } from "./_helpers";
import { findLoadingStandardViolations } from "../../../platform/packages/ui/e2e/loading-checks";

// #5435: 根拠を開いた直後（本文がまだ無い）の読み込み中の表示は、本文の領域を 1 つの灰色の枠で埋め、処理名と経過時間の
// 1 行と本文の行の形の Skeleton にし、本文の領域の外（上）に高い枠を出さない。内容はすべて合成のデータ。

const PROFILE = { id: "profile-a", name: "プロファイル A", description: null, status: "ACTIVE", knowledge_base_count: 1, created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:00:00Z", archived_at: null };
const WINDOW = { revision: "v1", anchor_status: "exact", section_path: ["合成の章"], previous_cursor: null, next_cursor: null, outline: [], scope: "paragraph",
  blocks: [{ id: "a", text: "合成の本文です。", offset: 0, highlights: [[0, 8]], cited: true, kind: "text", heading: ["合成の章"] }] };

test("根拠の最初の読み込みは本文の領域の中に 1 行で出し、上に高い枠を出さない (#5435)", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockDatabaseReady(page); await mockLocalAuth(page);
  const name = "synthetic.txt";
  const cited = { document_id: "doc-text", chunk_id: "chunk-text", file_name: name, text: "合成の本文です。", score: 1, metadata: { recipe_id: "r1", chunk_set_id: "cs1", element_ids: ["a"], context_role: "evidence", answer_citation_lines: [1], answer_citation_candidate_lines: [], answer_citation_provenance: "child" } };
  await page.route("**/api/search-answer-profiles**", (route) => route.fulfill({ json: { data: { items: [PROFILE], total: 1, limit: 50, offset: 0, has_next: false }, error_messages: [], warning_messages: [] } }));
  await page.route("**/api/chat/conversations**", (route) => {
    const detail = { id: "c-text", search_answer_profile_id: PROFILE.id, title: "合成", status: "ACTIVE", owned: true, message_count: 2, created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-01T00:00:00Z", messages: [
      { message_id: "u", conversation_id: "c-text", role: "USER", content: "合成の質問です。", status: "COMPLETE", created_at: "2026-11-01T00:00:00Z", citations: [], guardrail_warnings: [] },
      { message_id: "a", reply_to_message_id: "u", model: "synthetic-model", conversation_id: "c-text", role: "ASSISTANT", content: `資料により確認できた事項\n・合成の本文です。\n根拠：${name}`, status: "COMPLETE", created_at: "2026-11-01T00:00:01Z", citations: [cited], guardrail_warnings: [] },
    ] };
    return route.fulfill({ json: { data: new URL(route.request().url()).pathname.endsWith("/c-text") ? detail : { items: [{ ...detail, messages: undefined }], total: 1, offset: 0, limit: 25, has_next: false }, error_messages: [], warning_messages: [] } });
  });
  let release!: () => void;
  await page.route("**/api/documents/doc-text/reader?**", async (route) => {
    await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ json: { data: WINDOW, error_messages: [], warning_messages: [] } });
  });
  await page.goto(`/chat?conversation_id=c-text&search_answer_profile_id=${PROFILE.id}`);
  await page.getByTestId("chat-answer-body").getByRole("button").filter({ hasText: name }).first().click();
  const content = page.getByTestId("evidence-reader-content");
  const loading = page.getByTestId("evidence-reader-loading");
  await expect(loading).toBeVisible();
  // 本文の領域の中にあり、遅延の案内の行を予約しない。
  await expect(content.getByTestId("evidence-reader-loading")).toHaveCount(1);
  await expect(loading.locator("[data-processing-slow]")).toHaveCount(0);
  const indicator = (await loading.getByTestId("evidence-reader-loading-processing").boundingBox())!;
  expect(indicator.height).toBeLessThanOrEqual(page.viewportSize()!.width < 640 ? 64 : 32);
  // 灰色の面は枠の 1 つだけ（本文の領域には付けない）で、枠は本文の領域の高さを埋める。
  await expect(content).not.toHaveClass(/bg-surface-sunken/);
  const contentBox = (await content.boundingBox())!;
  const frameBox = (await loading.boundingBox())!;
  expect(frameBox.height).toBeGreaterThanOrEqual(contentBox.height - 2);
  expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
  await page.getByRole("dialog").last().screenshot({ path: testInfo.outputPath("initial-loading.png") });
  release();
  await expect(content.locator("mark")).toHaveText("合成の本文です。");
  await expect(loading).toHaveCount(0);
});
