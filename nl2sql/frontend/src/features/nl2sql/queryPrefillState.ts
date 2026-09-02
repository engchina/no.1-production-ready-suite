import type { HistoryItem, Nl2SqlEngine } from "./types";

// URL prefill で受け付けるのは画面(EngineSelector)で選べるエンジンだけ。
// "auto" は選択肢に無く、受理すると何も選ばれていない表示のまま実行されてしまう。
const ENGINES: readonly Nl2SqlEngine[] = ["select_ai", "select_ai_agent", "enterprise_ai_direct"];

export interface QueryPrefill {
  question: string;
  engine: Nl2SqlEngine | null;
  profileId: string;
}

export function parseNl2SqlEngine(value: string | null): Nl2SqlEngine | null {
  return ENGINES.includes(value as Nl2SqlEngine) ? (value as Nl2SqlEngine) : null;
}

export function prefillFromSearchParams(params: URLSearchParams): QueryPrefill {
  return {
    question: params.get("question") ?? "",
    engine: parseNl2SqlEngine(params.get("engine")),
    profileId: params.get("profile_id") ?? "",
  };
}

export function historyRerunUrl(item: HistoryItem, basePath = "/query"): string {
  const params = new URLSearchParams();
  params.set("question", item.question);
  params.set("engine", item.engine);
  if (item.profile_id) params.set("profile_id", item.profile_id);
  return `${basePath}?${params.toString()}`;
}
