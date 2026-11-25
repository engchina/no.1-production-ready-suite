import { describe, expect, it } from "vitest";

import {
  answerNoticeText,
  internalRecordArtifacts,
  runFileArtifacts,
  userOutputArtifacts,
} from "./run-artifacts";

describe("answerNoticeText", () => {
  it("最終回答の成果物の notice を返す", () => {
    expect(
      answerNoticeText([
        { kind: "answer", content: { text: "回答", notice: "注意書き" } },
      ]),
    ).toBe("注意書き");
  });

  it("注意書きの無い回答・古い Run・回答の無い Run は null", () => {
    expect(answerNoticeText([{ kind: "answer", content: { text: "回答" } }])).toBeNull();
    expect(answerNoticeText([{ kind: "answer", content: { text: "回答", notice: "  " } }])).toBeNull();
  });

  it("最も新しい回答の注意書きを使う", () => {
    expect(
      answerNoticeText([
        { kind: "answer", content: { text: "前の回答", notice: "前の文" } },
        { kind: "answer", content: { text: "新しい回答" } },
      ]),
    ).toBeNull();
  });
});

describe("runFileArtifacts", () => {
  const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const file = (id: string, content: Record<string, unknown>, name = "名前.xlsx") => ({ id, kind: "file", name, content });

  it("ファイルの成果物だけを、ファイル名・形式・大きさにする（元の順序）", () => {
    expect(
      runFileArtifacts([
        { id: "a0", kind: "answer", name: "回答", content: { text: "回答" } },
        file("a1", { file_name: "集計.xlsx", mime_type: XLSX, size_bytes: 2048, sha256: "0".repeat(64) }),
        file("a2", {
          file_name: "資料.pptx",
          mime_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          size_bytes: 10,
        }),
      ]),
    ).toEqual([
      { id: "a1", fileName: "集計.xlsx", type: "xlsx", sizeBytes: 2048 },
      { id: "a2", fileName: "資料.pptx", type: "pptx", sizeBytes: 10 },
    ]);
  });

  it("記録の欠けた成果物は、成果物の名前・その他の形式・大きさなしで出す", () => {
    expect(runFileArtifacts([file("a1", { mime_type: "application/zip", size_bytes: "12" })])).toEqual([
      { id: "a1", fileName: "名前.xlsx", type: "other", sizeBytes: null },
    ]);
  });

  it("ファイルの成果物は利用者の出力で、内部の記録に入れない", () => {
    const artifacts = [file("a1", {}), { id: "a2", kind: "support_task", name: "状態", content: {} }];
    expect(userOutputArtifacts(artifacts).map((item) => item.id)).toEqual(["a1"]);
    expect(internalRecordArtifacts(artifacts).map((item) => item.id)).toEqual(["a2"]);
  });
});
