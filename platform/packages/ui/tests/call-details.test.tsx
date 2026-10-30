// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { RunStepDetailsView } from "../src/components/data/run-step-details";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
it("選択した内容だけ表示し、未記録と制限・切り詰めを区別する", () => {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    act(() => root.render(<RunStepDetailsView details={{ fields: [{ label: "モデル", value: "model-a" }], call: {
      input: { status: "recorded", text: "synthetic-input" },
      output: { status: "withheld", text: "private-output" },
      schema: { status: "truncated", text: "synthetic-schema" },
    } }} timing={{ startedAt: "2026-01-01T00:00:00Z", durationMs: 42 }} />));
    const click = (label: string) => act(() => (Array.from(host.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find(el => el.textContent === label))!.click());
    expect(host.textContent).toContain("model-a");
    expect(host.textContent).not.toContain("synthetic-input");
    click("入力"); expect(host.textContent).toContain("synthetic-input");
    click("結果"); expect(host.textContent).toContain("現在の権限"); expect(host.textContent).not.toContain("private-output");
    click("Schema"); expect(host.textContent).toContain("synthetic-schema"); expect(host.textContent).toContain("先頭だけ");
    click("計時"); expect(host.textContent).toContain("42"); expect(host.textContent).toContain("記録されていません");
  } finally { act(() => root.unmount()); host.remove(); }
});
