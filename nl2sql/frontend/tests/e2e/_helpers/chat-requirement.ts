/**
 * チャットの AI要件確認（#1442）の e2e の応答。チャットで質問を送ると、SQL を作る前に
 * `POST /api/nl2sql/query-sessions`（`chat: true`）で要件を検査する。
 *
 * 既定は「確認が要らない」（`clear`）: 質問を出さずに反映（`/apply`）してから SQL の生成のジョブを投入する。
 * 確認の流れを確かめる spec は `create` に最初の段階、`answers` に回答ごとの次の段階を渡す。
 */
import type { Page, Request } from "@playwright/test";

import { requirementSessionData, type RequirementStage } from "./requirement-session";

export interface ChatRequirementMock {
  /** `POST /query-sessions` の本文（送った順）。 */
  creates: Record<string, unknown>[];
  /** `POST /query-sessions` の Idempotency-Key（送った順）。 */
  createKeys: string[];
  answers: Record<string, unknown>[];
  applies: number;
  cancels: number;
  /** 今の session の段階。 */
  stage: RequirementStage | "cancelled";
}

export interface ChatRequirementMockOptions {
  /** 作成の応答の段階（既定は確認が要らない `clear`）。 */
  create?: RequirementStage;
  /** 回答のたびに進む段階（足りなければ最後の段階のまま）。 */
  answers?: RequirementStage[];
  /** 作成の応答を遅らせる（要件の検査の経過を確かめる）。 */
  createDelayMs?: number;
  /** 作成を失敗させる（HTTP status と本文の error）。 */
  createFailure?: { status: number; error: string };
}

function path(request: Request) {
  return new URL(request.url()).pathname;
}

export async function mockChatRequirement(
  page: Page,
  options: ChatRequirementMockOptions = {},
): Promise<ChatRequirementMock> {
  const state: ChatRequirementMock = {
    creates: [],
    createKeys: [],
    answers: [],
    applies: 0,
    cancels: 0,
    stage: options.create ?? "clear",
  };
  let version = 1;
  let answerIndex = 0;
  const data = () => {
    if (state.stage === "cancelled") {
      const base = requirementSessionData("needs_answer", version);
      return { ...base, session: { ...base.session, status: "cancelled" }, requirement_state: null };
    }
    return requirementSessionData(state.stage, version);
  };
  await page.route(
    (url) => url.pathname.startsWith("/api/nl2sql/query-sessions"),
    async (route) => {
      const request = route.request();
      const pathname = path(request);
      if (pathname === "/api/nl2sql/query-sessions" && request.method() === "POST") {
        state.creates.push(request.postDataJSON());
        state.createKeys.push((await request.headerValue("idempotency-key")) ?? "");
        if (options.createDelayMs) await new Promise((resolve) => setTimeout(resolve, options.createDelayMs));
        if (options.createFailure) {
          return route.fulfill({
            status: options.createFailure.status,
            json: { error: options.createFailure.error },
          });
        }
        state.stage = options.create ?? "clear";
        version = 1;
        answerIndex = 0;
        return route.fulfill({ json: { data: data() } });
      }
      if (pathname.endsWith("/clarification-answers")) {
        state.answers.push(request.postDataJSON());
        state.stage = options.answers?.[answerIndex] ?? options.answers?.at(-1) ?? "ready";
        answerIndex += 1;
        version += 1;
        return route.fulfill({ json: { data: data() } });
      }
      if (pathname.endsWith("/apply")) {
        state.applies += 1;
        state.stage = "applied";
        return route.fulfill({ json: { data: data() } });
      }
      if (pathname.endsWith("/cancel")) {
        state.cancels += 1;
        state.stage = "cancelled";
        return route.fulfill({ json: { data: data() } });
      }
      return route.fulfill({ json: { data: data() } });
    },
  );
  return state;
}
