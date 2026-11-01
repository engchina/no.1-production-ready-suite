/** 3製品共通のモデル設定画面で、合成の保存状態だけを使って key の削除を確認する。 */
// platform に Playwright の依存を持たせず、製品から受け取る操作の形だけを宣言する。
interface Locator {
  click(): Promise<void>;
  check(): Promise<void>;
  fill(value: string): Promise<void>;
  isDisabled(): Promise<boolean>;
  isEnabled(): Promise<boolean>;
  count(): Promise<number>;
  getByRole(role: "tab" | "checkbox" | "button", options: { name: string | RegExp; exact?: boolean }): Locator;
}
interface Page {
  route(url: string, handler: (route: {
    request(): { method(): string; postDataJSON(): unknown };
    fulfill(options: { json: unknown }): Promise<void>;
  }) => Promise<void>): Promise<unknown>;
  goto(url: string): Promise<unknown>;
  reload(): Promise<unknown>;
  evaluate<R, A>(fn: (arg: A) => R, arg: A): Promise<R>;
  locator(selector: string): Locator;
  getByRole(role: "tab" | "checkbox" | "button", options: { name: string | RegExp; exact?: boolean }): Locator;
  screenshot(options: { path: string; fullPage: boolean }): Promise<unknown>;
}
interface Assertions {
  (value: unknown): {
    toBe(value: unknown): unknown;
    toEqual(value: unknown): unknown;
  };
  poll(callback: () => unknown | Promise<unknown>): { toBe(value: unknown): Promise<void> };
}

export async function exerciseModelKeyDeletion(
  page: Page,
  testInfo: { outputPath(name: string): string },
  expect: Assertions,
  theme: "light" | "dark",
) {
  const connections = [
    { connection_id: "primary", name: "プライマリ接続", field: "enterprise-api-key" },
    { connection_id: "secondary", name: "セカンダリ接続", field: "enterprise-secondary-api-key" },
    { connection_id: "tertiary", name: "ターシャリ接続", field: "enterprise-tertiary-api-key" },
  ];
  const keys = new Map(connections.map((connection) => [connection.connection_id, true]));
  let savedConnections = connections.map(({ connection_id }) => ({
    connection_id,
    endpoint: `https://${connection_id}.example/v1`,
    project_ocid: connection_id === "tertiary" ? "" : `ocid1.project.${connection_id}`,
    api_key: "",
    has_api_key: true,
    clear_api_key: false,
  }));
  let saves = 0;
  const response = () => ({
    data: {
      settings: {
        enterprise_ai: {
          connections: savedConnections,
          models: [
            { model_id: "synthetic-text", display_name: "合成テキストモデル", vision_enabled: false, connection_id: "primary" },
            { model_id: "synthetic-vision", display_name: "合成画像モデル", vision_enabled: true, connection_id: "secondary" },
          ],
          default_text_model_id: "synthetic-text",
          default_vision_model_id: "synthetic-vision",
          default_judge_model_id: "",
          api_path: "/responses",
          vlm_input_mode: "files_api",
          text_payload_template: "",
          vision_payload_template: "",
          text_response_path: "",
          vision_response_path: "",
          timeout_seconds: 60,
          max_retries: 3,
        },
        generative_ai: { embedding_model: "cohere.embed-v4.0", embedding_dim: 1536, rerank_model: "cohere.rerank-v4.0-fast" },
      },
      model_settings_file: "model-settings.json",
      source: "runtime",
      secret_source: "environment",
      legacy_secret_detected: false,
    },
    error_messages: [],
    warning_messages: [],
  });
  await page.route("**/api/settings/model", async (route) => {
    if (route.request().method() === "PATCH") {
      saves += 1;
      const payload = route.request().postDataJSON() as { enterprise_ai: { connections: typeof savedConnections } };
      savedConnections = payload.enterprise_ai.connections.map((connection: (typeof savedConnections)[number]) => {
        if (connection.clear_api_key) keys.set(connection.connection_id, false);
        else if (connection.api_key) keys.set(connection.connection_id, true);
        return { ...connection, api_key: "", has_api_key: keys.get(connection.connection_id) ?? false, clear_api_key: false };
      });
    }
    await route.fulfill({ json: response() });
  });
  await page.goto("/settings/model");
  await page.evaluate((value) => {
    document.documentElement.dataset.theme = value;
    document.documentElement.classList.remove("light", "dark");
  }, theme);
  for (const [index, connection] of connections.entries()) {
    await page.getByRole("tab", { name: new RegExp(`^${connection.name}`) }).click();
    const field = page.locator(`#${connection.field}`);
    const form = page.locator(`form:has(#${connection.field})`);
    const clear = form.getByRole("checkbox", { name: "保存済み API key を削除する" });
    await clear.check();
    await expect.poll(() => field.isDisabled()).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`key-delete-${connection.connection_id}-${theme}.png`), fullPage: true });
    const before = new Map(keys);
    await form.getByRole("button", { name: "OCI Enterprise AI: 保存", exact: true }).click();
    await expect.poll(() => saves).toBe(index + 1);
    await expect.poll(() => clear.count()).toBe(0);
    await expect.poll(() => field.isEnabled()).toBe(true);
    expect(keys.get(connection.connection_id)).toBe(false);
    for (const [id, present] of before) if (id !== connection.connection_id) expect(keys.get(id)).toBe(present);
  }
  await page.reload();
  await page.getByRole("tab", { name: /^プライマリ接続/ }).click();
  const endpoint = page.locator("#enterprise-endpoint");
  const form = page.locator("form:has(#enterprise-endpoint)");
  await endpoint.fill("https://primary.example/v2");
  await form.getByRole("button", { name: "OCI Enterprise AI: 保存", exact: true }).click();
  await expect.poll(() => saves).toBe(4);
  expect([...keys.values()]).toEqual([false, false, false]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth, undefined)).toBe(true);
}
