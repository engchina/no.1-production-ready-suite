import { describe, expect, it } from "vitest";

import {
  appendEvaluationCase,
  defaultFeedbackSearch,
  feedbackListParams,
  isFeedbackFiltered,
  parseFeedbackUrl,
} from "./FeedbackClient.logic";

describe("feedback URL state", () => {
  it("restores filters, page size and selected feedback", () => {
    const state = parseFeedbackUrl(
      new URLSearchParams(
        "period=90&search_answer_profile=bv-1&target=answer&rating=not_helpful&reason=incorrect&q=規程&sort=oldest&size=100&page=3&feedback=fb-1"
      )
    );

    expect(state).toMatchObject({
      periodDays: 90,
      searchAnswerProfileId: "bv-1",
      targetType: "answer",
      rating: "not_helpful",
      reason: "incorrect",
      q: "規程",
      sortOrder: "oldest",
      pageSize: 100,
      page: 3,
      feedbackId: "fb-1",
    });
    expect(feedbackListParams(state).offset).toBe(200);
  });

  it("falls back to stable defaults for invalid parameters", () => {
    const state = parseFeedbackUrl(new URLSearchParams("period=3&size=12&page=-2"));
    expect(state.periodDays).toBe(30);
    // 表示件数は 3 製品の一覧と同じ既定の 25 件（#1891・#1952）。
    expect(state.pageSize).toBe(25);
    expect(state.page).toBe(1);
  });

  // #1891: 独自の 25 / 50 / 100 をやめ、3 製品の一覧と同じ段にした。
  it("表示件数は共通の段（10 / 25 / 50 / 100）だけを受け付ける", () => {
    expect(parseFeedbackUrl(new URLSearchParams("size=10")).pageSize).toBe(10);
    expect(parseFeedbackUrl(new URLSearchParams("size=25")).pageSize).toBe(25);
    expect(parseFeedbackUrl(new URLSearchParams("size=20")).pageSize).toBe(25);
    expect(parseFeedbackUrl(new URLSearchParams(defaultFeedbackSearch())).pageSize).toBe(25);
  });
});

describe("feedback list count", () => {
  it("期間・条件・検索語のどれかがあれば絞り込み中（「N 件（全 M 件）」）", () => {
    expect(isFeedbackFiltered(parseFeedbackUrl(new URLSearchParams("period=all")))).toBe(false);
    expect(isFeedbackFiltered(parseFeedbackUrl(new URLSearchParams("period=all&sort=oldest&size=50")))).toBe(false);
    expect(isFeedbackFiltered(parseFeedbackUrl(new URLSearchParams(defaultFeedbackSearch())))).toBe(true);
    expect(isFeedbackFiltered(parseFeedbackUrl(new URLSearchParams("period=all&rating=helpful")))).toBe(true);
    expect(isFeedbackFiltered(parseFeedbackUrl(new URLSearchParams("period=all&q=規程")))).toBe(true);
  });
});

describe("appendEvaluationCase", () => {
  const evaluationCase = { id: "feedback-fb-1", query: "承認者は？", expected_answer_keywords: ["部長"] };

  it("未編集なら新しい要求を作る", () => {
    const result = appendEvaluationCase(null, evaluationCase);

    expect(result.ok && JSON.parse(result.json)).toEqual({ cases: [evaluationCase], top_k: 10 });
  });

  it("既存の要求へ追記し、同じ id のケースは置き換える", () => {
    const existing = JSON.stringify({
      cases: [{ id: "other", query: "別" }, { id: "feedback-fb-1", query: "古い" }],
      top_k: 20,
    });

    const result = appendEvaluationCase(existing, evaluationCase);

    expect(result.ok && JSON.parse(result.json)).toEqual({
      cases: [{ id: "other", query: "別" }, evaluationCase],
      top_k: 20,
    });
  });

  it("壊れた JSON や cases のない要求は上書きしない", () => {
    expect(appendEvaluationCase("{", evaluationCase)).toEqual({ ok: false });
    expect(appendEvaluationCase("{\"top_k\": 10}", evaluationCase)).toEqual({ ok: false });
  });
});
