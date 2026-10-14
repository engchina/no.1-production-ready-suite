import { ApiKeysAdminPage, MyApiKeysPage, mcpEndpoints } from "@production-ready/system-settings";

import { APP_BASE_PATH } from "@/lib/base-path";
import { apiKeysAdminApi, myApiKeysApi } from "./api";

/**
 * 自分の API キー（アカウントのメニューから開く。外部の MCP クライアント向け。#1805）。
 * 画面の実体は platform の共有パッケージ。ログインしている全員が使える。
 */
export function AccountApiKeysPage() {
  return (
    <MyApiKeysPage
      api={myApiKeysApi}
      endpoints={mcpEndpoints("nl2sql", APP_BASE_PATH, window.location.origin)}
    />
  );
}

/** 全員の API キー（「ユーザーとロール」の管理の画面。#1805）。 */
export function SecurityApiKeysPage() {
  return <ApiKeysAdminPage api={apiKeysAdminApi} />;
}
