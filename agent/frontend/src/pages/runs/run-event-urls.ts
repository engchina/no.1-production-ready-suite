// Run のイベントの購読の URL（SSE / WebSocket）。配信の基点（`/agent/` など。#1316）を付ける。
import { APP_BASE_PATH, appPath, appWebSocketUrl } from "../../lib/base-path";

/** SSE（EventSource）の URL。同じ origin の path（`/<base>api/runs/{id}/events?follow=true`）。 */
export function runEventStreamUrl(runId: string, base: string = APP_BASE_PATH): string {
  return appPath(`/api/runs/${encodeURIComponent(runId)}/events?follow=true`, base);
}

/** WebSocket の URL（`ws(s)://host/<base>api/runs/{id}/events/ws?...`）。 */
export function runEventWebSocketUrl(
  runId: string,
  afterEventId: string | null = null,
  location: Pick<Location, "protocol" | "host"> = window.location,
  base: string = APP_BASE_PATH
): string {
  const params = new URLSearchParams({ heartbeat_interval_seconds: "1" });
  if (afterEventId) {
    params.set("after_event_id", afterEventId);
  }
  return appWebSocketUrl(`/api/runs/${runId}/events/ws?${params.toString()}`, location, base);
}
