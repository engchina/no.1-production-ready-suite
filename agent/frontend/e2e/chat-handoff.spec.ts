import { expect, test } from "./fixtures/mock-api";

// #1283: RAG のチャットから引き継いだ質問（`/chat?question=&entry=rag_escalation&reason=`）。
// 入力欄に入れるだけで自動では送らず、送ると Run の metadata に入口と理由を残す。

const QUESTION = "受注 A-100 の今の承認状態を確かめたい";

function handoffUrl(question: string, entry = "rag_escalation", reason = "needs_environment_data") {
  const params = new URLSearchParams({ question, entry, reason });
  return `/chat?${params.toString()}`;
}

test("RAG から引き継いだ質問は入力欄に入るだけで、送ると入口と理由を Run に残す", async ({ page, mockApi }, testInfo) => {
  await page.goto(handoffUrl(QUESTION));

  const composer = page.getByRole("textbox", { name: "質問" });
  await expect(composer).toHaveValue(QUESTION);
  await expect(composer).toBeFocused();
  await expect(page.getByTestId("chat-handoff-notice")).toContainText("RAG のチャットから引き継いだ質問です");
  // 読んだ query は URL から外す（再読込・戻るで下書きを上書きしない）。
  await expect(page).toHaveURL(/\/chat$/);
  // 自動では送らない。
  expect(mockApi.lastRequest("POST", "/api/runs")).toBeUndefined();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("chat-handoff.png"), fullPage: true });

  // 再読込しても送るまで下書きと入口を残す。
  await page.reload();
  await expect(composer).toHaveValue(QUESTION);
  await expect(page.getByTestId("chat-handoff-notice")).toBeVisible();

  await composer.press("Enter");
  await expect(page.getByTestId("chat-conversation").getByText(`「${QUESTION}」への回答です。`)).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/runs")?.body).toEqual({
    goal: QUESTION,
    agent_id: "default",
    metadata: { entry: "rag_escalation", entry_reason: "needs_environment_data" },
  });
  await expect(page.getByTestId("chat-handoff-notice")).toHaveCount(0);

  // 次の質問には入口を付けない（引き継いだのは最初の質問だけ）。
  await composer.fill("ほかの受注は？");
  await composer.press("Enter");
  await expect(page.getByTestId("chat-conversation").getByText("「ほかの受注は？」への回答です。")).toBeVisible();
  expect((mockApi.lastRequest("POST", "/api/runs")?.body as { metadata?: unknown }).metadata).toBeUndefined();
});

test("知らない入口は質問だけを入れ、下書きを消すと案内も消える", async ({ page, mockApi }) => {
  await page.goto(handoffUrl("質問だけ", "unknown"));
  const composer = page.getByRole("textbox", { name: "質問" });
  await expect(composer).toHaveValue("質問だけ");
  await expect(page.getByTestId("chat-handoff-notice")).toHaveCount(0);
  await composer.press("Enter");
  await expect(page.getByTestId("chat-conversation").getByText("「質問だけ」への回答です。")).toBeVisible();
  expect((mockApi.lastRequest("POST", "/api/runs")?.body as { metadata?: unknown }).metadata).toBeUndefined();

  await page.goto(handoffUrl(QUESTION));
  await expect(page.getByTestId("chat-handoff-notice")).toBeVisible();
  await composer.fill("");
  await expect(page.getByTestId("chat-handoff-notice")).toHaveCount(0);
  await composer.fill("別の質問");
  await expect(page.getByTestId("chat-handoff-notice")).toHaveCount(0);
});
