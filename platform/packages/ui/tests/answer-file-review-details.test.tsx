// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerFileReview, FILE_REVIEW_JA, type AnswerFileReviewDetails, type FileReviewState } from "../src";

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

async function render(rating: "helpful" | "not_helpful", save = vi.fn(async () => undefined)) {
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
      <AnswerFileReview
        rootId="answer-1"
        t={t}
        initialRating={rating}
        load={async () => state(rating)}
        change={async () => state(rating)}
        vote={async () => undefined}
        details={details}
        onHistory={() => undefined}
        renderVersion={() => null}
      />,
    ),
  );
  return save;
}

const summary = () => host.querySelector("summary");
const button = (name: string) =>
  Array.from(host.querySelectorAll<HTMLButtonElement>("button")).find((item) => item.textContent?.trim() === name);

describe("AnswerFileReview の理由・修正した回答の入力（#5906）", () => {
  it("「解決した」では入力の見出しを出さない", async () => {
    await render("helpful");
    expect(summary()).toBeNull();
  });

  it("「未解決」の後に開いて、理由と修正した回答をその版の ID で保存する", async () => {
    const save = await render("not_helpful");
    expect(summary()?.textContent).toContain(FILE_REVIEW_JA["fileReview.details"]);
    await act(async () => summary()!.click());
    expect(button("フィードバックを保存")?.disabled).toBe(true);
    await act(async () => button("内容が間違っている")!.click());
    const corrected = host.querySelectorAll<HTMLTextAreaElement>("textarea")[1];
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(corrected, "合成の正しい回答");
      corrected.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button("フィードバックを保存")!.click());
    expect(save).toHaveBeenCalledWith("answer-1", {
      rating: "not_helpful",
      reason: "incorrect",
      comment: null,
      correctedAnswer: "合成の正しい回答",
    });
    expect(host.querySelector("fieldset")).toBeNull();
  });

  it("保存に失敗したら文言と再試行を出し、入力を閉じない", async () => {
    await render("not_helpful", vi.fn(async () => Promise.reject(new Error("保存できませんでした。"))));
    await act(async () => summary()!.click());
    await act(async () => button("内容が間違っている")!.click());
    await act(async () => button("フィードバックを保存")!.click());
    expect(host.querySelector("[role=alert]")?.textContent).toContain("保存できませんでした。");
    expect(host.querySelector("fieldset")).not.toBeNull();
  });
});
