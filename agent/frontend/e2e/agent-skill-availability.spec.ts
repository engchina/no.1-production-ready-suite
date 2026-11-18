import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";
import { chooseSelectFieldOption } from "./fixtures/select-field";

// #5351: 削除されたスキル（API の unavailable_skill_ids。#5342）と、RAG の回答をそのまま返すスキル
// （API の final_answer_tools）を、業務 Agent の一覧・編集・チャットに示す。

const AGENT_ID = "agent-5351";
const REMOVED_SKILL = "removed-skill-a";

/** 公開中の v1 が削除されたスキルを選び、下書きは RAG チャットを選んだ業務 Agent。 */
function seedAgent(mockApi: MockApi) {
  mockApi.state.agents.push({
    id: AGENT_ID,
    name: "合成の業務 Agent A",
    description: "合成の説明",
    instructions: "合成の指示",
    skill_ids: ["rag-chat"],
    model_id: "",
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [
      {
        version: 1,
        name: "合成の業務 Agent A",
        description: "合成の説明",
        instructions: "合成の指示",
        skill_ids: [REMOVED_SKILL],
        model_id: "",
        note: "",
        published_at: MOCK_NOW,
        published_by: null,
      },
    ],
    published_version: 1,
    unpublished_changes: true,
    draft_changed: true,
    unavailable_skill_ids: [REMOVED_SKILL],
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

test.describe("削除されたスキルと RAG の回答をそのまま返すスキル（#5351）", { tag: "@responsive" }, () => {
  test("一覧: 削除されたスキルを選んだ業務 Agent の行だけに警告の印を出す", async ({ page, mockApi }, testInfo) => {
    seedAgent(mockApi);
    await page.goto("/agents");

    const row = page.getByTestId(`agent-row-${AGENT_ID}`);
    await expect(row).toBeVisible();
    const badge = row.locator('[data-status-variant="warning"]', { hasText: "削除されたスキルあり" });
    await expect(badge).toBeVisible();
    // 色だけで表さない（警告のアイコンを添える）。
    await expect(badge.locator("svg")).toHaveCount(1);
    await expect(page.getByTestId("agent-row-default")).not.toContainText("削除されたスキルあり");
    await expectNoPageHorizontalOverflow(page);
    await row.screenshot({ path: testInfo.outputPath("agents-row-unavailable.png") });
  });

  test("編集: 削除されたスキルと RAG の回答の案内を、スキルの区画の 1 つの枠に出す", async ({ page, mockApi }, testInfo) => {
    seedAgent(mockApi);
    await page.goto(`/agents?id=${AGENT_ID}`);
    await expect(page.getByRole("heading", { name: "合成の業務 Agent A", level: 1 })).toBeVisible();

    // 概要の公開の状態にも同じ印。
    await expect(page.getByText("削除されたスキルあり")).toBeVisible();

    const notices = page.getByTestId("agent-skill-notices");
    await expect(notices).toHaveCount(1);
    // 枠の色は最も重い項目（警告）。
    await expect(notices).toHaveAttribute("data-surface-tint", "warning");
    const unavailable = notices.getByTestId("agent-skills-unavailable");
    await expect(unavailable).toContainText("削除されたスキルが選ばれています");
    await expect(unavailable).toContainText(`次のスキルは削除されたため使えません: ${REMOVED_SKILL}`);
    await expect(unavailable).toContainText("スキルを選び直して保存し、公開してください。");
    await expect(notices.getByTestId("agent-final-answer-notice")).toHaveText(
      "回答は RAG がそのまま返します（書き方は検索・回答プロファイルで設定します）。対象のスキル: RAG チャット"
    );

    // 選ぶ前に分かるよう、回答をそのまま返すスキルの候補に印を出す（RAG 検索も）。
    const picker = page.getByTestId("agent-skill-picker");
    await expect(picker.getByRole("option", { name: /^RAG チャット(?!＋)/ })).toContainText("RAG の回答をそのまま返す");
    await expect(picker.getByRole("option", { name: /^RAG 検索(?!＋)/ })).toContainText("RAG の回答をそのまま返す");
    await expect(picker.getByRole("option", { name: /^構造化データ照会/ })).not.toContainText("RAG の回答をそのまま返す");
    await expectNoPageHorizontalOverflow(page);
    await notices.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("agent-editor-skill-notices.png") });

    // RAG チャットを外すと RAG の回答の案内は消え、削除されたスキル（公開中の版）の案内は残る。
    await picker.getByRole("option", { name: /^RAG チャット(?!＋)/ }).click();
    await expect(notices.getByTestId("agent-final-answer-notice")).toHaveCount(0);
    await expect(notices).toContainText(REMOVED_SKILL);
  });

  test("チャット: 選んだ業務 Agent に削除されたスキルがあれば、送信の前に警告を出す（送信は止めない）", async (
    { page, mockApi },
    testInfo
  ) => {
    const target = mockApi.state.agents.find((agent) => agent.id === "default");
    if (!target) throw new Error("既定の業務 Agent が mock にありません");
    target.unavailable_skill_ids = [REMOVED_SKILL];
    await page.goto("/chat");

    const warning = page.getByTestId("chat-agent-skills-unavailable");
    await expect(warning).toContainText("この業務 Agent は削除されたスキルを選んでいます");
    await expect(warning).toContainText("送信すると実行が失敗することがあります。");
    await expect(warning).toContainText(REMOVED_SKILL);
    // 警告で会話の欄が低くなっても（375px）、空の状態は末尾へ送らず先頭から見せる（上端が隠れない。#5397）。
    const log = page.getByRole("log");
    const emptyTitle = log.getByText("質問を入力して会話を始めます");
    await expect(emptyTitle).toBeVisible();
    await expect.poll(() => log.evaluate((element) => element.scrollTop)).toBe(0);
    const logBox = await log.boundingBox();
    const emptyIconBox = await log.locator("svg").first().boundingBox();
    expect(emptyIconBox!.y).toBeGreaterThanOrEqual(logBox!.y);
    await page.getByLabel("質問").fill("合成の質問");
    await expect(page.getByTestId("chat-send")).toBeEnabled();
    await expectNoPageHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("chat-skill-unavailable.png") });

    // 削除されたスキルの無い業務 Agent に替えると消える。
    await chooseSelectFieldOption(page.locator("#chat-agent"), "data-query");
    await expect(page.getByTestId("chat-agent-skills-unavailable")).toHaveCount(0);
  });
});
