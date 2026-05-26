/**
 * jobStore — управляет жизненным циклом деплоя.
 *
 * Backend API:
 *   POST /api/deployment/start         — создаёт деплой, получает deployment_id
 *   GET  /api/deployment/{id}          — текущий статус + логи из БД
 *   GET  /api/deployment/{id}/stream   — SSE stream событий
 *
 * Persistence:
 *   localStorage["activeDeploymentId"] — сохраняет ID между перезагрузками страницы
 */

import { create } from "zustand";
import type { StoreApi } from "zustand";
import { useDeployStore } from "./deployStore";

// ── Типы ──────────────────────────────────────────────────────────

export interface ActiveDeployment {
  id: string;
  startedAt: string;
  status: "running" | "success" | "failed" | "waiting_license";
  currentPhase: string;
  progress: number;
}

interface JobState {
  activeDeployment: ActiveDeployment | null;
  logs: string[];
  isLaunching: boolean;
  isRestoring: boolean;
  launchDeploy: () => Promise<void>;
  restoreDeployment: () => Promise<void>;
  /** Загружает произвольный деплой по ID (клик из History) */
  activateDeployment: (id: string) => Promise<void>;
  clearJob: () => void;
}

// ── Прогресс по фазе ───────────────────────────────────────────────

const PHASE_PROGRESS: Record<string, number> = {
  configuration:    2,
  preflight:        8,
  infra_setup:     20,
  node_startup:    35,
  cluster_config:  50,
  license_request: 55,
  waiting_license: 60,
  license_install: 70,
  remaining_nodes: 80,
  haproxy_setup:   88,
  health_checks:   90,
  reporting:       95,
  success:        100,
  failed:           0,
};

function phaseToProgress(phase: string): number {
  return PHASE_PROGRESS[phase] ?? 0;
}

function phaseToStatus(phase: string): ActiveDeployment["status"] {
  if (phase === "success") return "success";
  if (phase === "failed")  return "failed";
  if (phase === "waiting_license") return "waiting_license";
  return "running";
}

// ── Хелперы ───────────────────────────────────────────────────────

function ipToHostname(ip: string, prefix: string): string {
  return `${prefix}-${ip.replace(/\./g, "-")}`;
}

function ts(): string {
  return new Date().toLocaleTimeString("ru");
}

function formatLogTs(isoTs: string): string {
  try {
    return new Date(isoTs).toLocaleTimeString("ru");
  } catch {
    return ts();
  }
}

const LS_KEY = "activeDeploymentId";

function saveActiveId(id: string) {
  try { localStorage.setItem(LS_KEY, id); } catch { /* ignore */ }
}

function loadActiveId(): string | null {
  try { return localStorage.getItem(LS_KEY); } catch { return null; }
}

function clearActiveId() {
  try { localStorage.removeItem(LS_KEY); } catch { /* ignore */ }
}

// ── Polling (фоллбэк — тянет состояние из БД каждые 3с) ───────────
// Дополняет SSE: ловит события пропущенные при переподключении,
// а также фазы где оркестратор не пушит в очередь (waiting_license).

let _pollTimer: ReturnType<typeof setInterval> | null = null;
let _pollDeploymentId: string | null = null;

function startPolling(id: string, set: ZSet): void {
  stopPolling();
  _pollDeploymentId = id;

  const poll = async () => {
    if (_pollDeploymentId !== id) return;
    try {
      const resp = await fetch(`/api/deployment/${id}`);
      if (!resp.ok) return;
      const data = await resp.json() as {
        deployment_id: string;
        status: string;
        created_at: string;
        logs: Array<{ level: string; phase: string | null; message: string; created_at: string }>;
      };

      const phase    = data.status;
      const status   = phaseToStatus(phase);
      const progress = phaseToProgress(phase);
      const dbLogs   = [...data.logs]
        .reverse()
        .map(l => `[${formatLogTs(l.created_at)}] [${l.level}]  ${l.message}`);

      set((st) => {
        if (!st.activeDeployment || st.activeDeployment.id !== id) return {};
        return {
          activeDeployment: { ...st.activeDeployment, status, currentPhase: phase, progress },
          logs: dbLogs,
        };
      });

      if (status === "success" || status === "failed") {
        stopPolling();
        if (status === "success" || status === "failed") clearActiveId();
      }
    } catch { /* игнорируем сетевые ошибки */ }
  };

  _pollTimer = setInterval(poll, 3_000);
}

function stopPolling(): void {
  if (_pollTimer !== null) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  _pollDeploymentId = null;
}

/** Конвертирует состояние deployStore в тело запроса POST /api/deployment/start */
function buildRequestBody(s: ReturnType<typeof useDeployStore.getState>) {
  const backends   = s.getEffectiveBackends();
  const frontends  = s.getEffectiveFrontends();
  const primaryStorage = s.storageNodes.find((n) => n.isPrimary) ?? s.storageNodes[0];
  const storageIp  = primaryStorage?.ip ?? "";

  /** SSH для конкретной ноды */
  const nodeSsh = (ip: string) => {
    const ssh = s.getNodeSsh(ip);
    return {
      ssh_user:     ssh.user || "user",
      ssh_password: ssh.authMode === "password" ? (ssh.password || undefined) : undefined,
      ssh_key_path: ssh.authMode === "key"      ? (ssh.keyPath  || undefined) : undefined,
      ssh_port:     ssh.port || 22,
    };
  };

  const makeServer = (ip: string, role: string, prefix: string) => ({
    ip,
    hostname: ipToHostname(ip, prefix),
    role,
    ...nodeSsh(ip),
  });

  let packageConfig: object | null = null;
  if (s.packageType === "controller_file" && s.packageValue) {
    // Файл на машине контроллера — копируем по SFTP на каждую ноду (local_file)
    packageConfig = {
      default_source: { source_type: "local_file", local_path: s.packageValue, package_format: "auto" },
    };
  } else if (s.packageType === "url" && s.packageValue) {
    packageConfig = {
      default_source: { source_type: "url", url: s.packageValue, package_format: "auto" },
    };
  } else if (s.packageType === "server_path" && s.packageValue) {
    packageConfig = {
      default_source: { source_type: "server_path", server_path: s.packageValue, package_format: "auto" },
    };
  }

  return {
    cluster_config: {
      backends:        backends.map((ip)  => makeServer(ip, "backend",  "be")),
      frontends:       frontends.map((ip) => makeServer(ip, "frontend", "fe")),
      database_server: makeServer(storageIp, "database", "db"),
      nfs_server:      makeServer(storageIp, "nfs",      "nfs"),
      haproxy_servers:    s.haproxyNodes.map((n) => makeServer(n.ip, "haproxy", "haproxy")),
      monitoring_servers: s.monitoringNodes.map((n) => makeServer(n.ip, "monitoring", "mon")),
      package_config:  packageConfig,
    },
    license_config: {
      licensed_accounts:  s.licensedAccounts,
      cluster_backends:   Math.max(2, backends.length),
      cluster_frontends:  frontends.length,
      licensed_resources: Math.max(1, s.resourceAccounts),
      licensee_name:      s.licenseeRu || "Organization",
      licensee_name_eng:  s.licenseeEn || "Organization",
    },
    package_config: packageConfig,
  };
}

type ZSet = StoreApi<JobState>["setState"];
type ZGet = StoreApi<JobState>["getState"];

/** Подключает SSE-стрим и обновляет store через события */
function connectSSE(deployment_id: string, set: ZSet, _get: ZGet): EventSource {
  const es = new EventSource(`/api/deployment/${deployment_id}/stream`);

  es.onmessage = (e: MessageEvent) => {
    try {
      const event = JSON.parse(e.data as string) as {
        type: string;
        data?: { level?: string; message?: string; phase?: string; progress?: number; error?: string };
      };

      if (event.type === "log" && event.data) {
        const level   = event.data.level   ?? "INFO";
        const message = event.data.message ?? "";
        set((st) => ({ logs: [...st.logs, `[${ts()}] [${level}]  ${message}`] }));
      } else if (event.type === "phase" && event.data) {
        const phase    = event.data.phase    ?? "";
        const progress = event.data.progress ?? phaseToProgress(phase);
        set((st) => ({
          activeDeployment: st.activeDeployment
            ? { ...st.activeDeployment, currentPhase: phase, progress, status: phaseToStatus(phase) }
            : null,
          logs: [...st.logs, `[${ts()}] [PHASE] ${phase.replace(/_/g, " ")} (${progress}%)`],
        }));
      } else if (event.type === "completed") {
        es.close();
        set((st) => ({
          activeDeployment: st.activeDeployment
            ? { ...st.activeDeployment, status: "success", progress: 100, currentPhase: "success" }
            : null,
          logs: [...st.logs, `[${ts()}] [OK]   Установка завершена успешно`],
        }));
        clearActiveId();
      } else if (event.type === "failed") {
        const error = event.data?.error ?? "Неизвестная ошибка";
        es.close();
        set((st) => ({
          activeDeployment: st.activeDeployment
            ? { ...st.activeDeployment, status: "failed", currentPhase: "failed", progress: 0 }
            : null,
          logs: [...st.logs, `[${ts()}] [ERR]  ${error}`],
        }));
        clearActiveId();
      }
    } catch { /* игнорируем ошибки парсинга */ }
  };

  es.onerror = () => {
    es.close();
    set((st) => {
      const isStillRunning = st.activeDeployment?.status === "running";
      return {
        activeDeployment: isStillRunning && st.activeDeployment
          ? { ...st.activeDeployment, status: "failed", currentPhase: "failed" }
          : st.activeDeployment,
        logs: isStillRunning
          ? [...st.logs, `[${ts()}] [ERR]  Соединение с сервером прервано`]
          : st.logs,
      };
    });
  };

  return es;
}

// ── Store ─────────────────────────────────────────────────────────

export const useJobStore = create<JobState>((set, get) => ({
  activeDeployment: null,
  logs: [],
  isLaunching: false,
  isRestoring: false,

  // ── Запуск нового деплоя ────────────────────────────────────────
  launchDeploy: async () => {
    if (get().isLaunching) return;
    set({ isLaunching: true, logs: [], activeDeployment: null });

    const s = useDeployStore.getState();
    const body = buildRequestBody(s);

    try {
      const resp = await fetch("/api/deployment/start", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });

      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const err = await resp.json();
          detail = err?.detail?.message ?? err?.detail ?? detail;
          if (Array.isArray(err?.detail?.errors)) {
            detail += ": " + err.detail.errors.join("; ");
          }
        } catch { /* ignore */ }
        set({ isLaunching: false, logs: [`[${ts()}] [ERR]  ${detail}`] });
        return;
      }

      const { deployment_id } = (await resp.json()) as { deployment_id: string };
      saveActiveId(deployment_id);

      set({
        isLaunching: false,
        activeDeployment: {
          id:           deployment_id,
          startedAt:    new Date().toLocaleString("ru"),
          status:       "running",
          currentPhase: "configuration",
          progress:     0,
        },
        logs: [`[${ts()}] [INFO]  Деплой запущен (ID: ${deployment_id})`],
      });

      connectSSE(deployment_id, set, get);
      startPolling(deployment_id, set);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      set({ isLaunching: false, logs: [`[${ts()}] [ERR]  ${msg}`] });
    }
  },

  // ── Восстановление из localStorage + API ───────────────────────
  restoreDeployment: async () => {
    const savedId = loadActiveId();
    if (!savedId || get().activeDeployment) return;

    set({ isRestoring: true });
    try {
      const resp = await fetch(`/api/deployment/${savedId}`);
      if (!resp.ok) {
        clearActiveId();
        set({ isRestoring: false });
        return;
      }

      const data = await resp.json() as {
        deployment_id: string;
        status: string;
        created_at: string;
        logs: Array<{ level: string; phase: string | null; message: string; created_at: string }>;
      };

      const phase = data.status;
      const status = phaseToStatus(phase);
      const progress = phaseToProgress(phase);

      // Формируем логи из БД (порядок reversed — API возвращает desc)
      const dbLogs = [...data.logs]
        .reverse()
        .map(l => `[${formatLogTs(l.created_at)}] [${l.level}]  ${l.message}`);

      set({
        isRestoring: false,
        activeDeployment: {
          id:           data.deployment_id,
          startedAt:    new Date(data.created_at).toLocaleString("ru"),
          status,
          currentPhase: phase,
          progress,
        },
        logs: dbLogs,
      });

      // Подключаем SSE + polling только если деплой ещё в процессе
      if (status !== "success" && status !== "failed") {
        connectSSE(savedId, set, get);
        startPolling(savedId, set);
      } else {
        clearActiveId();
      }
    } catch {
      set({ isRestoring: false });
    }
  },

  // ── Активировать деплой по ID (клик из History) ────────────────
  activateDeployment: async (id: string) => {
    // Если уже этот — ничего не делаем
    if (get().activeDeployment?.id === id) return;

    set({ isRestoring: true, logs: [], activeDeployment: null });
    try {
      const resp = await fetch(`/api/deployment/${id}`);
      if (!resp.ok) { set({ isRestoring: false }); return; }

      const data = await resp.json() as {
        deployment_id: string;
        status: string;
        created_at: string;
        logs: Array<{ level: string; phase: string | null; message: string; created_at: string }>;
      };

      const phase    = data.status;
      const status   = phaseToStatus(phase);
      const progress = phaseToProgress(phase);
      const dbLogs   = [...data.logs]
        .reverse()
        .map(l => `[${formatLogTs(l.created_at)}] [${l.level}]  ${l.message}`);

      saveActiveId(id);
      set({
        isRestoring: false,
        activeDeployment: {
          id:           data.deployment_id,
          startedAt:    new Date(data.created_at).toLocaleString("ru"),
          status,
          currentPhase: phase,
          progress,
        },
        logs: dbLogs,
      });

      if (status !== "success" && status !== "failed") {
        connectSSE(id, set, get);
        startPolling(id, set);
      }
    } catch {
      set({ isRestoring: false });
    }
  },

  clearJob: () => {
    stopPolling();
    clearActiveId();
    set({ activeDeployment: null, logs: [] });
  },
}));
