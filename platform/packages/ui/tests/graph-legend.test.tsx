// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { GraphLegend } from "../src/graph";

// #5520: 凡例は画布に重ねる（既定）か、画布の外の行に置く。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const items = [{ id: "a", label: "種類 A" }, { id: "b", label: "種類 B" }];

describe("GraphLegend", () => {
  it("overlays the canvas by default without a visible heading", () => {
    act(() => root.render(<GraphLegend label="表示する種類" items={items} disabledIds={new Set()} onToggle={() => {}} testId="legend" />));
    const legend = host.querySelector('[data-testid="legend"]')!;
    expect(legend.className).toContain("absolute");
    expect(legend.getAttribute("aria-label")).toBe("表示する種類");
    expect(legend.textContent).not.toContain("表示する種類");
  });

  it("places an inline legend in the flow with a visible heading", () => {
    act(() => root.render(<GraphLegend placement="inline" label="表示する種類" items={items} disabledIds={new Set(["b"])} onToggle={() => {}} testId="legend" />));
    const legend = host.querySelector('[data-testid="legend"]')!;
    expect(legend.className).not.toContain("absolute");
    expect(legend.textContent).toContain("表示する種類");
    expect(host.querySelector('[data-testid="legend-b"]')!.getAttribute("aria-pressed")).toBe("false");
  });
});
