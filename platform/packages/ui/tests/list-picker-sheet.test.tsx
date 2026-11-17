// @vitest-environment happy-dom
import { act, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FormStatus, ListPickerSheet, type ListPickerItem } from "../src";

// #5279: 一覧から選んで追加するシート（右の SideSheet md・下部に「N 件を追加 / キャンセル」）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // タッチ端末として描く（閉じるボタンの Tooltip を出さない）。
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const items: ListPickerItem[] = ["doc-1", "doc-2", "doc-3"].map((name) => ({ key: name, label: name, textValue: name }));

function Harness({
  onSubmit = () => {},
  submitting = false,
  status,
}: {
  onSubmit?: (keys: string[]) => void;
  submitting?: boolean;
  status?: string;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const openerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={openerRef}
        type="button"
        data-testid="opener"
        aria-haspopup="dialog"
        onClick={() => {
          setSelected(new Set());
          setOpen(true);
        }}
      >
        文書を追加
      </button>
      <button type="button" data-testid="other">
        ほかの操作
      </button>
      <ListPickerSheet
        open={open}
        onClose={() => setOpen(false)}
        id="add-documents"
        title="文書を追加"
        closeLabel="閉じる"
        label="追加する文書の候補"
        items={items}
        selectedKeys={selected}
        selectedItems={items.filter((item) => selected.has(item.key))}
        onToggle={(item, next) =>
          setSelected((current) => {
            const copy = new Set(current);
            if (next) copy.add(item.key);
            else copy.delete(item.key);
            return copy;
          })
        }
        search={{ label: "追加する文書を検索", placeholder: "文書名で検索", value: "", onSearch: () => {} }}
        submitLabel={`${selected.size} 件を追加`}
        onSubmit={() => onSubmit([...selected])}
        submitting={submitting}
        cancelLabel="キャンセル"
        actionsLabel="文書の追加"
        status={<FormStatus tone="danger" message={status} />}
        returnFocusRef={openerRef}
        testId="add-documents"
      />
    </>
  );
}

const $ = <T extends Element = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const sheet = () => $<HTMLElement>('[data-testid="add-documents"]');
const opener = () => $<HTMLButtonElement>('[data-testid="opener"]');
const submit = () => $<HTMLButtonElement>('[data-testid="add-documents-submit"]');
const cancel = () => $<HTMLButtonElement>('[data-testid="add-documents-cancel"]');
const option = (name: string) =>
  Array.from(sheet().querySelectorAll<HTMLElement>('[role="option"]')).find((node) => node.textContent?.includes(name))!;
const click = (target: Element) =>
  act(() => {
    (target as HTMLElement).click();
  });
const key = (target: Element, init: KeyboardEventInit) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));
  });

describe("ListPickerSheet（#5279）", () => {
  it("右から出す md のシート（sm 未満は全画面）で、見出しはタイトルだけ。開くと検索欄へフォーカスし、見える label は出さない", () => {
    act(() => root.render(<Harness />));
    expect(sheet().getAttribute("role")).toBe("dialog");
    expect(sheet().hasAttribute("inert")).toBe(true);
    const classes = sheet().className.split(/\s+/);
    expect(classes).toEqual(expect.arrayContaining(["right-0", "w-full", "sm:w-[40rem]"]));
    expect(sheet().id).toBe("add-documents");

    opener().focus();
    click(opener());
    expect(sheet().getAttribute("data-state")).toBe("open");
    const headings = Array.from(sheet().querySelectorAll("h1, h2, h3, h4")).map((node) => node.textContent);
    expect(headings).toEqual(["文書を追加"]);
    const search = sheet().querySelector<HTMLInputElement>('input[type="search"]')!;
    expect(document.activeElement).toBe(search);
    expect(search.placeholder).toBe("文書名で検索");
    const label = sheet().querySelector<HTMLLabelElement>(`label[for="${search.id}"]`)!;
    expect(label.textContent).toBe("追加する文書を検索");
    expect(label.className).toContain("sr-only");
    // 一覧はシートの面に直に置く（ListPicker のカードの枠を描かない）。
    expect($('[data-testid="add-documents-list"]').className.split(/\s+/)).toContain("border-0");
  });

  it("選ぶまで確定は押せず、選ぶと onSubmit を呼ぶ。キャンセル・Escape で閉じ、開くボタンへフォーカスを戻す", () => {
    const onSubmit = vi.fn();
    act(() => root.render(<Harness onSubmit={onSubmit} />));
    click(opener());
    expect(submit().disabled).toBe(true);
    expect(submit().textContent).toBe("0 件を追加");
    click(option("doc-2"));
    expect(submit().disabled).toBe(false);
    expect(submit().textContent).toBe("1 件を追加");
    click(submit());
    expect(onSubmit).toHaveBeenCalledWith(["doc-2"]);
    // 確定の行は本文の外（シートの下端）にあり、名前を持つ group。
    const footer = $('[data-testid="add-documents-footer"]');
    expect(sheet().lastElementChild).toBe(footer);
    expect(footer.querySelector('[role="group"]')?.getAttribute("aria-label")).toBe("文書の追加");

    $<HTMLButtonElement>('[data-testid="other"]').focus();
    click(cancel());
    expect(sheet().hasAttribute("inert")).toBe(true);
    expect(document.activeElement).toBe(opener());

    click(opener());
    key(document.activeElement!, { key: "Escape" });
    expect(sheet().hasAttribute("inert")).toBe(true);
    expect(document.activeElement).toBe(opener());
  });

  it("確定の結果は確定の操作の直上（シートの中）に出す", () => {
    act(() => root.render(<Harness status="3 件のうち 1 件を追加しました。" />));
    click(opener());
    const footer = $('[data-testid="add-documents-footer"]');
    const alert = footer.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain("3 件のうち 1 件を追加しました。");
    expect(alert.compareDocumentPosition(footer.querySelector('[role="group"]')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("確定の処理中は閉じない（×・Escape・キャンセル）", () => {
    act(() => root.render(<Harness submitting />));
    click(opener());
    expect(submit().getAttribute("aria-busy")).toBe("true");
    expect(cancel().disabled).toBe(true);
    key(sheet(), { key: "Escape" });
    expect(sheet().hasAttribute("inert")).toBe(false);
    click(sheet().querySelector('button[aria-label="閉じる"]')!);
    expect(sheet().hasAttribute("inert")).toBe(false);
  });

  it("開き直すと一覧の中の状態（選択中だけ表示）を作り直す", () => {
    act(() => root.render(<Harness />));
    click(opener());
    click(option("doc-1"));
    const showSelected = () => $<HTMLButtonElement>('[data-testid="add-documents-list-show-selected"]');
    click(showSelected());
    expect(showSelected().getAttribute("aria-pressed")).toBe("true");
    click(cancel());
    click(opener());
    expect(showSelected().getAttribute("aria-pressed")).toBe("false");
  });
});
