// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ListDetailLayout, adjacentListItems } from "../src/components/app-shell/list-detail-layout";
import { RowTitleButton } from "../src/components/data/row-title-button";
import {
  LIST_DETAIL_DEFAULT_LIST_WIDTH_REM,
  clampListWidthRem,
  listDetailStorageKey,
  maxListWidthForContainer,
  nextListItemIndex,
  parseListDetailStoredState,
  serializeListDetailStoredState,
} from "../src/lib/list-detail";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let wide = true;
const listeners = new Set<() => void>();

function stubViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      // xl（分割）の判定だけを切り替える。Tooltip などのほかの問い合わせはタッチ端末（Tooltip を出さない）にする。
      return query.includes("1280px") ? wide : query.includes("pointer: coarse") || query.includes("hover: none");
    },
    media: query,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  }));
}

function setWide(next: boolean) {
  wide = next;
  act(() => listeners.forEach((listener) => listener()));
}

const ITEMS = ["alpha", "beta", "gamma"];

function Harness({ initial = null as string | null }) {
  const [selected, setSelected] = useState<string | null>(initial);
  return (
    <>
      <button type="button" data-testid="back" onClick={() => setSelected(null)}>
        一覧へ戻る
      </button>
      <ListDetailLayout
        id="things"
        listLabel="項目の一覧"
        detailLabel="項目の詳細"
        listHeader={<input aria-label="検索" data-testid="search" />}
        list={
          <ul>
            {ITEMS.map((item) => (
              <li key={item}>
                <RowTitleButton title={item} current={item === selected} onClick={() => setSelected(item)} />
              </li>
            ))}
          </ul>
        }
        listFooter={<p>ページ送り</p>}
        hasSelection={selected !== null}
        detail={<p data-testid="detail">{selected}</p>}
        emptyDetail={<p data-testid="empty">一覧から項目を選んでください</p>}
      />
    </>
  );
}

const $ = <T extends Element = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const layout = () => $('[data-testid="list-detail-things"]');
const listColumn = () => $('[data-testid="list-detail-things-list"]');
const detailColumn = () => $('[data-testid="list-detail-things-detail"]');
const itemButton = (name: string) =>
  Array.from(document.querySelectorAll<HTMLButtonElement>("[data-row-title-button]")).find(
    (button) => button.textContent === name
  )!;
const click = (target: Element) => act(() => (target as HTMLElement).click());
const key = (target: Element, init: KeyboardEventInit) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));
  });

beforeEach(() => {
  wide = true;
  listeners.clear();
  stubViewport();
  window.localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("ListDetailLayout（#2281）", () => {
  it("xl 以上は一覧（既定 20rem）と詳細を並べ、未選択は空の状態を出す（先頭を自動で選ばない）", () => {
    act(() => root.render(<Harness />));
    expect(layout().dataset.listDetailLayout).toBe("split");
    expect(layout().style.getPropertyValue("--list-detail-list-width")).toBe("20rem");
    expect(layout().className).toContain("grid-cols-[var(--list-detail-list-width)_0.75rem_minmax(0,1fr)]");
    expect(listColumn().classList.contains("hidden")).toBe(false);
    expect(detailColumn().classList.contains("hidden")).toBe(false);
    expect($('[data-testid="empty"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="detail"]')).toBeNull();
    expect(listColumn().getAttribute("aria-label")).toBe("項目の一覧");
    click(itemButton("beta"));
    expect($('[data-testid="detail"]').textContent).toBe("beta");
  });

  it("開閉ボタンで一覧を畳むと詳細が全幅になり、開閉は id ごとに保存される", () => {
    act(() => root.render(<Harness />));
    const toggle = $<HTMLButtonElement>('[data-testid="list-detail-things-toggle"]');
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-controls")).toBe(listColumn().id);
    expect(toggle.getAttribute("aria-label")).toBe("項目の一覧");
    click(toggle);
    expect(layout().dataset.listCollapsed).toBe("true");
    expect(listColumn().classList.contains("hidden")).toBe(true);
    expect(document.querySelector('[data-testid="list-detail-things-resizer"]')).toBeNull();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(JSON.parse(window.localStorage.getItem(listDetailStorageKey("things"))!)).toMatchObject({ collapsed: true });
  });

  it("分割線は矢印キーで幅を変え（Shift で 4rem）、Home で既定に戻し、幅を保存する", () => {
    act(() => root.render(<Harness />));
    const resizer = $('[data-testid="list-detail-things-resizer"]');
    expect(resizer.getAttribute("role")).toBe("separator");
    expect(resizer.getAttribute("aria-valuenow")).toBe("20");
    key(resizer, { key: "ArrowRight" });
    expect(resizer.getAttribute("aria-valuenow")).toBe("21");
    key(resizer, { key: "ArrowRight", shiftKey: true });
    expect(resizer.getAttribute("aria-valuenow")).toBe("25");
    expect(layout().style.getPropertyValue("--list-detail-list-width")).toBe("25rem");
    expect(JSON.parse(window.localStorage.getItem(listDetailStorageKey("things"))!)).toMatchObject({ listWidthRem: 25 });
    key(resizer, { key: "ArrowLeft", shiftKey: true });
    key(resizer, { key: "ArrowLeft", shiftKey: true });
    key(resizer, { key: "ArrowLeft", shiftKey: true });
    expect(resizer.getAttribute("aria-valuenow")).toBe("16");
    key(resizer, { key: "Home" });
    expect(resizer.getAttribute("aria-valuenow")).toBe(String(LIST_DETAIL_DEFAULT_LIST_WIDTH_REM));
  });

  it("↑ / ↓ / Home / End で一覧の項目の間をフォーカスが移り、検索欄の矢印キーは奪わない", () => {
    act(() => root.render(<Harness />));
    const first = itemButton("alpha");
    first.focus();
    key(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(itemButton("beta"));
    key(itemButton("beta"), { key: "End" });
    expect(document.activeElement).toBe(itemButton("gamma"));
    key(itemButton("gamma"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(itemButton("gamma"));
    key(itemButton("gamma"), { key: "Home" });
    expect(document.activeElement).toBe(itemButton("alpha"));
    const search = $<HTMLInputElement>('[data-testid="search"]');
    search.focus();
    key(search, { key: "ArrowDown" });
    expect(document.activeElement).toBe(search);
  });

  it("xl 未満は 1 ペイン: 一覧 → 選ぶと詳細だけ → 戻ると一覧を出して選んだ行へフォーカスを戻す", () => {
    wide = false;
    act(() => root.render(<Harness />));
    expect(layout().dataset.listDetailLayout).toBe("single");
    expect(layout().dataset.listDetailView).toBe("list");
    expect(detailColumn().classList.contains("hidden")).toBe(true);
    expect(document.querySelector('[data-testid="list-detail-things-toggle"]')).toBeNull();
    expect(document.querySelector('[data-testid="list-detail-things-resizer"]')).toBeNull();
    const beta = itemButton("beta");
    beta.focus();
    click(beta);
    expect(layout().dataset.listDetailView).toBe("detail");
    expect(listColumn().classList.contains("hidden")).toBe(true);
    expect(detailColumn().classList.contains("hidden")).toBe(false);
    expect(document.activeElement).toBe(detailColumn());
    click($('[data-testid="back"]'));
    expect(layout().dataset.listDetailView).toBe("list");
    expect(document.activeElement).toBe(itemButton("beta"));
  });

  it("幅が xl をまたいでも選択は保ち、2 ペインに戻ると一覧と詳細を両方出す", () => {
    wide = false;
    act(() => root.render(<Harness initial="gamma" />));
    expect(layout().dataset.listDetailView).toBe("detail");
    setWide(true);
    expect(layout().dataset.listDetailView).toBe("both");
    expect($('[data-testid="detail"]').textContent).toBe("gamma");
    expect(listColumn().classList.contains("hidden")).toBe(false);
  });

  it("畳んだ状態は 1 ペインでは効かない（一覧は 1 ペインの規則で出す）", () => {
    window.localStorage.setItem(listDetailStorageKey("things"), serializeListDetailStoredState({ listWidthRem: 20, collapsed: true }));
    wide = false;
    act(() => root.render(<Harness />));
    expect(layout().dataset.listCollapsed).toBe("false");
    expect(listColumn().classList.contains("hidden")).toBe(false);
  });
});

describe("list-detail の純粋関数", () => {
  it("保存の値を読み、壊れた値・範囲の外は直す", () => {
    expect(parseListDetailStoredState(null)).toEqual({ listWidthRem: 20, collapsed: false });
    expect(parseListDetailStoredState("{")).toEqual({ listWidthRem: 20, collapsed: false });
    expect(parseListDetailStoredState('{"listWidthRem":99,"collapsed":true}')).toEqual({ listWidthRem: 32, collapsed: true });
    expect(parseListDetailStoredState('{"listWidthRem":3}')).toEqual({ listWidthRem: 16, collapsed: false });
  });

  it("幅を 0.25rem 単位で範囲に収め、置き場所が狭いと詳細に 30rem を残す", () => {
    expect(clampListWidthRem(20.4)).toBe(20.5);
    expect(clampListWidthRem(Number.NaN)).toBe(20);
    expect(maxListWidthForContainer(70)).toBe(32);
    expect(maxListWidthForContainer(55)).toBe(25);
    expect(maxListWidthForContainer(40)).toBe(16);
  });

  it("↑ / ↓ / Home / End の移る先", () => {
    expect(nextListItemIndex("ArrowDown", -1, 3)).toBe(0);
    expect(nextListItemIndex("ArrowDown", 2, 3)).toBe(2);
    expect(nextListItemIndex("ArrowUp", 0, 3)).toBe(0);
    expect(nextListItemIndex("End", 0, 3)).toBe(2);
    expect(nextListItemIndex("Enter", 0, 3)).toBeNull();
    expect(nextListItemIndex("ArrowDown", 0, 0)).toBeNull();
  });

  it("前後の項目（今の並びの外なら両方 null）", () => {
    expect(adjacentListItems(ITEMS, "beta", (item) => item)).toEqual({ previous: "alpha", next: "gamma" });
    expect(adjacentListItems(ITEMS, "alpha", (item) => item)).toEqual({ previous: null, next: "beta" });
    expect(adjacentListItems(ITEMS, "delta", (item) => item)).toEqual({ previous: null, next: null });
    expect(adjacentListItems(ITEMS, null, (item) => item)).toEqual({ previous: null, next: null });
  });
});
