import { describe, expect, it } from "vitest";

import {
  ADB_TRANSITIONAL_STATES,
  DOCUMENT_ACTIVE_STATUSES,
  adbIsTransitioning,
  dashboardHasActiveWork,
  documentsHaveActiveWork,
} from "./queries";
import type { DashboardActivity, DocumentSummary, FileStatus } from "./api";

function doc(status: FileStatus): Pick<DocumentSummary, "status"> {
  return { status };
}

function activity(status: FileStatus): Pick<DashboardActivity, "status"> {
  return { status };
}

describe("documentsHaveActiveWork", () => {
  it("空配列/undefined は false", () => {
    expect(documentsHaveActiveWork([])).toBe(false);
    expect(documentsHaveActiveWork(undefined)).toBe(false);
  });

  it("取込/索引中があれば true", () => {
    expect(documentsHaveActiveWork([doc("INDEXED"), doc("INGESTING")])).toBe(true);
    expect(documentsHaveActiveWork([doc("INDEXING")])).toBe(true);
  });

  it("安定状態のみなら false(UPLOADED/REVIEW/INDEXED/ERROR)", () => {
    expect(
      documentsHaveActiveWork([doc("UPLOADED"), doc("REVIEW"), doc("INDEXED"), doc("ERROR")])
    ).toBe(false);
  });

  it("DOCUMENT_ACTIVE_STATUSES は INGESTING/INDEXING のみ", () => {
    expect([...DOCUMENT_ACTIVE_STATUSES].sort()).toEqual(["INDEXING", "INGESTING"]);
  });
});

describe("adbIsTransitioning", () => {
  it("null/undefined/空 は false", () => {
    expect(adbIsTransitioning(null)).toBe(false);
    expect(adbIsTransitioning(undefined)).toBe(false);
    expect(adbIsTransitioning("")).toBe(false);
  });

  it("起動/停止などの遷移状態は true", () => {
    for (const state of ADB_TRANSITIONAL_STATES) {
      expect(adbIsTransitioning(state)).toBe(true);
    }
    expect(adbIsTransitioning("STARTING")).toBe(true);
    expect(adbIsTransitioning("STOPPING")).toBe(true);
  });

  it("安定状態は false", () => {
    expect(adbIsTransitioning("AVAILABLE")).toBe(false);
    expect(adbIsTransitioning("STOPPED")).toBe(false);
    expect(adbIsTransitioning("FAILED")).toBe(false);
    expect(adbIsTransitioning("TERMINATED")).toBe(false);
  });
});

describe("dashboardHasActiveWork", () => {
  it("空配列/undefined は false", () => {
    expect(dashboardHasActiveWork([])).toBe(false);
    expect(dashboardHasActiveWork(undefined)).toBe(false);
  });

  it("進行中アクティビティがあれば true", () => {
    expect(dashboardHasActiveWork([activity("INDEXED"), activity("INGESTING")])).toBe(true);
  });

  it("安定状態のみなら false", () => {
    expect(dashboardHasActiveWork([activity("UPLOADED"), activity("INDEXED")])).toBe(false);
  });
});
