import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";

import { apiGet, isAbortError, isTimeoutError } from "@/lib/api";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import type { DbAdminObjectDetail } from "./types";

export const DB_OBJECT_DETAIL_TIMEOUT_MS = API_TIMEOUT_MS.interactiveDetail;

interface DbObjectDetailRequestOptions {
  collectionPath: string;
  loadErrorMessage: string;
  timeoutErrorMessage: string;
}

interface DbObjectDetailRequestState {
  selectedName: string;
  detail: DbAdminObjectDetail | null;
  setDetail: Dispatch<SetStateAction<DbAdminObjectDetail | null>>;
  loading: boolean;
  ddlLoading: boolean;
  error: string;
  load: (name: string) => Promise<void>;
  loadDdl: (name: string) => Promise<void>;
  clear: () => void;
  requestVersion: () => number;
}

function detailLoadError(cause: unknown, fallback: string): string {
  if (!(cause instanceof Error) || !cause.message.trim()) return fallback;
  return `${fallback} ${cause.message}`;
}

/**
 * テーブル/ビュー詳細の共通 request state。
 *
 * request sequence と AbortController の両方で latest-selection-wins を保証し、
 * DDL の後追い取得も含めて page-level 操作の loading/error state から分離する。
 */
export function useDbObjectDetailRequest({
  collectionPath,
  loadErrorMessage,
  timeoutErrorMessage,
}: DbObjectDetailRequestOptions): DbObjectDetailRequestState {
  const [selectedName, setSelectedName] = useState("");
  const [detail, setDetail] = useState<DbAdminObjectDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [ddlLoading, setDdlLoading] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const ddlControllerRef = useRef<{ name: string; controller: AbortController } | null>(null);
  const sequenceRef = useRef(0);

  const clear = useCallback(() => {
    sequenceRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    ddlControllerRef.current?.controller.abort();
    ddlControllerRef.current = null;
    setSelectedName("");
    setDetail(null);
    setLoading(false);
    setDdlLoading(false);
    setError("");
  }, []);

  const load = useCallback(
    async (name: string) => {
      const sequence = sequenceRef.current + 1;
      sequenceRef.current = sequence;
      controllerRef.current?.abort();
      ddlControllerRef.current?.controller.abort();
      ddlControllerRef.current = null;
      const controller = new AbortController();
      controllerRef.current = controller;
      setSelectedName(name);
      setDetail(null);
      setError("");
      setLoading(true);
      setDdlLoading(false);
      try {
        const nextDetail = await apiGet<DbAdminObjectDetail>(
          `${collectionPath}/${encodeURIComponent(name)}?include_ddl=0`,
          {
            signal: controller.signal,
            timeoutMs: DB_OBJECT_DETAIL_TIMEOUT_MS,
          },
        );
        if (sequence === sequenceRef.current && !controller.signal.aborted) {
          setDetail(nextDetail);
        }
      } catch (cause) {
        if (isAbortError(cause)) return;
        if (sequence === sequenceRef.current) {
          setError(
            isTimeoutError(cause)
              ? timeoutErrorMessage
              : detailLoadError(cause, loadErrorMessage),
          );
        }
      } finally {
        if (sequence === sequenceRef.current) {
          controllerRef.current = null;
          setLoading(false);
        }
      }
    },
    [collectionPath, loadErrorMessage, timeoutErrorMessage],
  );

  const loadDdl = useCallback(
    async (name: string) => {
      if (!detail || detail.name !== name || detail.ddl || ddlControllerRef.current?.name === name) {
        return;
      }
      const sequence = sequenceRef.current + 1;
      sequenceRef.current = sequence;
      ddlControllerRef.current?.controller.abort();
      const controller = new AbortController();
      ddlControllerRef.current = { name, controller };
      setDdlLoading(true);
      try {
        const nextDetail = await apiGet<DbAdminObjectDetail>(
          `${collectionPath}/${encodeURIComponent(name)}?include_ddl=1`,
          {
            signal: controller.signal,
            timeoutMs: DB_OBJECT_DETAIL_TIMEOUT_MS,
          },
        );
        if (sequence === sequenceRef.current && !controller.signal.aborted) {
          setDetail((current) =>
            current && current.name === name ? { ...current, ddl: nextDetail.ddl } : current,
          );
        }
      } catch {
        // DDL 取得失敗時は従来どおり空表示へ戻し、タブを開き直すと再試行できる。
      } finally {
        if (sequence === sequenceRef.current) {
          ddlControllerRef.current = null;
          setDdlLoading(false);
        }
      }
    },
    [collectionPath, detail],
  );

  useEffect(
    () => () => {
      sequenceRef.current += 1;
      controllerRef.current?.abort();
      ddlControllerRef.current?.controller.abort();
    },
    [],
  );

  const requestVersion = useCallback(() => sequenceRef.current, []);

  return {
    selectedName,
    detail,
    setDetail,
    loading,
    ddlLoading,
    error,
    load,
    loadDdl,
    clear,
    requestVersion,
  };
}
