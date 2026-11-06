import { describe, expect, it } from "vitest";

import type { LanguagePackDocumentUse, LanguagePackRecipeUse } from "@/lib/api";

import {
  languagePackBuildNotice,
  languagePackDisplayVersion,
  languagePackNeedsAction,
  languagePackPanelState,
  languagePackStatus,
  languagePackSummaryStatus,
  type LanguagePackStatus,
} from "./LanguagePackDocumentPanel.logic";

const empty: LanguagePackRecipeUse = { recipe_id: "recipe-1", chunk_set_id: null, release_id: null, version: null, ingest_hash: null, relation: "unrecorded", built_at: null, job_id: null, target_release_id: null, target_version: null };
const served = (release: string, version: number, relation: LanguagePackRecipeUse["relation"]): LanguagePackRecipeUse => ({ ...empty, chunk_set_id: "chunks-a", release_id: release, version, ingest_hash: `hash-${version}`, relation, built_at: "2026-10-17T00:00:00Z", job_id: "job-a" });
const documentUse = (...recipes: LanguagePackRecipeUse[]): LanguagePackDocumentUse => ({ current_release_id: "release-3", current_version: 3, recipes });

describe("languagePackPanelState", () => {
  it("索引も構築中の job も無ければ未構築にし、比較を作らない", () => {
    expect(languagePackPanelState(documentUse(), "recipe-1")).toEqual({ kind: "notBuilt" });
    // 他のレシピの索引・構築は、このレシピの状態にしない。
    expect(languagePackPanelState(documentUse({ ...served("release-3", 3, "current"), recipe_id: "recipe-2", target_release_id: "release-3" }), "recipe-1")).toEqual({ kind: "notBuilt" });
  });

  it("索引が無く構築中なら初回構築として対象版だけを返す", () => {
    expect(languagePackPanelState(documentUse({ ...empty, target_release_id: "release-3", target_version: 3 }), "recipe-1")).toEqual({ kind: "firstBuild", target: { releaseId: "release-3", version: 3 } });
    // 版の行が無ければ版番号を推定しない。
    expect(languagePackPanelState(documentUse({ ...empty, target_release_id: "release-x" }), "recipe-1")).toEqual({ kind: "firstBuild", target: { releaseId: "release-x", version: null } });
  });

  it("使用中と構築の対象が同じ版なら換版として扱わない", () => {
    const state = languagePackPanelState(documentUse({ ...served("release-3", 3, "current"), target_release_id: "release-3", target_version: 3 }), "recipe-1");
    expect(state.kind).toBe("sameVersionBuild");
    expect(state).not.toHaveProperty("comparison");
  });

  it("使用中と構築の対象が別の版なら、使用中 → 構築の対象を比較する", () => {
    const state = languagePackPanelState(documentUse({ ...served("release-1", 1, "different"), target_release_id: "release-2", target_version: 2 }), "recipe-1");
    expect(state).toMatchObject({ kind: "differentVersionBuild", target: { releaseId: "release-2", version: 2 }, comparison: { beforeId: "release-1", afterId: "release-2" } });
  });

  it.each([
    ["版の id が無い", { release_id: null }],
    ["取込設定の hash が無い", { ingest_hash: null }],
    ["版の行が無い", { version: null }],
  ])("索引はあるが%sときは判定不可にし、別の版として扱わない", (_name, missing) => {
    const active = { ...served("release-1", 1, "different"), ...missing, target_release_id: "release-2", target_version: 2 };
    const state = languagePackPanelState(documentUse(active), "recipe-1");
    expect(state).toMatchObject({ kind: "unrecorded", target: { releaseId: "release-2", version: 2 } });
    expect(state).not.toHaveProperty("comparison");
    expect(languagePackPanelState(documentUse({ ...active, target_release_id: null, target_version: null }), "recipe-1")).toMatchObject({ kind: "unrecorded", target: null });
  });

  it("構築中でなく版が記録されていれば、使用中 → 現在版を比較する", () => {
    expect(languagePackPanelState(documentUse(served("release-2", 2, "equivalent")), "recipe-1")).toMatchObject({ kind: "built", relation: "equivalent", comparison: { beforeId: "release-2", afterId: "release-3" } });
  });
});

describe("languagePackStatus（要約の行のバッジ）", () => {
  it.each<[string, LanguagePackRecipeUse[], LanguagePackStatus, number | null]>([
    ["索引も job も無い", [], "notBuilt", null],
    ["索引が無く構築中", [{ ...empty, target_release_id: "release-3", target_version: 3 }], "building", 3],
    ["同じ版で構築中", [{ ...served("release-3", 3, "current"), target_release_id: "release-3", target_version: 3 }], "building", 3],
    ["別の版で構築中", [{ ...served("release-1", 1, "different"), target_release_id: "release-3", target_version: 3 }], "building", 3],
    ["判定できない索引を構築中", [{ ...served("release-1", 1, "different"), ingest_hash: null, target_release_id: "release-3", target_version: null }], "building", null],
    ["現在版を使用", [served("release-3", 3, "current")], "current", 3],
    ["取込設定は同じ", [served("release-2", 2, "equivalent")], "equivalent", 2],
    ["取込設定に差分あり", [served("release-1", 1, "different")], "different", 1],
    ["使用版を判定できない", [{ ...served("release-1", 1, "different"), ingest_hash: null }], "unrecorded", null],
  ])("%s", (_name, recipes, status, version) => {
    const state = languagePackPanelState(documentUse(...recipes), "recipe-1");
    expect(languagePackStatus(state)).toBe(status);
    // 構築中は対象版、記録済みの索引はその版。判定できない索引の版は出さない。
    expect(languagePackDisplayVersion(state)).toBe(version);
  });

  it("対応が要るのは再処理を推奨・判定できないときだけ", () => {
    const needs = (["current", "equivalent", "different", "unrecorded", "building", "notBuilt"] as const).filter(languagePackNeedsAction);
    expect(needs).toEqual(["different", "unrecorded"]);
  });
});

describe("languagePackBuildNotice（状態メッセージの枠の構築中の案内）", () => {
  it("選んだレシピを構築中のときだけ対象版を返し、既存の索引を使い続けるかを分ける", () => {
    expect(languagePackBuildNotice(documentUse(served("release-3", 3, "current")), "recipe-1")).toBeNull();
    expect(languagePackBuildNotice(documentUse({ ...empty, target_release_id: "release-3", target_version: 3 }), "recipe-1")).toEqual({ version: 3, keepsActive: false });
    expect(languagePackBuildNotice(documentUse({ ...served("release-1", 1, "different"), target_release_id: "release-3", target_version: 3 }), "recipe-1")).toEqual({ version: 3, keepsActive: true });
    // 他のレシピの構築・レシピ未選択・言語資源の情報なしは案内しない。
    expect(languagePackBuildNotice(documentUse({ ...empty, recipe_id: "recipe-2", target_release_id: "release-3" }), "recipe-1")).toBeNull();
    expect(languagePackBuildNotice(documentUse({ ...empty, target_release_id: "release-3" }), null)).toBeNull();
    expect(languagePackBuildNotice(null, "recipe-1")).toBeNull();
  });
});

describe("languagePackSummaryStatus（文書インデックスの列）", () => {
  it("索引が無ければ未構築、構築中なら構築中、判定できない索引があれば判定できない", () => {
    expect(languagePackSummaryStatus(documentUse())).toBe("notBuilt");
    expect(languagePackSummaryStatus(documentUse({ ...empty, target_release_id: "release-3" }))).toBe("building");
    expect(languagePackSummaryStatus(documentUse({ ...served("release-3", 3, "current"), ingest_hash: null }))).toBe("unrecorded");
    expect(languagePackSummaryStatus(null)).toBe("unrecorded");
  });

  it("差分 > 判定できない > 構築中 > 同値 > 現在版の順で 1 つにまとめる", () => {
    const unrecorded = { ...served("release-3", 3, "current"), version: null };
    const building = { ...served("release-1", 1, "different"), recipe_id: "recipe-3", target_release_id: "release-3" };
    expect(languagePackSummaryStatus(documentUse(served("release-1", 1, "different"), unrecorded))).toBe("different");
    expect(languagePackSummaryStatus(documentUse(served("release-2", 2, "equivalent"), unrecorded))).toBe("unrecorded");
    expect(languagePackSummaryStatus(documentUse(served("release-2", 2, "equivalent"), building))).toBe("building");
    expect(languagePackSummaryStatus(documentUse(served("release-2", 2, "equivalent"), served("release-3", 3, "current")))).toBe("equivalent");
    expect(languagePackSummaryStatus(documentUse(served("release-3", 3, "current")))).toBe("current");
  });
});
