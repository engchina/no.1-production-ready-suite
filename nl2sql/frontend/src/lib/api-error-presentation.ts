import { ApiError, isTransportError } from "./api.ts";
import { t } from "./i18n.ts";

export interface ApiErrorDetail {
  label: string;
  value: string;
}

/** 失敗の面に出す情報（UX 契約 messaging.md §10.3。#900）。 */
export interface ApiErrorPresentation {
  /** 何が起きたか（1 文目）。 */
  summary: string;
  /** 次にできること。 */
  nextAction?: string;
  /** 「詳細」に畳む技術的な情報（要求・上限・HTTP ステータス・エラーコード・request ID・元の文）。 */
  details: ApiErrorDetail[];
}

function detail(label: string, value: string | number | undefined | null): ApiErrorDetail[] {
  return value === undefined || value === null || value === ""
    ? []
    : [{ label: t(label), value: String(value) }];
}

/**
 * API の失敗を、利用者の言葉の要約・次の操作・技術的な詳細に分ける。
 *
 * 応答が届かなかった失敗（timeout・通信断）は `ApiTransportError` の日本語の文を使い、
 * ブラウザの英語の文（`signal timed out` 等）は「詳細」の「元のメッセージ」にだけ出す。
 * backend の失敗（`ApiError`）は request ID を本文に重ねず「詳細」に出す。
 */
export function presentApiError(error: unknown, fallback: string): ApiErrorPresentation {
  if (isTransportError(error)) {
    return {
      summary: error.summary,
      nextAction: error.nextAction,
      details: [
        ...detail(
          "api.error.details.request",
          [error.method, error.path].filter(Boolean).join(" "),
        ),
        ...detail(
          "api.error.details.limit",
          error.timeoutMs
            ? t("api.error.details.limitSeconds", {
                seconds: Math.ceil(error.timeoutMs / 1000),
              })
            : undefined,
        ),
        ...detail("api.error.details.errorType", error.causeName),
        ...detail("api.error.details.rawMessage", error.causeMessage),
      ],
    };
  }
  if (error instanceof ApiError) {
    return {
      summary: error.baseMessages.join("\n") || fallback,
      details: [
        ...detail("api.error.details.status", error.status),
        ...detail("api.error.details.errorCode", error.problem?.code || error.errorCode),
        ...detail("common.requestId", error.problem?.request_id || error.requestId),
      ],
    };
  }
  if (error instanceof Error && error.message.trim()) {
    return { summary: error.message, details: [] };
  }
  return { summary: fallback, details: [] };
}
