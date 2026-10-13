// ロールを画面に出す共通の部品（#1682）。
// ロールはロールコード（例 `SYSTEM_ADMIN`）で出し、ID（UUID の `role_id`）は文字にも tooltip にも出さない。
// 参照できない（削除済みの）ロールは ID の代わりに「（削除済みのロール）」と出す。3 製品で同じ部品を使う。
import { StatusBadge, cn } from "@production-ready/ui";

import { t } from "./messages";

/** 画面に出すロールの参照。`role_id` は key にだけ使い、表示には使わない。 */
export interface RoleCodeReference {
  role_id: string;
  role_code?: string | null;
  /** 削除済み等で参照できないロール（backend の `AssignedRoleData.deleted`）。 */
  deleted?: boolean;
}

/** 削除済み・参照できないロールの文言の既定。 */
export function deletedRoleLabel(): string {
  return t("security.roles.deletedRole");
}

/**
 * ロールを画面に出す文字列。ロールコードを返し、コードが無い・削除済みのロールは
 * ID を出さずに「（削除済みのロール）」を返す。
 */
export function roleCodeLabel(
  role: Pick<RoleCodeReference, "role_code" | "deleted"> | null | undefined,
  deletedLabel: string = deletedRoleLabel(),
): string {
  const code = role?.role_code?.trim() ?? "";
  if (!role || role.deleted || !code) return deletedLabel;
  return code;
}

export interface RoleCodeBadgesProps {
  roles: readonly RoleCodeReference[];
  /** 削除済みのロールの文言（製品の i18n で差し替えるとき）。 */
  deletedLabel?: string;
  /** 一覧の名前（読み上げ用）。 */
  ariaLabel?: string;
  className?: string;
}

/** ロールをロールコードのバッジで並べる（業務プロファイルの「利用可能ロール」など）。 */
export function RoleCodeBadges({ roles, deletedLabel, ariaLabel, className }: RoleCodeBadgesProps) {
  const fallback = deletedLabel ?? deletedRoleLabel();
  return (
    <ul
      className={cn("flex flex-wrap gap-1.5", className)}
      aria-label={ariaLabel}
      data-testid="role-code-badges"
    >
      {roles.map((role) => (
        <li key={role.role_id} className="min-w-0">
          <StatusBadge
            icon={false}
            variant="neutral"
            label={roleCodeLabel(role, fallback)}
            className={role.deleted || !role.role_code?.trim() ? undefined : "font-mono"}
          />
        </li>
      ))}
    </ul>
  );
}
