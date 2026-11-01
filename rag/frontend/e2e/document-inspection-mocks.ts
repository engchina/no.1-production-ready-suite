/** 既存の合成文書fixtureを、閲覧用の部分取得APIの応答として返す。 */
import type { Route } from "@playwright/test";

export async function fulfillDocumentInspection(route: Route, options: Parameters<Route["fulfill"]>[0]) {
  const url = new URL(route.request().url());
  const envelope = options?.json as {data?: Record<string, unknown> | unknown[]} | undefined;
  if (envelope?.data && url.pathname.endsWith("/chunk-window") && Array.isArray(envelope.data)) {
    const offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? 25);
    const page = Number(url.searchParams.get("page"));
    const id = url.searchParams.get("chunk_id");
    const rows = (envelope.data as Record<string, unknown>[]).filter(c => (!id || c.chunk_id === id) && (!page || Number(c.page_start) <= page && Number(c.page_end) >= page));
    return route.fulfill({...options, json: {...envelope, data: {items: rows.slice(offset, offset + limit), total: rows.length, offset, limit}}});
  }
  if (envelope?.data && url.pathname.endsWith("/extraction-view") && !Array.isArray(envelope.data)) {
    const data = envelope.data;
    const payload = (data.payload ?? data) as Record<string, unknown>;
    const mode = url.searchParams.get("mode"), page = Number(url.searchParams.get("page"));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const elements = ((payload.elements ?? []) as Record<string, unknown>[]).filter(e => !page || Number(e.page_number) === page);
    const text = page ? elements.map(e=>e.text ?? "").join("\n\n") : String(payload.display_text ?? payload.raw_text ?? "");
    const limit = mode === "text" ? 8192 : 25;
    return route.fulfill({...options, json: {...envelope, data: {...data, content: "", offset, limit,
      total: mode === "text" ? text.length : elements.length,
      payload: {...payload, raw_text: mode === "text" ? text.slice(offset,offset+limit) : "", display_text: mode === "text" ? text.slice(offset,offset+limit) : "", elements: mode === "structure" ? elements.slice(offset,offset+limit) : []},
    }}});
  }
  return route.fulfill(options);
}
