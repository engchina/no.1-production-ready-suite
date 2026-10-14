import { ConfirmProvider } from "@production-ready/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  API_KEY_ADMIN_NAV_ITEM,
  API_KEY_PATHS,
  API_KEYS_QUERY_KEY,
  API_KEYS_ADMIN_QUERY_KEY,
  ApiKeysAdminPage,
  MyApiKeysPage,
  mcpEndpoints,
  type ApiKey,
} from "../src";

const KEY: ApiKey = {
  id: "0123456789abcdef",
  name: "会社の PC の Claude Code",
  token_prefix: "prak_0123456789abcdef_AbCd",
  products: ["rag", "nl2sql"],
  owner_user_uuid: "u-1",
  owner_display_name: "山田",
  created_at: "2026-10-14T00:00:00Z",
  expires_at: null,
  last_used_at: null,
  expired: false,
};

function render(node: ReactNode, data?: { mine?: ApiKey[]; all?: ApiKey[] }): string {
  const client = new QueryClient();
  if (data?.mine) client.setQueryData(API_KEYS_QUERY_KEY, { keys: data.mine });
  if (data?.all) client.setQueryData(API_KEYS_ADMIN_QUERY_KEY, { keys: data.all });
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <ConfirmProvider>{node}</ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

const pending = () => new Promise<never>(() => undefined);
const myApi = { list: pending, create: pending, revoke: pending };
const adminApi = { list: pending, revoke: pending };

describe("mcpEndpoints（#1805）", () => {
  it("Suite の配備（/<製品>/）では RAG と NL2SQL の両方の URL を出す", () => {
    expect(mcpEndpoints("rag", "/rag/", "https://suite.example.com")).toEqual([
      { product: "rag", url: "https://suite.example.com/rag/api/mcp" },
      { product: "nl2sql", url: "https://suite.example.com/nl2sql/api/mcp" },
    ]);
    expect(mcpEndpoints("nl2sql", "/nl2sql", "https://suite.example.com/")).toHaveLength(2);
  });

  it("開発の dev server（base が /）では開いている製品の URL だけ", () => {
    expect(mcpEndpoints("nl2sql", "/", "http://localhost:5174")).toEqual([
      { product: "nl2sql", url: "http://localhost:5174/api/mcp" },
    ]);
  });
});

describe("API キーの画面（#1805）", () => {
  it("パスとナビの項目は 2 つの画面で別", () => {
    expect(API_KEY_PATHS).toEqual({ mine: "/account/api-keys", admin: "/settings/security/api-keys" });
    expect(API_KEY_ADMIN_NAV_ITEM.href).toBe(API_KEY_PATHS.admin);
    expect(API_KEY_ADMIN_NAV_ITEM.labelKey).toBe("nav.securityApiKeys");
  });

  it("自分の画面は接続先・発行・自分のキーの一覧を出し、秘密は出さない", () => {
    const html = render(
      <MyApiKeysPage
        api={myApi}
        product="rag"
        endpoints={mcpEndpoints("rag", "/rag/", "https://suite.example.com")}
      />,
      { mine: [KEY] }
    );
    expect(html).toContain("https://suite.example.com/rag/api/mcp");
    expect(html).toContain("https://suite.example.com/nl2sql/api/mcp");
    expect(html).toContain("API キーを発行");
    expect(html).toContain("会社の PC の Claude Code");
    expect(html).toContain("prak_0123456789abcdef_AbCd…");
    expect(html).toContain("RAG・NL2SQL");
    expect(html).toContain("未使用");
    // 自分の画面には利用者の列を出さない。
    expect(html).not.toContain("利用者</th>");
    // 発行の既定の製品は開いている製品だけ。
    expect(html).toMatch(/checked="" value="rag"/);
    expect(html).not.toMatch(/checked="" value="nl2sql"/);
  });

  it("上限の件数に達したら発行を止めて案内する", () => {
    const keys = Array.from({ length: 10 }, (_, index) => ({ ...KEY, id: `key-${index}` }));
    const html = render(<MyApiKeysPage api={myApi} product="nl2sql" endpoints={[]} />, { mine: keys });
    expect(html).toContain("API キーは 1 人 10 件までです。");
  });

  it("読み込み中は経過時間の表示と表の形の Skeleton を出す", () => {
    const html = render(<MyApiKeysPage api={myApi} product="rag" endpoints={[]} />);
    expect(html).toContain('data-testid="api-keys-loading"');
    expect(html).toContain("API キーを読み込んでいます");
  });

  it("管理の画面は全員のキーを利用者付きで出し、発行の欄は持たない", () => {
    const html = render(<ApiKeysAdminPage api={adminApi} />, {
      all: [KEY, { ...KEY, id: "x", owner_display_name: "", expired: true }],
    });
    expect(html).toContain("全員の API キー");
    expect(html).toContain("山田");
    expect(html).toContain("削除されたユーザー");
    expect(html).toContain("期限切れ");
    expect(html).not.toContain("API キーを発行</");
  });
});
