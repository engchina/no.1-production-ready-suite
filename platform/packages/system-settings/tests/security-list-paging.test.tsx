// @vitest-environment happy-dom
import { ConfirmProvider } from "@production-ready/ui";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  RoleManagementPage,
  RolePermissionsPage,
  UserManagementPage,
  type PermissionRole,
  type RoleManagementApi,
  type RolePermissionsApi,
  type SecurityRole,
  type SecurityUser,
  type UserManagementApi,
} from "../src";
import { bindListPagingOwner, LIST_PAGING_NAMESPACE, readListPaging } from "../src/lists/listPaging";

// #1891: ユーザー・ロール・権限管理の一覧の件数は ListToolbar の ListCount 1 か所、一覧は 25 件ずつのページ
// （表示件数 10 / 25 / 50 / 100。既定 25。#1952）。検索・並べ替え・表示件数で 1 ページ目へ戻し、一覧 ⇄ 編集の行き来ではページを残す。
// #4635: 全幅の一覧 → 全幅のエディタ（A 型）。開いている対象は URL（?user= / ?role=）が持ち、エディタから一覧へ戻ると
// 開いていた行のあるページを出し、その行の名前へフォーカスを戻す。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // ページ番号・表示件数は作業状態（sessionStorage）に残るので、テストごとに消す。
  window.sessionStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});

const never = () => new Promise<never>(() => undefined);
const pad = (index: number) => String(index).padStart(2, "0");

function user(index: number): SecurityUser {
  return {
    user_uuid: `user-${pad(index)}`,
    login_user_id: `login${pad(index)}`,
    display_name: `利用者${pad(index)}`,
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: [],
    assigned_roles: [],
    is_bootstrap_admin: false,
  };
}

function role(index: number): PermissionRole {
  return {
    role_id: `role-${pad(index)}`,
    role_code: `ROLE_${pad(index)}`,
    display_name: `ロール${pad(index)}`,
    description: "",
    is_built_in: false,
    archived: false,
    version: 1,
    permissions: [],
  };
}

const USERS = Array.from({ length: 53 }, (_, index) => user(index + 1));
const ROLES = Array.from({ length: 27 }, (_, index) => role(index + 1));

const userApi: UserManagementApi = {
  users: () => Promise.resolve(USERS),
  createUser: never,
  updateUser: never,
  deleteUser: never,
  resetPassword: never,
  unlockUser: never,
  setUserEnabled: never,
  roles: () => Promise.resolve([] as SecurityRole[]),
};

async function render(node: ReactNode, entry = "/") {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <ConfirmProvider>{node}</ConfirmProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => undefined);
}

async function click(element: HTMLElement | null | undefined) {
  if (!element) throw new Error("押す対象が無い");
  await act(async () => {
    element.click();
  });
  await act(async () => undefined);
}

function byTestId(testId: string) {
  return host.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
}

function buttonByName(name: string, scope: ParentNode = host) {
  return Array.from(scope.querySelectorAll<HTMLButtonElement>("button")).find(
    (element) => element.getAttribute("aria-label") === name || element.textContent?.trim() === name,
  );
}

/** 行の名前（RowTitleButton の 1 行目。2 行目の補足は含めない）。 */
function rowTitleText(row: Element | null | undefined) {
  return row?.querySelector("[data-row-title-button] > span")?.textContent?.trim() ?? "";
}

/** 表の行の 1 列目（表示名・ロール名）。 */
function rowTitles(gridTestId: string) {
  return Array.from(host.querySelectorAll(`[data-testid="${gridTestId}"] tbody tr`)).map(rowTitleText);
}

/** 行の名前のリンク（エディタを開く）。 */
function rowTitleLink(gridTestId: string, index: number) {
  return host.querySelectorAll<HTMLElement>(`[data-testid="${gridTestId}"] tbody tr [data-row-title-button]`)[index];
}

function pageTitle() {
  return host.querySelector("h1")?.textContent?.trim() ?? "";
}

/** フォーカスのある行の名前のリンクの対象の ID。 */
function focusedRowId() {
  return (document.activeElement as HTMLElement | null)?.getAttribute("data-security-row-id") ?? null;
}

async function choosePageSize(paginationTestId: string, size: number) {
  const combobox = byTestId(paginationTestId)?.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]');
  await click(combobox);
  await click(document.querySelector<HTMLElement>(`[role="option"][data-value="${size}"]`));
}

async function search(testId: string, value: string) {
  const input = byTestId(testId)?.querySelector("input") ?? (byTestId(testId) as HTMLInputElement | null);
  if (!(input instanceof HTMLInputElement)) throw new Error("検索欄が無い");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  });
  await act(async () => undefined);
}

describe("ユーザー管理の一覧の件数とページ（#1891）", () => {
  it("件数はツールバーの ListCount 1 か所に出し、見出しのバッジに出さない", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    expect(byTestId("security-users-count")?.textContent).toBe("全 53 件");
    // 以前の見出しのバッジ（「53 / 53 件」）は出さない。
    expect(host.textContent).not.toContain("53 / 53 件");
  });

  it("全幅の表を 25 件ずつに分け、エディタを開かずに一覧だけを出す（#4635）", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    expect(rowTitles("security-users-grid")).toHaveLength(25);
    expect(rowTitles("security-users-grid")[0]).toBe("利用者01");
    expect(byTestId("security-users-pagination")?.textContent).toContain("1–25 / 53 件");
    // 未選択の詳細の空の状態は無い（一覧は全幅）。
    expect(host.textContent).not.toContain("ユーザーを選択してください");
    expect(byTestId("security-users-editor")).toBeNull();

    await click(buttonByName("次へ", byTestId("security-users-pagination") ?? host));
    expect(rowTitles("security-users-grid")[0]).toBe("利用者26");
    expect(byTestId("security-users-pagination")?.textContent).toContain("26–50 / 53 件");
  });

  it("?user= で開くとその利用者のエディタを出し、一覧へ戻るとその行のページとフォーカスに戻る（#4635）", async () => {
    await render(<UserManagementPage api={userApi} canManage />, "/settings/security/users?user=user-52");
    expect(byTestId("security-users-grid")).toBeNull();
    expect(pageTitle()).toBe("利用者52");
    expect(byTestId("security-users-editor")).not.toBeNull();

    await click(byTestId("security-users-back"));
    expect(byTestId("security-users-editor")).toBeNull();
    expect(byTestId("security-users-pagination")?.textContent).toContain("51–53 / 53 件");
    expect(focusedRowId()).toBe("user-52");
  });

  it("URL の利用者が一覧に無ければ「見つかりません」を出し、ほかの利用者を開かない", async () => {
    await render(<UserManagementPage api={userApi} canManage />, "/settings/security/users?user=missing");
    expect(byTestId("security-users-detail-not-found")?.textContent).toContain("ユーザーが見つかりません");
    expect(byTestId("security-users-editor")).toBeNull();
  });

  it("表示件数を変えると 1 ページ目に戻り、その件数で並べる", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    await click(buttonByName("次へ", byTestId("security-users-pagination") ?? host));
    await choosePageSize("security-users-pagination", 100);
    expect(rowTitles("security-users-grid")).toHaveLength(53);
    expect(byTestId("security-users-pagination")?.textContent).toContain("1–53 / 53 件");
  });

  it("検索すると 1 ページ目に戻り、件数は「一致 N 件（全 M 件）」にする", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    await click(buttonByName("次へ", byTestId("security-users-pagination") ?? host));
    await search("security-users-search", "利用者1");
    expect(byTestId("security-users-count")?.textContent).toBe("10 件（全 53 件）");
    expect(rowTitles("security-users-grid")[0]).toBe("利用者10");
    // 1 ページに収まり、表示件数の段（10 件）を超えないときはページ送りを出さない。
    expect(byTestId("security-users-pagination")).toBeNull();
  });

  it("一覧 → エディタ → 一覧へ戻ると、前のページに戻り、開いていた行へフォーカスを戻す", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    await click(buttonByName("次へ", byTestId("security-users-pagination") ?? host));
    await click(rowTitleLink("security-users-grid", 1));
    expect(byTestId("security-users-grid")).toBeNull();
    expect(pageTitle()).toBe("利用者27");

    await click(byTestId("security-users-back"));
    expect(byTestId("security-users-pagination")?.textContent).toContain("26–50 / 53 件");
    expect(focusedRowId()).toBe("user-27");
  });

  it("行の名前は ?user= のリンクで、狭い画面ではロールと状態を名前の下に畳む（#4635）", async () => {
    await render(<UserManagementPage api={userApi} canManage />);
    expect(rowTitleLink("security-users-grid", 0)?.getAttribute("href")).toBe("?user=user-01");
    expect(host.querySelector('[data-testid="security-users-row-summary"]')?.className).toContain("md:hidden");
  });
});

describe("一覧のページと表示件数の作業状態（#1891）", () => {
  it("別の画面へ移って戻っても（再描画しても）ページと表示件数が残り、利用者が変わると消える", async () => {
    // ログインした利用者に結び付ける（AuthProvider が呼ぶ）。
    bindListPagingOwner("user-a");
    await render(<UserManagementPage api={userApi} canManage />);
    await choosePageSize("security-users-pagination", 100);
    expect(byTestId("security-users-pagination")?.textContent).toContain("1–53 / 53 件");

    // 画面を外して付け直す（サイドナビで別の画面へ移って戻るのと同じ）。
    act(() => root.unmount());
    root = createRoot(host);
    await render(<UserManagementPage api={userApi} canManage />);
    expect(byTestId("security-users-pagination")?.textContent).toContain("1–53 / 53 件");
    expect(readListPaging("users")).toEqual({ page: 1, pageSize: 100 });

    // 同じ利用者（再読込）では残し、別の利用者になったら消す。
    bindListPagingOwner("user-a");
    expect(readListPaging("users").pageSize).toBe(100);
    bindListPagingOwner("user-b");
    expect(readListPaging("users")).toEqual({ page: 1, pageSize: 25 });
  });

  it("壊れた値・選択肢に無い表示件数は既定（1 ページ目・25 件）に戻す", () => {
    window.sessionStorage.setItem(`${LIST_PAGING_NAMESPACE}users`, JSON.stringify({ page: -2, pageSize: 7 }));
    expect(readListPaging("users")).toEqual({ page: 1, pageSize: 25 });
    window.sessionStorage.setItem(`${LIST_PAGING_NAMESPACE}users`, "{");
    expect(readListPaging("users")).toEqual({ page: 1, pageSize: 25 });
  });
});

describe("ロール管理の一覧の件数とページ（#1891）", () => {
  it("件数はツールバーに出し、25 件を超えるとページ送りを出す", async () => {
    const api: RoleManagementApi = {
      roles: () => Promise.resolve(ROLES),
      createRole: never,
      updateRole: never,
      archiveRole: never,
      restoreRole: never,
      deleteRole: never,
    };
    await render(<RoleManagementPage api={api} canManage />);
    expect(byTestId("security-roles-count")?.textContent).toBe("全 27 件");
    expect(host.textContent).not.toContain("27 / 27 件");
    expect(rowTitles("security-roles-grid")).toHaveLength(25);
    expect(byTestId("security-roles-pagination")?.textContent).toContain("1–25 / 27 件");
  });
});

describe("権限管理のロール一覧の件数とページ（#1891）", () => {
  const api: RolePermissionsApi = {
    roles: () => Promise.resolve(ROLES),
    permissions: () => Promise.resolve([]),
    save: never,
  };

  it("件数はツールバーに出し、25 件ずつに分ける", async () => {
    await render(<RolePermissionsPage api={api} canManage />, "/settings/security/permissions");
    expect(byTestId("security-permissions-count")?.textContent).toBe("全 27 件");
    expect(host.textContent).not.toContain("27 / 27 件");
    expect(rowTitles("security-permissions-grid")).toHaveLength(25);
    expect(byTestId("security-permissions-editor")).toBeNull();
  });

  it("?role= で開くとそのロールのエディタを出し、戻ると 2 ページ目のその行に戻る", async () => {
    await render(<RolePermissionsPage api={api} canManage />, "/settings/security/permissions?role=role-27");
    expect(pageTitle()).toBe("ロール27");
    expect(byTestId("security-permissions-editor")).not.toBeNull();
    await click(byTestId("security-permissions-back"));
    expect(byTestId("security-permissions-pagination")?.textContent).toContain("26–27 / 27 件");
    expect(focusedRowId()).toBe("role-27");
  });
});
