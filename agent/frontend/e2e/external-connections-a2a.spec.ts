import type { Page } from "@playwright/test";

import { expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";
import { chooseSelectFieldOption } from "./fixtures/select-field";

/**
 * #5932: 外部接続（旧「MCP 接続」）に、外部の Agent を A2A で呼ぶ接続を足す。
 * - ナビと画面の名前は「外部接続」（route・API path は変えない）。
 * - エディタでプロトコル（MCP / A2A）を選ぶ。A2A は endpoint と Agent card の URL・OCI 署名の認証を持つ。
 * - AIDP を配備した構成（API の `presets`）だけ、追加の画面に既定値と、標準の接続 `aidp` が出る。
 * 接続・Agent・URL はすべて合成（実在の環境の値ではない）。
 */

const AIDP_PRESET = {
  id: "aidp_agent",
  protocol: "a2a",
  auth_mode: "oci_signature",
  base_url_prefix: "https://gateway.aidp.region-a.oci.oraclecloud.com/agentendpoint/",
  base_url_example: "https://gateway.aidp.region-a.oci.oraclecloud.com/agentendpoint/<agentId>/a2a",
  timeout_seconds: 120,
};
const AIDP_ENDPOINT = `${AIDP_PRESET.base_url_prefix}agent-a/a2a`;

/** 標準の接続 `aidp`（AI Data Platform の Agent。endpoint は未設定）。 */
function seedAidpConnection(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.mcpConnectionPresets = [AIDP_PRESET];
  mockApi.state.mcpConnections.connections.unshift({
    server_id: "aidp",
    label: "AI Data Platform の Agent",
    protocol: "a2a",
    base_url: null,
    agent_card_url: null,
    auth_mode: "oci_signature",
    service_audience: null,
    timeout_seconds: 120,
    source: "builtin",
    removable: false,
    base_url_locked: false,
    configured: false,
    api_key_configured: false,
    oauth_configured: false,
    session_configured: false,
    service_token_configured: false,
    oci_signature_configured: true,
    trust_tool_annotations: true,
    ...overrides,
  });
}

/** 画面で追加した A2A の接続（外部の Agent A）。 */
function seedAgentConnection(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.mcpConnections.connections.unshift({
    server_id: "agent-a",
    label: "合成の外部 Agent A",
    protocol: "a2a",
    base_url: "https://agent-a.example.test/a2a",
    agent_card_url: null,
    auth_mode: "none",
    service_audience: null,
    timeout_seconds: 30,
    source: "runtime",
    removable: true,
    configured: true,
    api_key_configured: false,
    oauth_configured: false,
    session_configured: false,
    service_token_configured: false,
    oci_signature_configured: false,
    trust_tool_annotations: false,
    ...overrides,
  });
}

function isDesktop(page: Page): boolean {
  return (page.viewportSize()?.width ?? 1280) >= 768;
}

/** 選択欄を開いて選択肢の値（`data-value`）を読み、閉じる。 */
async function selectFieldValues(page: Page, id: string): Promise<string[]> {
  const combobox = page.locator(id);
  await combobox.click();
  const listbox = page.locator(`[id="${await combobox.getAttribute("aria-controls")}"]`);
  const values = await listbox.locator('[role="option"]').evaluateAll((options) =>
    options.map((option) => option.getAttribute("data-value") ?? "")
  );
  await page.keyboard.press("Escape");
  await expect(listbox).toBeHidden();
  return values;
}

test.describe("外部接続（MCP / A2A）", () => {
  test("ナビと画面の名前は「外部接続」で、一覧にプロトコルを出す", { tag: "@responsive" }, async ({ page, mockApi }) => {
    seedAidpConnection(mockApi);
    seedAgentConnection(mockApi);
    await page.goto("/settings/mcp-connections");

    // 名前だけを変える（route は /settings/mcp-connections のまま）。
    await expect(page.getByRole("heading", { name: "外部接続", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "MCP 接続" })).toHaveCount(0);
    if (isDesktop(page)) {
      // 左のナビ（運用設定）の項目の名前も同じ。
      const operations = page.locator("#nav-section-nav-section-operations");
      await expect(operations.locator('a[href="/settings/mcp-connections"]')).toHaveText("外部接続");
      await expect(operations.getByRole("link", { name: "MCP 接続" })).toHaveCount(0);
    }

    const table = page.getByRole("table", { name: "外部接続" });
    await expect(table.getByRole("columnheader", { name: "プロトコル" })).toBeVisible();
    const aidpRow = table.getByRole("row", { name: /AI Data Platform の Agent/ });
    await expect(aidpRow.getByText("A2A", { exact: true })).toBeVisible();
    await expect(aidpRow.getByText("OCI 署名", { exact: true })).toBeVisible();
    await expect(aidpRow.getByText("標準", { exact: true })).toBeVisible();
    await expect(table.getByRole("row", { name: /合成の外部 Agent A/ }).getByText("A2A", { exact: true })).toBeVisible();
    await expect(table.getByRole("row", { name: /ナレッジ検索（RAG）/ }).getByText("MCP", { exact: true })).toBeVisible();
    // 表の横スクロールは表の箱の中に閉じ、ページは横にはみ出さない。
    await expectNoPageHorizontalOverflow(page);
  });

  test(
    "プロトコルを切り替えると項目と認証の選択肢が変わり、A2A の接続を OCI 署名で追加できる",
    { tag: "@responsive" },
    async ({ page, mockApi }) => {
      await page.goto("/settings/mcp-connections?id=new");
      await expect(page.getByRole("heading", { name: "外部接続を追加", level: 1 })).toBeVisible();
      // AIDP を配備していない構成（presets が空）では、既定値の選択を出さない。
      await expect(page.locator("#mcp-server-preset")).toHaveCount(0);

      // 既定は MCP: セッション ID の欄があり、Agent card の欄は無い。OCI 署名は選べない。
      const protocol = page.locator("#mcp-server-protocol");
      await expect(protocol).toHaveAttribute("data-value", "mcp");
      await expect(page.getByLabel("MCP の URL")).toBeVisible();
      await expect(page.locator("#mcp-server-session")).toBeVisible();
      await expect(page.locator("#mcp-server-agent-card-url")).toHaveCount(0);
      expect(await selectFieldValues(page, "#mcp-server-auth-mode")).toEqual([
        "none",
        "api_key",
        "oauth_client_credentials",
        "service_token",
      ]);
      await expect(page.getByRole("switch", { name: "ツールの注釈（readOnlyHint）を信頼する" })).toBeVisible();
      await chooseSelectFieldOption(page.locator("#mcp-server-auth-mode"), "service_token");

      // A2A に切り替える: endpoint と Agent card の URL になり、セッション ID は消える。使えなくなった認証方式
      // （サービストークン）は「なし」に戻り、OCI 署名を選べる。
      await chooseSelectFieldOption(protocol, "a2a");
      await expect(page.getByLabel("Agent の endpoint の URL")).toBeVisible();
      await expect(page.getByLabel("MCP の URL")).toHaveCount(0);
      await expect(page.locator("#mcp-server-agent-card-url")).toBeVisible();
      await expect(page.locator("#mcp-server-session")).toHaveCount(0);
      await expect(page.locator("#mcp-server-auth-mode")).toHaveAttribute("data-value", "none");
      await expect(page.locator("#mcp-server-audience")).toHaveCount(0);
      expect(await selectFieldValues(page, "#mcp-server-auth-mode")).toEqual([
        "none",
        "api_key",
        "oauth_client_credentials",
        "oci_signature",
      ]);
      // 信頼のスイッチは「承認なしで呼ぶ」の意味になり、送信後の失敗では送り直さないことを書く。
      const trust = page.getByTestId("mcp-trust-annotations");
      await expect(trust.getByRole("switch", { name: "この接続の Agent を承認なしで呼ぶ" })).not.toBeChecked();
      await expect(trust).toContainText("送信後の失敗では送り直しません");

      // OCI 署名は接続ごとの資格情報の欄を持たない。
      await chooseSelectFieldOption(page.locator("#mcp-server-auth-mode"), "oci_signature");
      await expect(page.getByTestId("mcp-oci-signature-hint")).toContainText("「システム設定 › OCI 認証」の API キー");
      await expect(page.locator("#mcp-server-api-key")).toHaveCount(0);
      await expect(page.locator("#mcp-server-oauth-secret")).toHaveCount(0);
      await expectNoPageHorizontalOverflow(page);

      // MCP に戻すと OCI 署名は「なし」に戻る（MCP の接続では使えない）。
      await chooseSelectFieldOption(protocol, "mcp");
      await expect(page.locator("#mcp-server-auth-mode")).toHaveAttribute("data-value", "none");
      await chooseSelectFieldOption(protocol, "a2a");
      await chooseSelectFieldOption(page.locator("#mcp-server-auth-mode"), "oci_signature");

      await page.locator("#mcp-server-id").fill("agent-b");
      await page.locator("#mcp-server-label").fill("合成の外部 Agent B");
      await page.locator("#mcp-server-base-url").fill("https://agent-b.example.test/a2a");
      await page.locator("#mcp-server-agent-card-url").fill("https://agent-b.example.test/card.json");
      await page.getByRole("button", { name: "作成" }).click();
      await expect(page.getByText("外部接続を追加しました")).toBeVisible();
      await expect(page).toHaveURL(/\?id=agent-b$/);
      const body = mockApi.lastRequest("POST", "/api/settings/mcp-connections")?.body;
      expect(body).toEqual({
        server_id: "agent-b",
        label: "合成の外部 Agent B",
        protocol: "a2a",
        base_url: "https://agent-b.example.test/a2a",
        agent_card_url: "https://agent-b.example.test/card.json",
        auth_mode: "oci_signature",
        trust_tool_annotations: false,
        timeout_seconds: 10,
      });

      // 保存した接続は、OCI 認証の API キーの準備の状態を出す（値は出さない）。
      const status = page.getByRole("list", { name: "OCI 署名の状態" });
      await expect(status.getByRole("listitem")).toHaveCount(1);
      await expect(status).toContainText("OCI 認証の API キー");
      await expect(status).toContainText("設定済み");
      await expect(page.locator("#mcp-server-agent-card-url")).toHaveValue("https://agent-b.example.test/card.json");
      await expectNoPageHorizontalOverflow(page);
    }
  );

  test("AIDP の既定値を選ぶと、プロトコル・認証・タイムアウトと URL の雛形が入る", { tag: "@responsive" }, async ({ page, mockApi }) => {
    mockApi.state.mcpConnectionPresets = [AIDP_PRESET];
    await page.goto("/settings/mcp-connections?id=new");

    const preset = page.locator("#mcp-server-preset");
    await expect(preset).toHaveAttribute("data-value", "");
    await chooseSelectFieldOption(preset, { label: "AI Data Platform の Agent" });
    await expect(page.locator("#mcp-server-protocol")).toHaveAttribute("data-value", "a2a");
    await expect(page.locator("#mcp-server-auth-mode")).toHaveAttribute("data-value", "oci_signature");
    await expect(page.locator("#mcp-server-timeout")).toHaveValue("120");
    const url = page.locator("#mcp-server-base-url");
    await expect(url).toHaveValue(AIDP_PRESET.base_url_prefix);
    // 入力する URL の形は欄の補足に出す（placeholder に入れない）。
    await expect(url).toHaveAccessibleDescription(new RegExp("agentendpoint/<agentId>/a2a"));
    await expectNoPageHorizontalOverflow(page);

    // 「指定なし」に戻すと、既定値が入れた項目を初期の値へ戻す。
    await chooseSelectFieldOption(preset, { label: "指定なし" });
    await expect(page.locator("#mcp-server-protocol")).toHaveAttribute("data-value", "mcp");
    await expect(page.locator("#mcp-server-auth-mode")).toHaveAttribute("data-value", "none");
    await expect(page.locator("#mcp-server-timeout")).toHaveValue("10");
    await expect(url).toHaveValue("");

    // 既定値から追加する（接続 ID は管理者が決める）。
    await chooseSelectFieldOption(preset, "aidp_agent");
    await page.locator("#mcp-server-id").fill("agent-c");
    await url.fill(AIDP_ENDPOINT);
    await page.getByRole("button", { name: "作成" }).click();
    await expect(page.getByText("外部接続を追加しました")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/settings/mcp-connections")?.body).toEqual({
      server_id: "agent-c",
      label: null,
      protocol: "a2a",
      base_url: AIDP_ENDPOINT,
      auth_mode: "oci_signature",
      trust_tool_annotations: false,
      timeout_seconds: 120,
    });
  });

  test(
    "標準の接続（AI Data Platform の Agent）は endpoint の URL とタイムアウトだけを保存し、Agent card を確かめられる",
    { tag: "@responsive" },
    async ({ page, mockApi }) => {
      // OCI 認証の API キーが未設定の構成から始める。
      seedAidpConnection(mockApi, { oci_signature_configured: false });
      await page.goto("/settings/mcp-connections?id=aidp");

      await expect(page.getByRole("heading", { name: "AI Data Platform の Agent", level: 1 })).toBeVisible();
      // 概要は AIDP 用の文で、RAG / NL2SQL 用の文・配備の環境変数の名前を出さない。
      const banner = page.getByRole("status").filter({ hasText: "標準の接続" });
      await expect(banner).toContainText("Oracle AI Data Platform に配備した Agent を呼ぶ標準の接続です。");
      await expect(page.locator("main")).not.toContainText("AGENT_EXTERNAL_");
      await expect(page.locator("main")).not.toContainText("RAG / NL2SQL");
      // 足りないもの: endpoint の URL と、OCI 認証の API キー。
      const notice = page.getByRole("status").filter({ hasText: "この接続はまだ使えません" });
      await expect(notice).toContainText("Agent の endpoint の URL が未設定です");
      await expect(notice).toContainText("OCI 認証の API キーが未設定か、読み込めません");
      const status = page.getByRole("list", { name: "OCI 署名の状態" });
      await expect(status).toContainText("未設定");

      // 名前・プロトコル・認証・信頼は変えられず、Agent card の URL の欄は無い。
      await expect(page.locator("#mcp-server-label")).toBeDisabled();
      await expect(page.locator("#mcp-server-protocol")).toBeDisabled();
      await expect(page.locator("#mcp-server-protocol")).toHaveAttribute("data-value", "a2a");
      await expect(page.locator("#mcp-server-auth-mode")).toBeDisabled();
      await expect(page.locator("#mcp-server-auth-mode")).toHaveAttribute("data-value", "oci_signature");
      await expect(page.locator("#mcp-server-agent-card-url")).toHaveCount(0);
      await expect(page.locator("#mcp-server-session")).toHaveCount(0);
      const trust = page.getByTestId("mcp-trust-annotations");
      await expect(trust.getByRole("switch")).toBeChecked();
      await expect(trust.getByRole("switch")).toBeDisabled();
      await expect(trust).toContainText("既定で承認なしで呼びます");
      await expect(page.getByTestId("mcp-server-object-actions").getByRole("button")).toHaveCount(0);
      const url = page.locator("#mcp-server-base-url");
      await expect(url).toBeEnabled();
      await expect(url).toHaveAccessibleDescription(new RegExp("gateway\\.aidp\\.region-a.*agentendpoint/<agentId>/a2a"));
      await expect(page.getByRole("button", { name: "ツールを取得" })).toBeDisabled();
      await expectNoPageHorizontalOverflow(page);

      // AIDP の gateway でない host は backend が断る（保存の失敗は確定の行の直上に出す）。
      await url.fill("https://agent-a.example.test/a2a");
      await page.getByRole("button", { name: "保存", exact: true }).click();
      await expect(page.getByTestId("mcp-server-save-error")).toContainText("URL は AI Data Platform の Agent の endpoint");

      // OCI 認証を設定した後、endpoint の URL とタイムアウトを保存する（ほかの項目は送らない）。
      const saved = mockApi.state.mcpConnections.connections.find((connection) => connection.server_id === "aidp");
      if (saved) saved.oci_signature_configured = true;
      await url.fill(AIDP_ENDPOINT);
      await page.locator("#mcp-server-timeout").fill("180");
      await page.getByRole("button", { name: "保存", exact: true }).click();
      await expect(page.getByText("外部接続を保存しました")).toBeVisible();
      expect(mockApi.lastRequest("PATCH", "/api/settings/mcp-connections/aidp")?.body).toEqual({
        timeout_seconds: 180,
        base_url: AIDP_ENDPOINT,
      });
      await expect(notice).toHaveCount(0);
      await expect(status).toContainText("設定済み");

      // 「ツールを取得」は Agent card を読み、名前・説明・プロトコルの版と、メッセージを送る 1 つのツールを出す。
      await page.getByRole("button", { name: "ツールを取得" }).click();
      const result = page.getByTestId("mcp-tools-result");
      await expect(result).toHaveAttribute("data-tone", "success");
      await expect(result).toContainText("AI Data Platform の Agent の Agent card を取得しました。");
      const card = page.getByTestId("a2a-agent-card");
      await expect(card).toContainText("合成の外部 Agent");
      await expect(card).toContainText("合成の説明: 依頼の文を受け取り、結果の文を返す。");
      await expect(card).toContainText("0.3.0");
      const tools = isDesktop(page)
        ? page.getByRole("table", { name: "ツール" })
        : page.getByRole("region", { name: /^ツール/ });
      await expect(tools.getByText("send_message", { exact: true })).toBeVisible();
      await expect(tools.getByText("aidp__send_message")).toBeVisible();
      // 標準の接続の Agent は既定で承認なし（信頼した接続）。
      await expect(tools.getByText("承認なし（信頼した接続）")).toBeVisible();
      await expectNoPageHorizontalOverflow(page);
    }
  );

  test("OCI 署名の接続の 404 は、要る権限の案内を 1 つ目の確認ポイントに出す", { tag: "@responsive" }, async ({ page, mockApi }) => {
    seedAidpConnection(mockApi, { base_url: AIDP_ENDPOINT, configured: true });
    // backend の文（`a2a.OCI_SIGNATURE_NOT_FOUND_HINT` を含む失敗）。
    const message =
      "外部の Agent「AI Data Platform の Agent」が HTTP 404 を返しました。Agent が見つからないか、権限がありません" +
      "（この 2 つは同じ応答で、区別できません）。endpoint の URL が正しいか、Agent が配備されているかと、" +
      "署名に使う OCI ユーザーの、IAM の use ai-data-platforms の権限と、AI Data Platform の中のその Agent の USE 権限を確認してください。";
    await page.route("**/api/settings/mcp-connections/aidp/tools", (route) =>
      route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({ detail: { code: "a2a.http_error", message, details: { status_code: 404 } } }),
      })
    );
    await page.goto("/settings/mcp-connections?id=aidp");
    await page.getByRole("button", { name: "ツールを取得" }).click();

    const result = page.getByTestId("mcp-tools-result");
    await expect(result).toHaveAttribute("data-tone", "danger");
    await expect(result).toContainText("AI Data Platform の Agent からツールを取得できませんでした。");
    await expect(result).toContainText("use ai-data-platforms");
    await expect(result).toContainText("USE 権限");
    // 確認ポイントは A2A 用（MCP のエンドポイントの例を出さない）。
    await expect(result).toContainText("URL が A2A の endpoint を指しているか");
    await expect(result).not.toContainText("/api/mcp");
    await expect(page.getByTestId("a2a-agent-card")).toHaveCount(0);
    await expectNoPageHorizontalOverflow(page);
  });

  test("信頼にした A2A の接続は、確認の後に承認なしで呼ぶ設定を送る (desktop)", async ({ page, mockApi }) => {
    seedAgentConnection(mockApi);
    await page.goto("/settings/mcp-connections?id=agent-a");

    const trust = page.getByTestId("mcp-trust-annotations").getByRole("switch", { name: "この接続の Agent を承認なしで呼ぶ" });
    await expect(trust).not.toBeChecked();
    await trust.click();
    const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await expect(dialog).toContainText("この接続の Agent を承認なしで呼びますか？");
    await expect(dialog).toContainText("合成の外部 Agent A の Agent は、ツール権限で指定しない限り承認なしで呼ばれます。");
    await dialog.getByRole("button", { name: "承認なしで呼ぶ" }).click();
    await expect(trust).toBeChecked();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("外部接続を保存しました")).toBeVisible();
    // 変えていない URL は送らない。プロトコルと信頼を送る。
    expect(mockApi.lastRequest("PATCH", "/api/settings/mcp-connections/agent-a")?.body).toEqual({
      label: "合成の外部 Agent A",
      protocol: "a2a",
      auth_mode: "none",
      trust_tool_annotations: true,
      timeout_seconds: 30,
    });

    // ツール権限の画面は、既定の判定の理由を「信頼した接続の外部の Agent」と出す。
    await page.goto("/settings/tool-policy");
    const row = page.getByRole("table", { name: "ツール権限" }).getByRole("row", { name: /agent-a__send_message/ });
    await expect(row).toContainText("信頼した接続の外部の Agent");
  });
});
