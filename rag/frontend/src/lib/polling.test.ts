import { describe, expect, it } from "vitest";

import {
  ADB_TRANSITIONAL_STATES,
  DOCUMENT_ACTIVE_STATUSES,
  adbIsTransitioning,
  dashboardHasActiveWork,
  documentWorkspaceShouldRefresh,
  documentsHaveActiveWork,
  ingestionJobIsActive,
  ingestionSegmentHasActiveWork,
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

  it("取込/Chunk/索引中があれば true", () => {
    expect(documentsHaveActiveWork([doc("INDEXED"), doc("PREPROCESSING")])).toBe(true);
    expect(documentsHaveActiveWork([doc("INDEXED"), doc("INGESTING")])).toBe(true);
    expect(documentsHaveActiveWork([doc("CHUNKING")])).toBe(true);
    expect(documentsHaveActiveWork([doc("INDEXING")])).toBe(true);
  });

  it("安定状態のみなら false(UPLOADED/REVIEW/CHUNKED/INDEXED/ERROR)", () => {
    expect(
      documentsHaveActiveWork([
        doc("UPLOADED"),
        doc("REVIEW"),
        doc("CHUNKED"),
        doc("INDEXED"),
        doc("ERROR"),
      ])
    ).toBe(false);
  });

  it("DOCUMENT_ACTIVE_STATUSES は実行中ステータスのみ", () => {
    expect([...DOCUMENT_ACTIVE_STATUSES].sort()).toEqual([
      "CHUNKING",
      "INDEXING",
      "INGESTING",
      "PREPROCESSING",
    ]);
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

describe("ingestionJobIsActive", () => {
  it("QUEUED/RUNNING のみ true", () => {
    expect(ingestionJobIsActive("QUEUED")).toBe(true);
    expect(ingestionJobIsActive("RUNNING")).toBe(true);
    expect(ingestionJobIsActive("SUCCEEDED")).toBe(false);
    expect(ingestionJobIsActive("FAILED")).toBe(false);
    expect(ingestionJobIsActive(undefined)).toBe(false);
  });
});

describe("ingestionSegmentHasActiveWork", () => {
  it("QUEUED/RUNNING segment があれば true", () => {
    expect(ingestionSegmentHasActiveWork([{ status: "SUCCEEDED" }])).toBe(false);
    expect(ingestionSegmentHasActiveWork([{ status: "QUEUED" }])).toBe(true);
    expect(ingestionSegmentHasActiveWork([{ status: "RUNNING" }])).toBe(true);
  });
});

describe("documentWorkspaceShouldRefresh", () => {
  it("取込/索引中の文書では true", () => {
    expect(documentWorkspaceShouldRefresh({ documentStatus: "PREPROCESSING" })).toBe(true);
    expect(documentWorkspaceShouldRefresh({ documentStatus: "INGESTING" })).toBe(true);
    expect(documentWorkspaceShouldRefresh({ documentStatus: "INDEXING" })).toBe(true);
  });

  it("ERROR/REVIEW の画面でも投入済み job が動いていれば true", () => {
    expect(
      documentWorkspaceShouldRefresh({
        documentStatus: "ERROR",
        jobStatuses: ["QUEUED"],
      })
    ).toBe(true);
    expect(
      documentWorkspaceShouldRefresh({
        documentStatus: "REVIEW",
        jobStatuses: ["RUNNING"],
      })
    ).toBe(true);
  });

  it("投入直後の watch 窓では安定状態に入るまで true", () => {
    expect(
      documentWorkspaceShouldRefresh({
        documentStatus: "UPLOADED",
        localWatchProcessing: true,
      })
    ).toBe(true);
    expect(
      documentWorkspaceShouldRefresh({
        documentStatus: "REVIEW",
        localWatchProcessing: true,
      })
    ).toBe(false);
  });

  it("segment が進行中なら true", () => {
    expect(
      documentWorkspaceShouldRefresh({
        documentStatus: "UPLOADED",
        segmentStatuses: ["RUNNING"],
      })
    ).toBe(true);
  });
});
