import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { isUnreadableReleaseError, selectablePhase, startPhaseForUnreadable } from "./LanguagePackUnreadable.logic";

describe("現在の契約で読めない言語資源の版", () => {
  it("版の読み取り・比較の 409 だけを読めない版として扱う", () => {
    expect(isUnreadableReleaseError(new ApiError(409, ["保存済み言語資源が現在の契約を満たしません。"]))).toBe(true);
    expect(isUnreadableReleaseError(new ApiError(404, ["言語資源の公開版が見つかりません。"]))).toBe(false);
    expect(isUnreadableReleaseError(new ApiError(500, []))).toBe(false);
    expect(isUnreadableReleaseError(new Error("合成の失敗"))).toBe(false);
  });

  it("読めない版を使う対象は、ファイル準備のままか抽出から始める", () => {
    expect(startPhaseForUnreadable("PREPROCESS")).toBe("PREPROCESS");
    expect(startPhaseForUnreadable("EXTRACT")).toBe("EXTRACT");
    expect(startPhaseForUnreadable("CHUNK")).toBe("EXTRACT");
    expect(startPhaseForUnreadable("INDEX")).toBe("EXTRACT");
  });

  it("読めないと分かった後は Chunk 作成・索引の選択を未選択に戻す", () => {
    expect(selectablePhase("CHUNK", true)).toBe("");
    expect(selectablePhase("INDEX", true)).toBe("");
    expect(selectablePhase("EXTRACT", true)).toBe("EXTRACT");
    expect(selectablePhase("PREPROCESS", true)).toBe("PREPROCESS");
    expect(selectablePhase("CHUNK", false)).toBe("CHUNK");
    expect(selectablePhase("", false)).toBe("");
  });
});
