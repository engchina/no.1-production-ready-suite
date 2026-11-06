import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1874: スキルを Agent Skills の形（<name>/SKILL.md と references/ の zip）で書き出す。

function seedRuntimeSkill(mockApi: MockApi) {
  mockApi.state.skills.push({
    id: "Keiri_Steps",
    name: "経理の手順",
    description: "経理の質問の手順",
    instructions: "",
    mcp_requirements: [],
    references: [],
    enabled: true,
    tags: [],
    source: "runtime",
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

test("一覧の行の操作からスキルを zip で書き出す", async ({ page, mockApi }) => {
  seedRuntimeSkill(mockApi);
  let requested = "";
  await page.route("**/api/skills/*/export", async (route) => {
    requested = new URL(route.request().url()).pathname;
    await route.fulfill({
      status: 200,
      contentType: "application/zip",
      headers: { "Content-Disposition": 'attachment; filename="keiri-steps.zip"' },
      body: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18), "binary"),
    });
  });
  await page.goto("/skills");
  await page.getByTestId("skill-row-actions-Keiri_Steps").click();
  const downloading = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "Agent Skills の形で書き出す" }).click();
  const download = await downloading;
  // 名前は backend が決めた Agent Skills の name（フォルダ名）。
  expect(download.suggestedFilename()).toBe("keiri-steps.zip");
  expect(requested).toBe("/api/skills/Keiri_Steps/export");
  await expect(page.getByText("keiri-steps.zip を書き出しました")).toBeVisible();
});

test("書き出しに失敗したら理由を出す", async ({ page, mockApi }) => {
  seedRuntimeSkill(mockApi);
  await page.route("**/api/skills/*/export", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ data: null, error_messages: ["スキルが見つかりません。"], warning_messages: [] }),
    })
  );
  await page.goto("/skills");
  await page.getByTestId("skill-row-actions-Keiri_Steps").click();
  await page.getByRole("menuitem", { name: "Agent Skills の形で書き出す" }).click();
  await expect(page.getByText("スキルを書き出せませんでした。")).toBeVisible();
});

const SYNTHETIC_SKILL_ID = `synthetic-skill-${"identifier".repeat(24)}`;
const SYNTHETIC_DESCRIPTION = `合成の長い説明です。${"説明を省略せず保持します。".repeat(28)} ${"synthetic_identifier_".repeat(20)}`;
const SYNTHETIC_INSTRUCTIONS = Array.from({length:80},(_,i)=>`## 合成段落 ${i+1}\n\n完全な説明 ${i+1}。利用者が最後まで読み、同じ内容を書き出せることを確認します。`).join("\n\n");
for (const theme of ["light","dark"] as const) {
  test(`長いスキル概要と指示を省略せず操作できる ${theme}`,{tag:"@responsive"},async({page,mockApi},testInfo)=>{
    await page.addInitScript(value=>localStorage.setItem("production-ready-agent.ui",JSON.stringify({state:{theme:value,sidebarCollapsed:true},version:0})),theme);
    mockApi.state.skills.push({id:SYNTHETIC_SKILL_ID,name:`合成スキル${"SyntheticName".repeat(14)}`,description:SYNTHETIC_DESCRIPTION,instructions:SYNTHETIC_INSTRUCTIONS,mcp_requirements:[],references:[],enabled:true,tags:[],source:"builtin",created_at:MOCK_NOW,updated_at:MOCK_NOW});
    const zip=Buffer.from("PK\u0005\u0006"+"\u0000".repeat(18),"binary");let requested="";
    await page.route("**/api/skills/*/export",route=>{requested=decodeURIComponent(new URL(route.request().url()).pathname);return route.fulfill({contentType:"application/zip",headers:{"Content-Disposition":'attachment; filename="synthetic-skill.zip"'},body:zip});});
    await page.goto(`/skills?id=${encodeURIComponent(SYNTHETIC_SKILL_ID)}`);
    const button=page.getByTestId("skill-object-actions").getByRole("button",{name:"Agent Skills の形で書き出す",exact:true});await expect(button).toBeVisible();
    const geometry=await page.getByTestId("skill-instructions-view").locator("..").evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,max:getComputedStyle(e).maxHeight}));
    await testInfo.attach("instruction-geometry",{body:JSON.stringify(geometry),contentType:"application/json"});
    await testInfo.attach("export-geometry",{body:JSON.stringify(await button.boundingBox()),contentType:"application/json"});
    await button.scrollIntoViewIfNeeded();
    await page.screenshot({path:testInfo.outputPath(`skill-overview-${theme}.png`),fullPage:true});
    await expect.poll(()=>page.evaluate(()=>({document:document.documentElement.scrollWidth<=innerWidth,main:document.querySelector("main")!.scrollWidth<=document.querySelector("main")!.clientWidth}))).toEqual({document:true,main:true});
    const box=await button.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await button.locator("span").first().evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
    await expect(page.getByText(SYNTHETIC_DESCRIPTION,{exact:true})).toBeVisible();
    const region=page.getByRole("region",{name:"指示",exact:true});await expect(region).toBeVisible();expect(await region.evaluate(e=>e.scrollHeight>e.clientHeight)).toBe(true);
    await region.focus();await expect(region).toBeFocused();await page.keyboard.press("Control+End");
    await expect.poll(()=>region.evaluate(e=>e.scrollTop+e.clientHeight>=e.scrollHeight-2)).toBe(true);
    await expect(region.getByRole("heading",{name:"合成段落 80",exact:true})).toBeInViewport();
    await page.screenshot({path:testInfo.outputPath(`skill-instructions-end-${theme}.png`)});
    await button.focus();await expect(button).toBeFocused();const downloading=page.waitForEvent("download");await page.keyboard.press("Enter");const download=await downloading;
    expect(requested).toBe(`/api/skills/${SYNTHETIC_SKILL_ID}/export`);expect(download.suggestedFilename()).toBe("synthetic-skill.zip");
    const saved=testInfo.outputPath("synthetic-skill.zip");await download.saveAs(saved);const {readFile}=await import("node:fs/promises");expect(await readFile(saved)).toEqual(zip);
    expect(mockApi.state.skills.find(s=>s.id===SYNTHETIC_SKILL_ID)!.instructions).toBe(SYNTHETIC_INSTRUCTIONS);
  });
  test(`短いスキル指示を上限高へ引き伸ばさない ${theme}`,{tag:"@responsive"},async({page,mockApi})=>{
    await page.addInitScript(value=>localStorage.setItem("production-ready-agent.ui",JSON.stringify({state:{theme:value},version:0})),theme);
    mockApi.state.skills.push({id:"synthetic-short",name:"短い合成スキル",description:"短い説明",instructions:"短い合成本文。",mcp_requirements:[],references:[],enabled:true,tags:[],source:"builtin",created_at:MOCK_NOW,updated_at:MOCK_NOW});
    await page.goto("/skills?id=synthetic-short");const region=page.getByTestId("skill-instructions-view").locator("..");await expect(region).toBeVisible();const size=await region.evaluate(e=>({height:e.clientHeight,scroll:e.scrollHeight,root:parseFloat(getComputedStyle(document.documentElement).fontSize)}));expect(size.scroll).toBeLessThanOrEqual(size.height);expect(size.height).toBeLessThan(26.25*size.root);
  });
}
