// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RadioOptionGroup, type RadioOption } from "../src";

// #5340: 名前と 1 行の説明を持つラジオの群。Fieldset role="radiogroup" ＋ ネイティブのラジオ。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Mode = "first" | "second";

const OPTIONS: RadioOption<Mode>[] = [
  { value: "first", label: "選択肢 A", description: "A の説明" },
  { value: "second", label: "選択肢 B", description: "B の説明" },
];

let host: HTMLDivElement;
let root: Root;
let changes: Mode[];

function Harness({ disabled = false }: { disabled?: boolean }) {
  const [value, setValue] = useState<Mode>("first");
  return (
    <RadioOptionGroup<Mode>
      id="mode"
      legend="方法"
      helper="補足"
      options={OPTIONS}
      value={value}
      disabled={disabled}
      onValueChange={(next) => {
        changes.push(next);
        setValue(next);
      }}
    />
  );
}

function radios(): HTMLInputElement[] {
  return [...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
}

beforeEach(() => {
  changes = [];
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("RadioOptionGroup（#5340）", () => {
  it("fieldset を radiogroup にし、legend・補足を結ぶ", () => {
    act(() => root.render(<Harness />));
    const group = host.querySelector("fieldset");
    expect(group?.getAttribute("role")).toBe("radiogroup");
    expect(group?.querySelector("legend")?.textContent).toBe("方法");
    expect(group?.getAttribute("aria-describedby")).toBe("mode-hint");
  });

  it("選択肢はネイティブのラジオで、名前と説明を aria で結ぶ", () => {
    act(() => root.render(<Harness />));
    const [first, second] = radios();
    expect(first.name).toBe(second.name);
    expect(first.checked).toBe(true);
    expect(host.querySelector(`#${first.getAttribute("aria-labelledby")}`)?.textContent).toBe(
      "選択肢 A"
    );
    expect(host.querySelector(`#${first.getAttribute("aria-describedby")}`)?.textContent).toBe(
      "A の説明"
    );
  });

  it("選ぶと値を返し、選んだ行だけが checked になる", () => {
    act(() => root.render(<Harness />));
    act(() => radios()[1].click());
    expect(changes).toEqual(["second"]);
    expect(radios().map((radio) => radio.checked)).toEqual([false, true]);
  });

  it("無効の間は全部のラジオを disabled にし、選んだ内容は残す", () => {
    act(() => root.render(<Harness disabled />));
    expect(radios().every((radio) => radio.disabled)).toBe(true);
    expect(radios()[0].checked).toBe(true);
  });
});
