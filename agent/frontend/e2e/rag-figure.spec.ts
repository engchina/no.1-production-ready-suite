import type { Page, Route } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1311: チャットの出典・実行の詳細の RAG の図の根拠から、元の図（RAG の短命の署名つきの URL）を開く。

const THREAD_ID = `thread_${"f".repeat(32)}`;
const RUN_ID = "run-figure-seed";
// 120×60 の単色の PNG（RAG が返す図の画像の代わり）。
const FIGURE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAHgAAAA8CAIAAAAiz+n/AAAAaklEQVR42u3QQQ0AAAgEoEtiMBMbyxbOBxsJSPVwIApEi0a0aNEWRItGtGjRFkSLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSNatGhEi0a0aNGIFo3oXxYAgSoOxqX2IQAAAABJRU5ErkJggg==",
  "base64"
);
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

function seedFigureRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: RUN_ID,
    goal: "申請の手順は？",
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
      { id: "answer-figure", kind: "answer", name: "回答", created_at: MOCK_NOW, content: { text: "右上の「承認」ボタンを押します。" } },
      {
        id: "artifact-rag-figure",
        name: "rag__rag_search:step-1",
        kind: "rag_evidence",
        created_at: MOCK_NOW,
        content: {
          evidence: [
            {
              evidence_id: "chunk-figure",
              document_id: "doc-1",
              chunk_id: "chunk-figure",
              file_name: "申請手順.pdf",
              evidence_type: "figure_description",
              image_ref: { document_id: "doc-1", chunk_id: "chunk-figure", page: 2, bbox: [60, 80, 300, 200], bbox_unit: "absolute" },
              locator: { section_path: ["申請手順"], page_start: 2, page_end: 2 },
              excerpt: "申請画面。右上の「承認」ボタンを押す。",
              used_in_answer: true,
            },
            {
              evidence_id: "chunk-text",
              document_id: "doc-1",
              chunk_id: "chunk-text",
              file_name: "規程.pdf",
              evidence_type: "text",
              image_ref: null,
              locator: { section_path: ["規程"], page_start: 1, page_end: 1 },
              excerpt: "承認は課長が行う。",
              used_in_answer: true,
            },
          ],
        },
      },
    ],
  });
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

/** RAG の図の URL（`http://rag.e2e.test/api/figures/...`）への応答。読んだ回数を返す。 */
async function serveFigures(page: Page, statuses: number[] = []) {
  const seen: { url: string; referer: string | undefined }[] = [];
  await page.route("http://rag.e2e.test/**", async (route: Route) => {
    seen.push({ url: route.request().url(), referer: route.request().headers().referer });
    const status = statuses.shift() ?? 200;
    if (status !== 200) {
      await route.fulfill({ status, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      headers: { "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer" },
      body: FIGURE_PNG,
    });
  });
  return seen;
}

async function openThread(page: Page) {
  const toggle = page.getByTestId("chat-history-toggle");
  const viewport = page.viewportSize();
  if (viewport && viewport.width >= 1024) {
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    await page.getByTestId("chat-history").getByRole("button", { name: /申請の手順は？/ }).click();
    return;
  }
  await toggle.click();
  await page.getByRole("dialog", { name: "会話の履歴" }).getByText("申請の手順は？").click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`チャットの出典の図の根拠から元の図を開き、Escape で閉じる (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      seedFigureRun(mockApi);
      const figures = await serveFigures(page);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      await openThread(page);

      const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
      await turn.getByText("出典（2）").click();
      // 図の根拠だけに「図を開く」を出し、AI が読んだ説明であることを添える。
      await expect(turn.getByRole("button", { name: /^図を開く/ })).toHaveCount(1);
      await expect(turn.getByTestId("chat-source-figure-description")).toHaveText(
        "AI が図を読んだ説明です。元の図で確かめてください。"
      );
      const open = turn.getByRole("button", { name: "図を開く: 申請手順.pdf" });
      await open.click();

      const sheet = page.getByRole("dialog", { name: "図の根拠: 申請手順.pdf" });
      await expect(sheet).toBeVisible();
      const image = sheet.getByRole("img", { name: "申請手順.pdf の図（p.2 · 申請手順）" });
      await expect(image).toBeVisible();
      await expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
      await expect(sheet.getByTestId("rag-figure-loading")).toHaveCount(0);
      // 画面を見ている利用者として、その Run の根拠の図の URL を取る。RAG の URL へは Referer を送らない。
      const request = mockApi.lastRequest("GET", `/api/runs/${RUN_ID}/figure-url`);
      expect(request?.searchParams.get("document_id")).toBe("doc-1");
      expect(request?.searchParams.get("chunk_id")).toBe("chunk-figure");
      expect(figures).toHaveLength(1);
      expect(figures[0].referer).toBeUndefined();
      await expectNoHorizontalOverflow(page);
      // シートは右から出し切る（375px では全画面、広い画面では右端にそろう）。
      await expect
        .poll(async () => {
          const sheetBox = await sheet.boundingBox();
          return sheetBox ? Math.round(sheetBox.x + sheetBox.width) : -1;
        })
        .toBe(viewport.width);
      if (viewport.width < 640) expect((await sheet.boundingBox())?.x).toBe(0);
      const box = await image.boundingBox();
      expect(box?.width ?? 0).toBeLessThanOrEqual(viewport.width);
      await page.screenshot({ path: testInfo.outputPath(`figure-${viewport.name}-${theme}.png`) });

      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();
      await expect(open).toBeFocused();
      // 開き直すと URL を取り直す（短命の URL を使い回さない）。
      await open.press("Enter");
      await expect(sheet.getByRole("img")).toBeVisible();
      await expect.poll(() => figures.length).toBe(2);
    });
  }
}

test("図の画像を読めない（期限切れなど）ときは理由を出し、もう一度読み込める", async ({ page, mockApi }) => {
  seedFigureRun(mockApi);
  const figures = await serveFigures(page, [410]);
  await page.goto("/chat");
  await openThread(page);
  const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
  await turn.getByText("出典（2）").click();
  await turn.getByRole("button", { name: "図を開く: 申請手順.pdf" }).click();

  const sheet = page.getByRole("dialog", { name: "図の根拠: 申請手順.pdf" });
  const error = sheet.getByTestId("rag-figure-error");
  await expect(error).toContainText("図を開けませんでした");
  await expect(error).toContainText("URL の期限が切れたか、資料が更新された可能性があります。");
  await error.getByRole("button", { name: "もう一度読み込む" }).click();
  await expect(sheet.getByRole("img", { name: /申請手順.pdf の図/ })).toBeVisible();
  await expect(sheet.getByTestId("rag-figure-error")).toHaveCount(0);
  expect(figures).toHaveLength(2);
});

test("URL を取得できないときは backend の理由を出す", async ({ page, mockApi }) => {
  seedFigureRun(mockApi);
  await serveFigures(page);
  await page.route(`**/api/runs/${RUN_ID}/figure-url?**`, (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: ["資料が更新されたため、この図は開けません。もう一度質問してください。"],
        warning_messages: [],
      }),
    })
  );
  await page.goto("/chat");
  await openThread(page);
  const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
  await turn.getByText("出典（2）").click();
  await turn.getByRole("button", { name: "図を開く: 申請手順.pdf" }).click();
  const error = page.getByRole("dialog", { name: "図の根拠: 申請手順.pdf" }).getByTestId("rag-figure-error");
  await expect(error).toContainText("資料が更新されたため、この図は開けません。");
  await expect(error.getByRole("button", { name: "もう一度読み込む" })).toBeVisible();
});

for (const viewport of VIEWPORTS) {
  test(`実行の詳細の根拠からも図を開ける (${viewport.name})`, async ({ page, mockApi }) => {
    seedFigureRun(mockApi);
    await serveFigures(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto(`/runs?id=${RUN_ID}`);
    const open = page.getByRole("button", { name: "図を開く: 申請手順.pdf" });
    await open.scrollIntoViewIfNeeded();
    await open.click();
    const sheet = page.getByRole("dialog", { name: "図の根拠: 申請手順.pdf" });
    await expect(sheet.getByRole("img", { name: /申請手順.pdf の図/ })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await sheet.getByRole("button", { name: "図を閉じる" }).click();
    await expect(sheet).toBeHidden();
    await expect(open).toBeFocused();
  });
}
