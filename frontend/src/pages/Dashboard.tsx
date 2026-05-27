import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  RefreshCw, Server, AlertTriangle, Activity, Wifi, WifiOff,
  Shield, BarChart2, BarChart, Database, HardDrive, Scale,
  Plus, Pencil, Trash2, Terminal, ChevronDown, ChevronRight,
  MoreHorizontal, MoveRight, X, Check,
} from "lucide-react";
import { NodeStatusBadge } from "@/components/shared/NodeStatusBadge";
import {
  AddNodeModal,
  type MonitorNodePublic,
  type NodeType,
} from "@/components/dashboard/AddNodeModal";
import { NodeCommandsModal, type EnrichedCommand } from "@/components/dashboard/NodeCommandsModal";
import type { NodeStatus } from "@/mock/cluster";

// ── Cluster type ───────────────────────────────────────────────────

interface Cluster {
  id: number;
  name: string;
  color: string;
  description: string | null;
  sort_order: number;
}

// ── API live types ─────────────────────────────────────────────────

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

interface MergedNode {
  reg: MonitorNodePublicExtended;
  live: LiveNodeInfo | null;
}

// ── Node type grouping constants ───────────────────────────────────

const LB_TYPES: NodeType[]      = ["haproxy", "load_balancer"];
const STORAGE_TYPES: NodeType[] = ["nfs", "nfs_backup"];
const MONITORING_TYPES: NodeType[] = [
  "monitoring", "monitoring_prometheus", "monitoring_grafana", "monitoring_graylog",
];

const COLOR_OPTIONS = [
  { value: "blue",     dot: "bg-blue" },
  { value: "green",    dot: "bg-green" },
  { value: "yellow",   dot: "bg-yellow" },
  { value: "peach",    dot: "bg-peach" },
  { value: "mauve",    dot: "bg-mauve" },
  { value: "teal",     dot: "bg-teal" },
  { value: "sapphire", dot: "bg-sapphire" },
  { value: "lavender", dot: "bg-lavender" },
  { value: "red",      dot: "bg-red" },
];

// ── Helpers ────────────────────────────────────────────────────────

function formatUptime(uptime: string | null): string {
  if (!uptime) return "—";
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

function nodeTypeName(t: NodeType): string {
  switch (t) {
    case "ivamail_backend":       return "Backend";
    case "ivamail_frontend":      return "Frontend";
    case "haproxy":               return "HAProxy";
    case "load_balancer":         return "Load Balancer";
    case "nfs":                   return "NFS Primary";
    case "nfs_backup":            return "NFS Резерв";
    case "monitoring":            return "Мониторинг";
    case "monitoring_prometheus": return "Prometheus";
    case "monitoring_grafana":    return "Grafana";
    case "monitoring_graylog":    return "Graylog";
  }
}

// ── RoleIcon ───────────────────────────────────────────────────────

function RoleIcon({ nodeType, online }: { nodeType: NodeType; online: boolean }) {
  const cls = (color: string) => online ? `text-${color}` : "text-red";
  switch (nodeType) {
    case "haproxy":               return <Shield size={14} className={cls("blue")} />;
    case "load_balancer":         return <Scale size={14} className={cls("teal")} />;
    case "nfs":                   return <Database size={14} className={cls("peach")} />;
    case "nfs_backup":            return <HardDrive size={14} className={cls("yellow")} />;
    case "monitoring":            return <BarChart2 size={14} className={cls("mauve")} />;
    case "monitoring_prometheus": return <Activity size={14} className={cls("peach")} />;
    case "monitoring_grafana":    return <BarChart size={14} className={cls("green")} />;
    case "monitoring_graylog":    return <BarChart2 size={14} className={cls("sapphire")} />;
    default:                      return online
      ? <Wifi size={14} className="text-green" />
      : <WifiOff size={14} className="text-red" />;
  }
}

// ── ClusterDot ─────────────────────────────────────────────────────

function ClusterDot({ color, size = 8 }: { color: string; size?: number }) {
  const dotColor = `bg-${color}`;
  return <span className={`${dotColor} rounded-full shrink-0`} style={{ width: size, height: size }} />;
}

// ── NodeMoveMenu ───────────────────────────────────────────────────

function NodeMoveMenu({
  nodeId,
  currentClusterId,
  clusters,
  onMove,
  onClose,
}: {
  nodeId: number;
  currentClusterId: number | null;
  clusters: Cluster[];
  onMove: (nodeId: number, clusterId: number | null) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="absolute bottom-full right-0 mb-1 z-50 w-48 bg-mantle border border-surface1 rounded-xl shadow-2xl overflow-hidden py-1"
      onClick={(e) => e.stopPropagation()}
    >
      <p className="text-[9px] text-overlay0 uppercase tracking-wider px-3 pt-1.5 pb-1">
        Переместить в кластер
      </p>
      {clusters.length === 0 && (
        <p className="text-[11px] text-overlay0 px-3 py-2 italic">Нет кластеров</p>
      )}
      {clusters.map((c) => (
        <button
          key={c.id}
          disabled={c.id === currentClusterId}
          onClick={() => { onMove(nodeId, c.id); onClose(); }}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left hover:bg-surface0 transition-colors disabled:opacity-35 disabled:cursor-default"
        >
          <ClusterDot color={c.color} />
          <span className="truncate">{c.name}</span>
          {c.id === currentClusterId && <Check size={10} className="ml-auto text-green shrink-0" />}
        </button>
      ))}
      {currentClusterId !== null && (
        <>
          <div className="h-px bg-surface1 my-1" />
          <button
            onClick={() => { onMove(nodeId, null); onClose(); }}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left text-overlay0 hover:bg-surface0 hover:text-text transition-colors"
          >
            <X size={11} className="shrink-0" />
            Убрать из кластера
          </button>
        </>
      )}
    </div>
  );
}

// ── ClusterHeaderMenu ──────────────────────────────────────────────

function ClusterHeaderMenu({
  cluster,
  onRename,
  onChangeColor,
  onDelete,
  onClose,
}: {
  cluster: Cluster;
  onRename: () => void;
  onChangeColor: (color: string) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [showColors, setShowColors] = useState(false);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="absolute top-full right-0 mt-1 z-50 w-52 bg-mantle border border-surface1 rounded-xl shadow-2xl overflow-hidden py-1"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        onClick={() => { onRename(); onClose(); }}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left hover:bg-surface0 transition-colors"
      >
        <Pencil size={11} /> Переименовать
      </button>
      <button
        onClick={() => setShowColors(!showColors)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left hover:bg-surface0 transition-colors"
      >
        <span className={`w-3 h-3 rounded-full bg-${cluster.color} shrink-0`} />
        Цвет метки
        <ChevronDown size={10} className={`ml-auto transition-transform ${showColors ? "rotate-180" : ""}`} />
      </button>
      {showColors && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {COLOR_OPTIONS.map((c) => (
            <button
              key={c.value}
              onClick={() => { onChangeColor(c.value); onClose(); }}
              className={`w-5 h-5 rounded-full ${c.dot} hover:scale-125 transition-transform ${cluster.color === c.value ? "ring-2 ring-white/40" : ""}`}
              title={c.value}
            />
          ))}
        </div>
      )}
      <div className="h-px bg-surface1 my-1" />
      <button
        onClick={() => { onDelete(); onClose(); }}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left text-red hover:bg-red/10 transition-colors"
      >
        <Trash2 size={11} /> Удалить кластер
      </button>
    </div>
  );
}

// ── CreateClusterModal ─────────────────────────────────────────────

function CreateClusterModal({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, color: string) => void;
}) {
  const [name, setName] = useState("");
  const [color, setColor] = useState("blue");

  useEffect(() => {
    if (open) { setName(""); setColor("blue"); }
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: "blur(4px)", background: "rgba(17,17,27,0.7)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-surface0 border border-surface1 rounded-2xl w-full max-w-sm shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface1">
          <h2 className="text-sm font-semibold text-text">Новый кластер</h2>
          <button onClick={onClose} className="text-overlay0 hover:text-text transition-colors">
            <X size={16} />
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div className="space-y-1.5">
            <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">Название</label>
            <input
              autoFocus
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) { onCreate(name.trim(), color); onClose(); } }}
              placeholder="Производство"
              className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">Цвет</label>
            <div className="flex flex-wrap gap-2">
              {COLOR_OPTIONS.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setColor(c.value)}
                  className={`w-6 h-6 rounded-full ${c.dot} hover:scale-110 transition-transform ${color === c.value ? "ring-2 ring-white/50 scale-110" : ""}`}
                />
              ))}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-surface1">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-subtext hover:text-text bg-surface1 hover:bg-surface1/70 rounded-lg transition-colors"
          >
            Отмена
          </button>
          <button
            onClick={() => { if (name.trim()) { onCreate(name.trim(), color); onClose(); } }}
            disabled={!name.trim()}
            className="px-4 py-2 text-sm font-medium text-blue bg-blue/20 hover:bg-blue/30 border border-blue/30 rounded-lg transition-colors disabled:opacity-50"
          >
            Создать
          </button>
        </div>
      </div>
    </div>
  );
}

// ── RenameClusterModal ─────────────────────────────────────────────

function RenameClusterModal({
  cluster,
  onClose,
  onSave,
}: {
  cluster: Cluster | null;
  onClose: () => void;
  onSave: (id: number, name: string) => void;
}) {
  const [name, setName] = useState(cluster?.name ?? "");

  useEffect(() => { setName(cluster?.name ?? ""); }, [cluster]);

  if (!cluster) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: "blur(4px)", background: "rgba(17,17,27,0.7)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-surface0 border border-surface1 rounded-2xl w-full max-w-sm shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface1">
          <h2 className="text-sm font-semibold text-text">Переименовать кластер</h2>
          <button onClick={onClose} className="text-overlay0 hover:text-text transition-colors"><X size={16} /></button>
        </div>
        <div className="px-5 py-4">
          <input
            autoFocus
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) { onSave(cluster.id, name.trim()); onClose(); } }}
            className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text outline-none transition-colors"
          />
        </div>
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-surface1">
          <button onClick={onClose} className="px-4 py-2 text-sm text-subtext hover:text-text bg-surface1 rounded-lg transition-colors">Отмена</button>
          <button
            onClick={() => { if (name.trim()) { onSave(cluster.id, name.trim()); onClose(); } }}
            disabled={!name.trim()}
            className="px-4 py-2 text-sm font-medium text-blue bg-blue/20 hover:bg-blue/30 border border-blue/30 rounded-lg transition-colors disabled:opacity-50"
          >
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Shared card action props ───────────────────────────────────────

interface CardActionProps {
  clusters: Cluster[];
  onEdit: (n: MonitorNodePublic) => void;
  onDelete: (id: number) => void;
  deletingId: number | null;
  setDeletingId: (id: number | null) => void;
  onOpenCommands: (nodeId: number, label: string, commands?: EnrichedCommand[] | null, fetchedAt?: string | null) => void;
  onMoveToCluster: (nodeId: number, clusterId: number | null) => void;
}

// ── Card footer with move menu ─────────────────────────────────────

function CardFooter({
  node,
  live,
  discovering,
  onDiscover,
  moveMenuNodeId,
  setMoveMenuNodeId,
  ...props
}: CardActionProps & {
  node: MonitorNodePublicExtended;
  live: LiveNodeInfo | null;
  discovering: boolean;
  onDiscover: () => void;
  moveMenuNodeId: number | null;
  setMoveMenuNodeId: (id: number | null) => void;
}) {
  const online = live?.online ?? false;
  const isConfirming = props.deletingId === node.id;
  const savedCommands = node.cmd_commands ?? null;
  const cmdCount = savedCommands?.length ?? 0;
  const isMoveMenuOpen = moveMenuNodeId === node.id;

  return (
    <div className="mt-3 pt-2 border-t border-surface1 flex items-center justify-between">
      <span className="text-[9px] text-overlay0/50 font-mono">
        {live ? formatTime(live.checked_at) : "—"}
      </span>
      <div className="flex items-center gap-1 relative">
        {online && cmdCount === 0 && (
          <button
            onClick={onDiscover}
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

        {/* Move to cluster */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            setMoveMenuNodeId(isMoveMenuOpen ? null : node.id);
          }}
          className="p-1 rounded text-overlay0 hover:text-blue hover:bg-blue/10 transition-colors"
          title="Переместить в кластер"
        >
          <MoveRight size={11} />
        </button>
        {isMoveMenuOpen && (
          <NodeMoveMenu
            nodeId={node.id}
            currentClusterId={node.cluster_id}
            clusters={props.clusters}
            onMove={props.onMoveToCluster}
            onClose={() => setMoveMenuNodeId(null)}
          />
        )}

        <button onClick={() => props.onEdit(node)}
          className="p-1 rounded text-overlay0 hover:text-text hover:bg-surface1 transition-colors"
          title="Редактировать">
          <Pencil size={11} />
        </button>
        {isConfirming ? (
          <button onClick={() => props.onDelete(node.id)}
            className="px-2 py-0.5 rounded text-[10px] font-medium text-red bg-red/10 hover:bg-red/20 border border-red/20 transition-colors">
            Удалить?
          </button>
        ) : (
          <button onClick={() => props.setDeletingId(node.id)}
            className="p-1 rounded text-overlay0 hover:text-red hover:bg-red/10 transition-colors"
            title="Удалить">
            <Trash2 size={11} />
          </button>
        )}
      </div>
    </div>
  );
}

// ── IvaMailCard ────────────────────────────────────────────────────

function IvaMailCard({
  node,
  moveMenuNodeId,
  setMoveMenuNodeId,
  ...props
}: CardActionProps & {
  node: MergedNode;
  moveMenuNodeId: number | null;
  setMoveMenuNodeId: (id: number | null) => void;
}) {
  const { reg, live } = node;
  const online = live?.online ?? false;
  const status: NodeStatus = live ? (online ? "online" : "offline") : "unknown";
  const [discovering, setDiscovering] = useState(false);
  const extReg = reg as MonitorNodePublicExtended;
  const clusterStatus = live?.cluster_status ?? extReg.cmd_cluster_status ?? null;
  const savedCommands = extReg.cmd_commands ?? null;
  const cmdCount = savedCommands?.length ?? 0;
  const nodeLabel = reg.display_name ?? reg.hostname ?? reg.ip;
  const shortVersion = live?.version ? `v${live.version.split("_")[0]}` : null;

  const clusterBadgeClass =
    clusterStatus === "DISPATCHER" ? "text-blue ring-blue/40 bg-blue/10" :
    clusterStatus === "SLAVE"      ? "text-mauve ring-mauve/40 bg-mauve/10" :
                                     "text-yellow ring-yellow/40 bg-yellow/10";

  async function handleDiscover() {
    setDiscovering(true);
    try {
      const resp = await fetch(`/api/monitor/nodes/${reg.id}/discover-commands`, { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json() as { commands: EnrichedCommand[]; fetched_at: string };
      props.onOpenCommands(reg.id, nodeLabel, data.commands, data.fetched_at);
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
            onClick={() => props.onOpenCommands(reg.id, nodeLabel, savedCommands, extReg.cmd_help_fetched_at)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-overlay0 hover:text-blue hover:bg-blue/10 transition-colors text-[9px] font-mono"
            title="Команды сервера"
          >
            <Terminal size={10} />
            {cmdCount > 0 ? `CMD (${cmdCount})` : "CMD"}
          </button>
        </div>
      </div>

      <p className="font-mono text-sm text-text font-medium">{reg.ip}</p>
      <p className="text-[11px] text-overlay0 mt-0.5 truncate">{nodeLabel}</p>

      {live?.os_pretty && (
        <div className="mt-2 pt-2 border-t border-surface1">
          <p className="text-[10px] text-subtext">{live.os_pretty}</p>
        </div>
      )}

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
              }`}>{live.load.toFixed(2)}</span>
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

      {live && !online && live.error && (
        <div className="mt-2 pt-2 border-t border-red/10">
          <p className="text-[10px] text-red/80 leading-relaxed line-clamp-2">{live.error}</p>
        </div>
      )}

      <CardFooter
        node={extReg}
        live={live}
        discovering={discovering}
        onDiscover={handleDiscover}
        moveMenuNodeId={moveMenuNodeId}
        setMoveMenuNodeId={setMoveMenuNodeId}
        {...props}
      />
    </motion.div>
  );
}

// ── SshCard (NFS, HAProxy, LB, Monitoring) ─────────────────────────

function SshCard({
  node,
  wide,
  moveMenuNodeId,
  setMoveMenuNodeId,
  ...props
}: CardActionProps & {
  node: MergedNode;
  wide?: boolean;
  moveMenuNodeId: number | null;
  setMoveMenuNodeId: (id: number | null) => void;
}) {
  const { reg, live } = node;
  const online = live?.online ?? false;
  const status: NodeStatus = live ? (online ? "online" : "offline") : "unknown";
  const extReg = reg as MonitorNodePublicExtended;

  const isNfsBackup = reg.node_type === "nfs_backup";

  return (
    <motion.div
      variants={{ hidden: { opacity: 0, y: 12 }, visible: { opacity: 1, y: 0 } }}
      className={`bg-surface0 border rounded-xl p-4 flex flex-col gap-0 transition-colors ${
        wide ? "sm:col-span-2" : ""
      } ${
        online ? "border-surface1 hover:border-green/30" : live ? "border-red/20" : "border-surface1"
      }`}
    >
      <div className="flex items-start justify-between mb-2.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <RoleIcon nodeType={reg.node_type} online={online} />
          <span className="text-[10px] font-mono text-overlay0 uppercase tracking-wider">
            {nodeTypeName(reg.node_type)}
          </span>
          {isNfsBackup && (
            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded ring-1 text-yellow ring-yellow/40 bg-yellow/10">
              РЕЗЕРВ
            </span>
          )}
          {reg.node_type === "monitoring" && (
            <span className="text-[9px] font-mono px-1.5 py-0.5 rounded ring-1 text-mauve ring-mauve/40 bg-mauve/10">
              all-in-one
            </span>
          )}
        </div>
        <NodeStatusBadge status={status} />
      </div>

      <p className="font-mono text-sm text-text font-medium">{reg.ip}</p>
      <p className="text-[11px] text-overlay0 mt-0.5 truncate">
        {reg.display_name ?? reg.hostname ?? reg.ip}
      </p>

      {live && (
        <div className="mt-2 pt-2 border-t border-surface1 space-y-0.5">
          {online && <p className="text-[10px] text-green">SSH доступен ✓</p>}
          {live.os_pretty && <p className="text-[10px] text-subtext">{live.os_pretty}</p>}
        </div>
      )}

      {live && !online && live.error && (
        <div className="mt-2 pt-2 border-t border-red/10">
          <p className="text-[10px] text-red/80 leading-relaxed line-clamp-2">{live.error}</p>
        </div>
      )}

      <CardFooter
        node={extReg}
        live={live}
        discovering={false}
        onDiscover={() => {}}
        moveMenuNodeId={moveMenuNodeId}
        setMoveMenuNodeId={setMoveMenuNodeId}
        {...props}
      />
    </motion.div>
  );
}

// ── TypeSection ────────────────────────────────────────────────────

function TypeSection({
  title,
  nodes,
  onAddNode,
  cardType,
  moveMenuNodeId,
  setMoveMenuNodeId,
  ...props
}: CardActionProps & {
  title: string;
  nodes: MergedNode[];
  onAddNode: () => void;
  cardType: "ivamail" | "ssh";
  moveMenuNodeId: number | null;
  setMoveMenuNodeId: (id: number | null) => void;
}) {
  const isMonitoringAllInOne =
    cardType === "ssh" &&
    nodes.length === 1 &&
    nodes[0].reg.node_type === "monitoring";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-medium text-overlay0 uppercase tracking-wider whitespace-nowrap">
          {title} ({nodes.length})
        </span>
        <div className="flex-1 h-px bg-surface1/60" />
        <button
          onClick={onAddNode}
          className="flex items-center gap-1 px-2 py-1 text-[10px] text-overlay0 hover:text-text bg-surface1/50 hover:bg-surface1 rounded-md transition-colors"
        >
          <Plus size={10} /> Нода
        </button>
      </div>
      <motion.div
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2.5"
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: 0.04 } } }}
      >
        <AnimatePresence>
          {nodes.map((n) =>
            cardType === "ivamail" ? (
              <IvaMailCard
                key={n.reg.id}
                node={n}
                moveMenuNodeId={moveMenuNodeId}
                setMoveMenuNodeId={setMoveMenuNodeId}
                {...props}
              />
            ) : (
              <SshCard
                key={n.reg.id}
                node={n}
                wide={isMonitoringAllInOne}
                moveMenuNodeId={moveMenuNodeId}
                setMoveMenuNodeId={setMoveMenuNodeId}
                {...props}
              />
            )
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}

// ── ClusterBlock ───────────────────────────────────────────────────

function ClusterBlock({
  cluster,
  nodes,
  liveByIp,
  onAddNode,
  onRenameCluster,
  onChangeClusterColor,
  onDeleteCluster,
  moveMenuNodeId,
  setMoveMenuNodeId,
  ...props
}: CardActionProps & {
  cluster: Cluster | null;  // null = "Без кластера"
  nodes: MonitorNodePublicExtended[];
  liveByIp: Map<string, LiveNodeInfo>;
  onAddNode: (clusterId: number | null, type: NodeType) => void;
  onRenameCluster?: (c: Cluster) => void;
  onChangeClusterColor?: (id: number, color: string) => void;
  onDeleteCluster?: (id: number) => void;
  moveMenuNodeId: number | null;
  setMoveMenuNodeId: (id: number | null) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false);

  const merged: MergedNode[] = nodes.map((reg) => ({
    reg,
    live: liveByIp.get(reg.ip) ?? null,
  }));

  const backends   = merged.filter((n) => n.reg.node_type === "ivamail_backend");
  const frontends  = merged.filter((n) => n.reg.node_type === "ivamail_frontend");
  const lbs        = merged.filter((n) => LB_TYPES.includes(n.reg.node_type as NodeType));
  const storage    = merged.filter((n) => STORAGE_TYPES.includes(n.reg.node_type as NodeType));
  const monitoring = merged.filter((n) => MONITORING_TYPES.includes(n.reg.node_type as NodeType));

  const onlineCount = merged.filter((n) => n.live?.online).length;
  const totalCount  = merged.filter((n) => n.live !== null).length;
  const offlineCount = totalCount - onlineCount;

  const colorDotClass = cluster ? `bg-${cluster.color}` : "bg-overlay0";
  const accentColor   = cluster ? `bg-${cluster.color}` : "bg-surface1";

  return (
    <div className="border border-surface1/30 rounded-2xl overflow-hidden">
      {/* Thin accent line at top */}
      <div className={`h-px ${accentColor} opacity-50`} />
      {/* Cluster header */}
      <div className={`flex items-center gap-3 px-4 py-3 bg-surface0/40 ${!collapsed ? "border-b border-surface1/30" : ""}`}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-2 flex-1 min-w-0 text-left group"
        >
          {collapsed
            ? <ChevronRight size={14} className="text-overlay0 shrink-0" />
            : <ChevronDown size={14} className="text-overlay0 shrink-0" />}
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${colorDotClass}`} />
          <span className="text-sm font-semibold text-text truncate">
            {cluster ? cluster.name : "Без кластера"}
          </span>
          {totalCount > 0 && (
            <span className={`text-xs font-mono ml-1 shrink-0 ${offlineCount > 0 ? "text-red" : "text-green"}`}>
              {onlineCount}/{totalCount}
            </span>
          )}
          {nodes.length > 0 && (
            <span className="text-[10px] text-overlay0 ml-0.5 shrink-0">
              · {nodes.length} {nodes.length === 1 ? "нода" : "нод"}
            </span>
          )}
        </button>

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => onAddNode(cluster?.id ?? null, "ivamail_backend")}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[10px] text-subtext hover:text-text bg-surface1/50 hover:bg-surface1 border border-transparent hover:border-surface1 rounded-lg transition-colors"
          >
            <Plus size={10} /> Добавить ноду
          </button>

          {cluster && (
            <div className="relative">
              <button
                onClick={(e) => { e.stopPropagation(); setHeaderMenuOpen(!headerMenuOpen); }}
                className="p-1.5 rounded-lg text-overlay0 hover:text-text hover:bg-surface1 transition-colors"
                title="Настройки кластера"
              >
                <MoreHorizontal size={14} />
              </button>
              {headerMenuOpen && (
                <ClusterHeaderMenu
                  cluster={cluster}
                  onRename={() => onRenameCluster?.(cluster)}
                  onChangeColor={(color) => onChangeClusterColor?.(cluster.id, color)}
                  onDelete={() => onDeleteCluster?.(cluster.id)}
                  onClose={() => setHeaderMenuOpen(false)}
                />
              )}
            </div>
          )}
        </div>
      </div>

      {/* Cluster body — animated */}
      <AnimatePresence initial={false}>
      {!collapsed && (
        <motion.div
          key="body"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.18, ease: "easeInOut" }}
          className="overflow-hidden"
        >
        <div className="p-4 space-y-5 bg-base/20">
          {nodes.length === 0 && (
            <p className="text-[11px] text-overlay0 text-center py-4">
              Нет нод в этом {cluster ? "кластере" : "разделе"}.{" "}
              <button
                onClick={() => onAddNode(cluster?.id ?? null, "ivamail_backend")}
                className="text-blue hover:underline"
              >
                Добавить
              </button>
            </p>
          )}

          {backends.length > 0 && (
            <TypeSection
              title="Backend"
              nodes={backends}
              onAddNode={() => onAddNode(cluster?.id ?? null, "ivamail_backend")}
              cardType="ivamail"
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...props}
            />
          )}
          {frontends.length > 0 && (
            <TypeSection
              title="Frontend"
              nodes={frontends}
              onAddNode={() => onAddNode(cluster?.id ?? null, "ivamail_frontend")}
              cardType="ivamail"
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...props}
            />
          )}
          {lbs.length > 0 && (
            <TypeSection
              title="Балансировщики"
              nodes={lbs}
              onAddNode={() => onAddNode(cluster?.id ?? null, "haproxy")}
              cardType="ssh"
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...props}
            />
          )}
          {storage.length > 0 && (
            <TypeSection
              title="Хранилище"
              nodes={storage.sort((a, b) =>
                (a.reg.node_type === "nfs" ? -1 : 1) - (b.reg.node_type === "nfs" ? -1 : 1)
              )}
              onAddNode={() => onAddNode(cluster?.id ?? null, "nfs")}
              cardType="ssh"
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...props}
            />
          )}
          {monitoring.length > 0 && (
            <TypeSection
              title="Мониторинг"
              nodes={monitoring}
              onAddNode={() => onAddNode(cluster?.id ?? null, "monitoring")}
              cardType="ssh"
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...props}
            />
          )}
        </div>
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}

// ── EmptyState ─────────────────────────────────────────────────────

function EmptyState({ onImport, onAdd, importing }: { onImport: () => void; onAdd: () => void; importing: boolean }) {
  return (
    <div className="bg-surface0 border border-surface1 rounded-2xl p-12 flex flex-col items-center gap-4">
      <Server size={36} className="text-overlay0" />
      <div className="text-center">
        <p className="text-subtext text-sm font-medium">Реестр нод пуст</p>
        <p className="text-overlay0 text-xs mt-1">Добавьте ноды вручную или импортируйте из конфигурации деплоя</p>
      </div>
      <div className="flex items-center gap-2 mt-1">
        <button
          onClick={onImport}
          disabled={importing}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue bg-blue/10 hover:bg-blue/20 border border-blue/30 rounded-lg transition-colors disabled:opacity-50"
        >
          {importing && <span className="w-3 h-3 border border-blue border-t-transparent rounded-full animate-spin" />}
          Импортировать из деплоя
        </button>
        <button
          onClick={onAdd}
          className="px-4 py-2 text-sm text-subtext hover:text-text bg-surface1 hover:bg-surface1/70 rounded-lg transition-colors"
        >
          Добавить вручную
        </button>
      </div>
    </div>
  );
}

// ── Dashboard ──────────────────────────────────────────────────────

export function Dashboard() {
  const [registry, setRegistry] = useState<MonitorNodePublicExtended[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [liveStatus, setLiveStatus] = useState<LiveClusterStatus | null>(null);
  const [loadingRegistry, setLoadingRegistry] = useState(true);
  const [loadingLive, setLoadingLive] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [moveMenuNodeId, setMoveMenuNodeId] = useState<number | null>(null);

  // Node modal
  const [modalOpen, setModalOpen] = useState(false);
  const [editNode, setEditNode] = useState<MonitorNodePublic | null>(null);
  const [defaultNodeType, setDefaultNodeType] = useState<NodeType>("ivamail_backend");
  const [defaultClusterId, setDefaultClusterId] = useState<number | null>(null);

  // CMD modal
  const [cmdModalOpen, setCmdModalOpen] = useState(false);
  const [cmdModalNode, setCmdModalNode] = useState<{
    nodeId: number; nodeLabel: string;
    commands?: EnrichedCommand[] | null; fetchedAt?: string | null;
  } | null>(null);

  // Cluster modals
  const [createClusterOpen, setCreateClusterOpen] = useState(false);
  const [renameCluster, setRenameCluster] = useState<Cluster | null>(null);

  function openCommandsModal(nodeId: number, label: string, commands?: EnrichedCommand[] | null, fetchedAt?: string | null) {
    setCmdModalNode({ nodeId, nodeLabel: label, commands, fetchedAt });
    setCmdModalOpen(true);
  }

  // ── Data fetching ──────────────────────────────────────────────

  const fetchRegistry = useCallback(async () => {
    try {
      const resp = await fetch("/api/monitor/nodes");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const nodes: MonitorNodePublicExtended[] = await resp.json();
      setRegistry(nodes);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingRegistry(false);
    }
  }, []);

  const fetchClusters = useCallback(async () => {
    try {
      const resp = await fetch("/api/monitor/clusters");
      if (!resp.ok) return;
      const data: Cluster[] = await resp.json();
      setClusters(data);
    } catch { /* soft */ }
  }, []);

  const fetchLive = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const resp = await fetch("/api/cluster/nodes/live");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const result: LiveClusterStatus = await resp.json();
      setLiveStatus(result);
    } catch { /* soft */ }
    finally {
      setLoadingLive(false);
      if (showSpinner) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchRegistry();
    fetchClusters();
    fetchLive();
  }, [fetchRegistry, fetchClusters, fetchLive]);

  useEffect(() => {
    const interval = setInterval(() => fetchLive(), 30_000);
    return () => clearInterval(interval);
  }, [fetchLive]);

  useEffect(() => {
    if (deletingId === null) return;
    const handler = () => setDeletingId(null);
    window.addEventListener("click", handler, { capture: true });
    return () => window.removeEventListener("click", handler, { capture: true });
  }, [deletingId]);

  // ── Derived data ───────────────────────────────────────────────

  const liveByIp = new Map<string, LiveNodeInfo>();
  liveStatus?.nodes.forEach((n) => liveByIp.set(n.ip, n));

  // Group nodes by cluster_id
  const byCluster = new Map<number | null, MonitorNodePublicExtended[]>();
  for (const node of registry) {
    const key = node.cluster_id ?? null;
    if (!byCluster.has(key)) byCluster.set(key, []);
    byCluster.get(key)!.push(node);
  }

  const onlineCount  = liveStatus?.online ?? 0;
  const offlineCount = liveStatus?.offline ?? 0;
  const totalCount   = liveStatus?.total ?? 0;
  const loading = loadingRegistry || loadingLive;
  const registryEmpty = !loadingRegistry && registry.length === 0;

  // ── Handlers ───────────────────────────────────────────────────

  function openAddModal(clusterId: number | null, type: NodeType) {
    setEditNode(null);
    setDefaultNodeType(type);
    setDefaultClusterId(clusterId);
    setModalOpen(true);
  }

  function openEditModal(node: MonitorNodePublic) {
    setEditNode(node);
    setModalOpen(true);
  }

  function handleSaved(node: MonitorNodePublicExtended) {
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
      const resp = await fetch("/api/monitor/nodes/import-deploy?force=false", { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await fetchRegistry();
      await fetchLive();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  async function handleMoveToCluster(nodeId: number, clusterId: number | null) {
    try {
      const url = clusterId !== null
        ? `/api/monitor/nodes/${nodeId}/cluster?cluster_id=${clusterId}`
        : `/api/monitor/nodes/${nodeId}/cluster`;
      const resp = await fetch(url, { method: "PATCH" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const updated: MonitorNodePublicExtended = await resp.json();
      setRegistry((prev) => prev.map((n) => n.id === updated.id ? updated : n));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleCreateCluster(name: string, color: string) {
    try {
      const resp = await fetch("/api/monitor/clusters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, color }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const cluster: Cluster = await resp.json();
      setClusters((prev) => [...prev, cluster]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleRenameCluster(id: number, name: string) {
    try {
      const resp = await fetch(`/api/monitor/clusters/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const updated: Cluster = await resp.json();
      setClusters((prev) => prev.map((c) => c.id === updated.id ? updated : c));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleChangeClusterColor(id: number, color: string) {
    try {
      const resp = await fetch(`/api/monitor/clusters/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ color }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const updated: Cluster = await resp.json();
      setClusters((prev) => prev.map((c) => c.id === updated.id ? updated : c));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDeleteCluster(id: number) {
    try {
      const resp = await fetch(`/api/monitor/clusters/${id}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setClusters((prev) => prev.filter((c) => c.id !== id));
      // Обновляем ноды — они теперь cluster_id=null
      setRegistry((prev) => prev.map((n) => n.cluster_id === id ? { ...n, cluster_id: null } : n));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // ── Shared props ───────────────────────────────────────────────

  const cardProps: CardActionProps = {
    clusters,
    onEdit: openEditModal,
    onDelete: handleDelete,
    deletingId,
    setDeletingId,
    onOpenCommands: openCommandsModal,
    onMoveToCluster: handleMoveToCluster,
  };

  // ── Render ─────────────────────────────────────────────────────

  const ungroupedNodes = byCluster.get(null) ?? [];

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-text">Dashboard</h1>
          {loading ? (
            <p className="text-subtext text-sm mt-0.5">Загрузка...</p>
          ) : registryEmpty ? (
            <p className="text-subtext text-sm mt-0.5">Реестр нод пуст</p>
          ) : (
            <p className="text-subtext text-sm mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
              {clusters.length > 0 && <span>Кластеров: {clusters.length}</span>}
              {clusters.length > 0 && <span className="text-overlay0">·</span>}
              <span>Online: <span className="text-green font-medium">{onlineCount}</span>/{totalCount}</span>
              {offlineCount > 0 && <><span className="text-overlay0">·</span><span className="text-red">{offlineCount} offline</span></>}
              {liveStatus?.checked_at && (
                <><span className="text-overlay0">·</span><span className="text-overlay0 font-mono text-xs">{formatTime(liveStatus.checked_at)}</span></>
              )}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setCreateClusterOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-surface0 hover:bg-surface1 text-subtext text-sm rounded-lg border border-surface1 transition-colors"
          >
            <Plus size={14} /> Кластер
          </button>
          <button
            onClick={() => fetchLive(true)}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 bg-surface0 hover:bg-surface1 text-subtext text-sm rounded-lg border border-surface1 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 bg-red/10 border border-red/20 rounded-xl px-4 py-3">
          <AlertTriangle size={14} className="text-red shrink-0" />
          <span className="text-red text-sm">{error}</span>
          <button onClick={() => setError(null)} className="ml-auto text-red/60 hover:text-red">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Loading skeleton */}
      {loading && (
        <div className="space-y-4">
          <div className="h-10 bg-surface1 rounded-2xl animate-pulse" />
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="bg-surface0 border border-surface1 rounded-xl p-4 animate-pulse h-36" />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {registryEmpty && !error && (
        <EmptyState
          onImport={handleImport}
          onAdd={() => openAddModal(null, "ivamail_backend")}
          importing={importing}
        />
      )}

      {/* Cluster blocks */}
      {!loading && registry.length > 0 && (
        <div className="space-y-4">
          {clusters
            .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
            .map((cluster) => (
              <ClusterBlock
                key={cluster.id}
                cluster={cluster}
                nodes={byCluster.get(cluster.id) ?? []}
                liveByIp={liveByIp}
                onAddNode={openAddModal}
                onRenameCluster={setRenameCluster}
                onChangeClusterColor={handleChangeClusterColor}
                onDeleteCluster={handleDeleteCluster}
                moveMenuNodeId={moveMenuNodeId}
                setMoveMenuNodeId={setMoveMenuNodeId}
                {...cardProps}
              />
            ))}

          {/* Ungrouped nodes */}
          {(ungroupedNodes.length > 0 || clusters.length === 0) && (
            <ClusterBlock
              cluster={null}
              nodes={ungroupedNodes}
              liveByIp={liveByIp}
              onAddNode={openAddModal}
              moveMenuNodeId={moveMenuNodeId}
              setMoveMenuNodeId={setMoveMenuNodeId}
              {...cardProps}
            />
          )}

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
                    Обновлено: <span className="font-mono">{formatTime(liveStatus.checked_at)}</span>
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Add / Edit node modal */}
      <AddNodeModal
        open={modalOpen}
        onClose={() => { setModalOpen(false); setEditNode(null); }}
        onSaved={handleSaved}
        editNode={editNode}
        defaultNodeType={defaultNodeType}
        defaultClusterId={defaultClusterId}
      />

      {/* CMD modal */}
      <NodeCommandsModal
        open={cmdModalOpen}
        onClose={() => setCmdModalOpen(false)}
        nodeId={cmdModalNode?.nodeId ?? 0}
        nodeLabel={cmdModalNode?.nodeLabel ?? ""}
        initialCommands={cmdModalNode?.commands ?? undefined}
        fetchedAt={cmdModalNode?.fetchedAt}
      />

      {/* Create cluster modal */}
      <CreateClusterModal
        open={createClusterOpen}
        onClose={() => setCreateClusterOpen(false)}
        onCreate={handleCreateCluster}
      />

      {/* Rename cluster modal */}
      <RenameClusterModal
        cluster={renameCluster}
        onClose={() => setRenameCluster(null)}
        onSave={handleRenameCluster}
      />
    </div>
  );
}
