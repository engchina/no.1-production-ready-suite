import { describe, expect, it } from "vitest";

import type { ExternalParserConnectionData } from "@/lib/api";

import {
  connectionFormFromSettings,
  connectionFormIsDirty,
  connectionUpdatePayload,
  validateConnectionForm,
} from "./ExternalConnectionSheet.logic";

const saved: ExternalParserConnectionData = {
  backend: "dots_ocr",
  protocol: "openai_chat_completions",
  endpoint: "https://parser.example.com/v1",
  model: "served-model",
  api_key_configured: true,
  configured: true,
};

// #4740: Dots.OCR の接続設定は右のパネルで編集し、パネルの中で保存する。
describe("外部 GPU 解析エンジンの接続の編集", () => {
  it("保存済みの値を初期値にし、API key は空のまま始める", () => {
    expect(connectionFormFromSettings("dots_ocr", saved)).toEqual({
      endpoint: "https://parser.example.com/v1",
      model: "served-model",
      api_key: "",
      clear_api_key: false,
    });
  });

  it("未設定は既定の Model を初期値にし、未編集なら変更なしと判定する", () => {
    const form = connectionFormFromSettings("dots_ocr", undefined);
    expect(form.model).toBe("rednote-hilab/dots.mocr");
    expect(connectionFormIsDirty("dots_ocr", form, undefined)).toBe(false);
  });

  it("前後の空白だけの違いは変更に数えない", () => {
    const form = { ...connectionFormFromSettings("dots_ocr", saved), endpoint: " https://parser.example.com/v1 " };
    expect(connectionFormIsDirty("dots_ocr", form, saved)).toBe(false);
  });

  it("欄の値・新しい API key・保存済みのキーの削除を変更と判定する", () => {
    const base = connectionFormFromSettings("dots_ocr", saved);
    expect(connectionFormIsDirty("dots_ocr", base, saved)).toBe(false);
    expect(connectionFormIsDirty("dots_ocr", { ...base, endpoint: "https://other.example.com" }, saved)).toBe(true);
    expect(connectionFormIsDirty("dots_ocr", { ...base, model: "other" }, saved)).toBe(true);
    expect(connectionFormIsDirty("dots_ocr", { ...base, api_key: "synthetic" }, saved)).toBe(true);
    expect(connectionFormIsDirty("dots_ocr", { ...base, clear_api_key: true }, saved)).toBe(true);
  });

  it("API key は入力したときだけ送り、削除は明示したときだけ送る", () => {
    const base = connectionFormFromSettings("dots_ocr", saved);
    expect(connectionUpdatePayload("dots_ocr", base)).toEqual({
      backend: "dots_ocr",
      endpoint: "https://parser.example.com/v1",
      model: "served-model",
    });
    expect(connectionUpdatePayload("dots_ocr", { ...base, api_key: "synthetic" })).toMatchObject({ api_key: "synthetic" });
    expect(connectionUpdatePayload("dots_ocr", { ...base, clear_api_key: true })).toMatchObject({ clear_api_key: true });
  });

  it("送る値の前後の空白を除く", () => {
    const base = connectionFormFromSettings("dots_ocr", saved);
    expect(connectionUpdatePayload("dots_ocr", { ...base, endpoint: " https://a.example.com ", model: " m " })).toEqual({
      backend: "dots_ocr",
      endpoint: "https://a.example.com",
      model: "m",
    });
  });

  it("Endpoint の形と Model の必須を検証し、欄の id と対応する key で返す", () => {
    const base = connectionFormFromSettings("dots_ocr", saved);
    expect(validateConnectionForm("dots_ocr", base)).toEqual({});
    expect(Object.keys(validateConnectionForm("dots_ocr", { ...base, endpoint: "ftp://a.example.com" }))).toEqual(["dots_ocr.endpoint"]);
    expect(Object.keys(validateConnectionForm("dots_ocr", { ...base, endpoint: "http:a.example.com" }))).toEqual(["dots_ocr.endpoint"]);
    expect(Object.keys(validateConnectionForm("dots_ocr", { ...base, endpoint: "https://u:p@a.example.com" }))).toEqual(["dots_ocr.endpoint"]);
    expect(Object.keys(validateConnectionForm("dots_ocr", { ...base, model: " " }))).toEqual(["dots_ocr.model"]);
    // Endpoint が空なら Model は要らない（未設定に戻せる）。
    expect(validateConnectionForm("dots_ocr", { ...base, endpoint: "", model: "" })).toEqual({});
  });
});
