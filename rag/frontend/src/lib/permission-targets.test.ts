import { targetLoadRows } from "@engchina/production-ready-system-settings";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccessTargetsData, SecurityRole } from "./api";
import { CAPABILITY_PERMISSIONS } from "./permissions";
import {
  BUSINESS_VIEW_ACCESS_KEY,
  KNOWLEDGE_BASE_ACCESS_KEY,
  PERMISSIONS_API,
  accessTargetsLoader,
  ragPermissionTargets,
} from "./permission-targets";
import { canSubmitUpload, uploadKnowledgeBaseRequired } from "./upload-scope";
import { ragIdentityKey } from "@/components/security/AuthProvider";

const TARGETS: AccessTargetsData = {
  business_views: [
    { id: "bv-1", name: "人事 FAQ", status: "ACTIVE", description: "人事規程" },
    { id: "bv-2", name: "旧経理", status: "ARCHIVED", description: null },
  ],
  knowledge_bases: [{ id: "kb-1", name: "規程集", status: "ACTIVE", description: null }],
};

const ROLE: SecurityRole = {
  role_id: "role-1",
  role_code: "HR",
  display_name: "人事",
  description: "",
  is_built_in: false,
  archived: false,
  version: 3,
  permissions: ["menu.search"],
  business_view_ids: ["bv-1"],
  knowledge_base_ids: ["kb-1"],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("権限管理の対象（業務ビュー・KB）", () => {
  it("1 回の読み込みでは業務ビューと KB が同じ応答を共有し、signal が変われば取り直す", async () => {
    const fetchTargets = vi.fn().mockResolvedValue(TARGETS);
    const [views, bases] = ragPermissionTargets(accessTargetsLoader(fetchTargets));
    const first = new AbortController().signal;

    const [viewItems, baseItems] = await Promise.all([
      views.load({ signal: first }),
      bases.load({ signal: first }),
    ]);
    expect(fetchTargets).toHaveBeenCalledTimes(1);
    expect(viewItems).toEqual([
      { id: "bv-1", name: "人事 FAQ", description: "人事規程", status: undefined },
      { id: "bv-2", name: "旧経理", description: undefined, status: "アーカイブ済み" },
    ]);
    expect(targetLoadRows(baseItems).rows.map((item) => item.id)).toEqual(["kb-1"]);

    await views.load({ signal: new AbortController().signal });
    expect(fetchTargets).toHaveBeenCalledTimes(2);
  });

  it("業務ビューは業務ビュー管理、KB はナレッジベース管理の権限で全件が対象", () => {
    const [views, bases] = ragPermissionTargets(accessTargetsLoader(vi.fn()));
    expect(views.key).toBe(BUSINESS_VIEW_ACCESS_KEY);
    expect(bases.key).toBe(KNOWLEDGE_BASE_ACCESS_KEY);
    const viewsManage = new Set([CAPABILITY_PERMISSIONS.businessViewsManage]);
    const basesManage = new Set([CAPABILITY_PERMISSIONS.knowledgeBasesManage]);
    expect(views.grantsAll?.(viewsManage)).toBe(true);
    expect(views.grantsAll?.(basesManage)).toBe(false);
    expect(bases.grantsAll?.(basesManage)).toBe(true);
    expect(bases.grantsAll?.(viewsManage)).toBe(false);
    expect(views.selectedIds(ROLE)).toEqual(["bv-1"]);
    expect(bases.selectedIds(ROLE)).toEqual(["kb-1"]);
  });

  it("保存は PUT /api/security/roles/{id}/access へ version・権限・業務ビュー・KB を送る", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ data: { ...ROLE, version: 4 }, error_messages: [], warning_messages: [] }),
        { status: 200, headers: { "content-type": "application/json" } }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const saved = await PERMISSIONS_API.save(ROLE, {
      permissions: ["menu.search", "menu.chat"],
      targets: { [BUSINESS_VIEW_ACCESS_KEY]: ["bv-1", "bv-2"] },
    });

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/security/roles/role-1/access");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({
      version: 3,
      permissions: ["menu.search", "menu.chat"],
      business_view_ids: ["bv-1", "bv-2"],
      // 全件が対象のときや未選択のときは共通画面が空の一覧を渡す（key が無い場合も空）。
      knowledge_base_ids: [],
    });
    expect(saved.version).toBe(4);
  });
});

describe("利用者の対象範囲", () => {
  const user = {
    user_uuid: "u1",
    login_user_id: "u1",
    display_name: "U1",
    status: "ACTIVE",
    force_password_change: false,
    role_codes: ["HR"],
    is_system_admin: false,
    permissions: ["menu.search", "menu.chat"],
    allowed_business_view_ids: ["bv-2", "bv-1"],
    allowed_knowledge_base_ids: null,
    debug_mode: false,
    password_change_allowed: true,
  };

  it("identity key は権限・業務ビュー・KB の変化を検出し、並び順の違いは無視する", () => {
    const base = ragIdentityKey(user);
    expect(ragIdentityKey({ ...user, permissions: ["menu.chat", "menu.search"] })).toBe(base);
    expect(ragIdentityKey({ ...user, allowed_business_view_ids: ["bv-1", "bv-2"] })).toBe(base);
    expect(ragIdentityKey({ ...user, allowed_business_view_ids: ["bv-1"] })).not.toBe(base);
    expect(ragIdentityKey({ ...user, allowed_knowledge_base_ids: [] })).not.toBe(base);
    expect(ragIdentityKey({ ...user, permissions: ["menu.search"] })).not.toBe(base);
    expect(ragIdentityKey({ ...user, user_uuid: "u2" })).not.toBe(base);
  });

  it("KB が制限された利用者は、アップロード先の KB を選ばないと送信できない", () => {
    expect(uploadKnowledgeBaseRequired(user)).toBe(false);
    expect(uploadKnowledgeBaseRequired({ allowed_knowledge_base_ids: [] })).toBe(true);
    expect(uploadKnowledgeBaseRequired({ allowed_knowledge_base_ids: ["kb-1"] })).toBe(true);
    expect(uploadKnowledgeBaseRequired(null)).toBe(false);
    expect(canSubmitUpload(true, [])).toBe(false);
    expect(canSubmitUpload(true, ["kb-1"])).toBe(true);
    expect(canSubmitUpload(false, [])).toBe(true);
  });
});
