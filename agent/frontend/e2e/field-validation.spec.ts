import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

// #540 / #541: 入力のエラーは欄の直下（FieldError）に出し、送信時は最初のエラーの欄へフォーカスする。
// 押せないボタンだけで済ませず、空の数値を 0 として送らない。文言は「〇〇を入力してください。」などの型。

async function expectFieldError(page: Page, fieldId: string, message: string) {
  const field = page.locator(`#${fieldId}`);
  await expect(field).toHaveAttribute("aria-invalid", "true");
  // エラーは aria-describedby で結んだ要素（共有の TextField / TextareaField は `<id>-<生成 id>-error`。#584 / #631）。
  await expect(field).toHaveAttribute("aria-describedby", new RegExp(`${fieldId}-\\S*error`));
  const describedBy = (await field.getAttribute("aria-describedby")) ?? "";
  const errorId = describedBy.split(/\s+/).find((id) => id.startsWith(`${fieldId}-`) && id.endsWith("-error"));
  const error = page.locator(`[id="${errorId}"]`);
  await expect(error).toHaveText(message);
  await expect(error).toHaveAttribute("role", "alert");
}

async function expectNoHorizontalOverflow(page: Page) {
  const hasNoOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
  );
  expect(hasNoOverflow).toBe(true);
}

function patchCount(mockApi: { requests: Array<{ method: string; path: string }> }, suffix: string, method = "PATCH") {
  return mockApi.requests.filter((request) => request.method === method && request.path.endsWith(suffix)).length;
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test.describe(`欄のエラー (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("Run のゴールは必須で、未入力は欄の下に出してフォーカスする", async ({ page, mockApi }) => {
      await page.goto("/runs");
      const goal = page.locator("#run-goal");
      await expect(goal).toHaveAttribute("aria-required", "true");
      await expect(page.locator('label[for="run-goal"]')).toContainText("必須");
      await goal.fill("   ");
      await page.getByRole("button", { name: "実行を作成" }).click();

      await expectFieldError(page, "run-goal", "ゴールを入力してください。");
      await expect(goal).toBeFocused();
      expect(patchCount(mockApi, "/api/runs", "POST")).toBe(0);
      await expectNoHorizontalOverflow(page);
    });

    test("Runtime Safety は空を 0 として保存せず、欄の下に理由を出す", async ({ page, mockApi }) => {
      await page.goto("/settings/runtime-safety");
      await page.getByLabel("Run あたり最大ツール呼び出し").fill("");
      await page.getByLabel("Run あたり最大承認待ち").fill("-1");
      await page.getByRole("button", { name: "保存" }).click();

      await expectFieldError(page, "runtime-safety-max-tool-calls", "Run あたり最大ツール呼び出しを入力してください。");
      await expectFieldError(
        page,
        "runtime-safety-max-pending-approvals",
        "Run あたり最大承認待ちは 0 以上の整数を入力してください。"
      );
      await expect(page.locator("#runtime-safety-max-tool-calls")).toBeFocused();
      expect(patchCount(mockApi, "/settings/runtime-safety")).toBe(0);

      // 0 は「許可しない」という正当な値として保存できる。
      await page.getByLabel("Run あたり最大ツール呼び出し").fill("0");
      await expect(page.locator("#runtime-safety-max-tool-calls")).not.toHaveAttribute("aria-invalid", "true");
      await page.getByLabel("Run あたり最大承認待ち").fill("0");
      await page.getByRole("button", { name: "保存" }).click();
      await expect(page.getByText("設定を保存しました")).toBeVisible();
      expect(patchCount(mockApi, "/settings/runtime-safety")).toBe(1);
      await expectNoHorizontalOverflow(page);
    });

    test("Command Policy は欄ごとにエラーを出し、最初のエラーの欄へフォーカスする", async ({ page, mockApi }) => {
      await page.goto("/settings/command-policy");
      await page.getByLabel("Workspace root").fill("");
      await page.getByLabel("既定タイムアウト秒").fill("10");
      await page.getByLabel("最大タイムアウト秒").fill("5");
      await page.getByLabel("出力上限 bytes").fill("");
      await page.getByLabel("Artifact storage path").fill(" ");
      await page.getByRole("button", { name: "保存", exact: true }).click();

      await expectFieldError(page, "command-policy-workspace-root", "Workspace root を入力してください。");
      await expectFieldError(page, "command-policy-output-limit", "出力上限 bytes を入力してください。");
      await expectFieldError(
        page,
        "command-policy-default-timeout",
        "既定タイムアウト秒は最大タイムアウト秒以下の数値を入力してください。"
      );
      await expectFieldError(page, "command-policy-artifact-path", "Artifact storage path を入力してください。");
      await expect(page.locator("#command-policy-workspace-root")).toBeFocused();
      // 欄のエラーを Banner に重ねて出さない。
      await expect(page.getByRole("alert").filter({ hasText: "正の数値" })).toHaveCount(0);
      expect(patchCount(mockApi, "/settings/command-policy")).toBe(0);
      await expectNoHorizontalOverflow(page);
    });

    test("外部 MCP のタイムアウトは必須で、空を 0 として保存しない", async ({ page, mockApi }) => {
      await page.goto("/settings/external-mcp?id=new");
      await page.locator("#mcp-server-id").fill("crm");
      await page.locator("#mcp-server-timeout").fill("");
      await page.getByRole("button", { name: "作成" }).click();

      await expectFieldError(page, "mcp-server-timeout", "タイムアウト秒を入力してください。");
      await expect(page.locator("#mcp-server-timeout")).toBeFocused();
      await page.locator("#mcp-server-timeout").fill("601");
      await page.getByRole("button", { name: "作成" }).click();
      await expectFieldError(page, "mcp-server-timeout", "タイムアウト秒は 0 より大きく 600 以下の数値を入力してください。");
      expect(patchCount(mockApi, "/settings/external-mcp-servers", "POST")).toBe(0);
      await expectNoHorizontalOverflow(page);
    });

    test("外部 NL2SQL の数値は欄の下に理由を出す", async ({ page, mockApi }) => {
      await page.goto("/settings/external-nl2sql");
      await page.getByLabel("タイムアウト秒").fill("0");
      await page.getByLabel("既定取得件数").fill("");
      await page.getByRole("button", { name: "保存" }).click();

      await expectFieldError(page, "nl2sql-timeout", "タイムアウト秒は 0 より大きく 600 以下の数値を入力してください。");
      await expectFieldError(page, "nl2sql-default-limit", "既定取得件数を入力してください。");
      await expect(page.locator("#nl2sql-timeout")).toBeFocused();
      expect(patchCount(mockApi, "/settings/external-nl2sql")).toBe(0);
    });

    test("Skill の JSON の形式エラーは欄の下に出す", async ({ page, mockApi }) => {
      await page.goto("/skills?id=new");
      await page.locator("#skill-id").fill("e2e_json");
      await page.locator("#skill-name").fill("JSON 確認");
      await page.locator("#skill-mcp-requirements").fill("{");
      await page.locator("#skill-resource-ids").fill("{}");
      await page.getByRole("button", { name: "作成" }).click();

      await expectFieldError(page, "skill-mcp-requirements", "MCP 依存 (JSON) は有効な JSON で入力してください。");
      await expectFieldError(page, "skill-resource-ids", "Resource ID (JSON) は JSON の配列で入力してください。");
      await expect(page.locator("#skill-mcp-requirements")).toBeFocused();
      expect(patchCount(mockApi, "/api/skills", "POST")).toBe(0);
    });
  });
}
