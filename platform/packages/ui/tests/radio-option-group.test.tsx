// @vitest-environment happy-dom
import { Settings2 } from "lucide-react";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { RadioOptionGroup, RadioOptionGroupSkeleton, type RadioOption } from "../src";

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

  it("名前の左の印・名前の横のバッジ・行の右の操作を置き、読み上げの名前は名前だけにする（#5343）", () => {
    let opened = 0;
    function Rich() {
      const [value, setValue] = useState<Mode>("first");
      return (
        <RadioOptionGroup<Mode>
          id="rich"
          legend="方法"
          options={[
            {
              value: "first",
              label: "選択肢 A",
              description: "A の説明",
              icon: Settings2,
              badges: <span>既定</span>,
              trailing: (
                <button type="button" onClick={() => (opened += 1)}>
                  設定
                </button>
              ),
              inputData: { "data-availability": "ready", "data-testid": "rich-first" },
            },
            { value: "second", label: "選択肢 B" },
          ]}
          value={value}
          onValueChange={(next) => {
            changes.push(next);
            setValue(next);
          }}
        />
      );
    }
    act(() => root.render(<Rich />));
    const [first, second] = radios();
    expect(first.dataset.availability).toBe("ready");
    expect(first.dataset.testid).toBe("rich-first");
    // 名前は名前だけ。バッジと説明は説明として結ぶ（バッジが先）。
    expect(host.querySelector(`#${first.getAttribute("aria-labelledby")}`)?.textContent).toBe("選択肢 A");
    const described = (first.getAttribute("aria-describedby") ?? "").split(" ");
    expect(described.map((id) => host.querySelector(`#${id}`)?.textContent)).toEqual(["既定", "A の説明"]);
    expect(second.hasAttribute("aria-describedby")).toBe(false);
    // 印は label の中（読み上げない）。
    const label = host.querySelector<HTMLLabelElement>('label[for="rich-first"]');
    expect(label?.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    // 行の右の操作は label の外にあり、押しても選ばない。
    const button = host.querySelector("button");
    expect(label?.contains(button)).toBe(false);
    expect(button?.closest("[data-option-value]")?.getAttribute("data-option-value")).toBe("first");
    act(() => radios()[1].click());
    act(() => button?.click());
    expect(opened).toBe(1);
    expect(changes).toEqual(["second"]);
    expect(radios().map((radio) => radio.checked)).toEqual([false, true]);
  });

  it("選択肢だけの無効は、その選択肢だけを選べなくする（#5343）", () => {
    act(() =>
      root.render(
        <RadioOptionGroup<Mode>
          id="partial"
          legend="方法"
          options={[OPTIONS[0], { ...OPTIONS[1], disabled: true }]}
          value="first"
          onValueChange={(next) => changes.push(next)}
        />
      )
    );
    expect(radios().map((radio) => radio.disabled)).toEqual([false, true]);
    act(() => radios()[1].click());
    expect(changes).toEqual([]);
  });

  it("読み込み中の形は見出しと補足をそのまま出し、選択肢の数だけ場所を取って読み上げから外す（#5343）", () => {
    act(() => root.render(<RadioOptionGroupSkeleton legend="方法" helper="補足" count={2} />));
    const wrapper = host.firstElementChild;
    expect(wrapper?.getAttribute("aria-hidden")).toBe("true");
    expect(host.querySelector("legend")?.textContent).toBe("方法");
    expect(host.textContent).toContain("補足");
    expect(radios()).toHaveLength(0);
    expect(host.querySelectorAll(".animate-pulse")).toHaveLength(2);
  });
});
