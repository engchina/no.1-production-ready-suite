/** 3 製品の実画面で使う、機密を含まない並列呼び出しの合成記録。 */
export const syntheticTraceSummary = {
  schema_version: 1, operation_id: "synthetic-a", root_span_id: "1".repeat(16), telemetry_trace_id: "a".repeat(32), completeness: "complete", truncated: false, sampled: true,
  spans: [
    { span_id: "1".repeat(16), parent_span_id: null, operation_name: "synthetic.execute" },
    { span_id: "2".repeat(16), parent_span_id: "1".repeat(16), operation_name: "gen_ai.chat" },
    { span_id: "3".repeat(16), parent_span_id: "1".repeat(16), operation_name: "gen_ai.execute_tool" },
    { span_id: "4".repeat(16), parent_span_id: "2".repeat(16), operation_name: "synthetic.child" },
    { span_id: "5".repeat(16), parent_span_id: "4".repeat(16), operation_name: "synthetic.child" },
    { span_id: "6".repeat(16), parent_span_id: "5".repeat(16), operation_name: "synthetic.child" },
    { span_id: "7".repeat(16), parent_span_id: "6".repeat(16), operation_name: "synthetic.child" },
  ].map(span => ({ ...span, trace_id: "a".repeat(32), kind: "internal", status: "done", started_at: "2026-01-01T00:00:00.000Z", finished_at: "2026-01-01T00:00:02.000Z", duration_ms: 2000, timing_source: "measured", attributes: { "gen_ai.request.model": span.operation_name === "gen_ai.chat" ? `synthetic_model_${"segment_".repeat(24)}` : "model-a", "gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 0 } })),
};

export async function captureTraceLayouts(
  page: {
    keyboard: { press(key: string): Promise<void> };
    getByTestId(id: string): { focus(): Promise<void>; isVisible(): Promise<boolean>; getByText(text: string, options: { exact: boolean }): { click(): Promise<void> } };
    viewportSize(): { width: number; height: number } | null;
    setViewportSize(size: { width: number; height: number }): Promise<void>;
    evaluate(fn: () => unknown): Promise<unknown>;
    evaluate(fn: (value: string) => unknown, value: string): Promise<unknown>;
    screenshot(options: { path: string; fullPage: boolean }): Promise<unknown>;
  },
  testInfo: { outputPath(name: string): string },
  name: string,
) {
  await page.getByTestId("trace-details-toggle").focus();
  await page.keyboard.press("Space");
  if (await page.getByTestId("trace-details").isVisible()) throw new Error("Space で詳細を閉じられません。");
  await page.keyboard.press("Enter");
  if (!(await page.getByTestId("trace-details").isVisible())) throw new Error("Enter で詳細を開けません。");
  await page.keyboard.press("Tab");
  if (!(await page.evaluate(() => document.activeElement?.closest('[data-testid="trace-details"]') != null))) throw new Error("Tab で詳細の操作へ移れません。");
  await page.getByTestId("trace-details").getByText("gen_ai.chat", { exact: true }).click();
  const mobile = (page.viewportSize()?.width ?? 1280) <= 375;
  for (const width of mobile ? [375] : [1280, 1920]) {
    await page.setViewportSize({ width, height: mobile ? 812 : 900 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(value => {
        document.documentElement.classList.remove("light", "dark");
        document.documentElement.setAttribute("data-theme", value);
      }, theme);
      await page.evaluate(() => document.fonts.ready);
      if (!(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))) throw new Error("ページに横方向のはみ出しがあります。");
      await page.screenshot({ path: testInfo.outputPath(`${name}-${width}-${theme}.png`), fullPage: true });
    }
  }
}
