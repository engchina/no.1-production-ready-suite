import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

/**
 * API 境界から認証状態の変化を画面へ伝える window イベント（NL2SQL の名前をそのまま使う。#220）。
 * 製品の API client は応答ごとに `notifyAuthStatus` を呼ぶ。401 は `AuthProvider` が未認証に戻し、
 * 403 は `useForbiddenRedirect` を使う画面が権限なしの画面へ移す。
 */
export const AUTH_UNAUTHORIZED_EVENT = "app-auth-unauthorized";
export const AUTH_FORBIDDEN_EVENT = "app-auth-forbidden";

export interface AuthForbiddenDetail {
  requestId?: string;
}

/** HTTP status が 401 / 403 のとき、対応する window イベントを発火する。 */
export function notifyAuthStatus(status: number, requestId?: string): void {
  if (typeof window === "undefined") return;
  if (status === 401) window.dispatchEvent(new CustomEvent(AUTH_UNAUTHORIZED_EVENT));
  if (status === 403) {
    window.dispatchEvent(
      new CustomEvent<AuthForbiddenDetail>(AUTH_FORBIDDEN_EVENT, {
        detail: { requestId: requestId || undefined },
      }),
    );
  }
}

/** 403 のイベントを受けたら、request ID を state に載せて権限なしの画面へ移す（履歴は置き換える）。 */
export function useForbiddenRedirect(forbiddenPath: string): void {
  const navigate = useNavigate();
  useEffect(() => {
    const handleForbidden = (event: Event) => {
      const detail = (event as CustomEvent<AuthForbiddenDetail | undefined>).detail;
      navigate(forbiddenPath, {
        replace: true,
        state: { requestId: detail?.requestId },
      });
    };
    window.addEventListener(AUTH_FORBIDDEN_EVENT, handleForbidden);
    return () => window.removeEventListener(AUTH_FORBIDDEN_EVENT, handleForbidden);
  }, [forbiddenPath, navigate]);
}
