import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";

export const UI_STORAGE_KEY = "production-ready-rag.ui";
const LEGACY_SIDEBAR_COLLAPSED_STORAGE_KEY = "production-ready-rag.sidebarCollapsed";

type UiState = {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleSidebarCollapsed: () => void;
};

const memoryStorage = new Map<string, string>();

const fallbackStorage: StateStorage = {
  getItem: (name) => memoryStorage.get(name) ?? null,
  setItem: (name, value) => memoryStorage.set(name, value),
  removeItem: (name) => memoryStorage.delete(name),
};

function resolveStorage(): StateStorage {
  if (typeof window === "undefined") {
    return fallbackStorage;
  }
  return window.localStorage;
}

function initialSidebarCollapsed(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(LEGACY_SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: initialSidebarCollapsed(),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      toggleSidebarCollapsed: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
    }),
    {
      name: UI_STORAGE_KEY,
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
      storage: createJSONStorage(resolveStorage),
    }
  )
);
