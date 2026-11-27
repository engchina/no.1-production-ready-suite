// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AnswerFileReview,
  ConfirmProvider,
  FILE_REVIEW_JA,
  type AnswerFileReviewDetails,
  type FileReviewState,
} from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const t = (key: keyof typeof FILE_REVIEW_JA) => FILE_REVIEW_JA[key];
const state = (rating: "helpful" | "not_helpful"): FileReviewState => ({
  revision: 0,
  versions: [{ id: "answer-1", answer: "合成の回答", sources: {}, rating }],
  kept: {},
  seen: [],
  excluded: {},
  pending: {},
});

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

const vote = vi.fn(async () => undefined);
const load = vi.fn(async (rating: "helpful" | "not_helpful") => state(rating));

async function render(
  rating: "helpful" | "not_helpful",
  save = vi.fn(async () => undefined),
  loadOnMount?: boolean,
) {
  vote.mockClear();
  load.mockClear();
  const details: AnswerFileReviewDetails = {
    reasons: [{ value: "incorrect", label: "内容が間違っている" }],
    correctedAnswer: true,
    labels: {
      reasonLegend: "解決しなかった理由",
      commentLabel: "コメント",
      correctedAnswerLabel: "修正した回答",
      save: "フィードバックを保存",
      cancel: "キャンセル",
    },
    save,
  };
  await act(async () =>
    root.render(
      <ConfirmProvider>
        <AnswerFileReview
          rootId="answer-1"
          t={t}
          initialRating={rating}
          load={() => load(rating)}
          loadOnMount={loadOnMount}
          change={async () => state(rating)}
          vote={vote}
          details={details}
          onHistory={() => undefined}
          renderVersion={() => null}
        />
      </ConfirmProvider>,
    ),
  );
  return save;
}

const button = (name: string) =>
  Array.from(document.body.querySelectorAll<HTMLButtonElement>("button")).find(
    (item) => item.textContent?.trim() === name,
  );
const click = (name: string) => act(async () => button(name)!.click());
const REVIEW = FILE_REVIEW_JA["fileReview.title"];
const DETAILS = FILE_REVIEW_JA["fileReview.detailsWithAnswer"];

describe("AnswerFileReview の評価と、その後の操作（#5906）", () => {
  it("「解決した」では、資料の選び直しと理由の入力のボタンを出さない", async () => {
    await render("helpful");
    expect(button(REVIEW)).toBeUndefined();
    expect(button(DETAILS)).toBeUndefined();
  });

  it("「解決しなかった」は評価だけを保存し、資料を選び直す面を開かない", async () => {
    await render("helpful");
    load.mockClear();
    await click(FILE_REVIEW_JA["fileReview.notHelpful"]);
    expect(vote).toHaveBeenCalledWith("answer-1", "not_helpful");
    expect(load).not.toHaveBeenCalled();
    expect(document.body.querySelector("[data-state=open]")).toBeNull();
    expect(button(REVIEW)).toBeDefined();
    expect(button(DETAILS)).toBeDefined();
  });

  it("「資料を選んで再検索」で、資料を選び直す面を開く", async () => {
    await render("not_helpful");
    load.mockClear();
    await click(REVIEW);
    expect(load).toHaveBeenCalledTimes(1);
    expect(document.body.querySelector("[data-state=open]")).not.toBeNull();
    expect(vote).not.toHaveBeenCalled();
  });

  it("mount のときに以前の版を読む（既定）", async () => {
    await render("not_helpful");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("loadOnMount が false なら、資料を選び直す面を開くまで読まない（#5938）", async () => {
    await render("not_helpful", undefined, false);
    expect(load).not.toHaveBeenCalled();
    // 読み込み中の骨組みではなく、評価のボタンをすぐ出す。
    expect(button(FILE_REVIEW_JA["fileReview.notHelpful"])).toBeDefined();
    await click(REVIEW);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("理由と修正した回答を、その版の ID で保存し、記録済みを出す", async () => {
    const save = await render("not_helpful");
    await click(DETAILS);
    expect(button("フィードバックを保存")?.disabled).toBe(true);
    await click("内容が間違っている");
    const corrected = host.querySelectorAll<HTMLTextAreaElement>("textarea")[1];
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(corrected, "合成の正しい回答");
      corrected.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("フィードバックを保存");
    expect(save).toHaveBeenCalledWith("answer-1", {
      rating: "not_helpful",
      reason: "incorrect",
      comment: null,
      correctedAnswer: "合成の正しい回答",
    });
    expect(host.querySelector("fieldset")).toBeNull();
    expect(host.textContent).toContain(FILE_REVIEW_JA["fileReview.detailsSaved"]);
  });

  it("保存に失敗したら文言と再試行を出し、入力を閉じない", async () => {
    await render("not_helpful", vi.fn(async () => Promise.reject(new Error("保存できませんでした。"))));
    await click(DETAILS);
    await click("内容が間違っている");
    await click("フィードバックを保存");
    expect(host.querySelector("[role=alert]")?.textContent).toContain("保存できませんでした。");
    expect(host.querySelector("fieldset")).not.toBeNull();
  });

  it("理由を記録した後で「解決した」に変えるときは確かめ、取り消せば変えない", async () => {
    await render("not_helpful");
    await click(DETAILS);
    await click("内容が間違っている");
    await click("フィードバックを保存");
    await click(FILE_REVIEW_JA["fileReview.helpful"]);
    expect(document.body.textContent).toContain(FILE_REVIEW_JA["fileReview.clearDetailsDescription"]);
    await click("キャンセル");
    expect(vote).not.toHaveBeenCalled();
    await click(FILE_REVIEW_JA["fileReview.helpful"]);
    await click(FILE_REVIEW_JA["fileReview.clearDetailsConfirm"]);
    expect(vote).toHaveBeenCalledWith("answer-1", "helpful");
    expect(button(DETAILS)).toBeUndefined();
  });

  it("理由を記録していなければ、確かめずに「解決した」に変える", async () => {
    await render("not_helpful");
    await click(FILE_REVIEW_JA["fileReview.helpful"]);
    expect(vote).toHaveBeenCalledWith("answer-1", "helpful");
  });
});
