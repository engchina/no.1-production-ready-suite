import assert from "node:assert/strict";
import { test } from "node:test";
import { bindWorkspaceOwner, clearWorkspaceDrafts, draftKey, readDraft, writeDraft, WORKSPACE_DRAFT_TTL_MS, type DraftStorage } from "../src/lib/workspace-drafts";
class Storage implements DraftStorage {
  data = new Map<string, string>();
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { this.data.set(key, value); }
  removeItem(key: string) { this.data.delete(key); }
}
test("草稿はユーザー・DB・ページ・field で隔離し、別ログインと失効で消去する", () => {
  const storage = new Storage();
  storage.setItem("theme", "dark");
  bindWorkspaceOwner(storage, "alice");
  const key = draftKey("alice", "db1", "/query", "question:p1");
  assert.equal(writeDraft(storage, "alice", key, "下書き", 100), true);
  assert.equal(readDraft(storage, key, "", 101), "下書き");
  for (const other of [draftKey("alice", "db2", "/query", "question:p1"), draftKey("alice", "db1", "/query", "question:p2"), draftKey("bob", "db1", "/query", "question:p1")]) assert.equal(readDraft(storage, other, "", 101), "");
  assert.equal(readDraft(storage, key, "", 100 + WORKSPACE_DRAFT_TTL_MS), "");
  writeDraft(storage, "alice", key, "下書き", 100);
  bindWorkspaceOwner(storage, "bob");
  assert.equal(storage.getItem(key), null);
  assert.equal(writeDraft(storage, "alice", key, "遅延応答"), false);
  clearWorkspaceDrafts(storage);
  assert.equal(storage.length, 1);
  assert.equal(storage.getItem("theme"), "dark");
});
test("壊れた値・型の違う値・巨大な草稿・書込失敗を安全に扱う", () => {
  const storage = new Storage();
  bindWorkspaceOwner(storage, "alice");
  const key = draftKey("alice", "db1", "/query", "question");
  storage.setItem(key, "not-json");
  assert.equal(readDraft(storage, key, ""), "");
  writeDraft(storage, "alice", key, { bad: true });
  assert.equal(readDraft(storage, key, ""), "");
  assert.equal(writeDraft(storage, "alice", key, "x".repeat(200_001)), false);
  storage.setItem = () => { throw new Error("quota exceeded"); };
  assert.equal(writeDraft(storage, "alice", key, "SQL"), false);
});
