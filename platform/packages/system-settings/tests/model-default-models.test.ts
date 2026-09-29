import { describe, expect, it } from "vitest";

import {
  followModelChange,
  textModelOptions,
  validateDefaultModels,
  visionModelOptions,
} from "../src/model/defaultModels";
import type { EnterpriseAiConfiguredModel } from "../src/model/types";

const models: EnterpriseAiConfiguredModel[] = [
  { model_id: "llm-a", display_name: "標準", vision_enabled: false },
  { model_id: "vlm-b", display_name: "", vision_enabled: true },
  { model_id: "  ", display_name: "未入力", vision_enabled: true },
];

describe("validateDefaultModels（#499。backend の validate_default_models と同じ規則）", () => {
  it("正しい選択と、テキスト未選択（既定の Vision モデルを使う）はエラーにしない", () => {
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "llm-a",
        default_vision_model_id: "vlm-b",
      }),
    ).toEqual({});
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "",
        default_vision_model_id: "vlm-b",
      }),
    ).toEqual({});
    // テキストに Vision 対応のモデルを選んでもよい。
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "vlm-b",
        default_vision_model_id: "vlm-b",
      }),
    ).toEqual({});
  });

  it("モデルを登録していなければ既定の Vision モデルは必須ではない", () => {
    expect(
      validateDefaultModels({
        models: [{ model_id: "", display_name: "", vision_enabled: false }],
        default_text_model_id: "",
        default_vision_model_id: "",
      }),
    ).toEqual({});
  });

  it("Vision 対応のモデルがない・未選択・削除・Vision 対応でないをエラーにする", () => {
    const textOnly = [models[0]!];
    expect(
      validateDefaultModels({
        models: textOnly,
        default_text_model_id: "",
        default_vision_model_id: "",
      }).default_vision_model_id,
    ).toContain("画像入力（Vision）に対応したモデルがありません");
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "",
        default_vision_model_id: "",
      }).default_vision_model_id,
    ).toBe("既定の Vision モデルを選んでください。");
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "",
        default_vision_model_id: "gone",
      }).default_vision_model_id,
    ).toContain("「gone」は登録モデルにありません");
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "",
        default_vision_model_id: "llm-a",
      }).default_vision_model_id,
    ).toContain("「llm-a」は画像入力（Vision）に対応していません");
    expect(
      validateDefaultModels({
        models,
        default_text_model_id: "gone",
        default_vision_model_id: "vlm-b",
      }),
    ).toEqual({
      default_text_model_id: expect.stringContaining("「gone」は登録モデルにありません"),
    });
  });
});

describe("既定のモデルの選択肢", () => {
  it("Vision は画像入力に対応した登録モデルだけ、テキストは未選択 + 全登録モデル", () => {
    expect(visionModelOptions(models)).toEqual([{ value: "vlm-b", label: "vlm-b" }]);
    expect(textModelOptions(models)).toEqual([
      { value: "", label: "既定の Vision モデルを使う" },
      { value: "llm-a", label: "標準", description: "llm-a" },
      { value: "vlm-b", label: "vlm-b" },
    ]);
  });
});

describe("followModelChange", () => {
  const defaults = { default_text_model_id: "llm-a", default_vision_model_id: "vlm-b" };

  it("既定に選んだモデルの ID を書き換えたら追従する（消し切ったときは追従しない）", () => {
    expect(
      followModelChange(defaults, models[1], { ...models[1]!, model_id: "vlm-c" }),
    ).toEqual({ default_text_model_id: "llm-a", default_vision_model_id: "vlm-c" });
    expect(followModelChange(defaults, models[0], { ...models[0]!, model_id: "" })).toEqual(
      defaults,
    );
  });

  it("Vision 対応をオンにしたとき、既定の Vision モデルが未選択ならそのモデルを選ぶ", () => {
    const empty = { default_text_model_id: "", default_vision_model_id: "" };
    expect(
      followModelChange(empty, models[0], { ...models[0]!, vision_enabled: true }),
    ).toEqual({ default_text_model_id: "", default_vision_model_id: "llm-a" });
    // 既に選んでいれば変えない。Vision 対応のオフでも変えない（エラーで案内する）。
    expect(
      followModelChange(defaults, models[0], { ...models[0]!, vision_enabled: true }),
    ).toEqual(defaults);
    expect(
      followModelChange(defaults, models[1], { ...models[1]!, vision_enabled: false }),
    ).toEqual(defaults);
  });
});
