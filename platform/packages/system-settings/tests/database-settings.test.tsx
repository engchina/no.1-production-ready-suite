import { ConfirmProvider } from "@engchina/production-ready-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { DatabaseSettingsPage, type DatabaseSettingsApi } from "../src";

const pending = () => new Promise<never>(() => undefined);
const api: DatabaseSettingsApi = {
  getDatabaseSettings: pending,
  updateDatabaseSettings: pending,
  uploadDatabaseWallet: pending,
  downloadDatabaseWallet: pending,
  testDatabaseSettings: pending,
  getAdbInfo: pending,
  updateAdbSettings: pending,
  startAdb: pending,
  stopAdb: pending,
};

function render(node: React.ReactNode) {
  return renderToStaticMarkup(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ConfirmProvider>{node}</ConfirmProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("DatabaseSettingsPage", () => {
  it("読み込み中は状態表示を出す", () => {
    const html = render(<DatabaseSettingsPage api={api} />);
    expect(html).toContain('data-testid="settings-database-loading"');
    expect(html).toContain("データベース設定を読み込んでいます。");
  });

  it("製品の読み込み中表示に差し替えられる", () => {
    const html = render(
      <DatabaseSettingsPage api={api} loadingFallback={<p data-testid="product-loading" />} />,
    );
    expect(html).toContain('data-testid="product-loading"');
    expect(html).not.toContain('data-testid="settings-database-loading"');
  });
});
