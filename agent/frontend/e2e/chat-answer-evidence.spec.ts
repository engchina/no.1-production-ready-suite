import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #2328: Agent のチャットの回答を RAG のチャットと同じ共有の部品で出す。
// - 本文: 要約 →「資料により確認できた事項」（各項目の下に「根拠：文書 p.N」）→「資料から確認できなかった事項」。
//   本文の根拠の ID の印は文字で出さず、根拠の行にする。
// - 根拠の塊: 「AI に渡した N 件 ／ 回答で引用 M 件 …」の折りたたみ（既定は閉じる）、カード、一定の高さの中のスクロール。
// - 外した内容は本文の節に 1 回だけ出し、「回答の確かめ」に同じ項目を出し直さない。
// 内容はすべて合成のデータ。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];
const THREAD_ID = `thread_${"e".repeat(32)}`;
const GOAL = "合成の質問 A は？";
const RUN_ID = "run-evidence-1";
const CHUNK_A = "0f3a9c2e7b1d4e5f:cs_a:7";
const CHUNK_B = "0f3a9c2e7b1d4e5f:cs_a:12";
const CHUNK_C = "doc-c:cs_c:1";
const WITHHELD_QUOTE = "期限の延長は 2 回までです。";
const ANSWER = [
  "合成の質問 A の答えは次のとおりです。",
  "",
  "- 申請は 30 日前までに行います【証拠 ID: 0f3a9c2…:7】。",
  "- 承認は担当の部署が行います。",
  "",
  "**資料で確認できず除いた内容**",
  "",
  "根拠で確かめられなかった次の内容は、回答に載せていません。",
  `- 根拠で確かめられない: 「${WITHHELD_QUOTE}」`,
].join("\n");

function evidenceItem(chunkId: string, overrides: Record<string, unknown> = {}) {
  return {
    document_id: chunkId.split(":")[0],
    chunk_id: chunkId,
    file_name: `文書 ${chunkId.slice(0, 1).toUpperCase()}.pdf`,
    excerpt: `合成の本文（${chunkId}）。`,
    truncated: false,
    locator: { page_start: 3, page_end: 3, section_path: ["第 1 章"] },
    role: "retrieved_anchor",
    score: 0.812,
    rerank_score: 0.64,
    section_title: "第 1 節",
    cited: false,
    answer_use: null,
    ...overrides,
  };
}

function seedRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: RUN_ID,
    goal: GOAL,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    artifacts: [
      {
        id: "artifact-validation",
        kind: "answer_validation",
        name: "回答の検証",
        created_at: MOCK_NOW,
        content: {
          status: "completed",
          reason: null,
          valid: false,
          result: {
            status: "completed",
            valid: false,
            claims: [
              {
                answer_quote: "- 承認は担当の部署が行います。",
                status: "supported",
                chunk_id: CHUNK_B,
                reason: "合成の理由",
              },
              { answer_quote: WITHHELD_QUOTE, status: "unsupported", reason: "合成の理由" },
            ],
            findings: [],
            missing_evidence: [],
            stale_evidence: [],
          },
          withheld: { claims: 1, findings: 0, all: false },
          notice: { claims: 1, findings: 0, evidence: true, heading: "資料で確認できず除いた内容" },
        },
      },
      {
        id: "artifact-evidence",
        kind: "answer_evidence",
        name: "回答の根拠",
        created_at: MOCK_NOW,
        content: {
          items: [
            evidenceItem(CHUNK_A, { cited: true, answer_use: "confirmed" }),
            evidenceItem(CHUNK_B, { cited: true, locator: { page_start: 5, page_end: 5, section_path: [] } }),
            evidenceItem(CHUNK_C, { file_name: "文書 C.pdf", rerank_score: null }),
          ],
          passed: 3,
          cited: 2,
          markers: [{ text: "【証拠 ID: 0f3a9c2…:7】", chunk_ids: [CHUNK_A] }],
          passages: [
            { text: "- 申請は 30 日前までに行います【証拠 ID: 0f3a9c2…:7】。", chunk_ids: [CHUNK_A] },
            { text: "- 承認は担当の部署が行います。", chunk_ids: [CHUNK_B] },
          ],
        },
      },
      { id: "artifact-answer", kind: "answer", name: "回答", created_at: MOCK_NOW, content: { text: ANSWER } },
    ],
  });
}

async function openThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: new RegExp(`^${GOAL}`) }).click();
    return;
  }
  await page.getByRole("dialog", { name: "会話の履歴" }).getByText(GOAL).click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test(`回答を RAG と同じ節・根拠の行・根拠の塊で出し、外した内容は 1 回だけ出す (${viewport.name})`, { tag: "@responsive" }, async ({ page, mockApi }, testInfo) => {
    seedRun(mockApi);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/chat");
    await openThread(page);
    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    const body = turn.getByTestId(`chat-answer-${RUN_ID}`);

    // 要約の段落 →「資料により確認できた事項」→「資料から確認できなかった事項」。
    await expect(body.locator("p").first()).toHaveText("合成の質問 A の答えは次のとおりです。");
    const sections = body.getByTestId("answer-section");
    await expect(sections.locator("h4")).toHaveText(["資料により確認できた事項", "資料から確認できなかった事項"]);
    const confirmed = sections.nth(0).getByRole("listitem");
    await expect(confirmed).toHaveCount(2);
    // 根拠の ID の印は文字で出さず、根拠の行（「根拠：文書 p.N」）にする。
    await expect(body).not.toContainText("証拠 ID");
    await expect(body).not.toContainText("0f3a9c2");
    await expect(confirmed.nth(0)).toContainText("申請は 30 日前までに行います。");
    await expect(confirmed.nth(0).getByTestId("answer-citation-link")).toHaveText("根拠：文書 0.pdf p.3");
    await expect(confirmed.nth(1).getByTestId("answer-citation-link")).toHaveText("根拠：文書 0.pdf p.5");
    // 外した内容は本文の節に 1 回だけ。
    await expect(sections.nth(1)).toContainText(WITHHELD_QUOTE);
    await expect(turn.getByText(WITHHELD_QUOTE, { exact: false })).toHaveCount(1);

    // 根拠の行を押すと、その根拠を右のサイドパネル（RAG と同じ共通の部品。#2360）で開く。上部は根拠の要約
    // （番号・文書名・引用の印・Evidence・スコア・頁）と抜粋の全文。Agent は原文のプレビューを出さない。
    const citationLink = confirmed.nth(0).getByTestId("answer-citation-link");
    await citationLink.click();
    const sheet = page.getByRole("dialog", { name: "回答の根拠" });
    const summary = sheet.getByTestId("evidence-source-summary");
    await expect(summary.getByTestId("evidence-file-name")).toHaveText("文書 0.pdf");
    await expect(summary).toContainText("回答で引用");
    await expect(summary.getByTestId("evidence-route-badges")).toContainText("Evidence");
    await expect(summary.getByTestId("evidence-chips")).toContainText("p.3");
    await expect(summary.getByTestId("citation-text")).toHaveText(`合成の本文（${CHUNK_A}）。`);
    await expect(summary.getByTestId("citation-text")).toHaveAttribute("data-expanded", "true");
    await expect(sheet.getByTestId("evidence-source-preview")).toHaveCount(0);
    // 右端に付き、375px は全画面のシート。
    const sheetBox = await sheet.boundingBox();
    expect(Math.round(sheetBox!.x + sheetBox!.width)).toBe(viewport.width);
    if (viewport.width < 640) expect(Math.round(sheetBox!.x)).toBe(0);
    await page.screenshot({ path: testInfo.outputPath(`answer-evidence-sheet-${viewport.name}.png`) });
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(citationLink).toBeFocused();

    // 根拠の塊: 見出しは件数と内訳、既定は閉じる。開くとカード（引用の印・スコア・場所のチップ）。
    const evidence = turn.getByTestId("answer-citations");
    await expect(evidence.locator("summary")).toHaveText("AI に渡した 3 件 ／ 回答で引用 2 件 ／ うち該当確認済み 1 件");
    await expect(evidence).not.toHaveAttribute("open", "");
    await evidence.locator("summary").click();
    const cards = evidence.getByTestId("evidence-card");
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText("回答で引用");
    await expect(cards.nth(2)).not.toContainText("回答で引用");
    await expect(cards.nth(2)).toContainText("Rerank 未実行");
    await expect(cards.nth(0).getByTestId("evidence-chips")).toContainText("p.3");
    // カードの「根拠を表示」も、根拠の行と同じ右のサイドパネルを開く（RAG の「引用箇所を表示」と同じ。#2360）。
    const cardOpen = cards.nth(0).getByRole("button", { name: "文書 0.pdf の根拠を表示" });
    await cardOpen.click();
    await expect(sheet.getByTestId("evidence-source-summary").getByTestId("citation-text")).toHaveText(
      `合成の本文（${CHUNK_A}）。`
    );
    await sheet.getByRole("button", { name: "閉じる" }).click();
    await expect(sheet).toBeHidden();
    await expect(cardOpen).toBeFocused();
    // 一定の高さを超えたら中でスクロールする（チャット・実行の詳細で同じ）。
    await expect(evidence.locator("ul.bounded-scroll-area-lg")).toHaveCount(1);
    // 根拠の塊は回答の評価の前。
    const order = await turn.evaluate((element) => {
      const nodes = Array.from(element.querySelectorAll('[data-testid="answer-citations"], [data-testid^="chat-feedback"], [data-testid^="answer-feedback"]'));
      return nodes.map((node) => node.getAttribute("data-testid"));
    });
    expect(order[0]).toBe("answer-citations");
    // 1 つの塊だけ（以前の「出典」は出さない）。
    await expect(turn.getByText(/^出典（/)).toHaveCount(0);

    // 「回答の確かめ」は判定のバッジと要約だけで、本文の節の項目を出し直さない。
    await turn.getByTestId(`chat-review-${RUN_ID}-summary`).click();
    const review = turn.getByTestId(`chat-review-${RUN_ID}`);
    await expect(review).toContainText("資料で確かめられなかった 1 件の内容は、回答に載せていません。");
    await expect(review.getByTestId("answer-review-unverified")).toHaveCount(0);

    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`answer-evidence-${viewport.name}.png`), fullPage: true });
  });
}

test("段落に根拠を結び付けられない回答は今までの Markdown のまま出す", async ({ page, mockApi }) => {
  seedRun(mockApi);
  const run = mockApi.state.runs.find((item) => item.id === RUN_ID) as {
    artifacts: { kind: string; content: Record<string, unknown> }[];
  };
  run.artifacts = run.artifacts.map((artifact) =>
    artifact.kind === "answer_evidence"
      ? { ...artifact, content: { ...artifact.content, markers: [], passages: [] } }
      : artifact
  );
  await page.goto("/chat");
  await openThread(page);
  const body = page.getByTestId(`chat-answer-${RUN_ID}`);
  await expect(body.getByTestId("answer-section")).toHaveCount(0);
  await expect(body).toContainText("申請は 30 日前までに行います");
  // 根拠の塊は出す。
  await expect(page.getByTestId("answer-citations")).toHaveCount(1);
});

test("実行の詳細の回答も同じ節・根拠の塊で出す", async ({ page, mockApi }) => {
  seedRun(mockApi);
  await page.goto(`/runs?id=${RUN_ID}`);
  const section = page.getByTestId("run-answer-section");
  await expect(section.getByTestId("answer-section").locator("h4")).toHaveText([
    "資料により確認できた事項",
    "資料から確認できなかった事項",
  ]);
  await expect(section.getByTestId("answer-citations").locator("summary")).toHaveText(
    "AI に渡した 3 件 ／ 回答で引用 2 件 ／ うち該当確認済み 1 件"
  );
  // 成果物の一覧に回答の根拠を重ねない。
  await expect(page.getByTestId("run-artifact-artifact-evidence")).toHaveCount(0);
  // 回答の検証の成果物も、本文の節に書いた項目を出し直さない。
  await expect(page.getByTestId("run-review-artifact-validation").getByTestId("answer-review-unverified")).toHaveCount(0);
});
