import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";
import { chooseSelectFieldOption, expectSelectFieldValue } from "./fixtures/select-field";

// #5859: 業務 Agent の「資料・データで確かめていない回答」（資料・業務データのツールを 1 つも呼ばずに作った回答の扱い）。
// - 設定の欄は「回答前の確認」の次の区画。資料・業務データのツールを宣言したスキル（スキルの一覧の `grounding`）を
//   選んだときだけ出す。既定は「注意を付けて表示する」、もう 1 つは「表示せず「お答えできません」と返す」。
//   版の項目なので、保存すると公開していない変更になる。欄の名前は区画の見出しと同じ（見せるラベルは置かない）。
// - 案内の注意の文は backend の `GET /api/runtime/status` の `ungrounded_notice` を差し込む（文は backend だけが持つ）。
// - 既定で出した回答は、本文はモデルのまま、回答の注意書きの先頭に注意を出す（backend が成果物の `notice` に足す）。
// 内容はすべて合成のデータ。

const AGENT_ID = "agent-5859";
const THREAD_ID = `thread_${"5".repeat(32)}`;
const RUN_ID = "run-ungrounded-1";
const GOAL = "合成の質問 U は？";
const ANSWER = "合成の値の上限は 10 です。";
const UNGROUNDED_NOTICE = "この回答は、資料・業務データで確かめていません。";
const GENERAL_NOTICE = "この回答は AI が生成したものです。内容を確かめてから利用してください。";
const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

function seedAgent(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.agents.push({
    id: AGENT_ID,
    name: "合成の業務 Agent",
    description: "",
    instructions: "",
    // 資料・業務データのツールを宣言したスキル（api-defaults.json の rag-chat。grounding: true）。
    skill_ids: ["rag-chat"],
    model_id: "",
    data_scopes: {},
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [],
    published_version: null,
    unpublished_changes: true,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...overrides,
  });
}

test("業務 Agent の「資料・データで確かめていない回答」の扱いを保存できる", async ({ page, mockApi }) => {
  // この項目の無い保存済みの業務 Agent（#5859 より前）。
  seedAgent(mockApi);
  await page.goto(`/agents?id=${AGENT_ID}`);
  const heading = page.getByRole("heading", { name: "資料・データで確かめていない回答", level: 2 });
  await expect(heading).toBeVisible();

  const policy = page.locator(`#${AGENT_ID}-agent-ungrounded-answer-policy`);
  // 欄の名前は区画の見出しと同じ。汎用のラベル（「回答の扱い」）は置かない。
  await expect(page.getByRole("combobox", { name: "資料・データで確かめていない回答" })).toBeVisible();
  await expect(page.getByText("回答の扱い", { exact: true })).toHaveCount(0);
  // 案内は backend の注意の文を差し込む。
  await expect(page.getByTestId("agent-ungrounded-answer")).toContainText(
    `注意を付ける: 回答の下に「${UNGROUNDED_NOTICE}」を表示します。`,
  );
  // 項目の無い業務 Agent は既定（注意を付けて表示する）。
  await expectSelectFieldValue(policy, "annotate");
  await expect(policy).toHaveText("注意を付けて表示する");
  await expect(page.getByRole("button", { name: "変更を破棄" })).toBeDisabled();

  await chooseSelectFieldOption(policy, { label: "表示せず「お答えできません」と返す" });
  await expectSelectFieldValue(policy, "refuse");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("Agent を保存しました")).toBeVisible();
  // 回答前の確認と同じ保存に乗る（1 つの PATCH）。
  expect(mockApi.lastRequest("PATCH", `/api/agents/${AGENT_ID}`)?.body).toMatchObject({
    ungrounded_answer_policy: "refuse",
    clarification_policy: "confirm",
  });
  await expect(page.getByRole("button", { name: "変更を破棄" })).toBeDisabled();

  // 変更を破棄で、保存した内容に戻る。
  await chooseSelectFieldOption(policy, "annotate");
  await expect(page.getByRole("button", { name: "変更を破棄" })).toBeEnabled();
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expectSelectFieldValue(policy, "refuse");
});

test("保存済みの拒答の設定を開き、新しく作る業務 Agent は既定を送る", async ({ page, mockApi }) => {
  seedAgent(mockApi, { ungrounded_answer_policy: "refuse" });
  await page.goto(`/agents?id=${AGENT_ID}`);
  await expectSelectFieldValue(page.locator(`#${AGENT_ID}-agent-ungrounded-answer-policy`), "refuse");

  await page.goto("/agents?id=new");
  const heading = page.getByRole("heading", { name: "資料・データで確かめていない回答", level: 2 });
  // スキルを選ぶまでは効かないので出さない。資料・業務データのツールを宣言したスキルを選ぶと出す。
  await expect(page.getByRole("heading", { name: "回答前の確認", level: 2 })).toBeVisible();
  await expect(heading).toHaveCount(0);
  await page.getByTestId("agent-skill-picker").getByRole("option", { name: /^RAG チャット(?!＋)/ }).click();
  await expect(heading).toBeVisible();
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("合成の新しい業務 Agent");
  await page.getByRole("button", { name: "作成", exact: true }).click();
  await expect
    .poll(() => mockApi.lastRequest("POST", "/api/agents")?.body)
    .toMatchObject({ ungrounded_answer_policy: "annotate" });
});

test("資料・業務データのツールを宣言したスキルが無い業務 Agent には、設定の区画を出さない", async ({ page, mockApi }) => {
  // ファイルを作るスキルだけ（Control Plane のツール。grounding: false）の業務 Agent。
  mockApi.state.skills.push({
    id: "synthetic-file-skill",
    name: "合成のファイル作成",
    description: "",
    instructions: "",
    mcp_requirements: [{ server_id: "control-plane", tool_names: ["xlsx_create"] }],
    references: [],
    enabled: true,
    tags: [],
    source: "builtin",
    grounding: false,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  seedAgent(mockApi, { skill_ids: ["synthetic-file-skill"] });
  await page.goto(`/agents?id=${AGENT_ID}`);
  await expect(page.getByRole("heading", { name: "回答前の確認", level: 2 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "資料・データで確かめていない回答", level: 2 })).toHaveCount(0);
  await expect(page.getByTestId("agent-ungrounded-answer")).toHaveCount(0);
});

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`設定の区画は「回答前の確認」の次に、同じ枠・余白・補足の形で出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      seedAgent(mockApi);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto(`/agents?id=${AGENT_ID}`);

      const clarification = page.getByTestId("agent-clarification");
      const ungrounded = page.getByTestId("agent-ungrounded-answer");
      await expect(ungrounded).toBeVisible();
      await ungrounded.scrollIntoViewIfNeeded();

      // 区画の見出し・カード・欄を、隣の「回答前の確認」と比べる（置き場所と形をそろえる）。
      const metrics = await page.evaluate((agentId) => {
        const pick = (testId: string, fieldId: string) => {
          const grid = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement;
          const card = grid.parentElement!.parentElement as HTMLElement;
          const section = card.closest("section") as HTMLElement;
          const heading = section.querySelector("h2") as HTMLElement;
          // 見出しと説明の塊（説明は区画ごとに行数が違うので、塊の下端からカードまでの間隔を比べる）。
          const header = heading.parentElement!.parentElement as HTMLElement;
          const description = heading.nextElementSibling as HTMLElement;
          const control = document.getElementById(fieldId) as HTMLElement;
          const label = section.querySelector(`label[for="${fieldId}"]`) as HTMLElement | null;
          const helper = document.getElementById(control.getAttribute("aria-describedby")?.split(" ")[0] ?? "");
          const rect = (element: Element) => element.getBoundingClientRect();
          const style = (element: Element) => getComputedStyle(element);
          return {
            sectionTop: rect(section).top,
            headingLeft: rect(heading).left,
            headingFont: `${style(heading).fontSize}/${style(heading).fontWeight}`,
            headingToDescription: rect(description).top - rect(heading).bottom,
            descriptionFont: `${style(description).fontSize}/${style(description).color}`,
            headerToCard: rect(card).top - rect(header).bottom,
            cardLeft: rect(card).left,
            cardRight: rect(card).right,
            cardBorder: `${style(card).borderTopWidth} ${style(card).borderTopColor} ${style(card).borderRadius}`,
            cardBackground: style(card).backgroundColor,
            cardPaddingTop: rect(grid).top - rect(card).top,
            cardPaddingLeft: rect(grid).left - rect(card).left,
            cardPaddingBottom: rect(card).bottom - rect(grid).bottom,
            controlLeft: rect(control).left,
            controlHeight: rect(control).height,
            labelLeft: label ? rect(label).left : null,
            labelFont: label ? `${style(label).fontSize}/${style(label).fontWeight}/${style(label).color}` : null,
            labelToControl: label ? rect(control).top - rect(label).bottom : null,
            helperFont: helper ? `${style(helper).fontSize}/${style(helper).color}/${style(helper).lineHeight}` : null,
            helperLeft: helper ? rect(helper).left : null,
            helperRight: helper ? rect(helper).right : null,
            controlToHelper: helper ? rect(helper).top - rect(control).bottom : null,
          };
        };
        return {
          clarification: pick("agent-clarification", `${agentId}-agent-clarification-policy`),
          ungrounded: pick("agent-ungrounded-answer", `${agentId}-agent-ungrounded-answer-policy`),
          clarificationSectionBottom: (
            document.querySelector('[data-testid="agent-clarification"]')!.closest("section") as HTMLElement
          ).getBoundingClientRect().bottom,
          cardInnerRight: (() => {
            const grid = document.querySelector('[data-testid="agent-ungrounded-answer"]') as HTMLElement;
            const card = grid.parentElement!.parentElement as HTMLElement;
            return card.getBoundingClientRect().right - parseFloat(getComputedStyle(grid.parentElement!).paddingRight);
          })(),
        };
      }, AGENT_ID);
      const a = metrics.clarification;
      const b = metrics.ungrounded;
      // 「回答前の確認」のすぐ下の区画。
      expect(b.sectionTop).toBeGreaterThan(metrics.clarificationSectionBottom - 1);
      for (const key of [
        "headingLeft",
        "headingFont",
        "headingToDescription",
        "descriptionFont",
        "headerToCard",
        "cardLeft",
        "cardRight",
        "cardBorder",
        "cardBackground",
        "cardPaddingTop",
        "cardPaddingLeft",
        "cardPaddingBottom",
        "controlLeft",
        "controlHeight",
        "helperFont",
        "helperLeft",
        "controlToHelper",
      ] as const) {
        const [left, right] = [a[key], b[key]];
        if (typeof left === "number" && typeof right === "number") {
          expect(Math.abs(left - right), `${key} を「回答前の確認」とそろえる`).toBeLessThanOrEqual(1);
        } else {
          expect(right, `${key} を「回答前の確認」とそろえる`).toBe(left);
        }
      }
      // 補足は欄の幅で止めず、カードの行の幅で折り返す（行に 1 つの欄。#5817）。カードからははみ出さない。
      expect(b.helperRight!).toBeLessThanOrEqual(metrics.cardInnerRight + 1);
      await expectNoPageHorizontalOverflow(page);

      // 2 つの区画を 1 枚に写す（スクロールの位置と上端の固定の行に左右されないよう、写すときだけ画面を縦に伸ばす）。
      await page.setViewportSize({ width: viewport.width, height: 4000 });
      const clarificationSection = page.locator("section", { has: clarification });
      const ungroundedSection = page.locator("section", { has: ungrounded });
      await ungroundedSection.scrollIntoViewIfNeeded();
      const top = await clarificationSection.boundingBox();
      const bottom = await ungroundedSection.boundingBox();
      await page.screenshot({
        path: testInfo.outputPath(`agent-ungrounded-answer-${viewport.name}-${theme}.png`),
        clip: {
          x: 0,
          y: Math.max(0, top!.y - 16),
          width: viewport.width,
          height: bottom!.y + bottom!.height - top!.y + 32,
        },
      });
    });
  }
}

function seedUngroundedRun(mockApi: MockApi) {
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
        id: "answer-ungrounded",
        kind: "answer",
        name: "回答",
        created_at: MOCK_NOW,
        content: {
          text: ANSWER,
          // backend は値を変えず、basis で確かめていない回答であることを残す。
          outcome: { schema_version: 1, value: "answered", basis: "ungrounded_answer", rag_outcome: null, signals: [] },
          notice: `${UNGROUNDED_NOTICE}\n${GENERAL_NOTICE}`,
        },
      },
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

for (const viewport of VIEWPORTS) {
  test(`確かめていない回答は本文をそのまま出し、注意書きの先頭に注意を出す (${viewport.name})`, { tag: "@responsive" }, async ({ page, mockApi }, testInfo) => {
    seedUngroundedRun(mockApi);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/chat");
    await openThread(page);

    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    const body = turn.getByTestId(`chat-answer-${RUN_ID}`);
    await expect(body).toContainText(ANSWER);
    // 注意は本文に混ぜず、回答の注意書き（本文の後）の先頭の行に出す。
    await expect(body).not.toContainText(UNGROUNDED_NOTICE);
    const notice = turn.getByRole("complementary", { name: "回答の注意書き" });
    await expect(notice).toHaveCount(1);
    await expect(notice).toContainText(UNGROUNDED_NOTICE);
    await expect(notice).toContainText(GENERAL_NOTICE);
    const noticeText = await notice.innerText();
    expect(noticeText.indexOf(UNGROUNDED_NOTICE)).toBe(0);
    expect(noticeText.indexOf(GENERAL_NOTICE)).toBeGreaterThan(noticeText.indexOf(UNGROUNDED_NOTICE));
    const [bodyBox, noticeBox] = await Promise.all([body.boundingBox(), notice.boundingBox()]);
    expect(noticeBox!.y).toBeGreaterThan(bodyBox!.y + bodyBox!.height - 1);
    // 拒答ではないので「根拠不足」のバッジは出さず、内部の語も出さない。
    const turnText = await turn.innerText();
    expect(turnText).not.toContain("根拠不足");
    expect(turnText).not.toContain("お答えできません");
    expect(turnText).not.toContain("ungrounded_answer");
    await expectNoPageHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`ungrounded-answer-chat-${viewport.name}.png`), fullPage: true });
  });
}

test("実行の詳細の回答にも、確かめていない回答の注意を出す", async ({ page, mockApi }) => {
  seedUngroundedRun(mockApi);
  await page.goto(`/runs?id=${RUN_ID}`);
  const section = page.getByTestId("run-answer-section");
  await expect(section.getByRole("complementary", { name: "回答の注意書き" })).toContainText(UNGROUNDED_NOTICE);
  await expect(section).toContainText(ANSWER);
  await expect(section).not.toContainText("ungrounded_answer");
});
