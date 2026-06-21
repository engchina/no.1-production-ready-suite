export function elapsedSecondsSince(startedAtMs: number, nowMs = Date.now()) {
  return Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
}

export function formatElapsed(ms?: number | null) {
  if (ms === null || ms === undefined) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}秒`;
}
