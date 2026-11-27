import { expect, type Page, test } from "./fixtures/test";
import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

/**
 * RAG 検索の回答（#3488）: チャットと同じ見出し（「回答モデル: …」、根拠の画像を添付した回答は「（画像も参照）」）を
 * 出す。
 * 内容は合成。desktop / 375px は playwright の project。
 */

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const summary = {
  id: "profile-a",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  archived_at: null,
};

import { firstAnswer, nextAnswer, originalHits, reviewSnapshots } from "../../../platform/packages/ui/e2e/file-review-scenario";

const ANSWER = "合成の画面 A の下に保存のボタンがあります。";

async function mockApi(page: Page, attachedImages: number, review = false, citations = review ? originalHits : []) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq/suggest")) {
      return route.fulfill({ json: envelope({ suggestions: [] }) });
    }
    return route.fulfill({
      json: envelope({ items: [summary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  });
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: [
        `event: metadata\ndata: ${JSON.stringify({
          trace_id: "trace-model",
          elapsed_ms: 20,
          guardrail_warnings: [],
          diagnostics: {
            search_answer_profile_applied: "profile-a",
            retrieval_strategy: "hybrid",
            answer: {
              outcome: "conditional",
              execution_steps: [],
              evidence_tree: [],
              models: {
                llm: { model_id: "vendor.model-a", label: "合成のモデル A" },
                attached_images: attachedImages,
                embedding: "",
                rerank: "",
              },
            },
          },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: review ? firstAnswer : ANSWER })}\n\n`,
        `event: citations\ndata: ${JSON.stringify(citations)}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-model" })}\n\n`,
      ].join(""),
    })
  );
}

async function ask(page: Page) {
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await enableSearchAnswer(page);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問です。");
  await page.getByRole("button", { name: "検索", exact: true }).click();
}

test.describe("RAG 検索の回答の見出しと段落の印の節", { tag: "@responsive" }, () => {
  test("画像を添付した回答はモデルの名前に「（画像も参照）」を添え、矛盾・確認の節は 1 回だけ出す", async ({ page }, testInfo) => {
    await mockApi(page, 2);
    await ask(page);

    await expect(page.getByTestId("search-answer-model-heading")).toHaveText("回答モデル: 合成のモデル A（画像も参照）");
    await expect(page.getByTestId("search-answer-body")).toBeVisible();
    const headingBeforeBody = page.locator('[data-testid="search-answer-model-heading"] ~ [data-testid="search-answer-body"]');
    await expect(headingBeforeBody).toHaveCount(1);
    await expectNoPageOverflow(page);
    if (process.env.RAG_E2E_SCREENSHOT_DIR) {
      await page.getByTestId("search-answer-model-heading").scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `${process.env.RAG_E2E_SCREENSHOT_DIR}/search-answer-model-${testInfo.project.name}.png`,
        fullPage: true,
      });
    }
  });

  test("画像を添付しなかった回答はモデルの名前だけを出す", async ({ page }) => {
    await mockApi(page, 0);
    await ask(page);

    await expect(page.getByTestId("search-answer-model-heading")).toHaveText("回答モデル: 合成のモデル A");
  });
});

test("多数の根拠は画面の近くのカードだけを描き、最後の原文から操作元へ戻れる", { tag: "@responsive" }, async ({ page }) => {
  const citations = Array.from({ length: 4400 }, (_, index) => ({
    ...originalHits[0],
    chunk_id: `doc-a:${index}`,
    text: `合成の根拠 ${index + 1}`,
  }));
  await mockApi(page, 0, false, citations);
  await ask(page);
  const evidence = page.getByTestId("answer-citations");
  await evidence.locator("summary").click();
  const list = evidence.getByRole("list");
  const cards = list.getByTestId("evidence-card");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeLessThan(20);
  await expect(list.locator('[aria-posinset="1"]')).toHaveAttribute("aria-setsize", "4400");
  await list.focus();
  await page.keyboard.press("End");
  const last = list.locator('[aria-posinset="4400"]');
  await expect(last.getByTestId("citation-text")).toHaveText("合成の根拠 4400");
  const source = last.getByRole("button", { name: /根拠を表示/ });
  await source.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(source).toBeFocused();
  await list.focus();
  await page.keyboard.press("Home");
  await expect(list.locator('[aria-posinset="1"]')).toBeInViewport();
});

test("検索の回答で資料を選び、新候補を確認してから再回答する", {tag:"@responsive"}, async ({page}, testInfo) => {
  await mockApi(page,0,true);
  const states=reviewSnapshots("trace-model","question");
  let state=states.initial;
  const votes:string[]=[];
  await page.route("**/api/feedback/current**", route=>route.fulfill({json:envelope([])}));
  await page.route("**/api/feedback", route=>{
    const vote=route.request().postDataJSON();
    votes.push(vote.rating);
    if(vote.trace_id==="trace-model") state=states.unresolved;
    return route.fulfill({json:envelope(vote)});
  });
  await page.route("**/api/search/answers/trace-model/review", route=>{
    if(route.request().method()==="POST"){
      expect(route.request().headers()["content-type"]).toContain("application/json");
      const command=route.request().postDataJSON();
      if(command.action==="discover"){
        expect(command.selected).toEqual(["doc-a"]); state=states.pending;
      }else{
        expect(command.action).toBe("answer");
        expect(command.selected).toEqual(["doc-a","doc-c"]);state=states.next;
      }
    }
    return route.fulfill({json:envelope(state)});
  });
  await ask(page);
  await page.getByRole("button",{name:"未解決",exact:true}).click();
  const dialog=page.getByRole("dialog");
  await dialog.getByRole("radio",{name:"選んだ資料を残し、ほかも探す"}).check();
  await dialog.getByRole("checkbox",{name:"操作ガイド A.pdf"}).check();
  const list = dialog.getByRole("region", { name: "回答に使う資料を選ぶ" });
  const listHeight = await list.evaluate(element => element.clientHeight);
  const search = dialog.getByRole("searchbox");
  await expect(search).toHaveAccessibleName("ファイル名で絞り込み");
  await expect(dialog.getByRole("heading", { name: "AI に渡した資料", exact: true })).toBeVisible();
  await expect(dialog.getByRole("status")).toHaveText("全 2 件 · 選択 1 件 今回除外 1 件 · 累計除外 1 件");
  await search.fill("ガイド");
  await expect(dialog.getByRole("checkbox")).toHaveCount(1);
  await expect(dialog.getByRole("status")).toHaveText("表示 1/2 件 · 選択 1 件 今回除外 1 件（うち非表示 1 件） · 累計除外 1 件");
  const showAll = dialog.getByRole("button", { name: "全件表示", exact: true });
  await expect(showAll).toBeVisible();
  expect(await list.evaluate(element => element.clientHeight)).toBe(listHeight);
  await showAll.click();
  await expect(dialog.getByRole("checkbox")).toHaveCount(2);
  expect(await list.evaluate(element => element.clientHeight)).toBe(listHeight);
  await search.fill("ガイド");
  await dialog.getByRole("button",{name:"原文",exact:true}).click();
  await expect(dialog.getByText("設定画面を開きます。",{exact:true})).toBeVisible();
  if(process.env.RAG_E2E_SCREENSHOT_DIR) await page.screenshot({path:process.env.RAG_E2E_SCREENSHOT_DIR+"/rag-source-"+testInfo.project.name+".png"});
  await dialog.getByRole("button",{name:"戻る",exact:true}).click();
  await dialog.getByRole("button",{name:"選んだ資料を残して、ほかの候補を探す"}).click();
  const selected=dialog.getByRole("list",{name:"選択した資料"});
  await expect(selected.getByRole("listitem")).toHaveCount(1);
  await expect(dialog.getByRole("checkbox",{name:"確認手順 C.pdf"})).not.toBeChecked();
  await expect(page.getByText(nextAnswer,{exact:true})).toHaveCount(0);
  await dialog.getByRole("checkbox",{name:"確認手順 C.pdf"}).check();
  await selected.getByRole("button",{name:"確認手順 C.pdfを外す"}).click();
  await expect(dialog.getByRole("checkbox",{name:"確認手順 C.pdf"})).not.toBeChecked();
  await dialog.getByRole("checkbox",{name:"確認手順 C.pdf"}).check();
  if(process.env.RAG_E2E_SCREENSHOT_DIR) await page.screenshot({path:process.env.RAG_E2E_SCREENSHOT_DIR+"/rag-search-candidates-"+testInfo.project.name+".png"});
  await dialog.getByRole("button",{name:"選んだ資料だけで再回答"}).click();
  await expect(page.getByText(nextAnswer,{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"解決した",exact:true}).click();
  expect(votes).toEqual(["not_helpful","helpful"]);
  if(process.env.RAG_E2E_SCREENSHOT_DIR) await page.screenshot({path:process.env.RAG_E2E_SCREENSHOT_DIR+"/rag-search-answer-"+testInfo.project.name+".png"});
});

test(
  "55 ファイル・4400 件の根拠で選択した資料を 2 行以内に保つ",
  { tag: "@responsive" },
  async ({ page }, info) => {
    const names = Array.from(
      { length: 60 },
      (_, i) =>
        "合成資料 " + String(i + 1).padStart(2, "0") + "." + ["md", "txt", "docx", "pptx", "json"][i % 5],
    );
    const hits = Array.from({ length: 4400 }, (_, i) => {
      const doc = Math.floor(i / 80),
        n = (i % 80) + 1;
      return {
        ...originalHits[0],
        document_id: "f-" + doc,
        chunk_id: "c-" + i,
        file_name: names[doc],
        text: "合成の根拠 " + (i + 1),
        metadata: { ...originalHits[0].metadata, page_start: n, page_end: n, evidence_model_used: n === 1 },
      };
    });
    const fresh = names
      .slice(55)
      .map((name, i) => ({
        ...hits[0],
        document_id: "f-" + (55 + i),
        chunk_id: "new-" + i,
        file_name: name,
      }));
    const base = reviewSnapshots("trace-model", "question").unresolved;
    const source = { ...base.versions[0].sources.question, citations: hits };
    let state = { ...base, versions: [{ ...base.versions[0], sources: { question: source } }] };
    const commands: string[] = [];
    await mockApi(page, 0, true, hits);
    await page.route("**/api/feedback/current**", (r) => r.fulfill({ json: envelope([]) }));
    await page.route("**/api/feedback", (r) => r.fulfill({ json: envelope(r.request().postDataJSON()) }));
    await page.route("**/api/search/answers/trace-model/review", (r) => {
      if (r.request().method() === "POST") {
        const command = r.request().postDataJSON();
        commands.push(command.action);
        expect(command.selected).toHaveLength(39);
        state = {
          ...state,
          revision: 1,
          kept: { question: command.selected },
          pending: {
            question: {
              ...source,
              citations: [...hits.filter((h) => command.selected.includes(h.document_id)), ...fresh],
            },
          },
        };
      }
      return r.fulfill({ json: envelope(state) });
    });
    await ask(page);
    await page.getByRole("button", { name: "未解決", exact: true }).click();
    const panel = page.getByRole("dialog"),
      selected = panel.getByRole("list", { name: "選択した資料" });
    await panel.getByRole("radio", { name: "選んだ資料を残し、ほかも探す" }).check();
    for (const name of names.slice(0, 40)) await panel.getByRole("checkbox", { name, exact: true }).check();
    await expect(selected.getByRole("listitem")).toHaveCount(40);
    const size = await selected.evaluate((el) => ({
      height: el.clientHeight,
      total: el.scrollHeight,
      row: el.firstElementChild!.getBoundingClientRect().height,
      gap: parseFloat(getComputedStyle(el).rowGap),
    }));
    expect(size.height).toBeLessThanOrEqual(size.row * 2 + size.gap + 1);
    expect(size.total).toBeGreaterThan(size.height);
    await panel.getByPlaceholder("ファイル名の一部を入力").fill("55");
    await expect(selected.getByRole("listitem")).toHaveCount(40);
    await selected.getByRole("button").first().click();
    expect(commands).toHaveLength(0);
    await expect(selected.getByRole("listitem")).toHaveCount(39);
    await panel.getByPlaceholder("ファイル名の一部を入力").fill("");
    await expect(panel.getByRole("checkbox", { name: names[0] })).not.toBeChecked();
    if (process.env.RAG_E2E_SCREENSHOT_DIR)
      await page.screenshot({
        path: process.env.RAG_E2E_SCREENSHOT_DIR + "/" + info.project.name + "-review.png",
      });
    await panel.getByRole("button", { name: "選んだ資料を残して、ほかの候補を探す" }).click();
    await expect(panel.getByRole("checkbox", { name: names[55] })).not.toBeChecked();
    await panel.getByRole("checkbox", { name: names[55] }).check();
    await expect(selected.getByRole("listitem")).toHaveCount(40);
    await expectNoPageOverflow(page);
    if (process.env.RAG_E2E_SCREENSHOT_DIR)
      await page.screenshot({
        path: process.env.RAG_E2E_SCREENSHOT_DIR + "/" + info.project.name + "-candidates.png",
      });
  },
);

test("候補がなくても経過時間を示し、今回の選択に戻れる", { tag: "@responsive" }, async ({ page }, info) => {
  await mockApi(page, 0, true);
  const states = reviewSnapshots("trace-model", "question");
  const prior = { ...states.unresolved, excluded: { "doc-b": "一覧 B.txt" } };
  let current = prior;
  let voted = false;
  let release: (() => void) | undefined;
  const gate = () => new Promise<void>((done) => { release = done; });
  await page.route("**/api/feedback/current**", r => r.fulfill({ json: envelope([]) }));
  await page.route("**/api/feedback", r => {
    voted = true;
    return r.fulfill({ json: envelope(r.request().postDataJSON()) });
  });
  await page.route("**/api/search/answers/trace-model/review", async r => {
    if (r.request().method() === "POST") {
      const action = r.request().postDataJSON();
      if (action.action === "discover") {
        await gate();
        current = { ...prior, revision: 2, excluded: { ...prior.excluded, "doc-a": "操作ガイド A.pdf" },
          pending: { question: { ...prior.versions[0].sources.question, citations: [] } } };
      } else if (action.action === "back") {
        current = { ...prior, revision: 3 };
      } else {
        expect(action.action).toBe("reset");
        await gate();
        current = { ...states.unresolved, excluded: prior.excluded, revision: 4 };
      }
    } else if (voted) {
      await gate();
    }
    return r.fulfill({ json: envelope(!voted ? { ...prior, versions: states.initial.versions } : current) });
  });
  const dialog = page.getByRole("dialog");
  async function waitForProgress(label: string, shotName: string) {
    const progress = dialog.getByTestId("file-review-progress");
    await expect(progress.locator("[data-processing-label]")).toHaveText(label);
    await expect(progress.getByRole("timer")).toHaveText(/00:0[1-9]/);
    await expect(progress.locator(".animate-pulse").first()).toBeVisible();
    await expect(dialog.getByText("新しい候補はありません。")).toHaveCount(0);
    if (process.env.RAG_E2E_SCREENSHOT_DIR) {
      await page.screenshot({ path: process.env.RAG_E2E_SCREENSHOT_DIR + "/review-" + shotName + "-" + info.project.name + ".png" });
    }
    release!();
  }
  await ask(page);
  await page.getByRole("button", { name: "未解決", exact: true }).click();
  await waitForProgress("回答と資料を読み込んでいます", "open");
  await dialog.getByRole("radio", { name: "今回の資料を外して探し直す" }).check();
  await dialog.getByRole("button", { name: "今回の資料を除外して、ほかの候補を探す" }).click();
  await waitForProgress("ほかの候補を検索しています", "discover");
  await expect(dialog.getByText("新しい候補はありません。")).toBeVisible();
  await dialog.getByRole("button", { name: "戻る", exact: true }).click();
  await expect(dialog.getByRole("radio")).toHaveCount(3);
  await expect(dialog.getByRole("checkbox", { name: "操作ガイド A.pdf" })).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: "一覧 B.txt" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "この質問を最初から探す" }).click();
  await dialog.getByRole("button", { name: "この質問を最初から探す" }).click();
  await waitForProgress("この質問の資料を最初から検索しています", "restart");
  await expect(dialog.getByRole("radio")).toHaveCount(3);
});
