import type { HistoryItem, Nl2SqlEngine } from "./types";

const ENGINES: readonly Nl2SqlEngine[] = [
  "auto",
  "select_ai",
  "select_ai_agent",
  "enterprise_ai_direct",
];

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
