import { expect, type Page } from "./fixtures/test";
import { expectNoPageOverflow } from "./_helpers";

export async function mockFeaturePrompts(page: Page) {
  const keys = ["external_ocr", "heading_levels", "field_extraction", "navigation_summary", "raptor_summary_system", "raptor_summary"];
  const values = new Map<string, string>();
  const requests: { key: string; method: string }[] = [];
  await page.route("**/api/settings/answer-prompts**", async (route) => {
    const method = route.request().method();
    const key = route.request().url().split("/").at(-1)!;
    if (method === "PUT") values.set(key, route.request().postDataJSON().content);
    if (method === "DELETE") values.delete(key);
    if (method !== "GET") requests.push({ key, method });
    await route.fulfill({ json: { data: { prompts: keys.map((key) => ({
      key, content: values.get(key) ?? "合成の既定指示", default_content: "合成の既定指示",
      customized: values.has(key), required_placeholders: [], updated_at: null,
    })), stages: [] }, error_messages: [], warning_messages: [] } });
  });
  return requests;
}

export async function verifyFeaturePrompt(page: Page, key: string, title: string) {
  const summary = page.locator("summary").filter({ hasText: title });
  await summary.click();
  const disclosure = summary.locator("..");
  const editor = page.locator(`#answer-prompt-${key}`);
  await expect(editor).toBeEditable();
  await expect(editor).toHaveValue("合成の既定指示");
  await editor.fill("破棄する指示");
  await disclosure.getByRole("button", { name: "変更を破棄" }).click();
  await expect(editor).toHaveValue("合成の既定指示");
  await editor.fill("合成の保存指示");
  await disclosure.getByRole("button", { name: "プロンプトを保存" }).click();
  await expect(disclosure.getByRole("button", { name: "プロンプトを保存" })).toBeDisabled();
  await disclosure.getByRole("button", { name: "既定に戻す", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "既定に戻す", exact: true }).click();
  await expect(editor).toHaveValue("合成の既定指示");
  await expectNoPageOverflow(page);
}
