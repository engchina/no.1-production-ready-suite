// @vitest-environment happy-dom
import { ConfirmProvider } from "@engchina/production-ready-ui";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  RoleManagementPage,
  UserManagementPage,
  type RoleManagementApi,
  type SecurityRole,
  type UserManagementApi,
} from "../src";

// #531: 必須の欄は共有の「必須」タグと aria-required で示し、未入力は送信前に欄の下へ
// 「〇〇を入力してください。」を出して、最初のエラーの欄へフォーカスする（ブラウザの検証の吹き出しに任せない）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const pending = () => new Promise<never>(() => undefined);

const role: SecurityRole = {
  role_id: "role-1",
  role_code: "SALES",
  display_name: "営業",
  description: "",
  is_built_in: false,
  archived: false,
  version: 1,
};

async function renderPage(node: ReactNode) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <ConfirmProvider>{node}</ConfirmProvider>
      </MemoryRouter>,
    );
  });
  // 一覧の読み込み（Promise の解決）を反映する。
  await act(async () => {
    await Promise.resolve();
  });
}

function buttonByText(text: string): HTMLButtonElement {
  const button = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (candidate) => candidate.textContent?.trim() === text,
  );
  if (!button) throw new Error(`button not found: ${text}`);
  return button;
}

async function submitForm(labelledBy: string) {
  const form = host.querySelector<HTMLFormElement>(`form[aria-labelledby="${labelledBy}"]`);
  if (!form) throw new Error(`form not found: ${labelledBy}`);
  expect(form.noValidate).toBe(true);
  await act(async () => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  // フォーカスは commit の後の effect で移す（useFocusAfterCommit）。
  await act(async () => {
    await Promise.resolve();
  });
}

describe("RoleManagementPage の必須の欄", () => {
  it("未入力で送信すると欄の下にエラーを出し、ロールコードへフォーカスする", async () => {
    const createRole = vi.fn(pending);
    const api: RoleManagementApi = {
      roles: async () => [],
      createRole,
      updateRole: pending,
      archiveRole: pending,
      restoreRole: pending,
      deleteRole: pending,
    };
    await renderPage(<RoleManagementPage api={api} canManage />);
    await act(async () => buttonByText("新規作成").click());

    const code = host.querySelector<HTMLInputElement>("#security-role-code");
    expect(code?.getAttribute("aria-invalid")).toBeNull();
    // 必須のタグは共有の FieldLabel が出し、入力側の required で伝えるので読み上げない。
    expect(host.querySelector('label[for="security-role-code"]')?.textContent).toBe("ロールコード必須");
    expect(host.querySelector('label[for="security-role-name"]')?.textContent).toBe("ロール名必須");

    await submitForm("security-roles-form-heading");

    expect(host.querySelector("#security-role-code-error")?.textContent).toContain(
      "ロールコードを入力してください。",
    );
    expect(host.querySelector("#security-role-name-error")?.textContent).toContain(
      "ロール名を入力してください。",
    );
    expect(document.activeElement?.id).toBe("security-role-code");
    expect(createRole).not.toHaveBeenCalled();
  });
});

describe("UserManagementPage の必須の欄", () => {
  it("未入力で送信すると欄の下にエラーを出し、ログインユーザーIDへフォーカスする", async () => {
    const createUser = vi.fn(pending);
    const api: UserManagementApi = {
      users: async () => [],
      createUser,
      updateUser: pending,
      deleteUser: pending,
      resetPassword: pending,
      unlockUser: pending,
      setUserEnabled: pending,
      roles: async () => [role],
    };
    await renderPage(<UserManagementPage api={api} canManage />);
    await act(async () => buttonByText("新規作成").click());

    expect(host.querySelector('label[for="security-user-login-user-id"]')?.textContent).toBe(
      "ログインユーザーID必須",
    );
    // 一時パスワードは任意（空なら backend が発行する）なので、タグを付けない。
    expect(host.querySelector('label[for="security-user-temporary-password"]')?.textContent).not.toContain(
      "必須",
    );
    // ロールは radiogroup の aria-required で伝えるので、legend のタグは読み上げない。
    const legendBadge = host.querySelector("#security-users-role-legend span");
    expect(legendBadge?.textContent).toBe("必須");
    expect(legendBadge?.getAttribute("aria-hidden")).toBe("true");
    expect(host.querySelector('[role="radiogroup"]')?.getAttribute("aria-required")).toBe("true");

    await submitForm("security-users-form-heading");

    expect(host.querySelector("#security-user-login-user-id-error")?.textContent).toContain(
      "ログインユーザーIDを入力してください。",
    );
    expect(host.querySelector("#security-user-display-name-error")?.textContent).toContain(
      "表示名を入力してください。",
    );
    expect(host.querySelector("#security-users-role-error")?.textContent).toContain(
      "ロールを1つ選択してください。",
    );
    expect(document.activeElement?.id).toBe("security-user-login-user-id");
    expect(createUser).not.toHaveBeenCalled();
  });
});
