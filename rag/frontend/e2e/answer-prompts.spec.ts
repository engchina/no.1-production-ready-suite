import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

// 回答プロンプトの画面（#595）。回答を作る指示のテンプレートと、回答の各工程（読み取り専用）だけを持つ。
// 以前の system prompt の版（作成・有効化）は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

/** TanStack Query の window focus 再取得を起こす(タブへ戻った操作に相当)。 */
async function refetchOnFocus(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答プロンプトの画面はテンプレートだけを出し、版の一覧と作成は無い (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) })
    );

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText("回答生成のプロンプト", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "版を作成" })).toHaveCount(0);
    await expect(page.getByTestId("prompt-version-list")).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText("DocRAG");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "回答プロンプト" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoPageOverflow(page);
  });

  test(`回答プロンプトの読み込み中は経過時間と形の Skeleton を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      await gate;
      await route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) });
    });

    await page.goto("/settings/prompts");

    const loading = page.getByTestId("settings-prompts-loading");
    await expect(loading).toBeVisible();
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答生成のプロンプトを検証・保存・既定に戻せて、各段は読み取り専用で見られる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: { method: string; body: unknown }[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      const method = route.request().method();
      if (method === "GET") {
        await route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false) });
        return;
      }
      const body = method === "PUT" ? route.request().postDataJSON() : null;
      requests.push({ method, body });
      if (method === "PUT" && !String((body as { content: string }).content).includes("{{images}}")) {
        await route.fulfill({
          status: 422,
          json: { data: null, error_messages: ["必須の placeholder がありません: {{images}}"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json:
          method === "PUT"
            ? answerPromptsEnvelope((body as { content: string }).content, true)
            : answerPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false),
      });
    });

    await page.goto("/settings/prompts");

    const field = page.getByLabel("テンプレート");
    const save = page.getByRole("button", { name: "プロンプトを保存" });
    await expect(page.getByText("既定値", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "既定に戻す" })).toBeDisabled();
    await field.fill("質問だけ {{question}}");
    await save.click();
    // 必須の placeholder の欠けは送る前に欄の下へ出し、欄へフォーカスを移す（#1010）。
    await expect(page.getByText("必須の placeholder がありません: {{images}}")).toBeVisible();
    await expect(field).toBeFocused();

    await field.fill("独自 {{question}} {{images}}");
    await save.click();
    await expect(page.getByText("プロンプトを保存しました。")).toBeVisible();
    await expect(page.getByText(/^編集済み/)).toBeVisible();

    await page.getByRole("button", { name: "既定に戻す" }).click();
    await page.getByRole("alertdialog", { name: "既定のプロンプトに戻しますか？" }).getByRole("button", { name: "既定に戻す" }).click();
    await expect(page.getByText("既定のプロンプトに戻しました。")).toBeVisible();
    await expect(field).toHaveValue("既定のテンプレート {{question}} {{images}}");
    expect(requests.map((request) => request.method)).toEqual(["PUT", "DELETE"]);

    await page.getByTestId("answer-prompt-stage-audit").locator("summary").click();
    await expect(page.getByText("監査の system")).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`各段のプロンプトは処理の順の区分の見出しの下に並び、閉じたまま使われる条件が分かる（#2054, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{images}}", false, FLOW_STAGES) })
    );

    await page.goto("/settings/prompts");

    const stages = page.getByRole("region", { name: "処理の各段のプロンプト（読み取り専用）" });
    await expect(stages).toBeVisible();
    // 区分は処理の順（取込 → 質問の解釈 → 検索 → 根拠の判定 → 回答の生成 → 監査 → 評価）。
    await expect(stages.getByRole("heading", { level: 4 })).toHaveText([
      "取込",
      "質問の解釈",
      "検索",
      "根拠の判定",
      "回答の生成",
      "監査",
      "評価",
    ]);
    // 段は区分の中に入り、開かなくても使われる条件を 1 行で示す。
    const ingestion = stages.getByRole("region", { name: "取込" });
    const picture = ingestion.getByTestId("answer-prompt-stage-picture_description");
    await expect(picture).toContainText("図・画像の説明");
    await expect(picture).toContainText("使われる条件: 「図・画像を AI で読み取る」を有効にしたとき");
    await expect(picture.getByText("図の system")).toBeHidden();
    await expect(stages.getByRole("region", { name: "回答の生成" }).getByTestId("answer-prompt-stage-generation")).toContainText(
      "使われる条件: 常に（回答ごと）"
    );
    await expect(stages.getByRole("region", { name: "評価" }).getByTestId("answer-prompt-stage-evaluation")).toContainText(
      "使われる条件: 品質評価と、保存した回答を標準回答で評価するときだけ"
    );

    // 開くと部分の名前と内容が見える。
    await picture.locator("summary").click();
    await expect(picture.getByText("system プロンプト", { exact: true })).toBeVisible();
    await expect(picture.getByText("図の system")).toBeVisible();
    await expect(picture.getByText("表を読むときの対象の指示", { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答生成のプロンプトは空を送る前に止め、変更を破棄で保存値へ戻せる（#1010, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: string[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      if (route.request().method() !== "GET") requests.push(route.request().method());
      await route.fulfill({ json: answerPromptsEnvelope("保存版 {{question}} {{images}}", true) });
    });

    await page.goto("/settings/prompts");
    const field = page.getByRole("textbox", { name: "テンプレート", exact: true });
    const actions = page.getByRole("group", { name: "編集内容の操作" });
    const save = actions.getByRole("button", { name: "プロンプトを保存" });
    const discard = actions.getByRole("button", { name: "変更を破棄" });
    await expect(discard).toBeDisabled();

    await field.fill("   ");
    await expect(actions).toContainText("未保存の変更があります。");
    await save.click();
    await expect(page.getByText("テンプレートを入力してください。")).toBeVisible();
    await expect(field).toBeFocused();
    expect(requests).toEqual([]);

    await discard.click();
    await expect(field).toHaveValue("保存版 {{question}} {{images}}");
    await expect(page.getByText("テンプレートを入力してください。")).toHaveCount(0);
    await expect(save).toBeDisabled();
    await expect(actions.getByText(/^編集済み/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("回答生成のプロンプトは編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let content = "保存版 A {{question}} {{images}}";
  await page.route("**/api/settings/answer-prompts**", (route) =>
    route.fulfill({ json: answerPromptsEnvelope(content, true) })
  );

  await page.goto("/settings/prompts");
  const editor = page.getByRole("textbox", { name: "テンプレート", exact: true });
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。初回の取得（StrictMode の再購読による取り直しを含む）が
  // まだ途中だと、フォーカスの再取得はその取得（保存版 A）にまとめられるため、B が届くまで再取得を促す（#833）。
  content = "保存版 B {{question}} {{images}}";
  await expect
    .poll(async () => {
      await refetchOnFocus(page);
      return editor.inputValue();
    })
    .toBe(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("編集中 {{question}} {{images}}");
  content = "保存版 C {{question}} {{images}}";
  const refetched = page.waitForResponse("**/api/settings/answer-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("編集中 {{question}} {{images}}");
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

type StageFixture = { id: string; phase: string; prompts: { id: string; content: string }[] };

const DEFAULT_STAGES: StageFixture[] = [
  { id: "routing", phase: "question", prompts: [{ id: "system", content: "ルーティングの system" }] },
  { id: "audit", phase: "audit", prompts: [{ id: "system", content: "監査の system" }] },
];

// 7 つの区分にまたがる段（backend の PROMPT_STAGES の一部。内容は合成）。
const FLOW_STAGES: StageFixture[] = [
  {
    id: "picture_description",
    phase: "ingestion",
    prompts: [
      { id: "system", content: "図の system" },
      { id: "table", content: "表の指示" },
    ],
  },
  { id: "field_extraction", phase: "ingestion", prompts: [{ id: "instruction", content: "項目の指示" }] },
  { id: "history_rewrite", phase: "question", prompts: [{ id: "system", content: "書き換えの system" }] },
  { id: "routing", phase: "question", prompts: [{ id: "system", content: "ルーティングの system" }] },
  { id: "screen_linking", phase: "retrieval", prompts: [{ id: "system", content: "画面の system" }] },
  { id: "crag", phase: "grounding", prompts: [{ id: "system", content: "判定の system" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "system", content: "生成の system" }] },
  {
    id: "audit",
    phase: "audit",
    prompts: [
      { id: "system", content: "監査の system" },
      { id: "compactRule", content: "理由の規則" },
    ],
  },
  { id: "evaluation", phase: "evaluation", prompts: [{ id: "scope", content: "範囲の指示" }] },
];

function answerPromptsEnvelope(content: string, customized: boolean, stages: StageFixture[] = DEFAULT_STAGES) {
  return {
    data: {
      prompts: [
        {
          key: "vlm_answer",
          content,
          default_content: "既定のテンプレート {{question}} {{images}}",
          customized,
          required_placeholders: ["question", "images"],
          updated_at: customized ? "2026-09-26T01:00:00Z" : null,
        },
      ],
      stages,
    },
    error_messages: [],
    warning_messages: [],
  };
}
