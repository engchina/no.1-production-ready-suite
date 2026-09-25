"use client";

import { useCallback, useState } from "react";

/**
 * 一覧の行選択を管理するフック（参照実装の useSelection の設計を踏襲）。
 */
export function useSelection<T extends string>() {
  const [selected, setSelected] = useState<ReadonlySet<T>>(new Set());

  const isSelected = useCallback((id: T) => selected.has(id), [selected]);

  const toggle = useCallback((id: T) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback((ids: T[]) => {
    setSelected((prev) => (prev.size === ids.length ? new Set<T>() : new Set(ids)));
  }, []);

  const clear = useCallback(() => setSelected(new Set<T>()), []);

  return {
    selected,
    count: selected.size,
    isSelected,
    toggle,
    toggleAll,
    clear,
  };
}
