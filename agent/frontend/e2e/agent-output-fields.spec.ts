
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";

// #2549: 業務 Agent の出力の項目。編集画面で定義し、チャットに記録した項目の表と CSV を出す。

const OUTPUT_FIELDS = [
  { key: "item", label: "項目", type: "text", required: true, description: "" },
  { key: "matched", label: "一致", type: "boolean", required: true, description: "" },
];

function seedAgent(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.agents.push({
    id: "agent-2549",
    name: "Agent O",
    description: "書類を照合する",
    instructions: "書類を照合する。",
    skill_ids: [],
    model_id: "",
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [],
    published_version: null,
    unpublished_changes: true,
    output_fields: OUTPUT_FIELDS,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...overrides,
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
]) {
  test(`編集画面で出力の項目を足し、key の誤りを直して保存できる (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/agents?id=new");
    await page.getByRole("textbox", { name: "名前", exact: true }).fill("Agent P");
    const section = page.getByTestId("agent-output-fields");
    await expect(section).toContainText("出力の項目はありません");

    await page.getByTestId("agent-output-add").click();
    const row = page.getByTestId("agent-output-field-0");
    await row.getByRole("textbox", { name: "表示名" }).fill("金額");
    await row.getByRole("textbox", { name: "key" }).fill("Amount");
    await page.getByRole("button", { name: "作成", exact: true }).click();
    await expect(row.getByText("英小文字で始め、英小文字・数字・_ の 40 文字以内にしてください。")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/agents")).toBeUndefined();

    await row.getByRole("textbox", { name: "key" }).fill("amount");
    // #2562: 評価の比べ方は文字の項目だけに出す。
    await expect(row.getByLabel("評価の比べ方")).toBeVisible();
    await row.getByLabel("型").click();
    await page.getByRole("option", { name: "数値" }).click();
    await expect(row.getByLabel("評価の比べ方")).toHaveCount(0);
    await row.getByRole("switch", { name: "「金額」を必須にする" }).click();
    await page.getByTestId("agent-output-add").click();
    const note = page.getByTestId("agent-output-field-1");
    await note.getByRole("textbox", { name: "表示名" }).fill("確かめたこと");
    await note.getByRole("textbox", { name: "key" }).fill("note");
    await note.getByLabel("評価の比べ方").click();
    await page.getByRole("option", { name: "意味が同じ" }).click();
    await expectNoPageHorizontalOverflow(page);
    await section.screenshot({ path: testInfo.outputPath(`agent-output-fields-${viewport.name}.png`) });

    await page.getByRole("button", { name: "作成", exact: true }).click();
    await expect(page.getByText("Agent を作成しました")).toBeVisible();
    expect((mockApi.lastRequest("POST", "/api/agents")?.body as { output_fields: unknown }).output_fields).toEqual([
      { key: "amount", label: "金額", type: "number", required: true, description: "", match: "exact" },
      { key: "note", label: "確かめたこと", type: "text", required: false, description: "", match: "meaning" },
    ]);
  });
}

test("書類と業務データの照合のテンプレートを選ぶと、出力の項目が入る", async ({ page, mockApi }) => {
  mockApi.state.agentTemplates.push({
    id: "document-data-check",
    category: "共通（書類の確認）",
    name: "書類と業務データの照合",
    description: "添付の書類を業務データと照合する",
    instructions: "書類を照合する。\n\n## 共通のルール\n- 日本語で答える。",
    skill_ids: ["structured-data-query"],
    sample_questions: ["添付した書類を照合してください。", "明細を照合してください。"],
    evaluation_cases: [{ question: "照合してください。", expected: "添付を求める" }],
    output_fields: [
      { key: "item", label: "項目", type: "text", required: true, description: "" },
      { key: "document_value", label: "書類の値", type: "text", required: true, description: "" },
      { key: "matched", label: "一致", type: "boolean", required: true, description: "" },
    ],
  });
  await page.goto("/agents?id=new");
  await page.getByTestId("agent-template-document-data-check").click();

  const section = page.getByTestId("agent-output-fields");
  await expect(section.getByRole("textbox", { name: "表示名" })).toHaveCount(3);
  await expect(page.getByTestId("agent-output-field-1").getByRole("textbox", { name: "表示名" })).toHaveValue("書類の値");
});

test("チャットに、記録した出力の項目を表と CSV で出す", async ({ page, mockApi }) => {
  seedAgent(mockApi, { published_version: 1, unpublished_changes: false });
  mockApi.state.runOutputContent = {
    columns: [
      { name: "item", label: "項目", type: "text" },
      { name: "matched", label: "一致", type: "boolean" },
    ],
    rows: [
      { item: "A", matched: true },
      { item: "B", matched: false },
      { item: "C", matched: null },
    ],
  };
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "質問" }).fill("書類を照合して");
  await page.getByTestId("chat-send").click();

  const output = page.locator('[data-testid^="chat-output-"]').first();
  await expect(output).toContainText("出力の項目");
  await expect(output).toContainText("はい");
  await expect(output).toContainText("いいえ");
  await expect(output).toContainText("記録なし");
  await expect(output).not.toContainText("NULL");
  await expect(output.getByRole("button", { name: /CSV/ })).toBeVisible();
});

test("差し戻しても記録しなかった回答には、記録の無いことを出す", async ({ page, mockApi }) => {
  seedAgent(mockApi, { published_version: 1, unpublished_changes: false });
  mockApi.state.runs.push({
    id: "run-2549",
    goal: "書類を照合して",
    agent_id: "agent-2549",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [{ id: "run-2549-answer", name: "回答", kind: "answer", created_at: MOCK_NOW, content: { text: "照合しました。" } }],
    pending_tool_calls: [],
    metadata: { tool_grounding: { output_retry: true } },
    created_by_user_uuid: "local",
    thread_id: "thread_2549",
    thread_owner_user_uuid: "local",
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  await page.goto("/chat?agent_id=agent-2549&thread_id=thread_2549");
  await expect(page.getByTestId("chat-output-missing-run-2549")).toContainText("出力の項目が記録されませんでした");
});

test("業務 Agent と出力項目の長い説明は3行で折り返し、Enterで保存せず内部改行を保持する", { tag: "@responsive" }, async ({ page, mockApi }, testInfo) => {
  await page.goto("/agents?id=new");
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("Agent M");
  await page.getByLabel("指示", { exact: true }).fill("合成の指示。");
  const description = page.locator('[id$="-agent-description"]');
  await expect(description).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(description).toHaveAttribute("rows", "3");
  await expect(description).toHaveCSS("resize", "vertical");
  const text = "合成の説明を折り返して読みます。".repeat(24);
  await description.fill(text);
  await description.press("End");
  await description.press("Enter");
  await description.pressSequentially("合成の最終行。");
  const expectedDescription = `${text}\n合成の最終行。`;
  await expect(description).toHaveValue(expectedDescription);
  expect(mockApi.lastRequest("POST", "/api/agents")).toBeUndefined();
  await page.getByTestId("agent-output-add").click();
  const row = page.getByTestId("agent-output-field-0");
  await row.getByRole("textbox", { name: "表示名" }).fill("項目 M");
  await row.getByRole("textbox", { name: "key" }).fill("item_m");
  const outputDescription = row.locator('textarea[id$="-description"]');
  await expect(outputDescription).toHaveAttribute("rows", "3");
  await outputDescription.fill("合成の項目説明。");
  await outputDescription.press("End");
  await outputDescription.press("Enter");
  await outputDescription.pressSequentially("合成の確認内容。");
  await expect(outputDescription).toHaveValue("合成の項目説明。\n合成の確認内容。");
  expect(mockApi.lastRequest("POST", "/api/agents")).toBeUndefined();
  await description.evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect(await description.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  expect(await description.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await expectNoPageHorizontalOverflow(page);
  await description.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("agent-multiline-description.png"), fullPage: true });
  await page.getByRole("button", { name: "作成", exact: true }).click();
  await expect(page.getByText("Agent を作成しました")).toBeVisible();
  const request = mockApi.lastRequest("POST", "/api/agents")?.body as { description: string; output_fields: Array<{ description: string }> };
  expect(request.description).toBe(expectedDescription);
  expect(request.output_fields[0].description).toBe("合成の項目説明。\n合成の確認内容。");
  const savedAgent = mockApi.state.agents.find(agent => agent.name === "Agent M");
  expect(savedAgent).toBeDefined();
  await page.goto(`/agents?id=${encodeURIComponent(String(savedAgent!.id))}`);
  await page.reload();
  await expect(page.locator('[id$="-agent-description"]')).toHaveValue(expectedDescription);
  await expect(page.getByTestId("agent-output-field-0").locator('textarea[id$="-description"]')).toHaveValue("合成の項目説明。\n合成の確認内容。");
});
