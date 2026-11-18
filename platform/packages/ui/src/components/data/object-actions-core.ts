import type { LucideIcon } from "lucide-react";

export type EntityActionTone = "default" | "danger";

export interface EntityAction {
  id: string;
  label: string;
  ariaLabel?: string;
  icon?: LucideIcon;
  tone?: EntityActionTone;
  onSelect: () => void | Promise<void>;
  disabled?: boolean;
  loading?: boolean;
  visible?: boolean;
  /** `ObjectActionBar` でボタンに出さず、「その他の操作」のメニューにだけ入れる（頻度の低い操作が並ぶとき。#5478）。 */
  menuOnly?: boolean;
  testId?: string;
}

export function visibleEntityActions(actions: readonly EntityAction[]) {
  return actions.filter((action) => action.visible !== false);
}

export function splitObjectActions(actions: readonly EntityAction[], maxInline = 2) {
  const visible = visibleEntityActions(actions);
  const inline = visible.filter((action) => action.tone !== "danger" && !action.menuOnly).slice(0, maxInline);
  const inlineIds = new Set(inline.map((action) => action.id));
  const overflow = visible.filter((action) => !inlineIds.has(action.id));
  return { inline, overflow };
}
