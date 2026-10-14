import { API_KEY_PRODUCTS, type ApiKeyProduct, type McpEndpoint } from "./types";

/**
 * 画面に出す MCP の接続先（#1805）。
 *
 * 1 台の Compute に配備した Suite は `/<製品>/` の prefix で同じ origin に並ぶ（`suite-nginx.sh`）。
 * 開いている製品の base が `/<製品>/` なら、3 製品（RAG・NL2SQL・Agent。#1874）の URL を出す。それ以外
 * （開発の dev server など、製品ごとに origin が違う）は開いている製品の URL だけを出す。
 */
export function mcpEndpoints(product: ApiKeyProduct, basePath: string, origin: string): McpEndpoint[] {
  const trimmed = basePath.replace(/^\/+|\/+$/g, "");
  const base = trimmed ? `/${trimmed}/` : "/";
  const trimmedOrigin = origin.replace(/\/+$/, "");
  if (base === `/${product}/`) {
    return API_KEY_PRODUCTS.map((item) => ({ product: item, url: `${trimmedOrigin}/${item}/api/mcp` }));
  }
  return [{ product, url: `${trimmedOrigin}${base}api/mcp` }];
}
