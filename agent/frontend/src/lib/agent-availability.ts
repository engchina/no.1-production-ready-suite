import type { AgentProfile } from "@/lib/api";

/**
 * 利用者の Run（チャット・自動実行）で使える業務 Agent か（#792）。
 * 利用者の Run は公開中の版で実行する（#770）ため、無効・移行が要る・公開した版が無い業務 Agent は使えない。
 * 品質評価は下書きでも評価できるので、この判定を使わない。
 */
export function isRunnableAgent(agent: Pick<AgentProfile, "enabled" | "migration_required" | "published_version">): boolean {
  return agent.enabled && !agent.migration_required && agent.published_version !== null;
}
