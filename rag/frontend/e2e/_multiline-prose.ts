import { expect, type Locator } from "./fixtures/test";

/** 長文の折り返し・縦スクロール・Enter の保存前編集を共通で確かめる。 */
export async function editThreeLineProse(field: Locator): Promise<string> {
  await expect(field).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(field).toHaveAttribute("rows", "3");
  await expect(field).toHaveCSS("resize", "vertical");
  const originalHeight = await field.evaluate((element) => element.clientHeight);
  const text = Array.from({ length: 5 }, () => "合成の説明を確かめる文章です。".repeat(2)).join("\n");
  await field.fill(text);
  await field.press("End");
  await field.press("Enter");
  await field.pressSequentially("合成の末尾");
  const expected = `${text}\n合成の末尾`;
  await expect(field).toHaveValue(expected);
  expect(await field.evaluate((element) => element.clientHeight)).toBe(originalHeight);
  expect(await field.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  expect(await field.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await field.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  expect(await field.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  return expected;
}
