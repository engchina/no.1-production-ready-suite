import { processFailureDetails } from "@production-ready/ui";
import { describe, expect, it } from "vitest";

import { chatAnswerFailure, failedStageLabel, runFailure, storageFailureNotice } from "./process-failure";

describe("RAG の失敗を共通の失敗の表示に写す（Issue 2342）", () => {
  it("チャットの失敗は止まった工程を 1 文目に入れ、backend の文を原因、エラーの種類・コード・時刻を詳細にする", () => {
    const failure = chatAnswerFailure({
      message: "検索処理中にエラーが発生しました。",
      traceId: "trace-1",
      errorType: "DatabaseError",
      errorCode: "ORA-03146",
      stage: "文書検索",
      occurredAt: "2026-10-18T06:30:41Z",
    });
    expect(failure.summary).toBe("「検索文から根拠の候補を集める」で処理が止まりました。");
    expect(failure.cause).toBe("検索処理中にエラーが発生しました。");
    expect(failure.nextAction).toContain("トレース ID を添えて管理者に連絡");
    expect(failure).toMatchObject({ failedStep: "検索文から根拠の候補を集める", errorCode: "ORA-03146", traceId: "trace-1" });
  });

  it("工程の分からない失敗（保存済みの ERROR の回答）は定型の 1 文目にする", () => {
    expect(chatAnswerFailure({ message: "中断しました。", traceId: "t" }).summary).toBe("回答を作成できませんでした。");
  });

  it("止まった段はパイプラインの工程・回答フローの工程のどちらの名前でも引く", () => {
    expect(failedStageLabel("answer_record_save")).toBe("質問と回答と根拠の記録を保存する");
    expect(failedStageLabel("answer_step:文書検索（2回目）")).toBe("検索文から根拠の候補を集める（2 回目）");
    expect(failedStageLabel("文書検索")).toBe("検索文から根拠の候補を集める");
  });

  it("保存できなかった記録は、保存できなかったものごとの影響とコードを warning にする", () => {
    expect(storageFailureNotice([], "t")).toBeNull();
    const notice = storageFailureNotice(
      [
        { step: "answer_record_save", error_type: "DatabaseError", error_code: "ORA-03146" },
        { step: "chat_message_save", error_type: "DatabaseError", error_code: "ORA-40441" },
      ],
      "trace-1"
    );
    expect(notice?.summary).toBe("回答は表示していますが、一部を保存できませんでした。");
    expect(notice?.cause?.split("\n")).toHaveLength(2);
    expect(notice?.errorCode).toBe("ORA-03146・ORA-40441");
    expect(notice?.failedStep).toBe("質問と回答と根拠の記録を保存する・回答を会話に保存する");
  });

  it("実行の詳細は失敗した実行だけ、チャットと同じ 1 文目にする", () => {
    expect(runFailure({ status: "success", error_stage: null, error_type: null, error_code: null })).toBeNull();
    expect(
      runFailure({ status: "error", error_stage: "retrieval", error_type: "DatabaseError", error_code: "ORA-03146" })
    ).toMatchObject({ summary: "「文書から根拠の候補を検索する」で処理が止まりました。", errorCode: "ORA-03146", failedStep: "文書から根拠の候補を検索する" });
  });
});


describe("安全な実行の失敗分類（Issue 4412）", () => {
  const base = { status: "error" as const, error_stage: "retrieval", error_type: "RuntimeError", error_code: null };
  it("古い記録と分類できなかった記録を区別する", () => {
    expect(runFailure(base)?.cause).toBe("原因の詳細は記録されていません。");
    expect(runFailure({ ...base, failure_diagnosis: { kind: "unknown" } })?.cause).toContain("特定できませんでした");
  });
  it.each([
    ["dns", "名前を解決"], ["refused", "接続を拒否"], ["unreachable", "通信経路"], ["reset", "途中で切断"],
  ] as const)("記録された %s だけを説明する", (cause, expected) => {
    expect(runFailure({ ...base, failure_diagnosis: { kind: "network", network_failure_cause: cause } })?.cause).toContain(expected);
  });
  it.each([
    "rate_limited", "quota_exhausted", "unavailable", "server_error", "not_supported", "network", "tls", "timeout",
    "authentication", "permission", "not_found", "bad_request", "too_large", "safety", "invalid_output", "truncated", "unknown",
  ] as const)("分類 %s に原因と次の操作が用意されている", (kind) => {
    const result = runFailure({ ...base, failure_diagnosis: { kind } });
    expect(result?.cause).toBeTruthy();
    expect(result?.nextAction).toBeTruthy();
    expect(result?.cause).not.toContain("failure.diagnosis.");
    expect(result?.nextAction).not.toContain("failure.diagnosis.");
    expect(result?.errorType).toBe("RuntimeError");
  });
  it("型だけで原因を推定せず、TLS とネットワーク原因を混同しない", () => {
    expect(runFailure({ ...base, error_type: "ConnectError" })?.cause).toContain("記録されていません");
    expect(runFailure({ ...base, failure_diagnosis: { kind: "tls", network_failure_cause: "dns" } })?.cause).toContain("TLS");
  });
});


describe("履歴の停止工程の優先順（Issue 4436）", () => {
  it.each([
    [{ kind: "network", stage: "回答の生成" }, "retrieval", "回答の生成"],
    [{ kind: "network", stage: null }, "retrieval", "文書から根拠の候補を検索する"],
    [null, "retrieval", "文書から根拠の候補を検索する"],
    [null, null, null],
    [{ kind: "network", stage: null }, null, null],
  ] as const)("診断の工程→旧工程の順で一つに解決し、欠測は補完しない", (diagnosis, outer, expected) => {
    const result = runFailure({ status: "error", error_stage: outer, error_type: "RuntimeError", error_code: null, failure_diagnosis: diagnosis });
    expect(result?.failedStep).toBe(expected);
    expect(result?.summary).toBe(expected ? `「${expected}」で処理が止まりました。` : "回答を作成できませんでした。");
  });
});


describe("失敗の詳細にも保存済みの安全な原因を示す（Issue 4508）", () => {
  it.each([
    [undefined, "記録されていません"],
    [{ kind: "unknown" as const }, "特定できませんでした"],
    [{ kind: "network" as const, network_failure_cause: "refused" as const }, "接続を拒否"],
  ])("原因の本文と詳細が一致する: %j", (diagnosis, expected) => {
    const failure = runFailure({ status: "error", error_stage: null, error_type: "RuntimeError", error_code: null, failure_diagnosis: diagnosis });
    expect(failure).not.toBeNull();
    const details = processFailureDetails(failure!);
    expect(details.find(entry => entry.label === "原因")?.value).toBe(failure?.cause);
    expect(details.find(entry => entry.label === "原因")?.value).toContain(expected);
    expect(details.find(entry => entry.label === "エラーの種類")?.value).toBe("RuntimeError");
  });
});
