import structuredSource from "./fixtures/structured-source-reader.json";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

// #2512: 抜粋の長い根拠で「抜粋と頁・章節」を開いても、原文のプレビューを画面の外へ押し出さない（Agent と同じ共通の
// `EvidenceSourceSheet`）。抜粋は要約の中の高さに上限のある領域でスクロールし（Tab で届き、下端のフェードで続きを
// 示す）、テキストの原本は引用した箇所へ移って強調する。内容はすべて合成のデータ。

const PROFILE = {
  id: "profile-a",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-10-18T00:00:00Z",
  updated_at: "2026-10-18T00:00:00Z",
  archived_at: null,
};

const LINES = Array.from({ length: 80 }, (_, index) => `合成の項目 ${index + 1} の本文です。`);
const DOCUMENT_TEXT = [
  "{",
  '  "items": [',
  ...LINES.map((line, index) => `    { "id": ${index + 1}, "text": "${line}" },`),
  "  ]",
  "}",
].join("\n");
const EXCERPT = LINES.slice(49, 79).join("\n");

const CITATIONS = [
  {
    document_id: "doc-a",
    chunk_id: "doc-a:cs-1:1",
    text: EXCERPT,
    score: 0.5,
    rerank_score: 0.7,
    file_name: "文書 A.json",
    category_name: null,
    metadata: { context_role: "evidence", vector_rank: 1, content_kind: "text", section_path: "第 1 章" },
  },
];

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-text",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: { retrieval_strategy: "hybrid", retrieval_strategy_adapter: "grounded" },
    })}\n\n`,
    `event: citations\ndata: ${JSON.stringify(CITATIONS)}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-text" })}\n\n`,
  ].join("");
}

async function search(page: Page) {
  await page.addInitScript(() => localStorage.setItem("production-ready-rag.ui", JSON.stringify({state:{theme:matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"},version:0})));
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [PROFILE], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: searchStreamBody() })
  );
  await page.route("**/api/documents/doc-a/content**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: DOCUMENT_TEXT })
  );
  await page.route("**/api/documents/doc-a/reader?**", route => route.fulfill({ json: { data: readerWindow(EXCERPT, new URL(route.request().url()).searchParams) } }));
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByTestId("evidence-card")).toHaveCount(1);
}

test("抜粋の長い根拠を開いてもプレビューは画面に残り、テキストの原本は引用の箇所を強調する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await search(page);
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.json の根拠を表示" }).click();
  const sheet = page.getByRole("dialog", { name: "検索で見つかった箇所" });
  const summary = sheet.getByTestId("evidence-source-summary");
  const toggle = summary.getByTestId("evidence-excerpt-toggle");
  await toggle.click();
  const scroll = summary.getByRole("region", { name: "抜粋と頁・章節" });
  await expect(scroll.getByTestId("citation-text")).toContainText(LINES[49]);
  // 抜粋は上限の高さの中でスクロールし、続きがあることを下端のフェードで示す。
  await expect(scroll).toHaveAttribute("data-overflowing", "true");
  await expect(scroll.getByTestId("evidence-excerpt-fade")).toHaveCSS("opacity", "1");
  const viewport = page.viewportSize()!;
  expect((await scroll.boundingBox())!.height).toBeLessThanOrEqual(viewport.height * 0.3);
  // プレビューは押し出されず、画面の中に十分な高さで残る。
  const preview = sheet.getByTestId("evidence-source-preview");
  const previewBox = (await preview.boundingBox())!;
  expect(previewBox.y + previewBox.height / 2).toBeLessThanOrEqual(viewport.height);
  expect(viewport.height - previewBox.y).toBeGreaterThanOrEqual(viewport.height * 0.35);
  const reader = preview.getByTestId("evidence-reader");
  await expect(reader.locator("mark")).toContainText(LINES[49]);
  await expect(reader.locator("mark")).toBeInViewport();
  await expect(reader).toContainText("追加の参考文脈");
  await expect(reader.getByRole("table", {name:"引用元の表"}).getByRole("columnheader").first()).toContainText("項目");
  await reader.getByRole("button", { name: "後文を読む", exact: true }).click();
  await expect(reader).toContainText("合成の続き");
  await reader.getByRole("button", { name: "引用位置に戻る", exact: true }).click();
  await expect(reader.locator("mark")).toContainText(LINES[49]);
  await expect(reader.locator("mark")).toBeInViewport();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`rag-long-excerpt-${testInfo.project.name}.png`) });
  // 抜粋の領域は Tab で届き、キーボードで最後まで読める（読み終えるとフェードを消す）。
  await toggle.focus();
  await page.keyboard.press("Tab");
  await expect(scroll).toBeFocused();
  await page.keyboard.press("End");
  await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(scroll.getByTestId("evidence-excerpt-fade")).toHaveCSS("opacity", "0");
});

// #4092: 再索引で引用の版が無くなった根拠は、赤いエラーと再試行ではなく案内にする。本当の障害はエラーのまま。
test("引用の版が無い根拠は案内を出し、障害はエラーと再試行のまま", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await search(page);
  let failure = { status: 409, json: { data: null, error_messages: ["資料の版が変わりました。"], warning_messages: [], error_code: "source_stale" } as object };
  await page.route("**/api/documents/doc-a/reader?**", route => route.fulfill(failure));
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.json の根拠を表示" }).click();
  const sheet = page.getByRole("dialog", { name: "検索で見つかった箇所" });
  const preview = sheet.getByTestId("evidence-source-preview");
  const notice = preview.getByTestId("source-version-stale-notice");
  await expect(notice).toContainText("資料が再処理されたため、回答時点の引用の版は表示できません。");
  await expect(notice.getByRole("status")).toBeVisible();
  await expect(notice).toBeInViewport();
  await expect(preview.getByRole("alert")).toHaveCount(0);
  await expect(preview.getByRole("button")).toHaveCount(0);
  // 保存した抜粋は要約で読める。
  await expect(sheet.getByTestId("evidence-source-summary")).toContainText("文書 A.json");
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`rag-stale-citation-${testInfo.project.name}.png`) });
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();

  failure = { status: 500, json: { data: null, error_messages: ["合成の障害です。"], warning_messages: [] } };
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.json の根拠を表示" }).click();
  await expect(preview.getByRole("alert")).toContainText("合成の障害です。");
  await expect(preview.getByRole("button", { name: "再試行" })).toBeVisible();
  await expect(preview.getByTestId("source-version-stale-notice")).toHaveCount(0);
});

function readerWindow(quote: string, params: URLSearchParams) {
  const cursor = params.get("cursor");
  const scope = params.get("scope");
  const section = params.get("section_id");
  const continuation = cursor !== null;
  const body = section ? "目次で選んだ節の冒頭" : scope === "parent" ? "親章節の冒頭" : scope === "section" ? "本節の冒頭" : cursor === "before" ? "合成の前文" : continuation ? "合成の続き" : "前文。".repeat(400) + quote;
  return { revision: "v1", anchor_status: "exact", section_path: ["章 A", "節 A"], scope: scope ?? "paragraph",
    blocks: [{ id: continuation ? cursor : scope ?? "anchor", text: body, offset: 0, highlights: continuation || scope ? [] : [[1200, 1200 + quote.length]], cited: !continuation && !scope }, { id: "context", kind: "table", text: "<table><tr><th>項目</th><th>値</th></tr><tr><td>A</td><td>1</td></tr></table>", offset: 0, highlights: [], cited: false }],
    previous_cursor: scope ? null : "before", next_cursor: continuation ? null : "after",
    outline: Array.from({length:25}, (_,i) => ({id:`section-${i}`,title:`節 ${i+1}：合成の長い章節見出しを折り返して表示する`,parent_id:null,has_children:false})) };
}

test("長い目次は本文を押し出さず、五つの読み取り操作で選んだ範囲へ移る", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await search(page);
  await expect(page.locator("html")).toHaveClass(new RegExp(testInfo.project.use.colorScheme === "dark" ? "dark" : "^(?!.*dark)"));
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.json の根拠を表示" }).click();
  const reader = page.getByTestId("evidence-reader");
  const body = reader.getByRole("region", {name:"引用元の本文"});
  const outline = reader.getByTestId("evidence-reader-outline");
  await expect(body.locator("mark")).toBeInViewport();
  await reader.getByRole("button", {name:"前文を読む",exact:true}).click();
  await expect(body).toContainText("合成の前文");
  await expect(body.locator('[data-reader-current="true"]').locator("p").first()).toBeInViewport();
  await expect(reader.getByTestId("evidence-reader-location")).toHaveText("前文へ移動しました。");
  await reader.getByRole("button", {name:"本節を読む",exact:true}).click();
  await expect(body).toContainText("本節の冒頭");
  await expect(body.locator('[data-reader-current="true"]').locator("p").first()).toBeInViewport();
  await expect(body).toBeFocused();
  await expect.poll(() => body.evaluate(node => node.scrollTop)).toBe(0);
  await expect(reader.getByRole("button", {name:"前文を読む",exact:true})).toBeDisabled();
  await reader.getByRole("button", {name:"親章節を読む",exact:true}).click();
  await expect(body).toContainText("親章節の冒頭");
  await expect(body.locator('[data-reader-current="true"]').locator("p").first()).toBeInViewport();
  await reader.getByTestId("evidence-reader-outline-toggle").click();
  await expect(outline).toHaveAttribute("open", "");
  const nav = outline.getByRole("navigation", {name:"目次"});
  expect((await nav.boundingBox())!.height).toBeGreaterThan(page.viewportSize()!.height * .2);
  await expect(body).toBeInViewport();
  expect((await body.boundingBox())!.height).toBeGreaterThanOrEqual(page.viewportSize()!.height * .2);
  await page.screenshot({path:testInfo.outputPath(`reader-outline-${testInfo.project.name}.png`)});
  await nav.getByRole("button").last().focus();
  await page.keyboard.press("Enter");
  await expect(outline).not.toHaveAttribute("open", "");
  await expect(body).toContainText("目次で選んだ節の冒頭");
  await expect(body.locator('[data-reader-current="true"]').locator("p").first()).toBeInViewport();
  await page.screenshot({path:testInfo.outputPath(`reader-section-selected-${testInfo.project.name}.png`),animations:"disabled"});
  await expect(body).toBeFocused();
  await reader.getByRole("button", {name:"引用位置に戻る",exact:true}).click();
  await expect(body.locator("mark")).toBeInViewport();
  await reader.getByRole("button", {name:"後文を読む",exact:true}).click();
  await expect(body).toContainText("合成の続き");
  await expect(body.locator('[data-reader-current="true"]').locator("p").first()).toBeInViewport();
  await page.screenshot({path:testInfo.outputPath(`reader-after-selected-${testInfo.project.name}.png`),animations:"disabled"});
  await expect(reader.getByRole("button", {name:"後文を読む",exact:true})).toBeDisabled();
  await expectNoPageOverflow(page);
});


test("前後文の明示移動は新しい本文を表示し、四窓目でも取得が重複しない", {tag:"@responsive"}, async ({page}) => {
  await search(page);
  await page.route("**/api/documents/doc-a/reader?**", route => {
    const params = new URL(route.request().url()).searchParams;
    const number = Number(params.get("cursor") ?? "0");
    return route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],scope:"paragraph",
      blocks:[{id:`window-${number}`,text:`窓 ${number} の本文。` + "合成の長い段落。\n".repeat(100),offset:0,highlights:[],cited:number===0}],
      previous_cursor:number>0?String(number-1):null,next_cursor:String(number+1),outline:[]}}});
  });
  await page.getByTestId("evidence-card").getByRole("button", {name:"文書 A.json の根拠を表示"}).click();
  const reader = page.getByTestId("evidence-reader");
  const body = reader.getByRole("region", {name:"引用元の本文"});
  for(let i=1;i<=2;i++) {
    await reader.getByRole("button",{name:"後文を読む",exact:true}).click();
    await expect(body.locator(`[data-reader-block="window-${i}:0"]`)).toHaveCount(1);
    await expect(body.locator(`[data-reader-block="window-${i}:0"]`).locator("p").first()).toBeInViewport();
  }
  const retained = body.locator('[data-reader-block="window-1:0"]');
  await retained.evaluate(node => node.scrollIntoView({block:"start"}));
  await expect(retained.locator("p").first()).toBeInViewport();
  await reader.getByRole("button",{name:"後文を読む",exact:true}).click();
  await expect(body.locator('[data-reader-block="window-3:0"]')).toHaveCount(1);
  await expect(body.locator("[data-reader-block]")).toHaveCount(3);
  await expect(body.locator('[data-reader-block="window-0:0"]')).toHaveCount(0);
  await expect(body.locator('[data-reader-block="window-3:0"]').locator("p").first()).toBeInViewport();
  await reader.getByRole("button",{name:"前文を読む",exact:true}).click();
  await expect(body.locator('[data-reader-block="window-0:0"]')).toHaveCount(1);
  await expect(body.locator("[data-reader-block]")).toHaveCount(3);
  await expect(body.locator('[data-reader-block="window-0:0"]').locator("p").first()).toBeInViewport();
});

test("原本ノードは展開時だけ取得し、版と対応範囲を保持する", {tag:"@responsive"}, async ({page},testInfo) => {
  await search(page);
  const inputs: URLSearchParams[] = [];
  let release!: () => void;
  const wait = new Promise<void>(resolve => {release = resolve;});
  let failNext = false;
  await page.route("**/api/documents/doc-a/source-map?**",async route => {
    const params = new URL(route.request().url()).searchParams; inputs.push(params);
    const index = Number(params.get("source_index") ?? 0);
    await wait;
    if (failNext) return route.fulfill({status:503,json:{data:null,error_messages:["合成の原本取得失敗"],warning_messages:[]}});
    return route.fulfill({json:{data:{status:"exact",revision:"ex1",chunk_revision:"v1",element_id:"anchor",variant:"original",pointer:`/nodes/${index}`,source_index:index,source_count:2,text:"😀引用した合成本文",highlights:[[1,3]],coordinate_unit:"unicode_codepoint",next_offset:null,previous_offset:null}}});
  });
  await page.getByTestId("evidence-card").getByRole("button",{name:"文書 A.json の根拠を表示"}).click();
  const reader=page.getByTestId("evidence-reader");
  const panel=reader.getByTestId("original-source-panel").first();
  await expect(panel).toBeVisible(); expect(inputs).toHaveLength(0);
  await panel.locator("summary").click();
  await expect(panel.locator("[data-processing-label]")).toBeVisible();
  await expect(panel).toContainText("00:01",{timeout:4000});
  release();
  const content=panel.getByTestId("original-source-content");
  await expect(content.locator("mark")).toHaveText("引用");
  expect(inputs[0].get("expected_revision")).toBe("v1");
  expect(inputs[0].get("element_id")).toBe("anchor");
  await expect(content).toBeInViewport();
  await expect(content).toBeFocused();
  await page.screenshot({path:testInfo.outputPath(`original-node-${testInfo.project.name}.png`),animations:"disabled"});
  failNext = true;
  await panel.getByRole("button",{name:"次の原本ノード",exact:true}).click();
  await expect(panel).toContainText("合成の原本取得失敗");
  await expect(content).toContainText("/nodes/0");
  failNext = false;
  await panel.getByRole("button",{name:"再試行",exact:true}).click();
  await expect(content).toContainText("/nodes/1");
  expect(inputs.at(-1)?.get("source_index")).toBe("1");
  await expectNoPageOverflow(page);
});


for (const extension of ["txt", "md", "markdown", "csv", "xlsx", "docx", "json", "html", "htm"]) {
  test(`Chatの原文の段落を強調する (${extension})`, {tag:"@responsive"}, async ({page}, testInfo) => {
    await mockDatabaseReady(page); await mockLocalAuth(page);
    const name = `synthetic.${extension}`;
    const structured = extension === "csv" || extension === "xlsx" || extension === "docx" ? structuredSource[extension] : null;

    const cited = {document_id:"doc-text",chunk_id:"chunk-text",file_name:name,text:structured?.window.blocks.find(block=>block.cited)?.text ?? "見出し\n合成😀の本文です。",score:1,metadata:{recipe_id:"r1",chunk_set_id:"cs1",element_ids:structured ? [structured.element_id] : ["h","a"],context_role:"evidence",answer_citation_lines:[1],answer_citation_provenance:"child"}};
    await page.route("**/api/search-answer-profiles**", route => route.fulfill({json:{data:{items:[PROFILE],total:1,limit:50,offset:0,has_next:false},error_messages:[],warning_messages:[]}}));
    await page.route("**/api/chat/conversations**", route => {
      const detail = {id:"c-text",search_answer_profile_id:PROFILE.id,title:"合成の原文確認",status:"ACTIVE",owned:true,message_count:2,created_at:"2026-11-01T00:00:00Z",updated_at:"2026-11-01T00:00:00Z",messages:[
        {message_id:"u-text",conversation_id:"c-text",role:"USER",content:"合成の値を教えてください。",status:"COMPLETE",created_at:"2026-11-01T00:00:00Z",citations:[],guardrail_warnings:[]},
        {message_id:"a-text",reply_to_message_id:"u-text",model:"synthetic-model",conversation_id:"c-text",role:"ASSISTANT",content:`資料により確認できた事項\n・合成😀の本文です。\n根拠：${name}`,status:"COMPLETE",created_at:"2026-11-01T00:00:01Z",citations:[cited],guardrail_warnings:[]},
      ]};
      return route.fulfill({json:{data:new URL(route.request().url()).pathname.endsWith("/c-text") ? detail : {items:[{...detail,messages:undefined}],total:1,offset:0,limit:25,has_next:false},error_messages:[],warning_messages:[]}});
    });
    await page.route("**/api/documents/doc-text/reader?**", route => route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],blocks:[
      {id:"h",text:"見出し",offset:0,highlights:[[0,3]],cited:true},
      {id:"a",text:"合成😀の本文です。",offset:0,highlights:[[0,9]],cited:true},
      {id:"other",text:"別の文脈です。",offset:0,highlights:[],cited:false},
    ],outline:[],previous_cursor:extension === "md" ? "before" : null,next_cursor:extension === "md" ? "after" : null,scope:"paragraph"},error_messages:[],warning_messages:[]}}));
    if(structured) {
      await page.route("**/api/documents/doc-text/recipes/r1/preview-source?**",route=>route.fulfill({json:{data:{file_name:"synthetic.jsonl",content_type:"application/vnd.production-ready.structured-records+jsonl",is_pdf:false,is_structured_records:true}}}));
      await page.route("**/api/documents/doc-text/reader?**",route=>route.fulfill({json:{data:{...structured.window,revision:"cs1"}}}));
    }
    const genericOriginal = ["json", "html", "htm"].includes(extension);
    const original = extension === "json" ? '{"a":"同じ値","b":"同じ値"}' : '<p>同じ値</p><p>同じ値</p>';
    const selectedOffset = original.lastIndexOf("同じ値");
    const originalInputs: URLSearchParams[] = [];
    if (genericOriginal) await page.route("**/api/documents/doc-text/source-map?**", route => {
      originalInputs.push(new URL(route.request().url()).searchParams);
      return route.fulfill({json:{data:{status:"exact",revision:"ex1",chunk_revision:"v1",anchor_status:"element",element_id:"a",variant:"original",pointer:extension === "json" ? "/b" : "p[2]",source_index:0,source_count:1,text:original,offset:0,highlights:[[selectedOffset,selectedOffset+3]],coordinate_unit:"unicode_codepoint",previous_offset:null,next_offset:null},error_messages:[],warning_messages:[]}});
    });
    await page.goto(`/chat?conversation_id=c-text&search_answer_profile_id=${PROFILE.id}`);
    await page.getByTestId("chat-answer-body").getByRole("button").filter({hasText:name}).first().click();
    const reader=page.getByTestId("evidence-reader-content");
    if(structured) {
      await expect(reader.locator("[data-source-record]")).not.toHaveCount(0);
      await expect(reader.locator("mark").first()).toBeVisible();
      await expect(reader.getByRole("table").last()).toBeVisible();
    } else await expect(reader.locator("mark")).toHaveText(["見出し","合成😀の本文です。"]);
    await expect(reader.locator('[data-reader-cited="false"] mark')).toHaveCount(0);
    if (extension === "md") {
      let release!: () => void;
      let fail = false;
      await page.route("**/api/documents/doc-text/reader?**", async route => {
        await new Promise<void>(resolve => { release = resolve; });
        if (fail) await route.fulfill({status:500,json:{error_messages:["合成の取得失敗"]}});
        else await route.fulfill({json:{data:readerWindow("合成😀の本文です。",new URL(route.request().url()).searchParams)}});
      });
      const root = page.getByTestId("evidence-reader");
      for (const direction of ["前文を読む", "後文を読む"]) {
        fail = direction === "後文を読む";
        await root.getByRole("button",{name:direction,exact:true}).click();
        const loading = root.getByTestId("evidence-reader-loading");
        await expect(loading).toBeVisible();
        await expect(reader).toHaveAttribute("aria-busy","true");
        await expect(reader).toHaveClass(/bg-surface-sunken/);
        await expect(loading.locator(".animate-pulse")).toHaveCount(0);
        await expect(loading.locator("[data-processing-label]")).toBeVisible();
        expect((await loading.boundingBox())!.height).toBeLessThan(120);
        await expect(loading).toContainText("00:01",{timeout:4000});
        await page.screenshot({path:testInfo.outputPath(`markdown-loading-${direction}-${testInfo.project.name}.png`)});
        release();
        await expect(reader).toHaveAttribute("aria-busy","false");
        await expect(reader).not.toHaveClass(/bg-surface-sunken/);
        if (fail) {
          await expect(root).toContainText("合成の取得失敗");
          fail = false;
          await root.getByRole("button",{name:"再試行",exact:true}).click();
          await expect(reader).toHaveAttribute("aria-busy","true");
          await expect(loading).toBeVisible();
          release();
          await expect(reader).toHaveAttribute("aria-busy","false");
          await expect(reader).toContainText("合成の続き");
        }
        else await expect(reader).toContainText("合成の前文");
      }
    }
    if (genericOriginal) {
      const panel = page.getByTestId("original-source-panel").first();
      await panel.locator("summary").focus();
      await page.keyboard.press("Enter");
      await expect(panel.locator("mark")).toHaveText("同じ値");
      expect(originalInputs[0].get("expected_revision")).toBe("v1");
      const mark = panel.locator("mark");
      expect(await mark.evaluate(el => el.previousSibling?.textContent)).toBe(original.slice(0,selectedOffset));
      await expect(panel.getByText("原本 · 1 / 1",{exact:true})).toBeVisible();
    }
    await expectNoPageOverflow(page);
    await page.screenshot({path:testInfo.outputPath(`text-source-chat-${extension}.png`)});
  });
}


test("通常表は保存された範囲に対応する2つ目のセルだけを強調する", {tag:"@responsive"}, async ({page}, testInfo) => {
  await search(page);
  const text = '<table><tr><th>項目</th><th>値</th></tr><tr><td>合成A</td><td>同じ値😀 &amp; 前<br>後</td></tr><tr><td>合成B</td><td>同じ値😀 &amp; 前<br>後</td></tr></table>';
  const start = Array.from(text.slice(0,text.lastIndexOf("同じ値"))).length;
  await page.route("**/api/documents/doc-a/reader?**", route => route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],scope:"paragraph",blocks:[{id:"table",kind:"table",text,offset:0,cited:true,highlights:[[start,start+3]]}],outline:[],previous_cursor:null,next_cursor:null}}}));
  await page.getByTestId("evidence-card").getByRole("button",{name:"文書 A.json の根拠を表示"}).click();
  const reader = page.getByTestId("evidence-reader-content");
  await expect(reader.getByRole("table")).toBeVisible();
  await page.screenshot({path:testInfo.outputPath(`table-initial-${testInfo.project.name}.png`),animations:"disabled"});
  await expect(reader.locator("mark")).toHaveText("同じ値😀 & 前\n後");
  await expect(reader.getByRole("cell").filter({hasText:"合成A"})).toBeVisible();
  await expect(reader.getByRole("row").filter({hasText:"合成A"}).locator("mark")).toHaveCount(0);
  await expect(reader).toContainText("セル単位で強調");
  await expectNoPageOverflow(page);
  await page.screenshot({path:testInfo.outputPath(`table-highlight-${testInfo.project.name}.png`),animations:"disabled"});
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({width:1920,height:1080});
    await expectNoPageOverflow(page);
    await page.screenshot({path:testInfo.outputPath("table-highlight-wide.png"),animations:"disabled"});
    await page.setViewportSize({width:1280,height:900});
    await page.evaluate(()=>document.documentElement.classList.add("dark"));
    await expectNoPageOverflow(page);
    await page.screenshot({path:testInfo.outputPath("table-highlight-dark.png"),animations:"disabled"});
  }
});


test("実解析の縦線表は同じ保存範囲からセルを強調し、参照文脈には付けない", {tag:"@responsive"}, async ({page}, testInfo) => {
  await search(page);
  const text='| 項目 | 値 |\n| 合成A | 同じ値😀 & 前 後 |\n| 合成B | 同じ値😀 & 前 後 |';
  const start=Array.from(text.slice(0,text.lastIndexOf('同じ値'))).length;
  await page.route("**/api/documents/doc-a/reader?**",route=>route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],scope:"paragraph",blocks:[{id:"table",kind:"table",text,offset:0,cited:true,highlights:[[start,start+3]]},{id:"context",kind:"table",text,offset:0,cited:false,highlights:[[start,start+3]]}],outline:[],previous_cursor:null,next_cursor:null}}}));
  await page.getByTestId("evidence-card").getByRole("button",{name:"文書 A.json の根拠を表示"}).click();
  const reader=page.getByTestId("evidence-reader-content");
  const cited=reader.locator('[data-reader-cited="true"]');
  await expect(cited.getByRole("table")).toHaveCount(1);
  await expect(cited.locator("mark")).toHaveText("同じ値😀 & 前 後");
  await expect(cited.getByRole("row").filter({hasText:"合成A"}).locator("mark")).toHaveCount(0);
  await expect(reader.locator('[data-reader-cited="false"] mark')).toHaveCount(0);
  await expect(reader).toContainText("セル単位で強調");
  await expectNoPageOverflow(page);
  await page.screenshot({path:testInfo.outputPath(`pipe-table-${testInfo.project.name}.png`),animations:"disabled"});
  if(testInfo.project.name==="desktop"){
    await page.setViewportSize({width:1920,height:1080});await expectNoPageOverflow(page);
    await page.screenshot({path:testInfo.outputPath("pipe-table-wide.png"),animations:"disabled"});
    await page.setViewportSize({width:1280,height:900});await page.evaluate(()=>document.documentElement.dataset.theme="dark");await expectNoPageOverflow(page);
    await page.screenshot({path:testInfo.outputPath("pipe-table-dark.png"),animations:"disabled"});
  }
});

test("表の不完全な断片は文字位置を強調し、範囲を推測して表にしない", {tag:"@responsive"}, async ({page},testInfo)=>{
  await search(page);
  const text='途中😀 | 値 |\n| 合成B | 続き |';
  await page.route("**/api/documents/doc-a/reader?**",route=>route.fulfill({json:{data:{revision:"v1",anchor_status:"exact",section_path:[],scope:"paragraph",blocks:[{id:"fragment",kind:"table",text,offset:30,cited:true,highlights:[[2,3]]}],outline:[],previous_cursor:null,next_cursor:null}}}));
  await page.getByTestId("evidence-card").getByRole("button",{name:"文書 A.json の根拠を表示"}).click();
  const reader=page.getByTestId("evidence-reader-content");
  await expect(reader.getByRole("table")).toHaveCount(0);
  await expect(reader.locator("mark")).toHaveText("😀");
  await expect(reader).toContainText(text);
  await expect(reader).toContainText("原文の文字位置を強調");
  await expectNoPageOverflow(page);
  await page.screenshot({path:testInfo.outputPath(`table-fragment-${testInfo.project.name}.png`),animations:"disabled"});
});
