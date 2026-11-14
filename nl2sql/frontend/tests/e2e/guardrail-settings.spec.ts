import {expect,test} from "./_helpers/test";
import {mockDatabaseGateReady} from "./_helpers/database-gate";
test.beforeEach(async({page})=>{await mockDatabaseGateReady(page);});

// 安全チェックは 3 製品で共通の設定（platform の共通画面。#5063）。
test("安全チェックの入力と出力の検査を独立に保存する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  let state={input_enabled:false,output_enabled:false};
  const saved:unknown[]=[];
  await page.route("**/api/settings/guardrail",async route=>{
    if(route.request().method()==="PATCH") { state=route.request().postDataJSON();saved.push(state); }
    await route.fulfill({json:{data:{...state,oci_configured:true,oci_warning_code:null},error_messages:[],warning_messages:[]}});
  });
  await page.goto("/settings/guardrail");
  await expect(page.getByRole("heading",{name:"安全チェック",level:1})).toBeVisible();
  const input=page.getByRole("checkbox",{name:"入力を検査する",exact:true});
  const output=page.getByRole("checkbox",{name:"出力を検査する",exact:true});
  await expect(input).not.toBeChecked();await expect(output).not.toBeChecked();
  await input.check();
  await page.getByRole("button",{name:"保存",exact:true}).click();
  await expect.poll(()=>state.input_enabled).toBe(true);
  await page.reload();
  await expect(input).toBeChecked();await expect(output).not.toBeChecked();
  expect(saved).toEqual([{input_enabled:true,output_enabled:false}]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath("guardrail-settings.png"),fullPage:true});
});

test("OCI の compartment が無いまま有効にしようとすると注意を出す", { tag: "@responsive" }, async ({ page }) => {
  await page.route("**/api/settings/guardrail",route=>route.fulfill({json:{data:{input_enabled:false,output_enabled:false,oci_configured:false,oci_warning_code:"oci_guardrails_compartment_missing"},error_messages:[],warning_messages:[]}}));
  await page.goto("/settings/guardrail");
  await page.getByRole("checkbox",{name:"出力を検査する",exact:true}).check();
  await expect(page.getByText(/compartment が未設定です/)).toBeVisible();
  await expect(page.getByRole("link",{name:"OCI 認証設定を開く"})).toBeVisible();
});
