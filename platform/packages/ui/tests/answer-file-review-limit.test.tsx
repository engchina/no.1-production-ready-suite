// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AnswerFileReview,
  ConfirmProvider,
  FILE_REVIEW_DEFAULT_SELECTION_LIMIT,
  FILE_REVIEW_JA,
  selectionLimit,
  selectionLimited,
  type FileReviewAction,
  type FileReviewState,
  type ReviewText,
} from "../src";
import { limitFileNames, limitSnapshot } from "../e2e/file-review-scenario";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const t: ReviewText = (key, params = {}) =>
  FILE_REVIEW_JA[key].replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? ""));
const TASK = "question";
const names = limitFileNames(7);

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

const change = vi.fn(async (action: FileReviewAction): Promise<FileReviewState> => {
  void action;
  return limitSnapshot("answer-1", TASK, 7).state;
});

/** 資料を選び直す面を開いた状態にする。 */
async function open(state: FileReviewState) {
  change.mockClear();
  await act(async () =>
    root.render(
      <ConfirmProvider>
        <AnswerFileReview
          rootId="answer-1"
          t={t}
          initialRating="not_helpful"
          load={async () => state}
          loadOnMount={false}
          change={change}
          vote={async () => undefined}
          onHistory={() => undefined}
          renderVersion={() => null}
        />
      </ConfirmProvider>,
    ),
  );
  await act(async () => button(FILE_REVIEW_JA["fileReview.title"])!.click());
}

const button = (name: string) =>
  Array.from(document.body.querySelectorAll<HTMLButtonElement>("button")).find(
    (item) => item.textContent?.trim() === name,
  );
const checkbox = (name: string) =>
  Array.from(document.body.querySelectorAll<HTMLLabelElement>("label"))
    .find((item) => item.textContent?.trim() === name)!
    .querySelector<HTMLInputElement>('input[type="checkbox"]')!;
const radio = (name: string) =>
  Array.from(document.body.querySelectorAll<HTMLLabelElement>("label"))
    .find((item) => item.textContent?.trim() === name)!
    .querySelector<HTMLInputElement>('input[type="radio"]')!;
const check = (name: string) => act(async () => checkbox(name).click());
const choose = (name: string) => act(async () => radio(name).click());
const notice = () => document.body.querySelector<HTMLElement>('[data-testid="file-review-limit"]');
const status = () => document.body.querySelector('[role="dialog"] [role="status"]')!.textContent ?? "";

const ONLY = FILE_REVIEW_JA["fileReview.only"];
const MIX = FILE_REVIEW_JA["fileReview.mix"];
const REPLACE = FILE_REVIEW_JA["fileReview.replace"];
const ANSWER = FILE_REVIEW_JA["fileReview.confirm"];
const FIND = FILE_REVIEW_JA["fileReview.find"];

describe("資料を選び直す面の上限の値", () => {
  it("状態の値を使い、無い・形の違う値は予備の値にする", () => {
    expect(FILE_REVIEW_DEFAULT_SELECTION_LIMIT).toBe(5);
    expect(selectionLimit({ selection_limit: 3 })).toBe(3);
    for (const value of [null, 0, -1, 2.5, Number.NaN]) expect(selectionLimit({ selection_limit: value })).toBe(5);
    expect(selectionLimit(null)).toBe(5);
  });

  it("「別の資料を探す」だけ上限を掛けない（候補の確認の面には掛ける）", () => {
    expect([selectionLimited("only", false), selectionLimited("mix", false), selectionLimited(null, false)]).toEqual([true, true, true]);
    expect(selectionLimited("replace", false)).toBe(false);
    expect(selectionLimited(null, true)).toBe(true);
  });
});

describe("AnswerFileReview の選べる資料の上限（#5981）", () => {
  it("上限に達すると、選んでいない資料を選べなくして理由を出す（選んだ資料は外せる）", async () => {
    await open(limitSnapshot("answer-1", TASK, 7).state);
    expect(status()).toContain("全 7 件・選択 0 / 5 件");
    for (const name of names.slice(0, 4)) await check(name);
    expect(status()).toContain("選択 4 / 5 件");
    expect(notice()).toBeNull();
    expect(checkbox(names[5]).disabled).toBe(false);

    await check(names[4]);
    expect(status()).toContain("選択 5 / 5 件");
    expect(notice()!.textContent).toBe("一度に選べる資料は 5 件までです。");
    for (const name of names.slice(5)) {
      expect(checkbox(name).disabled).toBe(true);
      // 選べない理由を読み上げにも結び付ける。
      expect(checkbox(name).getAttribute("aria-describedby")).toBe(notice()!.id);
    }
    // 選んだ資料は外せて、外すとまた選べる。上限ちょうどでは主ボタンを押せる。
    expect(checkbox(names[0]).disabled).toBe(false);
    expect(button(FIND)!.disabled).toBe(false);
    await check(names[0]);
    expect(notice()).toBeNull();
    expect(checkbox(names[6]).disabled).toBe(false);
  });

  it("「別の資料を探す」の間は上限を掛けず、戻したときに超えていれば主ボタンを止めて理由を出す", async () => {
    await open(limitSnapshot("answer-1", TASK, 7).state);
    await choose(REPLACE);
    for (const name of names) await check(name);
    expect(status()).toContain("全 7 件・選択 7 件");
    expect(notice()).toBeNull();
    expect(button(FIND)!.disabled).toBe(false);

    // 方法を戻しても選択は勝手に外さない。
    await choose(MIX);
    expect(status()).toContain("選択 7 / 5 件");
    expect(notice()!.textContent).toBe("一度に選べる資料は 5 件までです。2 件外してください。");
    expect(button(FIND)!.disabled).toBe(true);
    expect(button(FIND)!.getAttribute("aria-describedby")).toBe(notice()!.id);
    await choose(ONLY);
    expect(button(ANSWER)!.disabled).toBe(true);

    await check(names[0]);
    await check(names[1]);
    expect(notice()!.textContent).toBe("一度に選べる資料は 5 件までです。");
    expect(button(ANSWER)!.disabled).toBe(false);
  });

  it("前の再回答から引き継いだ選択が上限を超えていても、勝手に外さずに主ボタンを止める", async () => {
    const kept = Array.from({ length: 4 }, (_, index) => "doc-" + (index + 1));
    await open(limitSnapshot("answer-1", TASK, 7, { limit: 3, kept }).state);
    await choose(ONLY);
    expect(status()).toContain("選択 4 / 3 件");
    expect(notice()!.textContent).toBe("一度に選べる資料は 3 件までです。1 件外してください。");
    expect(button(ANSWER)!.disabled).toBe(true);
    expect(names.slice(0, 4).every((name) => checkbox(name).checked)).toBe(true);
  });

  it("状態が上限を持たないときは予備の 5 件で止める", async () => {
    await open(limitSnapshot("answer-1", TASK, 7, { limit: null }).state);
    for (const name of names.slice(0, 5)) await check(name);
    expect(status()).toContain("選択 5 / 5 件");
    expect(checkbox(names[5]).disabled).toBe(true);
  });

  it("要求に選んだ方法を付ける", async () => {
    await open(limitSnapshot("answer-1", TASK, 7).state);
    await check(names[0]);
    await act(async () => button(FIND)!.click());
    expect(change).toHaveBeenLastCalledWith({ task: TASK, action: "discover", selected: ["doc-1"], revision: 0, method: "mix" });

    await choose(REPLACE);
    await act(async () => button(FIND)!.click());
    expect(change).toHaveBeenLastCalledWith({ task: TASK, action: "discover", selected: [], revision: 0, method: "replace" });

    await choose(ONLY);
    await check(names[1]);
    await act(async () => button(ANSWER)!.click());
    expect(change).toHaveBeenLastCalledWith({ task: TASK, action: "answer", selected: ["doc-2"], revision: 0, method: "only" });
  });
});
