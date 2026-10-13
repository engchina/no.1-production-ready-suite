// @vitest-environment happy-dom
import { ConfirmProvider } from "@production-ready/ui";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  RoleCodeBadges,
  UserManagementPage,
  roleCodeLabel,
  type SecurityRole,
  type SecurityUser,
  type UserManagementApi,
} from "../src";

// #1682: 画面にロールの ID（UUID）を出さず、ロールコードを出す。参照できないロールは「（削除済みのロール）」。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SYSTEM_ADMIN_ID = "00000000-0000-0000-0000-000000000001";
const DELETED_ID = "6f1c2d3e-4b5a-4c6d-8e7f-90a1b2c3d4e5";
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/iu;

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

async function render(node: ReactNode) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <ConfirmProvider>{node}</ConfirmProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => undefined);
}

/** 画面の文字と属性（title・aria-label など）のどこにも UUID が無い。 */
function expectNoUuidShown(scope: HTMLElement) {
  expect(scope.textContent ?? "").not.toMatch(UUID_PATTERN);
  for (const element of Array.from(scope.querySelectorAll<HTMLElement>("*"))) {
    for (const name of ["title", "aria-label", "aria-description", "placeholder"]) {
      expect(element.getAttribute(name) ?? "").not.toMatch(UUID_PATTERN);
    }
  }
}

describe("roleCodeLabel（#1682）", () => {
  it("ロールコードを返し、コードが無い・削除済みは ID を出さない文言にする", () => {
    expect(roleCodeLabel({ role_code: "SYSTEM_ADMIN" })).toBe("SYSTEM_ADMIN");
    expect(roleCodeLabel({ role_code: "  HR_USER " })).toBe("HR_USER");
    expect(roleCodeLabel({ role_code: "", deleted: true })).toBe("（削除済みのロール）");
    expect(roleCodeLabel({ role_code: "HR_USER", deleted: true })).toBe("（削除済みのロール）");
    expect(roleCodeLabel({ role_code: null })).toBe("（削除済みのロール）");
    expect(roleCodeLabel(undefined, "削除されたロール")).toBe("削除されたロール");
  });
});

describe("RoleCodeBadges（#1682）", () => {
  it("ロールをロールコードのバッジで並べ、ID を出さない", async () => {
    await render(
      <RoleCodeBadges
        ariaLabel="利用可能ロール"
        roles={[
          { role_id: SYSTEM_ADMIN_ID, role_code: "SYSTEM_ADMIN" },
          { role_id: "r-hr", role_code: "HR_USER" },
          { role_id: DELETED_ID, role_code: "", deleted: true },
        ]}
      />,
    );
    const list = host.querySelector<HTMLUListElement>('[data-testid="role-code-badges"]');
    expect(list?.getAttribute("aria-label")).toBe("利用可能ロール");
    const labels = Array.from(list?.querySelectorAll("li") ?? []).map((item) => item.textContent?.trim());
    expect(labels).toEqual(["SYSTEM_ADMIN", "HR_USER", "（削除済みのロール）"]);
    expectNoUuidShown(host);
  });
});

function securityRole(id: string, code: string, name: string): SecurityRole {
  return { role_id: id, role_code: code, display_name: name, description: "", is_built_in: false, archived: false, version: 1 };
}

const never = () => new Promise<never>(() => undefined);

function userApi(users: SecurityUser[]): UserManagementApi {
  return {
    users: () => Promise.resolve(users),
    createUser: never,
    updateUser: never,
    deleteUser: never,
    resetPassword: never,
    unlockUser: never,
    setUserEnabled: never,
    roles: () =>
      Promise.resolve([
        { ...securityRole(SYSTEM_ADMIN_ID, "SYSTEM_ADMIN", "システム管理者"), is_built_in: true },
        securityRole("r-hr", "HR_USER", "人事利用者"),
      ]),
  };
}

function baseUser(overrides: Partial<SecurityUser>): SecurityUser {
  return {
    user_uuid: "user-1",
    login_user_id: "alice",
    display_name: "Alice",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: [],
    is_bootstrap_admin: false,
    ...overrides,
  };
}

describe("ユーザー管理の削除済みのロール（#1682）", () => {
  it("backend が参照できないと返したロールは「（削除済みのロール）」と出し、UUID を出さない", async () => {
    const user = baseUser({
      role_ids: ["r-hr", DELETED_ID],
      assigned_roles: [
        { role_id: "r-hr", role_code: "HR_USER", display_name: "人事利用者", is_built_in: false, archived: false },
        // backend の AssignedRoleData.unresolved() の形（コード・表示名は空）。
        { role_id: DELETED_ID, role_code: "", display_name: "", is_built_in: false, archived: true, deleted: true },
      ],
    });
    await render(<UserManagementPage api={userApi([user])} canManage />);

    expect(host.textContent).toContain("（削除済みのロール）");
    expect(host.textContent).toContain("人事利用者");
    expectNoUuidShown(host);
  });

  it("assigned_roles が無いときも、ロール一覧に無い ID を名前に使わない", async () => {
    const user = baseUser({ role_ids: [SYSTEM_ADMIN_ID, DELETED_ID] });
    await render(<UserManagementPage api={userApi([user])} canManage />);

    expect(host.textContent).toContain("システム管理者");
    expect(host.textContent).toContain("（削除済みのロール）");
    expectNoUuidShown(host);
  });
});
