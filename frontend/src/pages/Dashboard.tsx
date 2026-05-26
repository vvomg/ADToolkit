import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  RefreshCw,
  Server,
  AlertTriangle,
  Activity,
  Wifi,
  WifiOff,
  Shield,
  BarChart2,
  Database,
  Plus,
  Pencil,
  Trash2,
  Terminal,
} from "lucide-react";
import { NodeStatusBadge } from "@/components/shared/NodeStatusBadge";
import {
  AddNodeModal,
  type MonitorNodePublic,
} from "@/components/dashboard/AddNodeModal";
import { NodeCommandsModal, type EnrichedCommand } from "@/components/dashboard/NodeCommandsModal";
import type { NodeStatus } from "@/mock/cluster";

// ── API Types ──────────────────────────────────────────────────────

interface LiveNodeInfo {
  ip: string;
  hostname: string;
  role: string;
  check_type: "cmd" | "ssh";
  online: boolean;
  error: string | null;
  os_name: string | null;
  os_version: string | null;
  os_pretty: string | null;
  version: string | null;
  uptime: string | null;
  load: number | null;
  connections: number | null;
  cluster_status: string | null;
  raw_info: Record<string, unknown> | null;
  checked_at: string;
}

// Extended reg type — backend may return cmd fields
interface MonitorNodePublicExtended extends MonitorNodePublic {
  cmd_commands?: EnrichedCommand[] | null;
  cmd_help_fetched_at?: string | null;
  cmd_cluster_status?: string | null;
}

interface LiveClusterStatus {
  nodes: LiveNodeInfo[];
  total: number;
  online: number;
  offline: number;
  checked_at: string;
}

// ── Merged node for rendering ──────────────────────────────────────

interface MergedNode {
  reg: MonitorNodePublic;
  live: LiveNodeInfo | null;
}

// ── Helpers ────────────────────────────────────────────────────────

function formatUptime(uptime: string | null): string {
  if (!uptime) return "—";
  // Try ISO date
  try {
    const d = new Date(uptime);
    if (!isNaN(d.getTime())) {
      const diffMs = Date.now() - d.getTime();
      if (diffMs < 0) return uptime;
      const days = Math.floor(diffMs / 86_400_000);
      const hours = Math.floor((diffMs % 86_400_000) / 3_600_000);
      const mins = Math.floor((diffMs % 3_600_000) / 60_000);
      if (days > 0) return `↑ ${days}d ${hours}h`;
      if (hours > 0) return `↑ ${hours}h ${mins}m`;
      return `↑ ${mins}m`;
    }
  } catch { /* not a date */ }
  // Try numeric seconds
  const secs = Number(uptime);
  if (!isNaN(secs) && secs > 0) {
    const days = Math.floor(secs / 86_400);
    const hours = Math.floor((secs % 86_400) / 3_600);
    const mins = Math.floor((secs % 3_600) / 60);
    if (days > 0) return `↑ ${days}d ${hours}h`;
    if (hours > 0) return `↑ ${hours}h ${mins}m`;
    return `↑ ${mins}m`;
  }
  return `↑ ${uptime}`;
}

function formatTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString("ru"); }
  catch { return iso; }
}

function nodeTypeName(t: MonitorNodePublic["node_type"]): string {
  switch (t) {
    case "ivamail_backend":  return "Backend";
    case "ivamail_frontend": return "Frontend";
    case "haproxy":          return "HAProxy";
    case "nfs":              return "NFS/БД";
    case "monitoring":       return "Мониторинг";
  }
}

// ── Role icon ──────────────────────────────────────────────────────

function RoleIcon({
  nodeType,
  online,
}: {
  nodeType: MonitorNodePublic["node_type"];
  online: boolean;
}) {
  switch (nodeType) {
    case "haproxy":
      return <Shield size={14} className={online ? "text-blue" : "text-red"} />;
    case "nfs":
      return <Database size={14} className={online ? "text-peach" : "text-red"} />;
    case "monitoring":
      return <BarChart2 size={14} className={online ? "text-mauve" : "text-red"} />;
    default:
      return online
        ? <Wifi size={14} className="text-green" />
        : <WifiOff size={14} className="text-red" />;
  }
}

// ── IVA Mail card ──────────────────────────────────────────────────

function IvaMailCard({
  node,
  onEdit,
  onDelete,
  deletingId,
  setDeletingId,
  onOpenCommands,
}: {
  node: MergedNode;
  onEdit: (n: MonitorNodePublic) => void;
  onDelete: (id: number) => void;
  deletingId: number | null;
  setDeletingId: (id: number | null) => void;
  onOpenCommands: (nodeId: number, label: string, commands?: EnrichedCommand[] | null, fetchedAt?: string | null) => void;
}) {
  const { reg, live } = node;
  const online = live?.online ?? false;
  const status: NodeStatus = live ? (online ? "online" : "offline") : "unknown";
  const isConfirming = deletingId === reg.id;
  const [discovering, setDiscovering] = useState(false);

  const extReg = reg as MonitorNodePublicExtended;
  const clusterStatus = live?.cluster_status ?? extReg.cmd_cluster_status ?? null;
  const savedCommands = extReg.cmd_commands ?? null;
  const cmdCount = savedCommands?.length ?? 0;
  const nodeLabel = reg.display_name ?? reg.hostname ?? reg.ip;

  // Short version: "26.05.7006_24b8d87/..." → "v26.05.7006"
  const shortVersion = live?.version
    ? `v${live.version.split("_")[0]}`
    : null;

  const clusterBadgeClass =
    clusterStatus === "DISPATCHER"
      ? "text-blue ring-blue/40 bg-blue/10"
      : clusterStatus === "SLAVE"
      ? "text-mauve ring-mauve/40 bg-mauve/10"
      : "text-yellow ring-yellow/40 bg-yellow/10";

  async function handleDiscover(e: React.MouseEvent) {
    e.stopPropagation();
    setDiscovering(true);
    try {
      const resp = await fetch(`/api/monitor/nodes/${reg.id}/discover-commands`, { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json() as { commands: EnrichedCommand[]; fetched_at: string };
      onOpenCommands(reg.id, nodeLabel, data.commands, data.fetched_at);
    } catch { /* silently ignore */ }
    finally { setDiscovering(false); }
  }

  return (
    <motion.div
      variants={{ hidden: { opacity: 0, y: 12 }, visible: { opacity: 1, y: 0 } }}
      className={`bg-surface0 border rounded-xl p-4 flex flex-col gap-0 transition-colors ${
        online ? "border-surface1 hover:border-green/30" : live ? "border-red/20" : "border-surface1"
      }`}
    >
      {/* Top row */}
      <div className="flex items-start justify-between mb-2.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <RoleIcon nodeType={reg.node_type} online={online} />
          <span className="text-[10px] font-mono text-overlay0 uppercase tracking-wider">
            {nodeTypeName(reg.node_type)}
          </span>
          {clusterStatus && (
            <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ring-1 ${clusterBadgeClass}`}>
              {clusterStatus}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <NodeStatusBadge status={status} />
          <button
            onClick={() => onOpenCommands(reg.id, nodeLabel, savedCommands, extReg.cmd_help_fetched_at)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-overlay0 hover:text-blue hover:bg-blue/10 transition-colors text-[9px] font-mono"
            title="Команды сервера"
          >
            <Terminal size={10} />
            {cmdCount > 0 ? `CMD (${cmdCount})` : "CMD"}
          </button>
        </div>
      </div>

      {/* IP + hostname */}
      <p className="font-mono text-sm text-text font-medium">{reg.ip}</p>
      <p className="text-[11px] text-overlay0 mt-0.5 truncate">
        {nodeLabel}
      </p>

      {/* OS */}
      {live?.os_pretty && (
        <div className="mt-2 pt-2 border-t border-surface1">
          <p className="text-[10px] text-subtext">{live.os_pretty}</p>
        </div>
      )}

      {/* CMD live data */}
      {online && live?.check_type === "cmd" && (
        <div className="mt-2 pt-2 border-t border-surface1 space-y-1">
          {shortVersion && (
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-overlay0">Версия</span>
              <span className="text-[10px] font-mono text-subtext">{shortVersion}</span>
            </div>
          )}
          {live.uptime && (
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-overlay0">Аптайм</span>
              <span className="text-[10px] font-mono text-green">{formatUptime(live.uptime)}</span>
            </div>
          )}
          {live.load !== null && (
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-overlay0">Нагрузка</span>
              <span className={`text-[10px] font-mono ${
                live.load > 0.8 ? "text-red" : live.load > 0.5 ? "text-yellow" : "text-subtext"
              }`}>
                {live.load.toFixed(2)}
              </span>
            </div>
          )}
          {live.connections !== null && (
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-overlay0">Аккаунты</span>
              <span className="text-[10px] font-mono text-subtext">{live.connections}</span>
            </div>
          )}
        </div>
      )}

      {/* Web links */}
      <div className="mt-2 pt-2 border-t border-surface1 space-y-1">
        <a href={`http://${reg.ip}/User/Client/`} target="_blank" rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-[10px] text-blue hover:text-blue/80 transition-colors">
          <span>🔗</span> Веб-интерфейс
        </a>
        <a href={`http://${reg.ip}/Admin/`} target="_blank" rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-[10px] text-blue hover:text-blue/80 transition-colors">
          <span>🔗</span> Администрирование
        </a>
      </div>

      {/* Offline error */}
      {live && !online && live.error && (
        <div className="mt-2 pt-2 border-t border-red/10">
          <p className="text-[10px] text-red/80 leading-relaxed line-clamp-2">{live.error}</p>
        </div>
      )}

      {/* Footer */}
      <div className="mt-3 pt-2 border-t border-surface1 flex items-center justify-between">
        <span className="text-[9px] text-overlay0/50 font-mono">
          {live ? formatTime(live.checked_at) : "—"}
        </span>
        <div className="flex items-center gap-1">
          {online && cmdCount === 0 && (
            <button
              onClick={handleDiscover}
              disabled={discovering}
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[9px] text-overlay0 hover:text-peach hover:bg-peach/10 border border-surface1 hover:border-peach/30 transition-colors disabled:opacity-50"
              title="Обнаружить команды"
            >
              {discovering
                ? <span className="w-2 h-2 border border-peach border-t-transparent rounded-full animate-spin" />
                : <Terminal size={9} />}
              {discovering ? "..." : "Обнаружить"}
            </button>
          )}
          <button onClick={() => onEdit(reg)}
            className="p-1 rounded text-overlay0 hover:text-text hover:bg-surface1 transition-colors"
            title="Редактировать">
            <Pencil size={11} />
          </button>
          {isConfirming ? (
            <button onClick={() => onDelete(reg.id)}
              className="px-2 py-0.5 rounded text-[10px] font-medium text-red bg-red/10 hover:bg-red/20 border border-red/20 transition-colors"
              title="Подтвердить удаление">
              Удалить?
            </button>
          ) : (
            <button onClick={() => setDeletingId(reg.id)}
              className="p-1 rounded text-overlay0 hover:text-red hover:bg-red/10 transition-colors"
              title="Удалить">
              <Trash2 size={11} />
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── SSH-only card ──────────────────────────────────────────────────

function SshCard({
  node,
  onEdit,
  onDelete,
  deletingId,
  setDeletingId,
}: {
  node: MergedNode;
  onEdit: (n: MonitorNodePublic) => void;
  onDelete: (id: number) => void;
  deletingId: number | null;
  setDeletingId: (id: number | null) => void;
}) {
  const { reg, live } = node;
  const online = live?.online ?? false;
  const status: NodeStatus = live ? (online ? "online" : "offline") : "unknown";
  const isConfirming = deletingId === reg.id;

  return (
    <motion.div
      variants={{ hidden: { opacity: 0, y: 12 }, visible: { opacity: 1, y: 0 } }}
      className={`bg-surface0 border rounded-xl p-4 flex flex-col gap-0 transition-colors ${
        online ? "border-surface1 hover:border-green/30" : live ? "border-red/20" : "border-surface1"
      }`}
    >
      {/* Top row */}
      <div className="flex items-start justify-between mb-2.5">
        <div className="flex items-center gap-1.5">
          <RoleIcon nodeType={reg.node_type} online={online} />
          <span className="text-[10px] font-mono text-overlay0 uppercase tracking-wider">
            {nodeTypeName(reg.node_type)}
          </span>
        </div>
        <NodeStatusBadge status={status} />
      </div>

      {/* IP + hostname */}
      <p className="font-mono text-sm text-text font-medium">{reg.ip}</p>
      <p className="text-[11px] text-overlay0 mt-0.5 truncate">
        {reg.display_name ?? reg.hostname ?? reg.ip}
      </p>

      {/* SSH status + OS */}
      {live && (
        <div className="mt-2 pt-2 border-t border-surface1 space-y-0.5">
          {online ? (
            <p className="text-[10px] text-green">SSH доступен ✓</p>
          ) : null}
          {live.os_pretty && (
            <p className="text-[10px] text-subtext">{live.os_pretty}</p>
          )}
        </div>
      )}

      {/* Offline error */}
      {live && !online && live.error && (
        <div className="mt-2 pt-2 border-t border-red/10">
          <p className="text-[10px] text-red/80 leading-relaxed line-clamp-2">{live.error}</p>
        </div>
      )}

      {/* Footer */}
      <div className="mt-3 pt-2 border-t border-surface1 flex items-center justify-between">
        <span className="text-[9px] text-overlay0/50 font-mono">
          {live ? formatTime(live.checked_at) : "—"}
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onEdit(reg)}
            className="p-1 rounded text-overlay0 hover:text-text hover:bg-surface1 transition-colors"
            title="Редактировать"
          >
            <Pencil size={11} />
          </button>
          {isConfirming ? (
            <button
              onClick={() => onDelete(reg.id)}
              className="px-2 py-0.5 rounded text-[10px] font-medium text-red bg-red/10 hover:bg-red/20 border border-red/20 transition-colors"
              title="Подтвердить удаление"
            >
              Удалить?
            </button>
          ) : (
            <button
              onClick={() => setDeletingId(reg.id)}
              className="p-1 rounded text-overlay0 hover:text-red hover:bg-red/10 transition-colors"
              title="Удалить"
            >
              <Trash2 size={11} />
            </button>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── Section header ─────────────────────────────────────────────────

function SectionHeader({
  title,
  onAdd,
}: {
  title: string;
  onAdd: () => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-medium text-overlay0 uppercase tracking-wider whitespace-nowrap">
        {title}
      </span>
      <div className="flex-1 h-px bg-surface1" />
      <button
        onClick={onAdd}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-subtext hover:text-text bg-surface0 hover:bg-surface1 border border-surface1 rounded-lg transition-colors"
      >
        <Plus size={12} />
        Добавить ноду
      </button>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────

function EmptyState({
  onImport,
  onAdd,
  importing,
}: {
  onImport: () => void;
  onAdd: () => void;
  importing: boolean;
}) {
  return (
    <div className="bg-surface0 border border-surface1 rounded-2xl p-12 flex flex-col items-center gap-4">
      <Server size={36} className="text-overlay0" />
      <div className="text-center">
        <p className="text-subtext text-sm font-medium">Реестр нод пуст</p>
        <p className="text-overlay0 text-xs mt-1">
          Добавьте ноды вручную или импортируйте из конфигурации деплоя
        </p>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <button
          onClick={onImport}
          disabled={importing}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue bg-blue/10 hover:bg-blue/20 border border-blue/30 rounded-lg transition-colors disabled:opacity-50"
        >
          {importing && (
            <span className="w-3 h-3 border border-blue border-t-transparent rounded-full animate-spin" />
          )}
          Импортировать из деплоя
        </button>
        <button
          onClick={onAdd}
          className="px-4 py-2 text-sm text-subtext hover:text-text bg-surface1 hover:bg-surface1/70 rounded-lg transition-colors"
        >
          Добавить ноду вручную
        </button>
      </div>
    </div>
  );
}

// ── Dashboard ──────────────────────────────────────────────────────

export function Dashboard() {
  const [registry, setRegistry] = useState<MonitorNodePublic[]>([]);
  const [liveStatus, setLiveStatus] = useState<LiveClusterStatus | null>(null);
  const [loadingRegistry, setLoadingRegistry] = useState(true);
  const [loadingLive, setLoadingLive] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // Add node modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editNode, setEditNode] = useState<MonitorNodePublic | null>(null);
  const [defaultNodeType, setDefaultNodeType] =
    useState<MonitorNodePublic["node_type"]>("ivamail_backend");

  // CMD commands modal state
  const [cmdModalOpen, setCmdModalOpen] = useState(false);
  const [cmdModalNode, setCmdModalNode] = useState<{
    nodeId: number;
    nodeLabel: string;
    commands?: EnrichedCommand[] | null;
    fetchedAt?: string | null;
  } | null>(null);

  function openCommandsModal(
    nodeId: number,
    label: string,
    commands?: EnrichedCommand[] | null,
    fetchedAt?: string | null,
  ) {
    setCmdModalNode({ nodeId, nodeLabel: label, commands, fetchedAt });
    setCmdModalOpen(true);
  }

  // ── Data fetching ──────────────────────────────────────────────

  const fetchRegistry = useCallback(async () => {
    try {
      const resp = await fetch("/api/monitor/nodes");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const nodes: MonitorNodePublic[] = await resp.json();
      setRegistry(nodes);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingRegistry(false);
    }
  }, []);

  const fetchLive = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const resp = await fetch("/api/cluster/nodes/live");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result: LiveClusterStatus = await resp.json();
      setLiveStatus(result);
    } catch {
      // Live fetch errors are soft — don't override main error
    } finally {
      setLoadingLive(false);
      if (showSpinner) setRefreshing(false);
    }
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchLive(false);
    setRefreshing(false);
  }, [fetchLive]);

  useEffect(() => {
    fetchRegistry();
    fetchLive();
  }, [fetchRegistry, fetchLive]);

  // 30s auto-refresh of live status only
  useEffect(() => {
    const interval = setInterval(() => fetchLive(), 30_000);
    return () => clearInterval(interval);
  }, [fetchLive]);

  // Dismiss delete confirm when clicking elsewhere
  useEffect(() => {
    if (deletingId === null) return;
    const handler = () => setDeletingId(null);
    window.addEventListener("click", handler, { capture: true, once: false });
    return () => window.removeEventListener("click", handler, { capture: true });
  }, [deletingId]);

  // ── Derived data ───────────────────────────────────────────────

  const liveByIp = new Map<string, LiveNodeInfo>();
  liveStatus?.nodes.forEach((n) => liveByIp.set(n.ip, n));

  const merged: MergedNode[] = registry.map((reg) => ({
    reg,
    live: liveByIp.get(reg.ip) ?? null,
  }));

  const ivaMailNodes = merged.filter(
    (n) =>
      n.reg.node_type === "ivamail_backend" ||
      n.reg.node_type === "ivamail_frontend"
  );
  const infraNodes = merged.filter(
    (n) =>
      n.reg.node_type !== "ivamail_backend" &&
      n.reg.node_type !== "ivamail_frontend"
  );

  const onlineCount = liveStatus?.online ?? 0;
  const offlineCount = liveStatus?.offline ?? 0;
  const totalCount = liveStatus?.total ?? 0;

  const loading = loadingRegistry || loadingLive;
  const registryEmpty = !loadingRegistry && registry.length === 0;

  // ── Handlers ───────────────────────────────────────────────────

  function openAddModal(type: MonitorNodePublic["node_type"]) {
    setEditNode(null);
    setDefaultNodeType(type);
    setModalOpen(true);
  }

  function openEditModal(node: MonitorNodePublic) {
    setEditNode(node);
    setModalOpen(true);
  }

  function handleSaved(node: MonitorNodePublic) {
    setRegistry((prev) => {
      const idx = prev.findIndex((n) => n.id === node.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = node;
        return next;
      }
      return [...prev, node];
    });
    fetchLive();
  }

  async function handleDelete(id: number) {
    setDeletingId(null);
    try {
      const resp = await fetch(`/api/monitor/nodes/${id}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setRegistry((prev) => prev.filter((n) => n.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleImport() {
    setImporting(true);
    try {
      const resp = await fetch("/api/monitor/nodes/import-deploy?force=false", {
        method: "POST",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await fetchRegistry();
      await fetchLive();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  // ── Shared card props helper ───────────────────────────────────

  const cardProps = {
    onEdit: openEditModal,
    onDelete: handleDelete,
    deletingId,
    setDeletingId,
    onOpenCommands: openCommandsModal,
  };

  // ── Render ─────────────────────────────────────────────────────

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text">Dashboard</h1>
          {loading ? (
            <p className="text-subtext text-sm mt-0.5">Загрузка...</p>
          ) : registryEmpty ? (
            <p className="text-subtext text-sm mt-0.5">Реестр нод пуст</p>
          ) : totalCount > 0 ? (
            <p className="text-subtext text-sm mt-0.5 flex items-center gap-1.5">
              Кластер IVA Mail · {onlineCount}/{totalCount} online
              {offlineCount > 0 && (
                <span className="text-red">· {offlineCount} offline</span>
              )}
              {liveStatus?.checked_at && (
                <span className="text-overlay0">
                  · {formatTime(liveStatus.checked_at)}
                </span>
              )}
            </p>
          ) : (
            <p className="text-subtext text-sm mt-0.5">Опрос узлов...</p>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 bg-surface0 hover:bg-surface1 text-subtext text-sm rounded-lg border border-surface1 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 bg-red/10 border border-red/20 rounded-xl px-4 py-3">
          <AlertTriangle size={14} className="text-red shrink-0" />
          <span className="text-red text-sm">{error}</span>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-6">
          <div className="h-4 w-48 bg-surface1 rounded animate-pulse" />
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="bg-surface0 border border-surface1 rounded-xl p-4 animate-pulse h-36"
              />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {registryEmpty && !error && (
        <EmptyState
          onImport={handleImport}
          onAdd={() => openAddModal("ivamail_backend")}
          importing={importing}
        />
      )}

      {/* Content */}
      {!loading && registry.length > 0 && (
        <div className="space-y-6">
          {/* IVA Mail section */}
          <div className="space-y-3">
            <SectionHeader
              title="Почтовые ноды IVA Mail"
              onAdd={() => openAddModal("ivamail_backend")}
            />
            {ivaMailNodes.length === 0 ? (
              <p className="text-[11px] text-overlay0 px-1">Нет почтовых нод</p>
            ) : (
              <motion.div
                className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
                initial="hidden"
                animate="visible"
                variants={{
                  visible: { transition: { staggerChildren: 0.05 } },
                }}
              >
                <AnimatePresence>
                  {ivaMailNodes.map((node) => (
                    <IvaMailCard
                      key={node.reg.id}
                      node={node}
                      {...cardProps}
                    />
                  ))}
                </AnimatePresence>
              </motion.div>
            )}
          </div>

          {/* Infrastructure section */}
          <div className="space-y-3">
            <SectionHeader
              title="Инфраструктура"
              onAdd={() => openAddModal("haproxy")}
            />
            {infraNodes.length === 0 ? (
              <p className="text-[11px] text-overlay0 px-1">Нет инфраструктурных нод</p>
            ) : (
              <motion.div
                className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3"
                initial="hidden"
                animate="visible"
                variants={{
                  visible: { transition: { staggerChildren: 0.05 } },
                }}
              >
                <AnimatePresence>
                  {infraNodes.map((node) => (
                    <SshCard
                      key={node.reg.id}
                      node={node}
                      {...cardProps}
                    />
                  ))}
                </AnimatePresence>
              </motion.div>
            )}
          </div>

          {/* Summary bar */}
          {totalCount > 0 && (
            <div className="flex items-center gap-4 px-4 py-3 bg-surface0 rounded-xl border border-surface1">
              <Activity size={14} className="text-overlay0 shrink-0" />
              <div className="flex items-center flex-wrap gap-4 text-xs w-full">
                <span className="text-overlay0">
                  Всего: <span className="text-text font-medium">{totalCount}</span>
                </span>
                <span className="text-overlay0">
                  Online: <span className="text-green font-medium">{onlineCount}</span>
                </span>
                {offlineCount > 0 && (
                  <span className="text-overlay0">
                    Offline: <span className="text-red font-medium">{offlineCount}</span>
                  </span>
                )}
                {liveStatus?.checked_at && (
                  <span className="text-overlay0 ml-auto">
                    Обновлено:{" "}
                    <span className="font-mono">{formatTime(liveStatus.checked_at)}</span>
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Add / Edit modal */}
      <AddNodeModal
        open={modalOpen}
        onClose={() => { setModalOpen(false); setEditNode(null); }}
        onSaved={handleSaved}
        editNode={editNode}
        defaultNodeType={defaultNodeType}
      />

      {/* CMD commands modal */}
      <NodeCommandsModal
        open={cmdModalOpen}
        onClose={() => setCmdModalOpen(false)}
        nodeId={cmdModalNode?.nodeId ?? 0}
        nodeLabel={cmdModalNode?.nodeLabel ?? ""}
        initialCommands={cmdModalNode?.commands ?? undefined}
        fetchedAt={cmdModalNode?.fetchedAt}
      />
    </div>
  );
}
