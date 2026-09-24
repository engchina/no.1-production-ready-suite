import { describe, expect, it } from "vitest";

import { feedbackListParams, pageWindow, parseFeedbackUrl } from "./FeedbackClient.logic";

describe("feedback URL state", () => {
  it("restores filters, page size and selected feedback", () => {
    const state = parseFeedbackUrl(
      new URLSearchParams(
        "period=90&business_view=bv-1&target=answer&rating=not_helpful&reason=incorrect&q=規程&sort=oldest&size=100&page=3&feedback=fb-1"
      )
    );

    expect(state).toMatchObject({
      periodDays: 90,
      businessViewId: "bv-1",
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
    expect(state.pageSize).toBe(50);
    expect(state.page).toBe(1);
  });
});

describe("feedback page window", () => {
  it("shows edge pages and ellipses around the current page", () => {
    expect(pageWindow(6, 12)).toEqual([1, "ellipsis", 5, 6, 7, "ellipsis", 12]);
  });

  it("shows every page for short results", () => {
    expect(pageWindow(2, 4)).toEqual([1, 2, 3, 4]);
  });
});
