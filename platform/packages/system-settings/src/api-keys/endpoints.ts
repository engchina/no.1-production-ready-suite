import { API_KEY_PRODUCTS, type ApiKeyProduct, type McpEndpoint, type McpEndpointRow } from "./types";

/**
 * 今開いている画面から見た MCP の接続先（#1805）。
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

/**
 * 画面に出す接続先の行（#1870）: 今開いている画面の URL と、配備の公開の URL
 * （`PLATFORM_PUBLIC_BASE_URL` + `/<製品>/api/mcp`。配備は製品を `/<製品>/` の prefix に置く）。
 * 公開の URL があれば RAG と NL2SQL の両方の行を作る（この画面から分からない製品の URL は null）。
 */
export function mcpEndpointRows(endpoints: McpEndpoint[], publicBaseUrl?: string | null): McpEndpointRow[] {
  const base = (publicBaseUrl ?? "").trim().replace(/\/+$/, "");
  if (!base) {
    return endpoints.map((endpoint) => ({ product: endpoint.product, localUrl: endpoint.url, publicUrl: null }));
  }
  return API_KEY_PRODUCTS.map((product) => ({
    product,
    localUrl: endpoints.find((endpoint) => endpoint.product === product)?.url ?? null,
    publicUrl: `${base}/${product}/api/mcp`,
  }));
}

/** 自作の CA の証明書の場所（Nginx が配る `/platform/ca.crt`）。https の URL のときだけ。 */
export function caCertificateUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" ? `${parsed.origin}/platform/ca.crt` : null;
  } catch {
    return null;
  }
}

/** 登録の例に書く接続先。URL は公開の URL を優先する（無ければこの画面の URL）。 */
function registrationTargets(rows: McpEndpointRow[]): { product: ApiKeyProduct; url: string }[] {
  return rows.flatMap((row) => {
    const url = row.publicUrl ?? row.localUrl;
    return url ? [{ product: row.product, url }] : [];
  });
}

/** 登録の例で保存する CA の証明書の場所。例の URL が https でなければ null（例に CA の行を出さない）。 */
export function mcpRegistrationCaUrl(rows: McpEndpointRow[]): string | null {
  return registrationTargets(rows).map((target) => caCertificateUrl(target.url)).find((value) => value !== null) ?? null;
}

/**
 * Claude Code の登録の例（`platform/docs/mcp-api-keys.md`「Claude Code」と同じ形。#1846・#1870・#4726）。
 *
 * - サーバー名は `rag` / `nl2sql` / `agent`（組み込みのスキルは `rag` / `nl2sql` を使う。`agent` は業務 Agent を呼ぶ）。
 * - `--header` は単一引用符で `${PR_API_KEY}` のまま保存させる（キーの値を `~/.claude.json` に書かない）。
 * - `MCP_TOOL_TIMEOUT` は `rag_search` のサーバーの上限 600 秒より長くする（配備の Nginx の 660 秒と同じ）。
 * - https の URL は、自作の CA の証明書を保存してから `NODE_EXTRA_CA_CERTS` で信頼させる。`-k` は信頼する前の CA を取得するため。
 * - 行末に `#` のコメントを書かない（zsh に貼るとエラーになる）。説明は画面の注記に書く。
 */
export function mcpRegistrationExample(rows: McpEndpointRow[]): string {
  const targets = registrationTargets(rows);
  if (targets.length === 0) return "";
  const caUrl = mcpRegistrationCaUrl(rows);
  const lines = ["export PR_API_KEY='prak_...'"];
  for (const target of targets) {
    lines.push(
      `claude mcp add --scope user --transport http ${target.product} ${target.url} \\`,
      "  --header 'Authorization: Bearer ${PR_API_KEY}'"
    );
  }
  lines.push("export MCP_TOOL_TIMEOUT=660000");
  if (caUrl) {
    lines.push(`curl -sk ${caUrl} -o ~/production-ready-ca.crt`, "export NODE_EXTRA_CA_CERTS=~/production-ready-ca.crt");
  }
  lines.push("claude");
  return lines.join("\n");
}
