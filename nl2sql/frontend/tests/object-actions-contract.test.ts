import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  splitObjectActions,
  visibleEntityActions,
  type EntityAction,
} from "../src/components/ObjectActionsCore";

const source = readFileSync(new URL("../src/components/ObjectActions.tsx", import.meta.url), "utf8");
const floatingSource = readFileSync(
  new URL("../src/components/FloatingMenu.tsx", import.meta.url),
  "utf8"
);

const actions: EntityAction[] = [
  { id: "edit", label: "編集", onSelect: () => {} },
  { id: "reset", label: "リセット", onSelect: () => {} },
  { id: "unlock", label: "ロック解除", onSelect: () => {} },
  { id: "delete", label: "削除", tone: "danger", onSelect: () => {} },
  { id: "hidden", label: "非表示", visible: false, onSelect: () => {} },
];

test("EntityAction は visible=false を表示対象から外す", () => {
  assert.deepEqual(
    visibleEntityActions(actions).map((action) => action.id),
    ["edit", "reset", "unlock", "delete"]
  );
});

test("ObjectActionBar は最大 2 件の非危険操作だけを inline に残す", () => {
  const split = splitObjectActions(actions);

  assert.deepEqual(
    split.inline.map((action) => action.id),
    ["edit", "reset"]
  );
  assert.deepEqual(
    split.overflow.map((action) => action.id),
    ["unlock", "delete"]
  );
});

test("行/詳細の overflow menu は ARIA とキーボード契約を持つ", () => {
  assert.match(source, /aria-haspopup="menu"/u);
  assert.match(source, /aria-expanded=\{open\}/u);
  assert.match(source, /aria-controls=\{menuId\}/u);
  assert.match(floatingSource, /role="menu"/u);
  assert.match(source, /role="menuitem"/u);
  for (const key of ["Escape", "ArrowDown", "ArrowUp", "Home", "End"]) {
    assert.match(source, new RegExp(`event\\.key === "${key}"`, "u"));
  }
  assert.match(source, /triggerRef\.current\?\.focus/u);
});

test("行/詳細の overflow menu は viewport 基準で反転し、狭い時だけ内部スクロールする", () => {
  assert.match(source, /FloatingActionMenu/u);
  assert.match(floatingSource, /createPortal/u);
  assert.match(floatingSource, /position|fixed/u);
  assert.match(floatingSource, /getScrollableAncestor/u);
  assert.match(floatingSource, /scrollHeight > element\.clientHeight/u);
  assert.match(floatingSource, /availableBelow/u);
  assert.match(floatingSource, /availableAbove/u);
  assert.match(floatingSource, /placement/u);
  assert.match(floatingSource, /maxHeight/u);
  assert.match(floatingSource, /data-floating-menu-placement/u);
  assert.match(floatingSource, /data-floating-menu-constrained/u);
  assert.match(floatingSource, /getBoundingClientRect/u);
  assert.match(floatingSource, /menu\.scrollHeight \+ menuBorderHeight/u);
  assert.match(floatingSource, /constrained \? \{ maxHeight/u);
  assert.match(floatingSource, /position\?\.constrained && "overflow-y-auto overscroll-contain"/u);
  assert.doesNotMatch(floatingSource, /"fixed[^"]*overflow-y-auto/u);
  assert.match(floatingSource, /addEventListener\("scroll", updatePosition, true\)/u);
  assert.match(source, /menuRef\.current\?\.contains\(target\)/u);
});

test("危険操作は menu 内で tone と区切りを持つ", () => {
  assert.match(source, /data-entity-action-tone/u);
  assert.match(source, /danger &&/u);
  assert.match(source, /border-t border-border/u);
  assert.match(source, /text-danger/u);
});
