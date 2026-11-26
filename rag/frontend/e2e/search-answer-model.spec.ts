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

async function mockApi(page: Page, attachedImages: number, review=false) {
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
        `event: citations\ndata: ${JSON.stringify(review ? originalHits : [])}\n\n`,
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
  await search.fill("ガイド");
  await expect(dialog.getByRole("checkbox")).toHaveCount(1);
  const showAll = dialog.getByRole("button", { name: "全件表示（除外予定 1 件が非表示）" });
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
  await dialog.getByRole("button",{name:/選択した1件を残して/}).click();
  await expect(dialog.getByRole("checkbox",{name:"確認手順 C.pdf"})).not.toBeChecked();
  await expect(page.getByText(nextAnswer,{exact:true})).toHaveCount(0);
  await dialog.getByRole("checkbox",{name:"確認手順 C.pdf"}).check();
  if(process.env.RAG_E2E_SCREENSHOT_DIR) await page.screenshot({path:process.env.RAG_E2E_SCREENSHOT_DIR+"/rag-search-candidates-"+testInfo.project.name+".png"});
  await dialog.getByRole("button",{name:"選んだ資料だけで再回答"}).click();
  await expect(page.getByText(nextAnswer,{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"解決した",exact:true}).click();
  expect(votes).toEqual(["not_helpful","helpful"]);
  if(process.env.RAG_E2E_SCREENSHOT_DIR) await page.screenshot({path:process.env.RAG_E2E_SCREENSHOT_DIR+"/rag-search-answer-"+testInfo.project.name+".png"});
});
