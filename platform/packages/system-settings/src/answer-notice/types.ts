/** 回答の注意書きの API の型（backend の pr_system_settings.answer_notice と同じ形。#2405）。 */
export interface AnswerNoticeSettingsData {
  enabled: boolean;
  /** 管理者が書いた文（未設定・既定に戻したときは null）。 */
  text: string | null;
  default_text: string;
  /** 回答に付ける文（`text` か既定の文）。無効のときも、有効にしたときの文。 */
  effective_text: string;
  max_chars: number;
  updated_at?: string | null;
}

export interface AnswerNoticeSettingsUpdate {
  enabled: boolean;
  /** null・空・既定の文なら既定に戻す。 */
  text: string | null;
}

/** 製品側の API 関数（fetch wrapper・認証・エラー形式は製品ごとに異なるため注入する）。 */
export interface AnswerNoticeApi {
  get: (options?: { signal?: AbortSignal }) => Promise<AnswerNoticeSettingsData>;
  update: (payload: AnswerNoticeSettingsUpdate) => Promise<AnswerNoticeSettingsData>;
}

/** 3製品で共通の React Query key。 */
export const ANSWER_NOTICE_QUERY_KEY = ["settings", "answer-notice"] as const;
