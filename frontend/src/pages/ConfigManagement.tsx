import { useState, useEffect, useRef, useCallback, useMemo, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronRight, ChevronDown, Server, Globe, Users,
  Download, Upload, GitBranch, Play, RotateCcw,
  CheckCircle2, AlertTriangle, Plus, Minus,
  Save, RefreshCw, Clock, FileText, Loader2,
  KeyRound, Eye, EyeOff, AlertCircle, BookOpen, Search, Terminal,
  Layers, Copy, Trash2, Pencil, X, ChevronUp, PlusCircle,
} from "lucide-react";
import { useDeployStore } from "@/stores/deployStore";
import { configApi, type CmdCreds, type ConfigData, type DiffEntry, type GitCommit, type ProfileMeta, type ProfileFull, type HistoryEntry, type PullAllEvent } from "@/api/configApi";

// ── Types ─────────────────────────────────────────────────────────────────────

// Нода из monitor_nodes DB (публичный вид)
interface MonitorNodeInfo {
  id: number;
  ip: string;
  hostname: string | null;
  display_name: string | null;
  node_type: string;          // "ivamail_backend" | "ivamail_frontend" | ...
  cmd_user: string;
  has_cmd_password: boolean;
  sort_order: number;
}

// Обогащённая команда (из discover-commands)
interface EnrichedCommand {
  name: string;
  syntax: string;
  section: string;
  description: string;
  available: boolean;
  documented: boolean;
}

// Команда из статического MD-справочника
interface CMDMethodDoc {
  name: string;
  syntax: string;
  section: string;
  description: string;
  documented: boolean;
  available: null | boolean;
}

// ── Tab types ─────────────────────────────────────────────────────────────────

type Tab = "editor" | "diff" | "history" | "playbooks" | "profiles" | "cmdref";

// ── UI primitives ─────────────────────────────────────────────────────────────

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
        active
          ? "bg-blue/15 text-blue"
          : "text-subtext hover:text-text hover:bg-surface1/50"
      }`}
    >
      {children}
    </button>
  );
}

function Badge({ children, color = "surface1" }: { children: ReactNode; color?: string }) {
  const cls: Record<string, string> = {
    surface1: "bg-surface1 text-overlay0",
    blue:     "bg-blue/10 text-blue",
    green:    "bg-green/10 text-green",
    yellow:   "bg-yellow/10 text-yellow",
    red:      "bg-red/10 text-red",
  };
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${cls[color] ?? cls.surface1}`}>
      {children}
    </span>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[10px] font-semibold text-overlay0 uppercase tracking-widest px-3 py-1.5 mt-2">
      {children}
    </p>
  );
}

function ErrMsg({ msg }: { msg: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-red bg-red/10 border border-red/20 rounded-lg px-3 py-2">
      <AlertCircle size={12} className="shrink-0" />
      <span className="font-mono break-all">{msg}</span>
    </div>
  );
}

// ── CMD Credentials bar ───────────────────────────────────────────────────────

interface CredsBarProps {
  user: string;
  pass: string;
  node: MonitorNodeInfo | null;
  onChange: (u: string, p: string) => void;
}

function CredsBar({ user, pass, node, onChange }: CredsBarProps) {
  const [open, setOpen]       = useState(false);
  const [showPw, setShowPw]   = useState(false);
  const [localU, setLocalU]   = useState(user);
  const [localP, setLocalP]   = useState(pass);
  const [loadingPw, setLoadingPw] = useState(false);
  const hasPass = pass.trim() !== "";

  // Когда меняется нода — подставить cmd_user
  useEffect(() => {
    if (node?.cmd_user) {
      setLocalU(node.cmd_user);
    }
  }, [node?.id, node?.cmd_user]);

  const apply = () => { onChange(localU, localP); setOpen(false); };
  // Применять учётные данные немедленно при каждом изменении (без нажатия OK)
  const onUserChange   = (v: string) => { setLocalU(v); onChange(v, localP); };
  const onPassChange   = (v: string) => { setLocalP(v); onChange(localU, v); };

  const handleLoadPassword = async () => {
    if (!node) return;
    setLoadingPw(true);
    try {
      const r = await fetch(`/api/monitor/nodes/${node.id}/credentials`);
      if (r.ok) {
        const data = await r.json() as { cmd_user: string; cmd_password: string };
        setLocalP(data.cmd_password);
      }
    } catch {
      // ignore
    } finally {
      setLoadingPw(false);
    }
  };

  return (
    <div className="flex items-center gap-2 bg-surface0 border border-surface1 rounded-xl px-4 py-2.5">
      <KeyRound size={13} className={hasPass ? "text-green" : "text-yellow"} />
      <span className="text-xs text-subtext">CMD:</span>
      {open ? (
        <>
          <input
            value={localU}
            onChange={(e) => onUserChange(e.target.value)}
            placeholder="user"
            className="text-xs font-mono bg-surface1 rounded px-2 py-0.5 w-24 outline-none focus:ring-1 focus:ring-blue/50 text-text"
          />
          <div className="relative flex items-center gap-1">
            <div className="relative">
              <input
                type={showPw ? "text" : "password"}
                value={localP}
                onChange={(e) => onPassChange(e.target.value)}
                placeholder="password"
                className="text-xs font-mono bg-surface1 rounded px-2 py-0.5 pr-7 w-32 outline-none focus:ring-1 focus:ring-blue/50 text-text"
                onKeyDown={(e) => e.key === "Enter" && apply()}
              />
              <button
                onClick={() => setShowPw((v) => !v)}
                className="absolute right-1 top-1/2 -translate-y-1/2 text-overlay0 hover:text-text"
              >
                {showPw ? <EyeOff size={10} /> : <Eye size={10} />}
              </button>
            </div>
            {node?.has_cmd_password && (
              <button
                onClick={handleLoadPassword}
                disabled={loadingPw}
                className="text-[10px] bg-surface1 hover:bg-surface2 px-2 py-0.5 rounded text-subtext transition-colors disabled:opacity-50 flex items-center gap-1"
              >
                {loadingPw ? <Loader2 size={9} className="animate-spin" /> : "↓"}
                Загрузить
              </button>
            )}
          </div>
          <button
            onClick={apply}
            className="text-xs bg-blue/90 hover:bg-blue text-mantle font-semibold px-2.5 py-0.5 rounded-lg transition-colors"
          >
            OK
          </button>
          <button
            onClick={() => setOpen(false)}
            className="text-xs text-subtext hover:text-text transition-colors"
          >
            Отмена
          </button>
        </>
      ) : (
        <button
          onClick={() => { setLocalU(user); setLocalP(pass); setOpen(true); }}
          className="flex items-center gap-1.5 text-xs font-mono text-subtext hover:text-text transition-colors"
        >
          <span>{user || "—"}</span>
          {hasPass
            ? <span className="text-green">●</span>
            : <span className="text-yellow">пароль не задан</span>}
        </button>
      )}
    </div>
  );
}

// ── Node selector bar ─────────────────────────────────────────────────────────

function NodeBar({
  nodes, selected, loading, onSelect,
}: {
  nodes: MonitorNodeInfo[];
  selected: MonitorNodeInfo | null;
  loading: boolean;
  onSelect: (n: MonitorNodeInfo) => void;
}) {
  return (
    <div className="flex items-center gap-3 bg-surface0 border border-surface1 rounded-xl px-4 py-2.5">
      <Server size={14} className="text-overlay0 shrink-0" />
      <span className="text-xs text-subtext">Нода:</span>
      <div className="flex gap-1.5">
        {loading ? (
          // Skeleton-заглушки
          <>
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-7 w-28 rounded-lg bg-surface1 animate-pulse"
              />
            ))}
          </>
        ) : (
          nodes.map((n) => {
            const label = n.display_name || n.hostname || n.ip;
            const badge = n.node_type === "ivamail_backend" ? "BE" : "FE";
            const isActive = selected?.ip === n.ip;
            return (
              <button
                key={n.ip}
                onClick={() => onSelect(n)}
                className={`flex flex-col items-start px-2.5 py-1 rounded-lg transition-colors text-left ${
                  isActive
                    ? "bg-blue/15 text-blue border border-blue/30"
                    : "bg-surface1 text-subtext hover:text-text"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span className={`w-1.5 h-1.5 rounded-full ${isActive ? "bg-blue" : "bg-overlay0"}`} />
                  <span className="text-xs font-mono">{label}</span>
                </div>
                <span className="text-[10px] text-overlay0 pl-3 font-mono">{n.ip} · {badge}</span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── Left tree panel ───────────────────────────────────────────────────────────

type TreeItem =
  | { kind: "module"; name: string }
  | { kind: "domain"; name: string }
  | { kind: "object"; domain: string; uid: string };

interface TreePanelProps {
  node:           MonitorNodeInfo;
  selection:      TreeItem | null;
  onSelect:       (item: TreeItem) => void;
  modules:        string[];        // live или saved модули
  loadingModules: boolean;
  liveModules:    string[] | null; // null = не загружены
  credsPass:      string;
  savedDomains:   string[];
  domainObjects:  Record<string, string[]>;
  loadingTree:    boolean;
  onExpandDomain: (domain: string) => void;
}

function TreePanel({
  selection, onSelect,
  modules, loadingModules, liveModules, credsPass,
  savedDomains, domainObjects,
  loadingTree, onExpandDomain,
}: TreePanelProps) {
  const [openDomains, setOpenDomains] = useState<Set<string>>(new Set());

  const toggleDomain = (d: string) => {
    setOpenDomains((prev) => {
      const next = new Set(prev);
      if (next.has(d)) {
        next.delete(d);
      } else {
        next.add(d);
        onExpandDomain(d);
      }
      return next;
    });
  };

  const isSelected = (item: TreeItem) => {
    if (!selection) return false;
    if (item.kind === "module" && selection.kind === "module") return item.name === selection.name;
    if (item.kind === "domain" && selection.kind === "domain") return item.name === selection.name;
    if (item.kind === "object" && selection.kind === "object")
      return item.domain === selection.domain && item.uid === selection.uid;
    return false;
  };

  return (
    <div className="w-52 shrink-0 border-r border-surface1 overflow-y-auto">
      {/* Modules */}
      <SectionLabel>
        <span className="flex items-center gap-1.5">
          Модули
          {loadingModules && <Loader2 size={8} className="animate-spin" />}
          {liveModules !== null && (
            <span className="text-[8px] text-green normal-case tracking-normal font-normal">live</span>
          )}
        </span>
      </SectionLabel>

      {modules.length === 0 && !loadingModules && credsPass === "" && (
        <p className="px-3 py-2 text-[10px] text-overlay0 italic leading-relaxed">
          Введите CMD пароль<br />чтобы загрузить модули
        </p>
      )}

      {modules.map((name) => {
        const item: TreeItem = { kind: "module", name };
        const active = isSelected(item);
        return (
          <button
            key={name}
            onClick={() => onSelect(item)}
            className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
              active
                ? "bg-blue/10 text-blue"
                : "text-subtext hover:text-text hover:bg-surface1/40"
            }`}
          >
            <Server size={10} className={active ? "text-blue" : "text-overlay0"} />
            <span className="font-mono flex-1">{name}</span>
          </button>
        );
      })}

      {/* Domains */}
      <SectionLabel>Домены</SectionLabel>
      {savedDomains.length === 0 && !loadingTree && (
        <p className="px-3 py-1.5 text-[10px] text-overlay0 italic">нет сохранённых доменов</p>
      )}
      {savedDomains.map((domain) => {
        const domItem: TreeItem = { kind: "domain", name: domain };
        const domActive = isSelected(domItem);
        const expanded  = openDomains.has(domain);
        const objects   = domainObjects[domain] ?? [];

        return (
          <div key={domain}>
            <div className="flex items-center">
              <button
                onClick={() => toggleDomain(domain)}
                className="p-1 text-overlay0 hover:text-text transition-colors"
              >
                {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              </button>
              <button
                onClick={() => onSelect(domItem)}
                className={`flex-1 flex items-center gap-1.5 py-1.5 pr-3 text-left text-xs transition-colors ${
                  domActive ? "text-blue" : "text-subtext hover:text-text"
                }`}
              >
                <Globe size={10} className={domActive ? "text-blue" : "text-overlay0"} />
                <span className="font-mono truncate">{domain}</span>
              </button>
            </div>

            <AnimatePresence initial={false}>
              {expanded && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden pl-6"
                >
                  {objects.length === 0 && (
                    <p className="px-2 py-1 text-[10px] text-overlay0 italic">нет объектов</p>
                  )}
                  {objects.map((uid) => {
                    const objItem: TreeItem = { kind: "object", domain, uid };
                    const active = isSelected(objItem);
                    return (
                      <button
                        key={uid}
                        onClick={() => onSelect(objItem)}
                        className={`w-full flex items-center gap-1.5 px-2 py-1 text-left text-xs transition-colors ${
                          active ? "bg-blue/10 text-blue" : "text-overlay0 hover:text-text"
                        }`}
                      >
                        <Users size={9} className="shrink-0" />
                        <span className="font-mono truncate">{uid}</span>
                      </button>
                    );
                  })}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}

// ── Config table ──────────────────────────────────────────────────────────────

/** Рекурсивно рендерит любое значение конфига (примитив / массив / объект). */
function ConfigValue({ val, depth = 0 }: { val: unknown; depth?: number }) {
  if (val === null || val === undefined)
    return <span className="text-overlay0 italic">null</span>;

  if (typeof val === "boolean")
    return <span className={val ? "text-green" : "text-red"}>{String(val)}</span>;

  if (typeof val !== "object")
    return <>{String(val)}</>;

  if (Array.isArray(val)) {
    if (val.length === 0) return <span className="text-overlay0 italic">[]</span>;
    // Массив примитивов — в одну строку
    if (val.every((i) => i === null || typeof i !== "object"))
      return <>{(val as unknown[]).map(String).join(", ")}</>;
    // Массив объектов — каждый элемент отдельным блоком
    return (
      <div className="flex flex-col gap-1 mt-0.5">
        {(val as unknown[]).map((item, idx) => (
          <div key={idx} className="pl-2 border-l-2 border-surface1">
            <ConfigValue val={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  // Обычный объект — рендер вложенных ключей
  const pairs = Object.entries(val as Record<string, unknown>);
  return (
    <div className={`flex flex-col gap-0.5 mt-0.5 ${depth === 0 ? "pl-2 border-l-2 border-surface1" : ""}`}>
      {pairs.map(([k, v]) => (
        <div key={k} className="flex gap-1.5 items-start text-[11px]">
          <span className="text-mauve/80 shrink-0 font-semibold">{k}:</span>
          <span className="text-subtext break-all">
            <ConfigValue val={v} depth={depth + 1} />
          </span>
        </div>
      ))}
    </div>
  );
}

function ConfigTable({
  config, editedKeys, onEdit,
}: {
  config: ConfigData;
  editedKeys?: Set<string>;
  onEdit?: (key: string, val: string) => void;
}) {
  const entries = Object.entries(config).filter(([k]) => !k.startsWith("_"));
  if (entries.length === 0) {
    return <p className="text-xs text-overlay0 italic pt-4 text-center">Конфиг пуст или не загружен</p>;
  }
  return (
    <table className="w-full text-xs font-mono">
      <thead>
        <tr className="border-b border-surface1">
          <th className="text-left text-overlay0 font-normal py-2 pr-4 w-48">Ключ</th>
          <th className="text-left text-overlay0 font-normal py-2">Значение</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, val]) => {
          const isEdited = editedKeys?.has(key);
          // Объекты и сложные массивы — только read-only через ConfigValue
          const isComplex = val !== null && val !== undefined && typeof val === "object" &&
            (!Array.isArray(val) || (val as unknown[]).some((i) => typeof i === "object" && i !== null));
          const displayVal = isComplex
            ? ""
            : Array.isArray(val)
              ? (val as unknown[]).map(String).join(", ")
              : String(val ?? "");
          return (
            <tr
              key={key}
              className={`border-b border-surface1/40 hover:bg-surface1/20 transition-colors ${
                isEdited ? "bg-yellow/5" : ""
              }`}
            >
              <td className="py-1.5 pr-4 text-mauve align-top">{key}</td>
              <td className="py-1.5">
                {isComplex ? (
                  <ConfigValue val={val} />
                ) : onEdit ? (
                  <input
                    value={displayVal}
                    onChange={(e) => onEdit(key, e.target.value)}
                    className={`w-full bg-transparent outline-none focus:bg-surface1/50 rounded px-1 -mx-1 transition-colors ${
                      isEdited ? "text-yellow" : "text-text"
                    }`}
                  />
                ) : (
                  <span className="text-text">{displayVal}</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Tab: Editor ───────────────────────────────────────────────────────────────

function EditorTab({ node, creds }: { node: MonitorNodeInfo; creds: CmdCreds }) {
  const [selection, setSelection] = useState<TreeItem | null>(null);
  const [config,    setConfig]    = useState<ConfigData>({});
  const [edits,     setEdits]     = useState<Record<string, string>>({});

  const [loadingStored, setLoadingStored] = useState(false);
  const [fetching,      setFetching]      = useState(false);
  const [saving,        setSaving]        = useState(false);
  const [fetchedLive,   setFetchedLive]   = useState(false);
  const [savedOk,       setSavedOk]       = useState(false);
  const [error,         setError]         = useState<string | null>(null);

  // Tree data
  const [savedModules,  setSavedModules]  = useState<string[]>([]);
  const [liveModules,   setLiveModules]   = useState<string[] | null>(null);
  const [loadingModules, setLoadingModules] = useState(false);
  const [savedDomains,  setSavedDomains]  = useState<string[]>([]);
  const [domainObjects, setDomainObjects] = useState<Record<string, string[]>>({});
  const [loadingTree,   setLoadingTree]   = useState(false);

  // Resolve displayed modules: live > saved
  const modules = liveModules ?? savedModules;

  // Load saved tree data when node changes
  useEffect(() => {
    setLoadingTree(true);
    setError(null);
    setLiveModules(null);
    Promise.all([
      configApi.stored.listModules(node.ip).catch(() => [] as string[]),
      configApi.stored.listDomains().catch(() => [] as string[]),
    ]).then(([mods, doms]) => {
      setSavedModules(mods);
      setSavedDomains(doms);
      // Установить первый модуль по умолчанию
      if (mods.length > 0) {
        setSelection({ kind: "module", name: mods[0] });
      } else {
        setSelection(null);
      }
    }).finally(() => setLoadingTree(false));
  }, [node.ip]);

  // Авто-загрузка live модулей при наличии пароля
  useEffect(() => {
    if (!creds.pass.trim()) return;
    setLoadingModules(true);
    fetch(`/api/config/live/nodes/${node.ip}/modules?cmd_user=${encodeURIComponent(creds.user)}&cmd_password=${encodeURIComponent(creds.pass)}`)
      .then((r) => r.ok ? r.json() as Promise<{ modules: string[] }> : null)
      .then((data) => {
        if (data?.modules) setLiveModules(data.modules);
      })
      .catch(() => {})
      .finally(() => setLoadingModules(false));
  }, [node.ip, creds.pass, creds.user]);

  const loadDomainObjects = useCallback((domain: string) => {
    configApi.stored.listObjects(domain)
      .then((uids) => setDomainObjects((prev) => ({ ...prev, [domain]: uids })))
      .catch(() => {/* ignore */});
  }, []);

  // Load stored config when selection changes
  useEffect(() => {
    if (!selection) { setConfig({}); return; }
    setConfig({});
    setEdits({});
    setFetchedLive(false);
    setSavedOk(false);
    setError(null);
    setLoadingStored(true);

    const load = async () => {
      try {
        if (selection.kind === "module") {
          const data = await configApi.stored.readModule(node.ip, selection.name);
          setConfig(data);
        } else if (selection.kind === "domain") {
          const data = await configApi.stored.readDomain(selection.name);
          setConfig(data);
        } else if (selection.kind === "object") {
          const data = await configApi.stored.readObject(selection.domain, selection.uid);
          setConfig(data);
        }
      } catch {
        // Not stored yet — show empty table, that's OK
        setConfig({});
      } finally {
        setLoadingStored(false);
      }
    };
    load();
  }, [selection, node.ip]);

  const select = (item: TreeItem) => {
    setSelection(item);
  };

  /** Fetch Live — reads from live CMD node, shows in table (not persisted) */
  const handleFetchLive = async () => {
    if (!selection) return;
    if (!creds.pass.trim()) { setError("Введите CMD пароль"); return; }
    setFetching(true);
    setError(null);
    try {
      let data: ConfigData = {};
      if (selection.kind === "module") {
        data = await configApi.live.readModule(node.ip, selection.name, creds);
      } else if (selection.kind === "domain") {
        data = await configApi.live.readDomain(node.ip, selection.name, creds);
      }
      setConfig(data);
      setFetchedLive(true);
      setEdits({});
    } catch (e) {
      setError(String(e));
    } finally {
      setFetching(false);
    }
  };

  /** Save — reads from live CMD and commits to config-store/ + git */
  const handleSave = async () => {
    if (!selection) return;
    if (!creds.pass.trim()) { setError("Введите CMD пароль"); return; }
    setSaving(true);
    setError(null);
    try {
      if (selection.kind === "module") {
        await configApi.save.module(node.ip, selection.name, creds);
        const mods = await configApi.stored.listModules(node.ip);
        setSavedModules(mods);
        const data = await configApi.stored.readModule(node.ip, selection.name);
        setConfig(data);
      } else if (selection.kind === "domain") {
        await configApi.save.domain(node.ip, selection.name, creds);
        const doms = await configApi.stored.listDomains();
        setSavedDomains(doms);
        const data = await configApi.stored.readDomain(selection.name);
        setConfig(data);
      }
      setSavedOk(true);
      setFetchedLive(false);
      setEdits({});
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const selectionLabel = () => {
    if (!selection) return "—";
    if (selection.kind === "module") return `Модуль: ${selection.name}`;
    if (selection.kind === "domain") return `Домен: ${selection.name}`;
    return `Объект: ${selection.uid} (${selection.domain})`;
  };

  const editedKeys = new Set(Object.keys(edits));
  const nodeLabel = node.display_name || node.hostname || node.ip;

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      <TreePanel
        node={node}
        selection={selection}
        onSelect={select}
        modules={modules}
        loadingModules={loadingModules}
        liveModules={liveModules}
        credsPass={creds.pass}
        savedDomains={savedDomains}
        domainObjects={domainObjects}
        loadingTree={loadingTree}
        onExpandDomain={loadDomainObjects}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {selection ? (
          <>
            <div className="flex items-center justify-between px-5 py-3 border-b border-surface1 shrink-0">
              <div>
                <p className="text-sm font-medium text-text">{selectionLabel()}</p>
                <p className="text-xs text-overlay0 font-mono mt-0.5">{nodeLabel} · {node.ip} · CMD :106</p>
              </div>
              <div className="flex items-center gap-2">
                {fetchedLive && <Badge color="green">live загружен</Badge>}
                {savedOk     && <Badge color="green">сохранён</Badge>}
                <button
                  onClick={handleFetchLive}
                  disabled={fetching || selection.kind === "object"}
                  className="flex items-center gap-1.5 text-xs bg-surface1 hover:bg-surface2 disabled:opacity-50 text-text px-3 py-1.5 rounded-lg transition-colors"
                >
                  {fetching ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
                  Fetch Live
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || selection.kind === "object"}
                  className="flex items-center gap-1.5 text-xs bg-blue/90 hover:bg-blue disabled:opacity-40 text-mantle font-semibold px-3 py-1.5 rounded-lg transition-colors"
                >
                  {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
                  Сохранить
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
              {error && <ErrMsg msg={error} />}
              {loadingStored
                ? <div className="flex items-center gap-2 text-xs text-overlay0 pt-4">
                    <Loader2 size={12} className="animate-spin" />
                    Загрузка из config-store...
                  </div>
                : <ConfigTable
                    config={config}
                    editedKeys={editedKeys}
                    onEdit={(key, val) => setEdits((prev) => ({ ...prev, [key]: val }))}
                  />
              }
              {!loadingStored && Object.keys(config).filter(k => !k.startsWith("_")).length === 0 && (
                <p className="text-xs text-overlay0 text-center pt-6">
                  Конфиг не сохранён в config-store. Нажмите «Сохранить» чтобы снять дамп с ноды.
                </p>
              )}

              {/* Inline CMD reference for selected item */}
              {!loadingStored && selection && (
                <CmdModuleRef selection={selection} nodeId={node.id} />
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-overlay0 text-sm">
            Выберите модуль или домен в дереве слева
          </div>
        )}
      </div>
    </div>
  );
}

// ── Inline CMD reference for a specific module ───────────────────────────────

// Точный список команд для работы с конфигом модуля (не по ключевому слову)
const MODULE_CONFIG_CMDS = new Set([
  "modulereadconfig", "moduleupdateconfig",
  "modulegetsetting", "modulesetsetting", "moduledelsetting",
  "moduleslist", "mailmoduleslist", "modulesetloglevel",
]);
// Русскоязычные секции для domain и object
const DOMAIN_SECTION  = "Работа с доменами";
const OBJECT_SECTION  = "Работа с объектами в доменах";

function CmdModuleRef({
  selection,
  nodeId,
}: {
  selection: TreeItem;
  nodeId: number;
}) {
  const [docs,         setDocs]         = useState<CMDMethodDoc[]>([]);
  const [nodeCommands, setNodeCommands] = useState<Map<string, boolean>>(new Map());
  const [open,         setOpen]         = useState(true);
  const [expandedCmd,  setExpandedCmd]  = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/monitor/cmd-reference")
      .then((r) => r.ok ? r.json() as Promise<CMDMethodDoc[]> : [])
      .then(setDocs)
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`/api/monitor/nodes/${nodeId}/commands`)
      .then((r) => r.status === 204 ? null : r.json() as Promise<{ commands: EnrichedCommand[] }>)
      .then((data) => {
        if (data?.commands) {
          const map = new Map<string, boolean>();
          data.commands.forEach((c) => map.set(c.name.toLowerCase(), c.available));
          setNodeCommands(map);
        }
      })
      .catch(() => {});
  }, [nodeId]);

  // Подставляет реальное имя модуля/домена в синтаксис команды
  const substituteName = useCallback((syntax: string, name: string) => {
    return syntax
      .replace(/"module_name"/gi, `"${name}"`)
      .replace(/"string"\|domUID/gi, `"${name}"`);
  }, []);

  const { filtered, label, subName } = useMemo(() => {
    if (selection.kind === "module") {
      return {
        label: `Модуль: ${selection.name}`,
        subName: selection.name,
        filtered: docs.filter((d) => MODULE_CONFIG_CMDS.has(d.name.toLowerCase())),
      };
    }
    if (selection.kind === "domain") {
      return {
        label: `Домен: ${selection.name}`,
        subName: selection.name,
        filtered: docs.filter((d) => d.section === DOMAIN_SECTION),
      };
    }
    if (selection.kind === "object") {
      return {
        label: `Объект: ${selection.uid}`,
        subName: selection.uid,
        filtered: docs.filter((d) => d.section === OBJECT_SECTION),
      };
    }
    return { label: "", subName: "", filtered: [] };
  }, [docs, selection]);

  if (filtered.length === 0) return null;

  return (
    <div className="border border-surface1 rounded-xl overflow-hidden mt-1">
      {/* Header */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-4 py-2.5 bg-surface0/60 hover:bg-surface0 transition-colors"
      >
        {open ? <ChevronDown size={11} className="text-overlay0" /> : <ChevronRight size={11} className="text-overlay0" />}
        <BookOpen size={11} className="text-overlay0" />
        <span className="text-xs text-subtext">CMD Справка</span>
        <span className="text-[10px] font-mono text-overlay0">· {label}</span>
        <span className="ml-auto text-[10px] font-mono bg-surface1 text-overlay0 px-1.5 py-0.5 rounded">
          {filtered.length}
        </span>
      </button>

      {/* Command list */}
      {open && (
        <div className="divide-y divide-surface1/40 max-h-96 overflow-y-auto">
          {filtered.map((cmd) => {
            const isExp  = expandedCmd === cmd.name;
            const avail  = nodeCommands.size > 0
              ? nodeCommands.get(cmd.name.toLowerCase()) ?? null
              : null;
            return (
              <div key={cmd.name}>
                <button
                  onClick={() => setExpandedCmd(isExp ? null : cmd.name)}
                  className={`w-full text-left px-4 py-2 flex items-center gap-2 transition-colors ${
                    isExp ? "bg-blue/5" : "hover:bg-surface1/20"
                  }`}
                >
                  {/* availability dot */}
                  {avail === true  && <span className="w-1.5 h-1.5 rounded-full bg-green/70 shrink-0" />}
                  {avail === false && <span className="w-1.5 h-1.5 rounded-full bg-overlay0/30 shrink-0" />}
                  {avail === null  && <span className="w-1.5 h-1.5 shrink-0" />}

                  <span className={`text-xs font-mono shrink-0 ${isExp ? "text-blue" : "text-text"}`}>
                    {cmd.name}
                  </span>
                  <span className="text-[10px] font-mono text-overlay0 truncate flex-1 text-left">
                    {substituteName(cmd.syntax, subName)}
                  </span>
                  {isExp
                    ? <ChevronUp   size={10} className="shrink-0 text-overlay0" />
                    : <ChevronDown size={10} className="shrink-0 text-overlay0" />}
                </button>

                {isExp && (
                  <div className="px-4 pb-3 pt-1 bg-surface0/30 space-y-2">
                    <code className="block font-mono text-[11px] text-peach bg-surface1 px-3 py-2 rounded-lg whitespace-pre-wrap leading-relaxed">
                      {substituteName(cmd.syntax, subName)}
                    </code>
                    {cmd.description ? (
                      <p className="text-xs text-subtext leading-relaxed whitespace-pre-wrap">
                        {cmd.description}
                      </p>
                    ) : (
                      <p className="text-xs text-overlay0 italic">Описание отсутствует</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Tab: Diff ─────────────────────────────────────────────────────────────────

const diffKindMeta = {
  changed: { icon: <AlertTriangle size={10} />, cls: "text-yellow", bg: "bg-yellow/5" },
  added:   { icon: <Plus  size={10} />,         cls: "text-green",  bg: "bg-green/5"  },
  removed: { icon: <Minus size={10} />,         cls: "text-red",    bg: "bg-red/5"    },
} as const;

// Статический список для DiffTab (fallback если live-модули недоступны)
const FALLBACK_MODULES = ["Cluster", "CMD", "SMTP", "IMAP", "POP3", "WebAccess", "AntiSpam", "Antivirus"];

// Единая функция раскраски строк Ansible-вывода (используется в DiffTab, HistoryTab, PlaybooksTab)
function ansibleLineColor(line: string | undefined): string {
  if (!line) return "text-overlay1";
  if (line.includes("PLAY RECAP"))    return "text-mauve font-semibold";
  if (line.includes("PLAY ["))        return "text-blue";
  if (line.includes("TASK ["))        return "text-subtext";
  if (line.startsWith("ok:"))         return "text-green";
  if (line.startsWith("changed:"))    return "text-yellow";
  if (line.startsWith("failed:"))     return "text-red";
  if (line.startsWith("fatal:"))      return "text-red";
  if (line.includes("[EXIT 0]"))      return "text-green font-semibold";
  if (line.includes("[EXIT"))         return "text-red font-semibold";
  if (line.includes("unreachable=0")) return "text-teal";
  return "text-overlay1";
}

function DiffTab({ node, creds }: { node: MonitorNodeInfo; creds: CmdCreds }) {
  const [module,   setModule]   = useState("Cluster");
  const [entries,  setEntries]  = useState<DiffEntry[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [compared, setCompared] = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [isIdentical, setIsIdentical] = useState(false);
  const [storedConfig, setStoredConfig] = useState<ConfigData | null>(null);
  const [liveConfig,   setLiveConfig]   = useState<ConfigData | null>(null);
  const [availableModules,  setAvailableModules]  = useState<string[]>(FALLBACK_MODULES);
  const [modulesSource,     setModulesSource]      = useState<"live" | "stored" | "fallback">("fallback");

  // Apply stream state
  const [applyRunning, setApplyRunning] = useState(false);
  const [applyLines,   setApplyLines]   = useState<string[]>([]);
  const [applyExitOk,  setApplyExitOk]  = useState<boolean | null>(null);
  const applyTermRef                    = useRef<HTMLPreElement>(null);
  const applyAbortRef                   = useRef<AbortController | null>(null);

  // Загружаем модули: 1) live CMD (если есть пароль), 2) config-store, 3) fallback
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      // 1. Live — точный список с ноды
      if (creds.pass.trim()) {
        try {
          const mods = await configApi.live.listModules(node.ip, creds);
          if (!cancelled && mods.length > 0) {
            setAvailableModules(mods);
            setModulesSource("live");
            return;
          }
        } catch { /* fall through */ }
      }
      // 2. Stored — что есть в config-store
      try {
        const mods = await configApi.stored.listModules(node.ip);
        if (!cancelled && mods.length > 0) {
          setAvailableModules(mods);
          setModulesSource("stored");
          return;
        }
      } catch { /* fall through */ }
      // 3. Fallback
      if (!cancelled) setModulesSource("fallback");
    };

    load();
    return () => { cancelled = true; };
  }, [node.ip, creds.user, creds.pass]);

  useEffect(() => {
    if (applyTermRef.current) applyTermRef.current.scrollTop = applyTermRef.current.scrollHeight;
  }, [applyLines]);

  useEffect(() => () => { applyAbortRef.current?.abort(); }, []);

  const compare = async () => {
    if (!creds.pass.trim()) { setError("Введите CMD пароль для сравнения с live"); return; }
    setLoading(true);
    setError(null);
    setCompared(false);
    setStoredConfig(null);
    setLiveConfig(null);
    try {
      const [result, stored, live] = await Promise.all([
        configApi.diff.module(node.ip, module, creds),
        configApi.stored.readModule(node.ip, module).catch(() => null),
        configApi.live.readModule(node.ip, module, creds).catch(() => null),
      ]);
      setEntries(result);
      setIsIdentical(result.length === 0);
      setStoredConfig(stored);
      setLiveConfig(live);
      setCompared(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateApply = () => {
    if (applyRunning) return;
    applyAbortRef.current?.abort();
    setApplyRunning(true);
    setApplyExitOk(null);
    setApplyLines([`[${new Date().toLocaleTimeString("ru")}] Генерация + применение конфига → ${node.ip}...`, ""]);
    // Объекты доменов не применимы для пофайлового diff-apply
    const APPLY_INCLUDE_OBJECTS = false as const;
    applyAbortRef.current = configApi.ansible.streamApplyV2(
      [node.ip],
      "full",
      APPLY_INCLUDE_OBJECTS,
      creds.user,
      creds.pass,
      (line) => setApplyLines((prev) => [...prev, line]),
      (ok)   => { setApplyRunning(false); setApplyExitOk(ok); },
    );
  };

  const nodeLabel = node.display_name || node.hostname || node.ip;

  const summary = {
    changed: entries.filter((e) => e.kind === "changed").length,
    added:   entries.filter((e) => e.kind === "added").length,
    removed: entries.filter((e) => e.kind === "removed").length,
  };

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div>
          <label className="text-xs text-subtext block mb-1 flex items-center gap-1.5">
            Модуль
            <span className={`text-[10px] px-1.5 py-0 rounded font-normal ${
              modulesSource === "live"     ? "bg-green/15 text-green"    :
              modulesSource === "stored"   ? "bg-blue/15 text-blue"      :
                                            "bg-surface1 text-overlay0"
            }`}>
              {modulesSource === "live" ? "live" : modulesSource === "stored" ? "stored" : "fallback"}
            </span>
          </label>
          <select
            value={module}
            onChange={(e) => { setModule(e.target.value); setCompared(false); }}
            className="text-xs bg-mantle border border-surface1 rounded-lg px-3 py-1.5 text-text focus:border-blue outline-none"
          >
            {availableModules.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <div className="self-end">
          <button
            onClick={compare}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs bg-blue/90 hover:bg-blue disabled:opacity-50 text-mantle font-semibold px-4 py-1.5 rounded-lg transition-colors"
          >
            {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            Сравнить с live ({nodeLabel})
          </button>
        </div>
        <div className="self-end">
          <button
            onClick={handleGenerateApply}
            disabled={applyRunning}
            className="flex items-center gap-1.5 text-xs bg-green/10 hover:bg-green/20 disabled:opacity-50 text-green border border-green/30 font-semibold px-4 py-1.5 rounded-lg transition-colors"
          >
            {applyRunning ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
            Generate &amp; Apply
          </button>
        </div>
      </div>

      {error && <ErrMsg msg={error} />}

      {compared && (
        <div className="flex items-center gap-2">
          {summary.changed > 0 && <Badge color="yellow">{summary.changed} изменено</Badge>}
          {summary.added   > 0 && <Badge color="green">{summary.added} добавлено</Badge>}
          {summary.removed > 0 && <Badge color="red">{summary.removed} удалено</Badge>}
          {isIdentical && (
            <span className="text-xs text-green flex items-center gap-1">
              <CheckCircle2 size={12} /> Конфиги идентичны
            </span>
          )}
        </div>
      )}

      {compared && (storedConfig !== null || liveConfig !== null) && (() => {
        // Собираем все ключи обоих конфигов, сортируем: сначала изменённые, потом остальные
        const diffKeys = new Set(entries.map((e) => e.key));
        const allKeys = Array.from(
          new Set([
            ...Object.keys(storedConfig ?? {}).filter((k) => !k.startsWith("_")),
            ...Object.keys(liveConfig   ?? {}).filter((k) => !k.startsWith("_")),
          ])
        ).sort((a, b) => {
          const aDiff = diffKeys.has(a) ? 0 : 1;
          const bDiff = diffKeys.has(b) ? 0 : 1;
          return aDiff - bDiff || a.localeCompare(b);
        });

        const diffByKey = Object.fromEntries(entries.map((e) => [e.key, e]));

        return (
          <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
            <div className="grid grid-cols-3 text-[10px] text-overlay0 font-semibold uppercase tracking-wider px-4 py-2 border-b border-surface1 bg-surface1/30 sticky top-0">
              <span>Ключ</span>
              <span>Сохранённое</span>
              <span>Live ({node.ip})</span>
            </div>
            <div className="divide-y divide-surface1/30 max-h-[60vh] overflow-y-auto">
              {allKeys.map((key) => {
                const diff = diffByKey[key];
                const meta = diff ? diffKindMeta[diff.kind] : null;
                const storedVal = storedConfig?.[key];
                const liveVal   = liveConfig?.[key];
                return (
                  <div key={key} className={`grid grid-cols-3 gap-2 px-4 py-2 text-xs font-mono ${meta?.bg ?? ""}`}>
                    {/* Ключ */}
                    <div className={`flex items-center gap-1.5 ${meta?.cls ?? "text-subtext"}`}>
                      {meta ? meta.icon : <span className="w-2.5 shrink-0" />}
                      <span className="text-text break-all">{key}</span>
                    </div>
                    {/* Сохранённое */}
                    <div className={diff?.kind === "removed" ? "text-red line-through opacity-60" : "text-subtext"}>
                      {storedVal !== undefined
                        ? <ConfigValue val={storedVal} />
                        : <span className="text-overlay0 italic">—</span>
                      }
                    </div>
                    {/* Live */}
                    <div className={
                      diff?.kind === "added"   ? "text-green"  :
                      diff?.kind === "changed" ? "text-yellow" : "text-subtext"
                    }>
                      {liveVal !== undefined
                        ? <ConfigValue val={liveVal} />
                        : <span className="text-overlay0 italic">—</span>
                      }
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {!compared && !loading && !error && applyLines.length === 0 && (
        <p className="text-sm text-overlay0 text-center pt-12">
          Выберите модуль и нажмите «Сравнить» или запустите «Generate &amp; Apply»
        </p>
      )}

      {/* Apply terminal */}
      {applyLines.length > 0 && (
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
          <div className="px-4 py-2 border-b border-surface1 flex items-center gap-2">
            <Terminal size={11} className="text-overlay0" />
            <span className={`w-2 h-2 rounded-full ${
              applyRunning           ? "bg-green animate-pulse" :
              applyExitOk === true  ? "bg-green"               :
              applyExitOk === false ? "bg-red"                 :
              "bg-overlay0"
            }`} />
            <span className="text-xs font-mono text-subtext">
              {applyRunning ? "Generate & Apply выполняется..." :
               applyExitOk === true ? "Apply завершён успешно" :
               applyExitOk === false ? "Apply завершён с ошибкой" : "Вывод"}
            </span>
          </div>
          <pre ref={applyTermRef} className="font-mono text-[11px] h-56 overflow-y-auto p-4 leading-relaxed">
            {applyLines.map((line, i) => (
              <div key={i} className={ansibleLineColor(line)}>{line || " "}</div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Tab: История ──────────────────────────────────────────────────────────────

function HistoryTab({ node, creds }: { node: MonitorNodeInfo; creds: CmdCreds }) {
  const [commits,        setCommits]        = useState<GitCommit[]>([]);
  const [selectedCommit, setSelectedCommit] = useState<GitCommit | null>(null);
  const [diff,           setDiff]           = useState<string | null>(null);
  const [loadingLog,     setLoadingLog]     = useState(true);
  const [loadingDiff,    setLoadingDiff]    = useState(false);
  const [rollingBack,    setRollingBack]    = useState(false);
  const [rolledBack,     setRolledBack]     = useState<string | null>(null);
  const [error,          setError]          = useState<string | null>(null);

  // Apply history
  const [applyHistory,    setApplyHistory]   = useState<HistoryEntry[]>([]);
  const [historyLoading,  setHistoryLoading] = useState(true);
  const [historyTotal,    setHistoryTotal]   = useState(0);
  const [historyExpanded, setHistoryExpanded] = useState<number | null>(null);
  const [historyOffset,   setHistoryOffset]  = useState(0);
  const HISTORY_LIMIT = 20;

  // Tags
  const [tags,        setTags]        = useState<string[]>([]);
  const [loadingTags, setLoadingTags] = useState(true);

  // Tag rollback stream
  const [tagRbRunning,  setTagRbRunning]  = useState<string | null>(null); // текущий откатываемый тег
  const [tagRbCurrent,  setTagRbCurrent]  = useState<string | null>(null); // тег чей вывод сейчас в терминале
  const [tagRbLines,    setTagRbLines]    = useState<string[]>([]);
  const [tagRbExitOk,   setTagRbExitOk]  = useState<boolean | null>(null);
  const tagRbTermRef                      = useRef<HTMLPreElement>(null);
  const tagRbAbortRef                     = useRef<AbortController | null>(null);

  // Ключ для принудительного перезапроса тегов
  const [tagsKey, setTagsKey] = useState(0);

  useEffect(() => {
    setLoadingLog(true);
    configApi.git.log(50)
      .then(setCommits)
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingLog(false));
  }, []);

  useEffect(() => {
    setHistoryLoading(true);
    configApi.history.list({ limit: HISTORY_LIMIT, offset: historyOffset })
      .then(({ history, total }) => { setApplyHistory(history); setHistoryTotal(total); })
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, [historyOffset]);

  useEffect(() => {
    setLoadingTags(true);
    configApi.git.tags()
      .then(setTags)
      .catch(() => {})
      .finally(() => setLoadingTags(false));
  }, [tagsKey]);

  useEffect(() => {
    if (tagRbTermRef.current) tagRbTermRef.current.scrollTop = tagRbTermRef.current.scrollHeight;
  }, [tagRbLines]);

  useEffect(() => () => { tagRbAbortRef.current?.abort(); }, []);

  const selectCommit = async (commit: GitCommit) => {
    setSelectedCommit(commit);
    setDiff(null);
    setRolledBack(null);
    setLoadingDiff(true);
    try {
      const d = await configApi.git.diff(commit.hash);
      setDiff(d);
    } catch (e) {
      setDiff(`Ошибка загрузки diff: ${e}`);
    } finally {
      setLoadingDiff(false);
    }
  };

  const handleRollback = async (hash: string) => {
    setRollingBack(true);
    setError(null);
    try {
      await configApi.git.rollback(hash);
      setRolledBack(hash);
    } catch (e) {
      setError(String(e));
    } finally {
      setRollingBack(false);
    }
  };

  const handleTagRollback = (tag: string, mode: "yaml_only" | "yaml_and_apply") => {
    if (tagRbRunning) return;
    tagRbAbortRef.current?.abort();
    setTagRbRunning(tag);
    setTagRbCurrent(tag);
    setTagRbExitOk(null);
    const modeLabel = mode === "yaml_only" ? "YAML only" : "YAML + Apply";
    setTagRbLines([`[${new Date().toLocaleTimeString("ru")}] Откат к тегу ${tag} (${modeLabel}) → ${node.ip}...`, ""]);
    tagRbAbortRef.current = configApi.ansible.streamRollback(
      [node.ip],
      tag,
      mode,
      creds.user,
      creds.pass,
      (line) => setTagRbLines((prev) => [...prev, line]),
      (ok)   => { setTagRbRunning(null); setTagRbExitOk(ok); },
    );
  };

  const handleDeleteHistory = async (id: number) => {
    try {
      await configApi.history.remove(id);
      setApplyHistory((prev) => prev.filter((e) => e.id !== id));
      setHistoryTotal((t) => t - 1);
      if (historyExpanded === id) setHistoryExpanded(null);
    } catch { /* ignore */ }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-4">
      {error && <ErrMsg msg={error} />}

      {/* ── Apply History ──────────────────────────────────────────────────── */}
      <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
        <div className="px-4 py-2.5 border-b border-surface1 flex items-center gap-2">
          <Clock size={13} className="text-overlay0" />
          <span className="text-xs font-medium text-subtext">История применений профилей</span>
          <span className="text-[10px] text-overlay0 font-mono ml-auto">{historyTotal} записей</span>
        </div>

        {historyLoading && (
          <div className="flex items-center gap-2 px-4 py-5 text-xs text-overlay0">
            <Loader2 size={12} className="animate-spin" /> Загрузка...
          </div>
        )}

        {!historyLoading && applyHistory.length === 0 && (
          <p className="px-4 py-5 text-xs text-overlay0 italic">
            История пуста — применения профилей ещё не записаны.
          </p>
        )}

        {!historyLoading && applyHistory.length > 0 && (
          <div className="divide-y divide-surface1/20">
            {applyHistory.map((entry) => {
              const expanded = historyExpanded === entry.id;
              const statusColor = entry.status === "ok" ? "text-green" : entry.status === "partial" ? "text-yellow" : "text-red";
              const statusIcon  = entry.status === "ok" ? "✓" : entry.status === "partial" ? "⚠" : "✗";
              const dt = new Date(entry.applied_at).toLocaleString("ru", { dateStyle: "short", timeStyle: "short" });
              return (
                <div key={entry.id}>
                  <button
                    onClick={() => setHistoryExpanded(expanded ? null : entry.id)}
                    className="w-full text-left px-4 py-2.5 hover:bg-surface1/20 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span className={`text-xs font-mono font-bold ${statusColor}`}>{statusIcon}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-text truncate">{entry.profile_name}</span>
                          <Badge color={entry.apply_mode === "ansible" ? "blue" : "surface1"}>{entry.apply_mode.toUpperCase()}</Badge>
                          <span className="text-[10px] text-overlay0 font-mono ml-auto shrink-0">{dt}</span>
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <span className="text-[10px] text-overlay0">
                            {entry.target_hosts.join(", ")} · {entry.modules_applied.length} модулей
                          </span>
                        </div>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDeleteHistory(entry.id); }}
                        className="shrink-0 text-overlay0 hover:text-red transition-colors p-0.5 rounded"
                        title="Удалить запись"
                      >
                        <Trash2 size={11} />
                      </button>
                      <ChevronRight size={12} className={`text-overlay0 transition-transform shrink-0 ${expanded ? "rotate-90" : ""}`} />
                    </div>
                  </button>

                  {expanded && (
                    <div className="px-4 pb-3 bg-mantle/30 border-t border-surface1/20 space-y-2 text-[11px]">
                      {/* Hosts + versions */}
                      <div className="flex flex-wrap gap-2 pt-2">
                        {entry.target_hosts.map((h) => (
                          <span key={h} className="font-mono bg-surface1 px-1.5 py-0.5 rounded text-text">
                            {h}
                            {entry.node_versions?.[h] && (
                              <span className="text-overlay0 ml-1">v{entry.node_versions[h]}</span>
                            )}
                          </span>
                        ))}
                      </div>
                      {/* Modules */}
                      <div className="flex flex-wrap gap-1.5">
                        {entry.modules_applied.map((m) => (
                          <span key={m} className="font-mono bg-blue/10 text-blue px-1.5 py-0.5 rounded">{m}</span>
                        ))}
                      </div>
                      {/* Playbook */}
                      {entry.playbook_path && (
                        <p className="text-overlay0 font-mono truncate">
                          📄 {entry.playbook_path}
                        </p>
                      )}
                      {/* Errors */}
                      {entry.errors && entry.errors.length > 0 && (
                        <div className="bg-red/10 border border-red/20 rounded p-2 space-y-0.5">
                          {entry.errors.map((e, i) => (
                            <div key={i} className="text-red font-mono">{e}</div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Pagination */}
        {historyTotal > HISTORY_LIMIT && (
          <div className="flex items-center gap-2 px-4 py-2 border-t border-surface1 text-xs text-overlay0">
            <button
              onClick={() => setHistoryOffset(Math.max(0, historyOffset - HISTORY_LIMIT))}
              disabled={historyOffset === 0}
              className="px-2 py-0.5 rounded hover:bg-surface1 disabled:opacity-40 transition-colors"
            >← Назад</button>
            <span className="mx-auto font-mono">
              {historyOffset + 1}–{Math.min(historyOffset + HISTORY_LIMIT, historyTotal)} / {historyTotal}
            </span>
            <button
              onClick={() => setHistoryOffset(historyOffset + HISTORY_LIMIT)}
              disabled={historyOffset + HISTORY_LIMIT >= historyTotal}
              className="px-2 py-0.5 rounded hover:bg-surface1 disabled:opacity-40 transition-colors"
            >Далее →</button>
          </div>
        )}
      </div>

      {/* Git Tags — Config Snapshots */}
      <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
        <div className="px-4 py-2.5 border-b border-surface1 flex items-center gap-2">
          <RotateCcw size={13} className="text-overlay0" />
          <span className="text-xs font-medium text-subtext">Снимки конфигурации (git tags)</span>
          <span className="text-[10px] text-yellow ml-1 flex items-center gap-1">
            <AlertTriangle size={9} />
            Откат применяется только к <span className="font-mono">{node.ip}</span>
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            {loadingTags && <Loader2 size={11} className="animate-spin text-overlay0" />}
            <button
              onClick={() => setTagsKey((k) => k + 1)}
              disabled={loadingTags}
              title="Обновить список тегов"
              className="text-overlay0 hover:text-text disabled:opacity-40 transition-colors"
            >
              <RefreshCw size={11} />
            </button>
          </div>
        </div>

        {!loadingTags && tags.length === 0 && (
          <p className="px-4 py-4 text-xs text-overlay0 italic">
            Снимков нет — запустите Config Dump для создания тега.
          </p>
        )}

        {tags.length > 0 && (
          <div className="divide-y divide-surface1/30 max-h-52 overflow-y-auto">
            {tags.map((tag) => {
              const isRunning = tagRbRunning === tag;
              return (
                <div key={tag} className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-xs font-mono text-mauve">{tag}</span>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => handleTagRollback(tag, "yaml_only")}
                      disabled={!!tagRbRunning}
                      title="Только git checkout тега, без применения на ноды"
                      className="flex items-center gap-1 text-[11px] bg-yellow/10 hover:bg-yellow/20 disabled:opacity-40 text-yellow border border-yellow/30 px-2 py-1 rounded-lg transition-colors"
                    >
                      {isRunning ? <Loader2 size={9} className="animate-spin" /> : <RotateCcw size={9} />}
                      Откатить YAML
                    </button>
                    <button
                      onClick={() => handleTagRollback(tag, "yaml_and_apply")}
                      disabled={!!tagRbRunning}
                      title="git checkout тега + применить конфиг через CMD на ноду"
                      className="flex items-center gap-1 text-[11px] bg-red/10 hover:bg-red/20 disabled:opacity-40 text-red border border-red/30 px-2 py-1 rounded-lg transition-colors"
                    >
                      {isRunning ? <Loader2 size={9} className="animate-spin" /> : <RotateCcw size={9} />}
                      Откатить + Применить
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Tag rollback terminal */}
        {tagRbLines.length > 0 && (
          <>
            <div className="px-4 py-2 border-t border-surface1 flex items-center gap-2 bg-surface1/20">
              <Terminal size={11} className="text-overlay0" />
              <span className={`w-2 h-2 rounded-full ${
                tagRbRunning           ? "bg-green animate-pulse" :
                tagRbExitOk === true  ? "bg-green"               :
                tagRbExitOk === false ? "bg-red"                 :
                "bg-overlay0"
              }`} />
              <span className="text-xs font-mono text-subtext">
                {tagRbRunning
                  ? `Откат ${tagRbCurrent}...`
                  : tagRbExitOk === true
                    ? `Откат ${tagRbCurrent} завершён успешно`
                    : tagRbExitOk === false
                      ? `Откат ${tagRbCurrent} завершён с ошибкой`
                      : "Вывод"}
              </span>
            </div>
            <pre ref={tagRbTermRef} className="font-mono text-[11px] h-44 overflow-y-auto p-4 leading-relaxed bg-mantle/50">
              {tagRbLines.map((line, i) => (
                <div key={i} className={ansibleLineColor(line)}>{line || " "}</div>
              ))}
            </pre>
          </>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {/* Commit list */}
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-surface1 flex items-center gap-2">
            <GitBranch size={13} className="text-overlay0" />
            <span className="text-xs font-medium text-subtext">Git log — config-store/</span>
          </div>

          {loadingLog && (
            <div className="flex items-center gap-2 px-4 py-6 text-xs text-overlay0">
              <Loader2 size={12} className="animate-spin" /> Загрузка истории...
            </div>
          )}
          {!loadingLog && commits.length === 0 && (
            <p className="px-4 py-6 text-xs text-overlay0 italic">
              История пуста — config-store/ ещё не имеет коммитов.
            </p>
          )}

          <div className="divide-y divide-surface1/30 max-h-80 overflow-y-auto">
            {commits.map((commit) => {
              const active = selectedCommit?.hash === commit.hash;
              return (
                <button
                  key={commit.hash}
                  onClick={() => selectCommit(commit)}
                  className={`w-full text-left px-4 py-3 transition-colors ${
                    active ? "bg-blue/8" : "hover:bg-surface1/30"
                  }`}
                >
                  <div className="flex items-center justify-between mb-0.5">
                    <span className={`font-mono text-xs ${active ? "text-blue" : "text-mauve"}`}>
                      {commit.short}
                    </span>
                    <span className="text-[10px] text-overlay0 flex items-center gap-1">
                      <Clock size={9} />
                      {commit.date.slice(0, 16).replace("T", " ")}
                    </span>
                  </div>
                  <p className="text-xs text-text truncate">{commit.message}</p>
                  <p className="text-[10px] text-overlay0 mt-0.5">{commit.author}</p>
                </button>
              );
            })}
          </div>
        </div>

        {/* Diff + rollback */}
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 border-b border-surface1 flex items-center justify-between shrink-0">
            <span className="text-xs font-medium text-subtext">
              {selectedCommit ? `Изменения в ${selectedCommit.short}` : "Выберите коммит"}
            </span>
            {selectedCommit && (
              <button
                onClick={() => handleRollback(selectedCommit.hash)}
                disabled={rollingBack}
                className="flex items-center gap-1.5 text-xs bg-red/10 hover:bg-red/20 disabled:opacity-50 text-red px-2.5 py-1 rounded-lg transition-colors"
              >
                {rollingBack ? <Loader2 size={10} className="animate-spin" /> : <RotateCcw size={10} />}
                Откатить
              </button>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {rolledBack === selectedCommit?.hash && (
              <div className="m-4 p-3 bg-green/10 border border-green/20 rounded-lg flex items-center gap-2">
                <CheckCircle2 size={14} className="text-green shrink-0" />
                <p className="text-xs text-green">
                  Откат к {rolledBack!.slice(0, 7)} выполнен. Примените через Ansible (вкладка Playbooks).
                </p>
              </div>
            )}

            {loadingDiff && (
              <div className="flex items-center gap-2 p-4 text-xs text-overlay0">
                <Loader2 size={12} className="animate-spin" /> Загрузка diff...
              </div>
            )}

            {diff && !loadingDiff && (
              <pre className="text-[11px] font-mono p-4 leading-relaxed min-h-48">
                {diff.split("\n").map((line, i) => {
                  const cls =
                    line.startsWith("+++") || line.startsWith("---") ? "text-subtext" :
                    line.startsWith("+")   ? "text-green"   :
                    line.startsWith("-")   ? "text-red"     :
                    line.startsWith("@@")  ? "text-mauve"   :
                    "text-overlay0";
                  return <div key={i} className={cls}>{line || " "}</div>;
                })}
              </pre>
            )}

            {!selectedCommit && !loadingDiff && (
              <p className="p-4 text-xs text-overlay0">Выберите коммит чтобы увидеть изменения</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Tab: Profiles ─────────────────────────────────────────────────────────────

function ProfilesTab({ nodes, creds, onSwitchTab }: { nodes: MonitorNodeInfo[]; creds: CmdCreds; onSwitchTab: (t: Tab) => void }) {
  // Sub-view: library (two-panel) or matrix
  const [view,       setView]       = useState<"library" | "matrix">("library");
  // Matrix sub-mode: A = presence list, B = values (current behaviour)
  const [matrixMode, setMatrixMode] = useState<"presence" | "values">("values");

  // Profile list
  const [profiles,      setProfiles]      = useState<ProfileMeta[]>([]);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState<string | null>(null);

  // Right-panel: selected profile detail
  const [selected,      setSelected]      = useState<ProfileFull | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // New profile creation
  const [newName, setNewName]             = useState("");
  const [creating, setCreating]           = useState(false);
  const [showNewInput, setShowNewInput]   = useState(false);

  // Inline rename
  const [renaming,    setRenaming]        = useState(false);
  const [renameValue, setRenameValue]     = useState("");

  // Notes editing
  const [editingNotes, setEditingNotes]   = useState(false);
  const [notesValue,   setNotesValue]     = useState("");

  // Module collapse state: module name → expanded bool
  const [expandedMods, setExpandedMods]   = useState<Record<string, boolean>>({});

  // Module JSON editing: module name → json string
  const [editingMod,   setEditingMod]     = useState<string | null>(null);
  const [editModJson,  setEditModJson]    = useState("");
  const [savingMod,    setSavingMod]      = useState(false);
  const [modError,     setModError]       = useState<string | null>(null);

  // Apply (SSE)
  const [applyHosts,      setApplyHosts]      = useState<Set<string>>(new Set());
  const [applyMode,       setApplyMode]       = useState<"cmd" | "ansible">("cmd");
  const [applyModsMask,   setApplyModsMask]   = useState<Set<string>>(new Set()); // empty = all
  const [applyRunning,    setApplyRunning]    = useState(false);
  const [applyLines,      setApplyLines]      = useState<string[]>([]);
  const [applyExitOk,     setApplyExitOk]     = useState<boolean | null>(null);
  const applyTermRef                          = useRef<HTMLPreElement>(null);
  const applyAbortRef                         = useRef<AbortController | null>(null);

  // Pull drawer (single node)
  const [pullOpen,       setPullOpen]       = useState(false);
  const [pullIp,         setPullIp]         = useState("");
  const [pullModules,    setPullModules]    = useState<string[]>(FALLBACK_MODULES);
  const [pullLoading,    setPullLoading]    = useState(false);
  const [pullError,      setPullError]      = useState<string | null>(null);
  // Доступные модули для pull (live → stored → fallback)
  const [pullAvailMods,  setPullAvailMods]  = useState<string[]>(FALLBACK_MODULES);
  const [pullModsLoading,setPullModsLoading]= useState(false);
  const [pullModsSource, setPullModsSource] = useState<"live" | "stored" | "fallback">("fallback");

  // Pull-all drawer (multiple nodes)
  const [pullAllOpen,     setPullAllOpen]     = useState(false);
  const [pullAllIps,      setPullAllIps]      = useState<Set<string>>(new Set());
  const [pullAllRunning,  setPullAllRunning]  = useState(false);
  const [pullAllLines,    setPullAllLines]    = useState<string[]>([]);
  // Accumulated per-node data: module → ip → config
  const [pullAllData,     setPullAllData]     = useState<Record<string, Record<string, Record<string, unknown>>>>({});
  // Detected conflicts: module name → list of ips that differ
  const [pullAllConflicts, setPullAllConflicts] = useState<Record<string, string[]>>({});
  // Conflict resolution per module: "first" | "last" | ip
  const [conflictResolution, setConflictResolution] = useState<Record<string, string>>({});
  const [pullAllSaving,   setPullAllSaving]   = useState(false);
  const pullAllAbortRef                       = useRef<AbortController | null>(null);
  const pullAllTermRef                        = useRef<HTMLDivElement>(null);

  // Search filter
  const [search, setSearch]               = useState("");

  // Profile → Playbook dialog
  const [toPlaybookSlug,    setToPlaybookSlug]    = useState<string | null>(null);
  const [toPlaybookHosts,   setToPlaybookHosts]   = useState<string[]>([]);
  const [toPlaybookLoading, setToPlaybookLoading] = useState(false);

  // Matrix: popover state
  const [matrixPopover, setMatrixPopover] = useState<{ slug: string; mod: string } | null>(null);

  // ── Lifecycle ────────────────────────────────────────────────────────────

  useEffect(() => {
    loadProfiles();
  }, []);

  useEffect(() => {
    if (applyTermRef.current) {
      applyTermRef.current.scrollTop = applyTermRef.current.scrollHeight;
    }
  }, [applyLines]);

  useEffect(() => () => { applyAbortRef.current?.abort(); }, []);
  useEffect(() => () => { pullAllAbortRef.current?.abort(); }, []);

  useEffect(() => {
    if (pullAllTermRef.current) pullAllTermRef.current.scrollTop = pullAllTermRef.current.scrollHeight;
  }, [pullAllLines]);

  // Загрузка списка модулей при смене ноды в pull-drawer (live → stored → fallback)
  useEffect(() => {
    if (!pullIp) return;
    let cancelled = false;
    setPullModsLoading(true);
    const load = async () => {
      // 1. Live — точный список с ноды
      if (creds.pass.trim()) {
        try {
          const mods = await configApi.live.listModules(pullIp, creds);
          if (!cancelled && mods.length > 0) {
            setPullAvailMods(mods);
            setPullModules(mods);
            setPullModsSource("live");
            setPullModsLoading(false);
            return;
          }
        } catch { /* fall through */ }
      }
      // 2. Stored — из config-store
      try {
        const mods = await configApi.stored.listModules(pullIp);
        if (!cancelled && mods.length > 0) {
          setPullAvailMods(mods);
          setPullModules(mods);
          setPullModsSource("stored");
          setPullModsLoading(false);
          return;
        }
      } catch { /* fall through */ }
      // 3. Fallback
      if (!cancelled) {
        setPullAvailMods(FALLBACK_MODULES);
        setPullModules(FALLBACK_MODULES);
        setPullModsSource("fallback");
        setPullModsLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [pullIp, creds.user, creds.pass]);

  // ── Helpers ───────────────────────────────────────────────────────────────

  async function loadProfiles() {
    setLoading(true);
    setError(null);
    try {
      const list = await configApi.profiles.list();
      setProfiles(list);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function openProfile(slug: string) {
    setDetailLoading(true);
    setSelected(null);
    setEditingMod(null);
    setModError(null);
    setExpandedMods({});
    setApplyLines([]);
    setApplyExitOk(null);
    setPullOpen(false);
    try {
      const full = await configApi.profiles.get(slug);
      setSelected(full);
      setRenameValue(full.name);
      setNotesValue(full.notes ?? "");
    } catch (e) {
      setError(String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const full = await configApi.profiles.create(newName.trim());
      setProfiles((prev) => [...prev, {
        slug: full.slug, name: full.name, created_at: full.created_at,
        updated_at: full.updated_at, notes: full.notes, module_names: full.module_names,
      }]);
      setNewName("");
      setShowNewInput(false);
      await openProfile(full.slug);
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  async function handleRename() {
    if (!selected || !renameValue.trim()) { setRenaming(false); return; }
    try {
      const updated = await configApi.profiles.update(selected.slug, { name: renameValue.trim() });
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, name: updated.name } : p));
    } catch (e) {
      setError(String(e));
    } finally {
      setRenaming(false);
    }
  }

  async function handleSaveNotes() {
    if (!selected) return;
    setEditingNotes(false);
    try {
      const updated = await configApi.profiles.update(selected.slug, { notes: notesValue });
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, notes: updated.notes } : p));
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleDuplicate() {
    if (!selected) return;
    const name = window.prompt("Имя нового профиля:", `${selected.name} (копия)`);
    if (!name) return;
    try {
      const dup = await configApi.profiles.duplicate(selected.slug, name);
      setProfiles((prev) => [...prev, {
        slug: dup.slug, name: dup.name, created_at: dup.created_at,
        updated_at: dup.updated_at, notes: dup.notes, module_names: dup.module_names,
      }]);
      await openProfile(dup.slug);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleDelete() {
    if (!selected) return;
    if (!window.confirm(`Удалить профиль «${selected.name}»? Это действие нельзя отменить.`)) return;
    try {
      await configApi.profiles.remove(selected.slug);
      setProfiles((prev) => prev.filter((p) => p.slug !== selected.slug));
      setSelected(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function handleRemoveModule(modName: string) {
    if (!selected) return;
    if (!window.confirm(`Удалить модуль «${modName}» из профиля?`)) return;
    setModError(null);
    try {
      const updated = await configApi.profiles.removeModule(selected.slug, modName);
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, module_names: updated.module_names } : p));
    } catch (e) {
      setModError(String(e));
    }
  }

  function startEditMod(modName: string) {
    if (!selected) return;
    setEditingMod(modName);
    setEditModJson(JSON.stringify(selected.modules[modName] ?? {}, null, 2));
    setModError(null);
  }

  async function handleSaveMod() {
    if (!selected || !editingMod) return;
    setSavingMod(true);
    setModError(null);
    try {
      const parsed = JSON.parse(editModJson) as Record<string, unknown>;
      const updated = await configApi.profiles.upsertModule(selected.slug, editingMod, parsed);
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, module_names: updated.module_names } : p));
      setEditingMod(null);
    } catch (e) {
      setModError(e instanceof SyntaxError ? `JSON ошибка: ${e.message}` : String(e));
    } finally {
      setSavingMod(false);
    }
  }

  function handleApply() {
    if (!selected || applyHosts.size === 0) return;
    applyAbortRef.current?.abort();
    setApplyRunning(true);
    setApplyExitOk(null);
    const modulesToApply = applyModsMask.size > 0 ? [...applyModsMask] : undefined;
    setApplyLines([
      `[${new Date().toLocaleTimeString("ru")}] Применение «${selected.name}» [${applyMode.toUpperCase()}] → ${[...applyHosts].join(", ")}${modulesToApply ? ` (${modulesToApply.length} модулей)` : " (все модули)"}...`,
      "",
    ]);
    applyAbortRef.current = configApi.profiles.streamApply(
      selected.slug,
      [...applyHosts],
      modulesToApply,
      creds.user,
      creds.pass,
      (line) => setApplyLines((prev) => [...prev, line]),
      (ok)   => { setApplyRunning(false); setApplyExitOk(ok); },
      applyMode,
    );
  }

  function handlePullAllStart() {
    if (!selected || pullAllIps.size === 0) return;
    pullAllAbortRef.current?.abort();
    setPullAllRunning(true);
    setPullAllLines([`[${new Date().toLocaleTimeString("ru")}] Загрузка со всех нод: ${[...pullAllIps].join(", ")}...`, ""]);
    setPullAllData({});
    setPullAllConflicts({});
    setConflictResolution({});

    // Accumulated data per module per ip
    const acc: Record<string, Record<string, Record<string, unknown>>> = {};

    pullAllAbortRef.current = configApi.profiles.streamPullAll(
      selected.slug,
      [...pullAllIps],
      creds.user,
      creds.pass,
      (event: PullAllEvent) => {
        if (event.type === "progress") {
          setPullAllLines((prev) => [...prev, `[${event.ip}] ${event.module} → ok`]);
          if (!acc[event.module]) acc[event.module] = {};
          acc[event.module][event.ip] = event.config;
          setPullAllData({ ...acc });
        } else if (event.type === "error") {
          setPullAllLines((prev) => [...prev, `[${event.ip}] ${event.module} → ОШИБКА: ${event.error}`]);
        } else if (event.type === "connect_error") {
          setPullAllLines((prev) => [...prev, `[${event.ip}] ПОДКЛЮЧЕНИЕ FAILED: ${event.error}`]);
        } else if (event.type === "done") {
          setPullAllLines((prev) => [...prev, ``, `Готово: ${event.total_ok} ok, ${event.total_err} ошибок`]);
          // Detect conflicts: modules where values differ across nodes
          const conflicts: Record<string, string[]> = {};
          for (const [mod, nodeData] of Object.entries(acc)) {
            const ips = Object.keys(nodeData);
            if (ips.length < 2) continue;
            const jsons = ips.map((ip) => JSON.stringify(nodeData[ip]));
            const allSame = jsons.every((j) => j === jsons[0]);
            if (!allSame) conflicts[mod] = ips;
          }
          setPullAllConflicts(conflicts);
        }
      },
      () => { setPullAllRunning(false); },
    );
  }

  async function handlePullAllSave() {
    if (!selected || Object.keys(pullAllData).length === 0) return;
    setPullAllSaving(true);
    const errors: string[] = [];

    for (const [module, nodeData] of Object.entries(pullAllData)) {
      const ips = Object.keys(nodeData);
      const hasConflict = pullAllConflicts[module];

      let chosenConfig: Record<string, unknown>;
      if (!hasConflict) {
        // No conflict — take from first node
        chosenConfig = nodeData[ips[0]];
      } else {
        const resolution = conflictResolution[module] ?? "first";
        if (resolution === "first") {
          chosenConfig = nodeData[ips[0]];
        } else if (resolution === "last") {
          chosenConfig = nodeData[ips[ips.length - 1]];
        } else {
          // resolution is an IP
          chosenConfig = nodeData[resolution] ?? nodeData[ips[0]];
        }
      }

      try {
        await configApi.profiles.upsertModule(selected.slug, module, chosenConfig);
      } catch (e) {
        errors.push(`${module}: ${e}`);
      }
    }

    try {
      const updated = await configApi.profiles.get(selected.slug);
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, module_names: updated.module_names } : p));
    } catch { /* ignore */ }

    setPullAllSaving(false);
    if (errors.length === 0) {
      setPullAllOpen(false);
      setPullAllData({});
      setPullAllConflicts({});
      setPullAllLines([]);
    } else {
      setPullAllLines((prev) => [...prev, "", `Ошибки сохранения: ${errors.join("; ")}`]);
    }
  }

  async function handlePull() {
    if (!selected || !pullIp) return;
    setPullLoading(true);
    setPullError(null);
    try {
      const updated = await configApi.profiles.pull(
        selected.slug, pullIp, pullModules, creds.user, creds.pass,
      );
      setSelected(updated);
      setProfiles((prev) => prev.map((p) => p.slug === updated.slug
        ? { ...p, module_names: updated.module_names } : p));
      setPullOpen(false);
    } catch (e) {
      setPullError(String(e));
    } finally {
      setPullLoading(false);
    }
  }

  async function profileToPlaybook(slug: string) {
    if (toPlaybookHosts.length === 0) return;
    setToPlaybookLoading(true);
    try {
      const r = await fetch(`/api/config/profiles/${slug}/to-playbook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hosts: toPlaybookHosts, mode: 'full' }),
      });
      if (r.ok) {
        setToPlaybookSlug(null);
        setToPlaybookHosts([]);
        onSwitchTab('playbooks');
      }
    } finally {
      setToPlaybookLoading(false);
    }
  }

  // ── Derived ───────────────────────────────────────────────────────────────

  const backendNodes  = nodes.filter((n) => n.node_type === "ivamail_backend");
  const frontendNodes = nodes.filter((n) => n.node_type === "ivamail_frontend");
  const ivamailNodes  = nodes.filter(
    (n) => n.node_type === "ivamail_backend" || n.node_type === "ivamail_frontend",
  );
  const filteredProfiles = profiles.filter((p) =>
    !search.trim() || p.name.toLowerCase().includes(search.toLowerCase()),
  );

  // All unique module names across all profiles (for matrix)
  const allModNames = useMemo(() => {
    const s = new Set<string>();
    profiles.forEach((p) => p.module_names.forEach((m) => s.add(m)));
    return [...s].sort();
  }, [profiles]);

  // ── Matrix apply (per-row) ────────────────────────────────────────────────

  const [matrixApplySlug,    setMatrixApplySlug]    = useState<string | null>(null);
  const [matrixApplyHosts,   setMatrixApplyHosts]   = useState<Set<string>>(new Set());
  const [matrixApplyRunning, setMatrixApplyRunning] = useState(false);
  const [matrixApplyLines,   setMatrixApplyLines]   = useState<string[]>([]);
  const [matrixApplyExitOk,  setMatrixApplyExitOk]  = useState<boolean | null>(null);
  const matrixTermRef                                = useRef<HTMLPreElement>(null);
  const matrixAbortRef                               = useRef<AbortController | null>(null);

  useEffect(() => {
    if (matrixTermRef.current) {
      matrixTermRef.current.scrollTop = matrixTermRef.current.scrollHeight;
    }
  }, [matrixApplyLines]);

  useEffect(() => () => { matrixAbortRef.current?.abort(); }, []);

  function handleMatrixApply(slug: string) {
    if (matrixApplyHosts.size === 0) return;
    const prof = profiles.find((p) => p.slug === slug);
    matrixAbortRef.current?.abort();
    setMatrixApplyRunning(true);
    setMatrixApplyExitOk(null);
    setMatrixApplyLines([`[${new Date().toLocaleTimeString("ru")}] Применение профиля «${prof?.name ?? slug}» → ${[...matrixApplyHosts].join(", ")}...`, ""]);
    matrixAbortRef.current = configApi.profiles.streamApply(
      slug,
      [...matrixApplyHosts],
      undefined,
      creds.user,
      creds.pass,
      (line) => setMatrixApplyLines((prev) => [...prev, line]),
      (ok)   => { setMatrixApplyRunning(false); setMatrixApplyExitOk(ok); },
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Sub-tab bar */}
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-surface1 bg-mantle/30 shrink-0">
        <button
          onClick={() => setView("library")}
          className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors ${
            view === "library" ? "bg-blue/15 text-blue" : "text-subtext hover:text-text hover:bg-surface1/50"
          }`}
        >
          <FileText size={11} />
          Библиотека
        </button>
        <button
          onClick={() => setView("matrix")}
          className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors ${
            view === "matrix" ? "bg-blue/15 text-blue" : "text-subtext hover:text-text hover:bg-surface1/50"
          }`}
        >
          <Layers size={11} />
          Матрица
        </button>
        {view === "matrix" && (
          <div className="flex items-center gap-0.5 bg-surface1/50 rounded-lg p-0.5 ml-2">
            {(["presence", "values"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMatrixMode(m)}
                className={`text-[10px] px-2 py-0.5 rounded-md transition-colors ${
                  matrixMode === m ? "bg-blue/20 text-blue" : "text-overlay0 hover:text-text"
                }`}
              >
                {m === "presence" ? "А: Наличие" : "Б: Значения"}
              </button>
            ))}
          </div>
        )}
        <div className="ml-auto text-[10px] text-overlay0">
          {profiles.length} профил{profiles.length === 1 ? "ь" : profiles.length < 5 ? "я" : "ей"}
        </div>
      </div>

      {error && (
        <div className="px-5 pt-3 shrink-0">
          <ErrMsg msg={error} />
        </div>
      )}

      {/* ── Library view ── */}
      {view === "library" && (
        <div className="flex flex-1 min-h-0 overflow-hidden relative">
          {/* Left panel: profile list */}
          <div className="w-64 shrink-0 border-r border-surface1 flex flex-col bg-mantle/20">
            {/* Search + New */}
            <div className="p-3 border-b border-surface1 space-y-2">
              <div className="flex items-center gap-2 bg-surface0 rounded-lg px-2.5 py-1.5 ring-1 ring-transparent focus-within:ring-blue/30 transition-all">
                <Search size={11} className="text-overlay0 shrink-0" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Поиск профиля..."
                  className="flex-1 text-xs bg-transparent outline-none text-text placeholder-overlay0"
                />
              </div>
              {showNewInput ? (
                <div className="flex items-center gap-1.5">
                  <input
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleCreate();
                      if (e.key === "Escape") { setShowNewInput(false); setNewName(""); }
                    }}
                    placeholder="Название профиля"
                    autoFocus
                    className="flex-1 text-xs bg-surface0 border border-surface1 rounded-lg px-2.5 py-1.5 outline-none focus:border-blue text-text"
                  />
                  <button
                    onClick={handleCreate}
                    disabled={creating || !newName.trim()}
                    className="text-xs bg-blue/90 hover:bg-blue disabled:opacity-40 text-mantle font-semibold px-2.5 py-1.5 rounded-lg transition-colors"
                  >
                    {creating ? <Loader2 size={10} className="animate-spin" /> : "OK"}
                  </button>
                  <button
                    onClick={() => { setShowNewInput(false); setNewName(""); }}
                    className="text-overlay0 hover:text-text transition-colors"
                  >
                    <X size={13} />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setShowNewInput(true)}
                  className="w-full flex items-center gap-1.5 text-xs text-subtext hover:text-blue hover:bg-blue/5 border border-dashed border-surface2 hover:border-blue/30 rounded-lg px-2.5 py-1.5 transition-colors"
                >
                  <PlusCircle size={11} />
                  Новый профиль
                </button>
              )}
            </div>

            {/* Profile list */}
            <div className="flex-1 overflow-y-auto">
              {loading && (
                <div className="flex items-center gap-2 px-4 py-6 text-xs text-overlay0">
                  <Loader2 size={12} className="animate-spin" /> Загрузка...
                </div>
              )}
              {!loading && filteredProfiles.length === 0 && (
                <p className="px-4 py-6 text-xs text-overlay0 italic text-center">
                  {search ? "Ничего не найдено" : "Нет профилей"}
                </p>
              )}
              {filteredProfiles.map((p) => {
                const isActive = selected?.slug === p.slug;
                return (
                  <div
                    key={p.slug}
                    className={`border-b border-surface1/30 group ${
                      isActive ? "bg-blue/8 border-l-2 border-l-blue" : "hover:bg-surface1/30"
                    }`}
                  >
                    <button
                      onClick={() => openProfile(p.slug)}
                      className="w-full text-left px-4 py-3 transition-colors"
                    >
                      <div className="flex items-center justify-between mb-0.5">
                        <span className={`text-xs font-medium truncate ${isActive ? "text-blue" : "text-text"}`}>
                          {p.name}
                        </span>
                        <span className="text-[10px] text-overlay0 shrink-0 ml-1 font-mono">
                          {p.module_names.length} мод.
                        </span>
                      </div>
                      {p.notes && (
                        <p className="text-[10px] text-overlay0 truncate leading-tight">{p.notes}</p>
                      )}
                      <div className="flex flex-wrap gap-1 mt-1">
                        {p.module_names.slice(0, 4).map((m) => (
                          <span key={m} className="text-[9px] bg-surface1 text-overlay0 px-1 py-0 rounded font-mono">
                            {m}
                          </span>
                        ))}
                        {p.module_names.length > 4 && (
                          <span className="text-[9px] text-overlay0">+{p.module_names.length - 4}</span>
                        )}
                      </div>
                    </button>
                    {/* Profile → Playbook action */}
                    <div className="px-4 pb-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={(e) => { e.stopPropagation(); setToPlaybookSlug(p.slug); setToPlaybookHosts([]); }}
                        className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-teal hover:bg-teal/10 rounded transition-colors"
                        title="Сгенерировать плейбук из профиля"
                      >
                        → Playbook
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Right panel: profile detail */}
          <div className="flex-1 flex flex-col overflow-hidden relative">
            {detailLoading && (
              <div className="flex items-center justify-center h-full gap-2 text-overlay0 text-sm">
                <Loader2 size={14} className="animate-spin" /> Загрузка профиля...
              </div>
            )}

            {!detailLoading && !selected && (
              <div className="flex items-center justify-center h-full text-overlay0 text-sm">
                Выберите профиль из списка слева
              </div>
            )}

            {!detailLoading && selected && (
              <div className="flex flex-col h-full overflow-hidden">
                {/* Header */}
                <div className="px-5 py-3 border-b border-surface1 shrink-0">
                  <div className="flex items-center justify-between gap-3">
                    {/* Inline rename */}
                    {renaming ? (
                      <input
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={handleRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename();
                          if (e.key === "Escape") setRenaming(false);
                        }}
                        autoFocus
                        className="text-sm font-semibold bg-surface1 rounded-lg px-2 py-0.5 outline-none focus:ring-1 focus:ring-blue/50 text-text flex-1 max-w-xs"
                      />
                    ) : (
                      <h2
                        className="text-sm font-semibold text-text cursor-pointer hover:text-blue transition-colors flex-1 truncate"
                        onClick={() => { setRenaming(true); setRenameValue(selected.name); }}
                        title="Нажмите для переименования"
                      >
                        {selected.name}
                      </h2>
                    )}
                    <div className="flex items-center gap-1.5 shrink-0">
                      <button
                        onClick={() => { setRenaming(true); setRenameValue(selected.name); }}
                        title="Переименовать"
                        className="p-1.5 text-overlay0 hover:text-blue hover:bg-blue/10 rounded-lg transition-colors"
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        onClick={handleDuplicate}
                        title="Дублировать"
                        className="p-1.5 text-overlay0 hover:text-mauve hover:bg-mauve/10 rounded-lg transition-colors"
                      >
                        <Copy size={12} />
                      </button>
                      <button
                        onClick={handleDelete}
                        title="Удалить профиль"
                        className="p-1.5 text-overlay0 hover:text-red hover:bg-red/10 rounded-lg transition-colors"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>

                  {/* Notes */}
                  {editingNotes ? (
                    <div className="mt-2 space-y-1">
                      <textarea
                        value={notesValue}
                        onChange={(e) => setNotesValue(e.target.value)}
                        rows={2}
                        className="w-full text-xs bg-surface1 rounded-lg px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-blue/40 text-text resize-none"
                      />
                      <div className="flex gap-1.5">
                        <button
                          onClick={handleSaveNotes}
                          className="text-[11px] bg-blue/90 hover:bg-blue text-mantle font-semibold px-2.5 py-0.5 rounded-lg transition-colors"
                        >
                          Сохранить
                        </button>
                        <button
                          onClick={() => { setEditingNotes(false); setNotesValue(selected.notes ?? ""); }}
                          className="text-[11px] text-subtext hover:text-text transition-colors"
                        >
                          Отмена
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p
                      className="text-xs text-overlay0 mt-1 cursor-pointer hover:text-subtext transition-colors"
                      onClick={() => { setEditingNotes(true); setNotesValue(selected.notes ?? ""); }}
                      title="Нажмите для редактирования заметок"
                    >
                      {selected.notes || <span className="italic">Добавить заметку…</span>}
                    </p>
                  )}

                  <p className="text-[10px] text-overlay0 mt-1 font-mono">
                    slug: {selected.slug} · обновлён: {selected.updated_at.slice(0, 16).replace("T", " ")}
                  </p>
                </div>

                {/* Scrollable body */}
                <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                  {/* Modules section */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-[10px] font-semibold text-overlay0 uppercase tracking-widest">
                        Модули ({selected.module_names.length})
                      </p>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => setPullOpen(true)}
                          className="flex items-center gap-1 text-[11px] text-blue hover:text-blue/80 hover:bg-blue/5 px-2 py-1 rounded-lg transition-colors"
                        >
                          <Download size={11} />
                          С ноды
                        </button>
                        <button
                          onClick={() => { setPullAllOpen(true); setPullAllIps(new Set(ivamailNodes.map((n) => n.ip))); }}
                          className="flex items-center gap-1 text-[11px] text-green hover:text-green/80 hover:bg-green/5 px-2 py-1 rounded-lg transition-colors"
                        >
                          <Download size={11} />
                          Со всех нод
                        </button>
                      </div>
                    </div>

                    {modError && <ErrMsg msg={modError} />}

                    {selected.module_names.length === 0 && (
                      <p className="text-xs text-overlay0 italic text-center py-4">
                        Нет модулей. Загрузите с ноды или добавьте вручную.
                      </p>
                    )}

                    <div className="space-y-2">
                      {selected.module_names.map((modName) => {
                        const expanded  = expandedMods[modName] ?? false;
                        const modConfig = selected.modules[modName] ?? {};
                        const entries   = Object.entries(modConfig).filter(([k]) => !k.startsWith("_"));
                        const isEditing = editingMod === modName;

                        return (
                          <div key={modName} className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
                            <div className="flex items-center gap-2 px-3 py-2">
                              <button
                                onClick={() => setExpandedMods((prev) => ({ ...prev, [modName]: !prev[modName] }))}
                                className="flex items-center gap-1.5 flex-1 text-left"
                              >
                                {expanded
                                  ? <ChevronUp   size={12} className="text-overlay0 shrink-0" />
                                  : <ChevronRight size={12} className="text-overlay0 shrink-0" />}
                                <span className="text-xs font-mono font-medium text-text">{modName}</span>
                                <span className="text-[10px] text-overlay0 font-mono ml-1">
                                  {entries.length} ключей
                                </span>
                              </button>
                              <div className="flex items-center gap-1 shrink-0">
                                <button
                                  onClick={() => startEditMod(modName)}
                                  title="Редактировать JSON"
                                  className="p-1 text-overlay0 hover:text-blue hover:bg-blue/10 rounded transition-colors"
                                >
                                  <Pencil size={11} />
                                </button>
                                <button
                                  onClick={() => handleRemoveModule(modName)}
                                  title="Удалить модуль"
                                  className="p-1 text-overlay0 hover:text-red hover:bg-red/10 rounded transition-colors"
                                >
                                  <X size={11} />
                                </button>
                              </div>
                            </div>

                            <AnimatePresence initial={false}>
                              {expanded && (
                                <motion.div
                                  initial={{ height: 0, opacity: 0 }}
                                  animate={{ height: "auto", opacity: 1 }}
                                  exit={{ height: 0, opacity: 0 }}
                                  transition={{ duration: 0.15 }}
                                  className="overflow-hidden border-t border-surface1"
                                >
                                  {isEditing ? (
                                    <div className="p-3 space-y-2">
                                      <textarea
                                        value={editModJson}
                                        onChange={(e) => setEditModJson(e.target.value)}
                                        rows={8}
                                        className="w-full text-[11px] font-mono bg-mantle rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-blue/40 text-text resize-y"
                                      />
                                      {modError && <ErrMsg msg={modError} />}
                                      <div className="flex gap-2">
                                        <button
                                          onClick={handleSaveMod}
                                          disabled={savingMod}
                                          className="flex items-center gap-1.5 text-xs bg-blue/90 hover:bg-blue disabled:opacity-50 text-mantle font-semibold px-3 py-1.5 rounded-lg transition-colors"
                                        >
                                          {savingMod ? <Loader2 size={10} className="animate-spin" /> : <Save size={10} />}
                                          Сохранить
                                        </button>
                                        <button
                                          onClick={() => { setEditingMod(null); setModError(null); }}
                                          className="text-xs text-subtext hover:text-text transition-colors px-3 py-1.5"
                                        >
                                          Отмена
                                        </button>
                                      </div>
                                    </div>
                                  ) : (
                                    <div className="px-4 py-2">
                                      {entries.length === 0 ? (
                                        <p className="text-[11px] text-overlay0 italic py-1">Пусто</p>
                                      ) : (
                                        <table className="w-full text-[11px] font-mono">
                                          <tbody>
                                            {entries.map(([k, v]) => (
                                              <tr key={k} className="border-b border-surface1/40 last:border-0">
                                                <td className="py-1 pr-3 text-mauve w-48 truncate">{k}</td>
                                                <td className="py-1 text-subtext truncate max-w-xs">
                                                  {Array.isArray(v) ? (v as unknown[]).join(", ") : String(v ?? "")}
                                                </td>
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      )}
                                    </div>
                                  )}
                                </motion.div>
                              )}
                            </AnimatePresence>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Apply section */}
                  <div className="bg-surface0 border border-surface1 rounded-xl p-4 space-y-3">
                    <p className="text-[10px] font-semibold text-overlay0 uppercase tracking-widest">
                      Применить профиль
                    </p>

                    {/* Mode toggle */}
                    <div className="flex items-center gap-1 bg-mantle rounded-lg p-0.5">
                      {(["cmd", "ansible"] as const).map((m) => (
                        <button
                          key={m}
                          onClick={() => setApplyMode(m)}
                          className={`flex-1 text-[11px] py-1 rounded-md transition-colors font-mono font-medium ${
                            applyMode === m
                              ? "bg-blue/20 text-blue"
                              : "text-overlay0 hover:text-text"
                          }`}
                        >
                          {m === "cmd" ? "CMD (прямо)" : "Ansible"}
                        </button>
                      ))}
                    </div>

                    {/* Target hosts */}
                    {ivamailNodes.length === 0 ? (
                      <p className="text-xs text-overlay0 italic">Нет IVA Mail нод в реестре</p>
                    ) : (
                      <div className="space-y-2">
                        <p className="text-[9px] text-overlay0/60 uppercase tracking-wider">Ноды</p>
                        {[
                          { label: "Backend",  list: backendNodes },
                          { label: "Frontend", list: frontendNodes },
                        ].map(({ label, list }) =>
                          list.length > 0 && (
                            <div key={label} className="space-y-1">
                              <p className="text-[9px] text-overlay0/40 uppercase tracking-wider">{label}</p>
                              <div className="flex flex-wrap gap-2">
                                {list.map((n) => {
                                  const nodeLabel = n.display_name || n.hostname || n.ip;
                                  const checked   = applyHosts.has(n.ip);
                                  return (
                                    <label key={n.ip} className="flex items-center gap-1.5 cursor-pointer select-none">
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={(e) => setApplyHosts((prev) => {
                                          const next = new Set(prev);
                                          if (e.target.checked) next.add(n.ip); else next.delete(n.ip);
                                          return next;
                                        })}
                                        className="accent-blue w-3.5 h-3.5"
                                      />
                                      <span className="text-xs text-subtext font-mono">{nodeLabel}</span>
                                    </label>
                                  );
                                })}
                              </div>
                            </div>
                          )
                        )}
                        <div className="flex items-center gap-2 pt-0.5">
                          <button onClick={() => setApplyHosts(new Set(ivamailNodes.map((n) => n.ip)))}
                            className="text-[10px] text-overlay0 hover:text-text transition-colors">все</button>
                          <span className="text-overlay0/40 text-[10px]">·</span>
                          <button onClick={() => setApplyHosts(new Set())}
                            className="text-[10px] text-overlay0 hover:text-text transition-colors">нет</button>
                        </div>
                      </div>
                    )}

                    {/* Module mask (which modules to apply) */}
                    {selected && selected.module_names.length > 0 && (
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-2">
                          <p className="text-[9px] text-overlay0/60 uppercase tracking-wider flex-1">Модули</p>
                          <button onClick={() => setApplyModsMask(new Set())}
                            className="text-[10px] text-overlay0 hover:text-text transition-colors">все</button>
                          <span className="text-overlay0/40 text-[10px]">·</span>
                          <button onClick={() => setApplyModsMask(new Set(selected.module_names))}
                            className="text-[10px] text-overlay0 hover:text-text transition-colors">выбрать все</button>
                        </div>
                        <div className="flex flex-wrap gap-2 bg-mantle/40 rounded-lg px-3 py-2 max-h-32 overflow-y-auto">
                          {selected.module_names.map((m) => {
                            // If mask is empty → all are "active" (unfiltered)
                            const active = applyModsMask.size === 0 || applyModsMask.has(m);
                            return (
                              <label key={m} className="flex items-center gap-1 cursor-pointer select-none">
                                <input
                                  type="checkbox"
                                  checked={active}
                                  onChange={(e) => setApplyModsMask((prev) => {
                                    // When empty set → all; first uncheck = switch to explicit list
                                    let next: Set<string>;
                                    if (prev.size === 0) {
                                      // Was "all" — switch to explicit mode with all except this one
                                      next = new Set(selected.module_names.filter((x) => x !== m));
                                    } else {
                                      next = new Set(prev);
                                      if (e.target.checked) next.add(m); else next.delete(m);
                                    }
                                    // If all selected → revert to empty (all)
                                    if (next.size === selected.module_names.length) return new Set();
                                    return next;
                                  })}
                                  className="accent-blue w-3 h-3"
                                />
                                <span className="text-[10px] text-subtext font-mono">{m}</span>
                              </label>
                            );
                          })}
                        </div>
                        {applyModsMask.size > 0 && (
                          <p className="text-[10px] text-yellow">
                            Будут применены только {applyModsMask.size} из {selected.module_names.length} модулей
                          </p>
                        )}
                      </div>
                    )}

                    <button
                      onClick={handleApply}
                      disabled={applyRunning || applyHosts.size === 0}
                      className="flex items-center gap-1.5 text-xs bg-green/10 hover:bg-green/20 disabled:opacity-50 text-green border border-green/30 font-semibold px-4 py-1.5 rounded-lg transition-colors"
                    >
                      {applyRunning ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
                      Применить [{applyMode.toUpperCase()}] →
                    </button>

                    {applyLines.length > 0 && (
                      <div className="bg-mantle/50 border border-surface1 rounded-xl overflow-hidden">
                        <div className="px-4 py-2 border-b border-surface1 flex items-center gap-2">
                          <Terminal size={11} className="text-overlay0" />
                          <span className={`w-2 h-2 rounded-full ${
                            applyRunning           ? "bg-green animate-pulse" :
                            applyExitOk === true  ? "bg-green"               :
                            applyExitOk === false ? "bg-red"                 :
                            "bg-overlay0"
                          }`} />
                          <span className="text-xs font-mono text-subtext">
                            {applyRunning
                              ? "Применение..."
                              : applyExitOk === true
                                ? "Завершено успешно"
                                : applyExitOk === false
                                  ? "Завершено с ошибкой"
                                  : "Вывод"}
                          </span>
                        </div>
                        <pre ref={applyTermRef} className="font-mono text-[11px] h-48 overflow-y-auto p-4 leading-relaxed">
                          {applyLines.map((line, i) => (
                            <div key={i} className={ansibleLineColor(line)}>{line || " "}</div>
                          ))}
                        </pre>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Pull-all drawer */}
            <AnimatePresence>
              {pullAllOpen && selected && (
                <motion.div
                  initial={{ x: 400, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  exit={{ x: 400, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                  className="absolute right-0 top-0 bottom-0 w-[420px] bg-mantle border-l border-surface1 z-20 flex flex-col shadow-xl"
                >
                  <div className="flex items-center justify-between px-5 py-3 border-b border-surface1 shrink-0">
                    <div>
                      <h3 className="text-sm font-semibold text-text">Загрузить со всех нод</h3>
                      <p className="text-[10px] text-overlay0 mt-0.5">Профиль: {selected.name}</p>
                    </div>
                    <button onClick={() => { setPullAllOpen(false); pullAllAbortRef.current?.abort(); }}
                      className="text-overlay0 hover:text-text transition-colors">
                      <X size={14} />
                    </button>
                  </div>

                  <div className="flex-1 overflow-y-auto p-5 space-y-4">
                    {/* IP selection */}
                    <div className="space-y-1.5">
                      <p className="text-[10px] text-overlay0 uppercase tracking-wider font-semibold">Ноды</p>
                      <div className="flex flex-wrap gap-2">
                        {ivamailNodes.map((n) => (
                          <label key={n.ip} className="flex items-center gap-1.5 cursor-pointer select-none">
                            <input
                              type="checkbox"
                              checked={pullAllIps.has(n.ip)}
                              onChange={(e) => setPullAllIps((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(n.ip); else next.delete(n.ip);
                                return next;
                              })}
                              className="accent-green w-3.5 h-3.5"
                            />
                            <span className="text-xs font-mono text-subtext">{n.display_name || n.ip}</span>
                          </label>
                        ))}
                      </div>
                    </div>

                    {/* Start button */}
                    <button
                      onClick={handlePullAllStart}
                      disabled={pullAllRunning || pullAllIps.size === 0}
                      className="flex items-center gap-1.5 text-xs bg-green/10 hover:bg-green/20 disabled:opacity-50 text-green border border-green/30 font-semibold px-4 py-1.5 rounded-lg transition-colors"
                    >
                      {pullAllRunning ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />}
                      Загрузить
                    </button>

                    {/* Terminal output */}
                    {pullAllLines.length > 0 && (
                      <div
                        ref={pullAllTermRef}
                        className="bg-base border border-surface1 rounded-xl p-3 h-40 overflow-y-auto text-[11px] font-mono space-y-0.5"
                      >
                        {pullAllLines.map((line, i) => (
                          <div key={i} className={line.includes("ОШИБКА") || line.includes("FAILED") ? "text-red" : line.includes("ok") ? "text-green" : "text-overlay0"}>
                            {line || " "}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Conflicts resolution */}
                    {Object.keys(pullAllConflicts).length > 0 && (
                      <div className="space-y-3">
                        <p className="text-[10px] font-semibold text-yellow uppercase tracking-wider flex items-center gap-1.5">
                          <AlertTriangle size={10} />
                          Конфликты ({Object.keys(pullAllConflicts).length})
                        </p>
                        <p className="text-[11px] text-subtext">
                          Для следующих модулей значения на нодах различаются. Выберите источник:
                        </p>
                        {Object.entries(pullAllConflicts).map(([mod, ips]) => (
                          <div key={mod} className="bg-surface0 border border-yellow/30 rounded-lg p-3 space-y-2">
                            <p className="text-xs font-mono text-yellow font-semibold">{mod}</p>
                            <div className="flex flex-wrap gap-1.5">
                              {(["first", "last", ...ips] as string[]).map((opt) => (
                                <label key={opt} className="flex items-center gap-1 cursor-pointer select-none">
                                  <input
                                    type="radio"
                                    name={`conflict-${mod}`}
                                    value={opt}
                                    checked={(conflictResolution[mod] ?? "first") === opt}
                                    onChange={() => setConflictResolution((prev) => ({ ...prev, [mod]: opt }))}
                                    className="accent-yellow w-3 h-3"
                                  />
                                  <span className="text-[10px] text-subtext font-mono">
                                    {opt === "first" ? "Первая нода" : opt === "last" ? "Последняя нода" : opt}
                                  </span>
                                </label>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Save button (shown after pull done) */}
                    {Object.keys(pullAllData).length > 0 && !pullAllRunning && (
                      <button
                        onClick={handlePullAllSave}
                        disabled={pullAllSaving}
                        className="flex items-center gap-1.5 text-xs bg-blue/10 hover:bg-blue/20 disabled:opacity-50 text-blue border border-blue/30 font-semibold px-4 py-1.5 rounded-lg transition-colors"
                      >
                        {pullAllSaving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
                        Сохранить в профиль ({Object.keys(pullAllData).length} модулей)
                      </button>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Pull drawer */}
            <AnimatePresence>
              {pullOpen && selected && (
                <motion.div
                  initial={{ x: 320, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  exit={{ x: 320, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                  className="absolute right-0 top-0 bottom-0 w-80 bg-mantle border-l border-surface1 p-5 z-10 flex flex-col shadow-xl"
                >
                  <div className="flex items-center justify-between mb-4 shrink-0">
                    <h3 className="text-sm font-semibold text-text">Загрузить с ноды</h3>
                    <button
                      onClick={() => { setPullOpen(false); setPullError(null); }}
                      className="text-overlay0 hover:text-text transition-colors"
                    >
                      <X size={14} />
                    </button>
                  </div>

                  <div className="space-y-4 flex-1 overflow-y-auto">
                    {/* Node selector */}
                    <div>
                      <label className="text-xs text-subtext block mb-1">Нода</label>
                      <select
                        value={pullIp}
                        onChange={(e) => setPullIp(e.target.value)}
                        className="w-full text-xs bg-surface0 border border-surface1 rounded-lg px-3 py-1.5 text-text focus:border-blue outline-none"
                      >
                        <option value="">— выберите —</option>
                        {nodes.map((n) => (
                          <option key={n.ip} value={n.ip}>
                            {n.display_name || n.hostname || n.ip} ({n.ip})
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Module checkboxes */}
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="flex items-center gap-1.5">
                          <label className="text-xs text-subtext">Модули для загрузки</label>
                          {pullIp && (
                            pullModsLoading
                              ? <Loader2 size={10} className="animate-spin text-overlay0" />
                              : <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded ${
                                  pullModsSource === "live"    ? "bg-green/10 text-green" :
                                  pullModsSource === "stored"  ? "bg-blue/10 text-blue" :
                                                                  "bg-surface1 text-overlay0"
                                }`}>
                                  {pullModsSource}
                                </span>
                          )}
                        </div>
                        {pullAvailMods.length > 0 && (
                          <div className="flex gap-2">
                            <button
                              onClick={() => setPullModules(pullAvailMods)}
                              className="text-[10px] text-blue hover:underline"
                            >все</button>
                            <button
                              onClick={() => setPullModules([])}
                              className="text-[10px] text-overlay0 hover:text-subtext hover:underline"
                            >нет</button>
                          </div>
                        )}
                      </div>
                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {pullModsLoading && !pullIp && (
                          <p className="text-[10px] text-overlay0 italic py-1">Выберите ноду</p>
                        )}
                        {pullModsLoading && pullIp && (
                          <p className="text-[10px] text-overlay0 italic py-1">Загрузка модулей…</p>
                        )}
                        {!pullModsLoading && pullAvailMods.map((m) => (
                          <label key={m} className="flex items-center gap-2 cursor-pointer select-none">
                            <input
                              type="checkbox"
                              checked={pullModules.includes(m)}
                              onChange={(e) => setPullModules((prev) =>
                                e.target.checked ? [...prev, m] : prev.filter((x) => x !== m),
                              )}
                              className="accent-blue w-3.5 h-3.5"
                            />
                            <span className="text-xs text-subtext font-mono">{m}</span>
                          </label>
                        ))}
                      </div>
                    </div>

                    {pullError && <ErrMsg msg={pullError} />}

                    <div className="text-[10px] text-overlay0 leading-relaxed">
                      CMD учётные данные: <span className="font-mono text-subtext">{creds.user}</span>
                      {creds.pass ? " ●" : " — пароль не задан"}
                    </div>
                  </div>

                  <button
                    onClick={handlePull}
                    disabled={pullLoading || !pullIp || pullModules.length === 0}
                    className="w-full flex items-center justify-center gap-2 text-xs bg-blue/90 hover:bg-blue disabled:opacity-50 text-mantle font-semibold py-2 rounded-lg transition-colors mt-4 shrink-0"
                  >
                    {pullLoading ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />}
                    Загрузить с ноды
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      )}

      {/* ── Matrix view ── */}
      {view === "matrix" && (
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden p-5 gap-4">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-overlay0 pt-6">
              <Loader2 size={12} className="animate-spin" /> Загрузка...
            </div>
          ) : profiles.length === 0 ? (
            <p className="text-sm text-overlay0 text-center pt-12">
              Нет профилей. Создайте профиль в режиме «Библиотека».
            </p>
          ) : (
            <>
              {/* Таблица: скролл по обеим осям, занимает всё доступное место */}
              <div className="flex-1 min-h-0 bg-surface0 border border-surface1 rounded-xl overflow-auto">

                {/* ── Режим А: Наличие модулей по профилям ── */}
                {matrixMode === "presence" && (
                  <table className="text-[11px] font-mono w-full">
                    <thead className="sticky top-0 z-10 bg-surface0">
                      <tr className="border-b border-surface1">
                        <th className="text-left text-overlay0 font-normal py-2.5 px-4 sticky left-0 z-20 bg-surface0 min-w-[160px]">
                          Модуль
                        </th>
                        {profiles.map((p) => (
                          <th key={p.slug} className="text-overlay0 font-normal py-2.5 px-3 text-center whitespace-nowrap">
                            <button
                              onClick={() => { setView("library"); openProfile(p.slug); }}
                              className="text-blue hover:underline"
                            >{p.name}</button>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {allModNames.map((mod) => {
                        const presences = profiles.map((p) => p.module_names.includes(mod));
                        const allHave   = presences.every(Boolean);
                        const noneHave  = presences.every((b) => !b);
                        return (
                          <tr key={mod} className={`border-b border-surface1/40 hover:bg-surface1/10 transition-colors ${
                            allHave ? "" : noneHave ? "opacity-50" : "bg-yellow/3"
                          }`}>
                            <td className="py-2 px-4 sticky left-0 bg-surface0 font-semibold text-text">
                              {mod}
                              {!allHave && !noneHave && (
                                <span className="ml-2 text-yellow text-[9px] align-middle">⚠</span>
                              )}
                            </td>
                            {presences.map((has, i) => (
                              <td key={profiles[i].slug} className="py-2 px-3 text-center">
                                {has
                                  ? <span className="text-green text-sm">✓</span>
                                  : <span className="text-overlay0/40 text-sm">—</span>
                                }
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}

                {/* ── Режим Б: Значения (текущий) ── */}
                {matrixMode === "values" && (
                <table className="text-[11px] font-mono">
                  <thead className="sticky top-0 z-10 bg-surface0">
                    <tr className="border-b border-surface1">
                      <th className="text-left text-overlay0 font-normal py-2.5 px-4 sticky left-0 z-20 bg-surface0 min-w-[160px]">
                        Профиль
                      </th>
                      {allModNames.map((m) => (
                        <th key={m} className="text-overlay0 font-normal py-2.5 px-2 text-center whitespace-nowrap">
                          {m}
                        </th>
                      ))}
                      <th className="text-overlay0 font-normal py-2.5 px-4 text-center whitespace-nowrap min-w-[120px] sticky right-0 bg-surface0">
                        Применить
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {profiles.map((p) => (
                      <tr key={p.slug} className="border-b border-surface1/40 hover:bg-surface1/10 transition-colors">
                        <td className="py-2.5 px-4 sticky left-0 bg-surface0 hover:bg-surface1/10">
                          <button
                            onClick={() => { setView("library"); openProfile(p.slug); }}
                            className="text-blue hover:underline text-left"
                          >
                            {p.name}
                          </button>
                        </td>
                        {allModNames.map((m) => {
                          const has     = p.module_names.includes(m);
                          const isPopup = matrixPopover?.slug === p.slug && matrixPopover?.mod === m;
                          return (
                            <td key={m} className="py-2.5 px-2 text-center relative">
                              {has ? (
                                <div className="relative inline-block">
                                  <button
                                    onClick={() => setMatrixPopover(isPopup ? null : { slug: p.slug, mod: m })}
                                    className="text-green hover:text-green/70 transition-colors"
                                    title={`${p.name} / ${m}`}
                                  >
                                    ●
                                  </button>
                                  {isPopup && (
                                    <div className="absolute z-20 left-1/2 -translate-x-1/2 bottom-full mb-2 w-52 bg-mantle border border-surface1 rounded-xl shadow-xl p-3 text-left">
                                      <p className="text-[10px] text-overlay0 font-semibold mb-1.5">{m}</p>
                                      {/* Show first 3 key:value pairs */}
                                      {Object.entries({} as Record<string, unknown>).slice(0, 0).map(([k, v]) => (
                                        <div key={k} className="flex gap-1 text-[10px]">
                                          <span className="text-mauve shrink-0">{k}:</span>
                                          <span className="text-subtext truncate">{String(v)}</span>
                                        </div>
                                      ))}
                                      <p className="text-[9px] text-overlay0 italic mt-1">
                                        Загрузите данные профиля для просмотра ключей
                                      </p>
                                      <button
                                        onClick={() => { setMatrixPopover(null); setView("library"); openProfile(p.slug); }}
                                        className="mt-2 text-[10px] text-blue hover:underline flex items-center gap-1"
                                      >
                                        → Открыть в Библиотеке
                                      </button>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="text-surface1">·</span>
                              )}
                            </td>
                          );
                        })}
                        <td className="py-2.5 px-4 text-center sticky right-0 bg-surface0 border-l border-surface1/30">
                          {matrixApplySlug === p.slug ? (
                            <div className="flex items-center gap-1 flex-wrap justify-center">
                              {backendNodes.length > 0 && (
                                <span className="text-[9px] text-overlay0 font-semibold mr-0.5">BE:</span>
                              )}
                              {backendNodes.map((n) => (
                                <label key={n.ip} className="flex items-center gap-1 cursor-pointer select-none">
                                  <input
                                    type="checkbox"
                                    checked={matrixApplyHosts.has(n.ip)}
                                    onChange={(e) => setMatrixApplyHosts((prev) => {
                                      const next = new Set(prev);
                                      if (e.target.checked) next.add(n.ip); else next.delete(n.ip);
                                      return next;
                                    })}
                                    className="accent-blue w-3 h-3"
                                  />
                                  <span className="text-[10px] text-subtext font-mono">
                                    {n.display_name || n.hostname || n.ip}
                                  </span>
                                </label>
                              ))}
                              {frontendNodes.length > 0 && (
                                <span className="text-[9px] text-overlay0 font-semibold ml-1 mr-0.5">FE:</span>
                              )}
                              {frontendNodes.map((n) => (
                                <label key={n.ip} className="flex items-center gap-1 cursor-pointer select-none">
                                  <input
                                    type="checkbox"
                                    checked={matrixApplyHosts.has(n.ip)}
                                    onChange={(e) => setMatrixApplyHosts((prev) => {
                                      const next = new Set(prev);
                                      if (e.target.checked) next.add(n.ip); else next.delete(n.ip);
                                      return next;
                                    })}
                                    className="accent-blue w-3 h-3"
                                  />
                                  <span className="text-[10px] text-subtext font-mono">
                                    {n.display_name || n.hostname || n.ip}
                                  </span>
                                </label>
                              ))}
                              <button
                                onClick={() => handleMatrixApply(p.slug)}
                                disabled={matrixApplyRunning || matrixApplyHosts.size === 0}
                                className="text-[10px] bg-green/10 hover:bg-green/20 disabled:opacity-50 text-green border border-green/30 px-2 py-0.5 rounded-lg transition-colors flex items-center gap-1"
                              >
                                {matrixApplyRunning ? <Loader2 size={9} className="animate-spin" /> : <Upload size={9} />}
                                Apply
                              </button>
                              <button
                                onClick={() => setMatrixApplySlug(null)}
                                className="text-overlay0 hover:text-text transition-colors"
                              >
                                <X size={11} />
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => { setMatrixApplySlug(p.slug); setMatrixApplyHosts(new Set()); setMatrixApplyLines([]); }}
                              className="text-[10px] text-subtext hover:text-green hover:bg-green/5 px-2 py-0.5 rounded-lg transition-colors"
                            >
                              → Apply
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                )} {/* end matrixMode === "values" */}
              </div>

              {/* Matrix apply terminal */}
              {matrixApplyLines.length > 0 && (
                <div className="shrink-0 bg-surface0 border border-surface1 rounded-xl overflow-hidden">
                  <div className="px-4 py-2 border-b border-surface1 flex items-center gap-2">
                    <Terminal size={11} className="text-overlay0" />
                    <span className={`w-2 h-2 rounded-full ${
                      matrixApplyRunning           ? "bg-green animate-pulse" :
                      matrixApplyExitOk === true  ? "bg-green"               :
                      matrixApplyExitOk === false ? "bg-red"                 :
                      "bg-overlay0"
                    }`} />
                    <span className="text-xs font-mono text-subtext">
                      {matrixApplyRunning ? "Применение..." :
                       matrixApplyExitOk === true ? "Завершено успешно" :
                       matrixApplyExitOk === false ? "Завершено с ошибкой" : "Вывод"}
                    </span>
                  </div>
                  <pre ref={matrixTermRef} className="font-mono text-[11px] h-48 overflow-y-auto p-4 leading-relaxed">
                    {matrixApplyLines.map((line, i) => (
                      <div key={i} className={ansibleLineColor(line)}>{line || " "}</div>
                    ))}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Profile → Playbook modal */}
      {toPlaybookSlug && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-surface0 border border-surface1 rounded-xl p-6 w-96 space-y-4 shadow-2xl">
            <h3 className="text-base font-semibold text-text">
              Сгенерировать плейбук из профиля
            </h3>
            <p className="text-xs text-subtext">Выберите целевые ноды:</p>
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {nodes.length === 0 ? (
                <p className="text-xs text-overlay0 italic">
                  Нет зарегистрированных нод. Добавьте ноды в Job Monitor.
                </p>
              ) : (
                nodes.map((n) => (
                  <label key={n.ip} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={toPlaybookHosts.includes(n.ip)}
                      onChange={e => setToPlaybookHosts(prev =>
                        e.target.checked ? [...prev, n.ip] : prev.filter(h => h !== n.ip)
                      )}
                      className="w-3.5 h-3.5 accent-blue"
                    />
                    <span className="font-mono text-xs text-text">{n.ip}</span>
                    {(n.display_name || n.hostname) && (
                      <span className="text-xs text-overlay0">{n.display_name || n.hostname}</span>
                    )}
                  </label>
                ))
              )}
            </div>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => profileToPlaybook(toPlaybookSlug)}
                disabled={toPlaybookLoading || toPlaybookHosts.length === 0}
                className="flex-1 py-2 bg-blue/90 hover:bg-blue text-base text-sm rounded-lg transition-colors disabled:opacity-50"
              >
                {toPlaybookLoading ? 'Генерация...' : 'Сгенерировать'}
              </button>
              <button
                onClick={() => { setToPlaybookSlug(null); setToPlaybookHosts([]); }}
                className="px-4 py-2 bg-surface1 text-subtext text-sm rounded-lg hover:bg-surface2 transition-colors"
              >
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Playbook meta (generated files) ──────────────────────────────────────────

interface PlaybookMeta {
  name: string;
  path: string;
  created_at: string;
  size_bytes: number;
  hosts: string[];
  mode: string;
  prefix: string;
}

// ── Tab: Playbooks ────────────────────────────────────────────────────────────

type PlaybookKey = "dump" | "apply";

const PLAYBOOK_META: Record<PlaybookKey, {
  icon: ReactNode; title: string; subtitle: string; description: string; color: string;
}> = {
  dump: {
    icon:        <Download size={16} />,
    title:       "Config Dump",
    subtitle:    "07-config-dump.yml",
    description: "Читает конфиги с нод через CMD → сохраняет в config-store/ → git commit",
    color:       "text-blue bg-blue/10 hover:bg-blue/20",
  },
  apply: {
    icon:        <Upload size={16} />,
    title:       "Config Apply",
    subtitle:    "08-config-apply.yml",
    description: "Применяет YAML из config-store/ к нодам через CMD",
    color:       "text-green bg-green/10 hover:bg-green/20",
  },
};

function PlaybooksTab({ node, creds }: { node: MonitorNodeInfo; creds: CmdCreds }) {
  const [running,        setRunning]        = useState<PlaybookKey | null>(null);
  const [lines,          setLines]          = useState<string[]>([]);
  const [exitOk,         setExitOk]         = useState<boolean | null>(null);
  const [includeObjects, setIncludeObjects] = useState(false);
  const termRef                             = useRef<HTMLPreElement>(null);
  const abortRef                            = useRef<AbortController | null>(null);

  // ── Generated playbooks list ────────────────────────────────────────────
  const [playbooks,        setPlaybooks]        = useState<PlaybookMeta[]>([]);
  const [pbLoading,        setPbLoading]        = useState(false);
  const [editPbName,       setEditPbName]       = useState<string | null>(null);
  const [editPbContent,    setEditPbContent]    = useState('');
  const [pbSaving,         setPbSaving]         = useState(false);
  const [pbRunName,        setPbRunName]        = useState<string | null>(null);
  const [pbRunLines,       setPbRunLines]       = useState<string[]>([]);
  const [pbRunning,        setPbRunning]        = useState(false);
  const [pbCmdUser,        setPbCmdUser]        = useState(creds.user);
  const [pbCmdPass,        setPbCmdPass]        = useState(creds.pass);
  const [toProfileLoading, setToProfileLoading] = useState<string | null>(null);

  const loadPlaybooks = async () => {
    setPbLoading(true);
    try {
      const r = await fetch('/api/config/playbooks/');
      if (r.ok) setPlaybooks(await r.json());
    } catch { /* ignore */ }
    finally { setPbLoading(false); }
  };

  const loadPlaybookContent = async (name: string) => {
    setEditPbName(name);
    const r = await fetch(`/api/config/playbooks/${encodeURIComponent(name)}`);
    const d = await r.json() as { content?: string };
    setEditPbContent(d.content ?? '');
  };

  const savePlaybookEdit = async () => {
    if (!editPbName) return;
    setPbSaving(true);
    try {
      const r = await fetch(`/api/config/playbooks/${encodeURIComponent(editPbName)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editPbContent }),
      });
      if (r.ok) {
        setEditPbName(null);
      }
    } catch { /* ignore — user can retry */ }
    finally { setPbSaving(false); }
  };

  const deletePlaybook = async (name: string) => {
    if (!confirm(`Удалить плейбук «${name}»?`)) return;
    const r = await fetch(`/api/config/playbooks/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (r.ok) {
      setPlaybooks(prev => prev.filter(p => p.name !== name));
    }
  };

  const runPlaybook = async (name: string) => {
    setPbRunName(name);
    setPbRunLines([]);
    setPbRunning(true);
    try {
      const r = await fetch(`/api/config/playbooks/${encodeURIComponent(name)}/run/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd_user: pbCmdUser, cmd_password: pbCmdPass }),
      });
      const reader = r.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) return;
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const chunks = buf.split('\n');
        buf = chunks.pop() ?? '';
        for (const chunk of chunks) {
          if (chunk.startsWith('data: ')) {
            const msg = chunk.slice(6);
            if (msg === '[DONE]') { setPbRunning(false); return; }
            if (msg) setPbRunLines(prev => [...prev.slice(-200), msg]);
          }
        }
      }
    } catch (e) {
      setPbRunLines(prev => [...prev, `ERROR: ${e}`]);
    } finally {
      setPbRunning(false);
    }
  };

  const playbookToProfile = async (name: string) => {
    setToProfileLoading(name);
    try {
      await fetch(`/api/config/playbooks/${encodeURIComponent(name)}/to-profile`, { method: 'POST' });
    } finally {
      setToProfileLoading(null);
    }
  };

  useEffect(() => {
    loadPlaybooks();
  }, []);

  useEffect(() => {
    if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight;
  }, [lines]);

  // Abort on unmount
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  const nodeLabel = node.display_name || node.hostname || node.ip;

  const run = (key: PlaybookKey) => {
    if (running) return;
    abortRef.current?.abort();

    setRunning(key);
    setExitOk(null);
    setLines([`[${new Date().toLocaleTimeString("ru")}] Запуск ${PLAYBOOK_META[key].subtitle} → ${nodeLabel} (${node.ip})...`, ""]);

    const onLine = (line: string) => setLines((prev) => [...prev, line]);
    const onDone = (ok: boolean) => {
      setRunning(null);
      setExitOk(ok);
      // Refresh generated playbooks list after dump completes
      if (key === "dump") {
        loadPlaybooks();
      }
    };

    const hosts = [node.ip];
    if (key === "dump") {
      abortRef.current = configApi.ansible.streamDumpV2(hosts, includeObjects, undefined, creds.user, creds.pass, onLine, onDone);
    } else {
      // v1: применяет сохранённый YAML из config-store/ напрямую (08-config-apply.yml)
      // v2 (DiffTab): генерирует плейбук из live-diff и применяет — другой flow
      abortRef.current = configApi.ansible.streamApply(hosts, onLine, onDone);
    }
  };

  return (
    <div className="p-5 space-y-4">
      <div className="grid grid-cols-2 gap-3">
        {(["dump", "apply"] as PlaybookKey[]).map((key) => {
          const meta      = PLAYBOOK_META[key];
          const isRunning = running === key;
          const colorParts = meta.color.split(" ");
          return (
            <div key={key} className="bg-surface0 border border-surface1 rounded-xl p-4 space-y-3">
              <div className="flex items-center gap-2">
                <span className={`p-1.5 rounded-lg ${colorParts.slice(1).join(" ")}`}>
                  <span className={colorParts[0]}>{meta.icon}</span>
                </span>
                <div>
                  <p className="text-sm font-medium text-text">{meta.title}</p>
                  <p className="text-[10px] font-mono text-overlay0">{meta.subtitle}</p>
                </div>
              </div>
              <p className="text-xs text-subtext leading-relaxed">{meta.description}</p>

              {/* Objects toggle — only for dump */}
              {key === "dump" && (
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={includeObjects}
                    onChange={(e) => setIncludeObjects(e.target.checked)}
                    className="accent-blue w-3.5 h-3.5"
                  />
                  <span className="text-xs text-subtext">Включить объекты доменов (--include-objects)</span>
                </label>
              )}

              <button
                onClick={() => run(key)}
                disabled={!!running}
                className={`w-full flex items-center justify-center gap-2 text-xs font-semibold py-2 rounded-lg transition-colors disabled:opacity-40 ${meta.color}`}
              >
                {isRunning
                  ? <><Loader2 size={12} className="animate-spin" /> Выполняется...</>
                  : <><Play size={12} /> Запустить</>}
              </button>
            </div>
          );
        })}
      </div>

      {lines.length > 0 && (
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
          <div className="px-4 py-2 border-b border-surface1 flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${
              running         ? "bg-green animate-pulse" :
              exitOk === true ? "bg-green"               :
              exitOk === false ? "bg-red"                :
              "bg-overlay0"
            }`} />
            <span className="text-xs font-mono text-subtext">
              {running
                ? "Playbook выполняется..."
                : exitOk === true
                  ? "Playbook завершён успешно"
                  : exitOk === false
                    ? "Playbook завершён с ошибкой"
                    : "Вывод"}
            </span>
          </div>
          <pre
            ref={termRef}
            className="font-mono text-[11px] h-72 overflow-y-auto p-4 leading-relaxed"
          >
            {lines.map((line, i) => (
              <div key={i} className={ansibleLineColor(line)}>{line || " "}</div>
            ))}
          </pre>
        </div>
      )}

      {/* ── Generated playbooks list ────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-text">Сгенерированные плейбуки</h2>
            <p className="text-xs text-overlay0 mt-0.5">Ansible-плейбуки, созданные операциями Config Dump / Apply</p>
          </div>
          <button
            onClick={loadPlaybooks}
            disabled={pbLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-surface0 hover:bg-surface1 border border-surface1 text-subtext text-sm rounded-lg transition-colors disabled:opacity-50"
          >
            <RefreshCw size={13} className={pbLoading ? 'animate-spin' : ''} />
            Обновить
          </button>
        </div>

        {pbLoading && (
          <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
            <p className="text-subtext text-sm">Загрузка...</p>
          </div>
        )}
        {!pbLoading && playbooks.length === 0 && (
          <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
            <p className="text-subtext text-sm">Плейбуков нет</p>
            <p className="text-overlay0 text-xs mt-1">Запустите Config Dump — плейбуки появятся здесь</p>
          </div>
        )}
        {playbooks.length > 0 && (
          <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
            {playbooks.map((pb, i) => (
              <div key={pb.name} className={i < playbooks.length - 1 ? 'border-b border-surface1/50' : ''}>
                {/* Row */}
                <div className="flex items-center justify-between px-4 py-3 hover:bg-surface1/30 transition-colors">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className="font-mono text-xs text-blue truncate max-w-xs" title={pb.name}>{pb.name}</span>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {pb.hosts.map(h => (
                        <span key={h} className="px-1.5 py-0.5 bg-blue/10 text-blue text-[10px] rounded font-mono">{h}</span>
                      ))}
                      <span className="px-1.5 py-0.5 bg-surface2 text-overlay0 text-[10px] rounded font-mono">{pb.mode}</span>
                    </div>
                    <span className="text-xs text-overlay0 whitespace-nowrap shrink-0">
                      {new Date(pb.created_at).toLocaleString('ru')}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 ml-4 shrink-0">
                    <button
                      onClick={() => { setPbRunName(pb.name); setPbRunLines([]); }}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-green hover:bg-green/10 rounded transition-colors"
                      title="Запустить"
                    >
                      ▶ Run
                    </button>
                    <button
                      onClick={() => loadPlaybookContent(pb.name)}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-blue hover:bg-blue/10 rounded transition-colors"
                      title="Редактировать"
                    >
                      ✎ Edit
                    </button>
                    <button
                      onClick={() => playbookToProfile(pb.name)}
                      disabled={toProfileLoading === pb.name}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-mauve hover:bg-mauve/10 rounded transition-colors disabled:opacity-50"
                      title="Конвертировать в профиль"
                    >
                      {toProfileLoading === pb.name ? '...' : '→ Profile'}
                    </button>
                    <button
                      onClick={() => deletePlaybook(pb.name)}
                      className="flex items-center gap-1 px-2 py-1 text-xs text-red hover:bg-red/10 rounded transition-colors"
                      title="Удалить"
                    >
                      ✕
                    </button>
                  </div>
                </div>

                {/* YAML Editor (inline, shown when this pb is being edited) */}
                {editPbName === pb.name && (
                  <div className="px-4 pb-4 border-t border-surface1/50 bg-mantle space-y-3 pt-3">
                    <textarea
                      value={editPbContent}
                      onChange={e => setEditPbContent(e.target.value)}
                      rows={20}
                      className="w-full bg-crust border border-surface1 rounded-lg px-3 py-2 text-xs font-mono text-text focus:border-blue outline-none resize-y"
                      spellCheck={false}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={savePlaybookEdit}
                        disabled={pbSaving}
                        className="px-3 py-1.5 bg-blue text-base text-sm rounded-lg hover:bg-blue/80 transition-colors disabled:opacity-50"
                      >
                        {pbSaving ? 'Сохранение...' : 'Сохранить'}
                      </button>
                      <button
                        onClick={() => setEditPbName(null)}
                        className="px-3 py-1.5 bg-surface1 text-subtext text-sm rounded-lg hover:bg-surface2 transition-colors"
                      >
                        Отмена
                      </button>
                    </div>
                  </div>
                )}

                {/* Run panel (inline, shown when this pb is selected for run) */}
                {pbRunName === pb.name && (
                  <div className="px-4 pb-4 border-t border-surface1/50 bg-mantle space-y-3 pt-3">
                    <div className="flex items-end gap-3 flex-wrap">
                      <div className="space-y-1">
                        <label className="text-xs text-subtext block">CMD User</label>
                        <input
                          type="text" value={pbCmdUser} onChange={e => setPbCmdUser(e.target.value)}
                          placeholder="admin"
                          className="bg-surface1 border border-surface2 rounded-lg px-3 py-1.5 text-sm text-text focus:border-blue outline-none w-32"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs text-subtext block">Password</label>
                        <input
                          type="password" value={pbCmdPass} onChange={e => setPbCmdPass(e.target.value)}
                          placeholder="••••••••"
                          className="bg-surface1 border border-surface2 rounded-lg px-3 py-1.5 text-sm text-text focus:border-blue outline-none w-32"
                        />
                      </div>
                      <button
                        onClick={() => runPlaybook(pb.name)}
                        disabled={pbRunning}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-green/90 hover:bg-green text-base text-sm rounded-lg transition-colors disabled:opacity-50"
                      >
                        {pbRunning ? <><Loader2 size={11} className="animate-spin" /> Running...</> : '▶ Запустить'}
                      </button>
                      <button
                        onClick={() => { setPbRunName(null); setPbRunLines([]); }}
                        className="px-3 py-1.5 bg-surface1 text-subtext text-sm rounded-lg hover:bg-surface2 transition-colors"
                      >
                        Закрыть
                      </button>
                    </div>
                    {pbRunLines.length > 0 && (
                      <div className="bg-crust rounded-lg p-3 max-h-64 overflow-y-auto">
                        {pbRunLines.map((line, idx) => (
                          <div key={idx} className={`font-mono text-xs ${line.startsWith('ERROR') ? 'text-red' : 'text-subtext'}`}>
                            {line}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── CMD Reference Tab — per-node ──────────────────────────────────────────────

function groupCmdBySection(cmds: CMDMethodDoc[]): Record<string, CMDMethodDoc[]> {
  return cmds.reduce<Record<string, CMDMethodDoc[]>>((acc, cmd) => {
    const s = cmd.section || "Прочие";
    if (!acc[s]) acc[s] = [];
    acc[s].push(cmd);
    return acc;
  }, {});
}

type AvailFilter = "all" | "available" | "unavailable";

function CmdReferenceTab({ node }: { node: MonitorNodeInfo | null }) {
  const [docs,         setDocs]         = useState<CMDMethodDoc[]>([]);
  const [nodeCommands, setNodeCommands] = useState<Map<string, boolean>>(new Map());
  const [fetchedAt,    setFetchedAt]    = useState<string | null>(null);
  const [discovering,  setDiscovering]  = useState(false);
  const [docCount,     setDocCount]     = useState(0);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState<string | null>(null);
  const [search,       setSearch]       = useState("");
  const [selected,     setSelected]     = useState<CMDMethodDoc | null>(null);
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());
  const [filter,       setFilter]       = useState<AvailFilter>("all");

  // Layer 1 — один раз при mount
  useEffect(() => {
    setLoading(true);
    fetch("/api/monitor/cmd-reference")
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<CMDMethodDoc[]>; })
      .then((data) => {
        setDocs(data);
        const sections = [...new Set(data.map((c) => c.section || "Прочие"))];
        setOpenSections(new Set(sections));
        if (data.length > 0) setSelected(data[0]);
      })
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Layer 2 — при смене ноды
  useEffect(() => {
    if (!node) return;
    fetch(`/api/monitor/nodes/${node.id}/commands`)
      .then((r) => r.status === 204 ? null : r.json() as Promise<{ commands: EnrichedCommand[]; count: number; fetched_at: string }>)
      .then((data) => {
        if (data?.commands) {
          const map = new Map<string, boolean>();
          data.commands.forEach((c) => map.set(c.name.toLowerCase(), c.available));
          setNodeCommands(map);
          setFetchedAt(data.fetched_at);
          setDocCount(data.count);
        } else {
          setNodeCommands(new Map());
          setFetchedAt(null);
          setDocCount(0);
        }
      })
      .catch(() => {});
  }, [node?.id]);

  const handleDiscover = async () => {
    if (!node) return;
    setDiscovering(true);
    try {
      const r = await fetch(`/api/monitor/nodes/${node.id}/discover-commands`, { method: "POST" });
      const data = await r.json() as { commands: EnrichedCommand[]; count: number; fetched_at: string };
      const map = new Map<string, boolean>();
      data.commands.forEach((c) => map.set(c.name.toLowerCase(), c.available));
      setNodeCommands(map);
      setFetchedAt(data.fetched_at);
      setDocCount(data.count);
    } finally {
      setDiscovering(false);
    }
  };

  const filtered = useMemo(() => {
    let cmds = docs;
    if (search.trim()) {
      const q = search.toLowerCase();
      cmds = cmds.filter(
        (c) => c.name.toLowerCase().includes(q) ||
               c.syntax.toLowerCase().includes(q) ||
               (c.description?.toLowerCase().includes(q) ?? false),
      );
    }
    if (filter === "available" && nodeCommands.size > 0) {
      cmds = cmds.filter((c) => nodeCommands.get(c.name.toLowerCase()) === true);
    } else if (filter === "unavailable" && nodeCommands.size > 0) {
      cmds = cmds.filter((c) => nodeCommands.get(c.name.toLowerCase()) !== true);
    }
    return cmds;
  }, [docs, search, filter, nodeCommands]);

  const grouped  = useMemo(() => groupCmdBySection(filtered), [filtered]);
  const sections = Object.keys(grouped).sort();

  function toggleSection(s: string) {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s); else next.add(s);
      return next;
    });
  }

  const nodeLabel = node ? (node.display_name || node.hostname || node.ip) : null;
  const showEmptyDiscover = nodeCommands.size === 0 && !fetchedAt && !!node;

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left panel */}
      <div className="w-52 shrink-0 border-r border-surface1 flex flex-col bg-mantle/30">
        <div className="p-3 border-b border-surface1 space-y-2">
          <div className="flex items-center gap-2 bg-surface0 rounded-lg px-2.5 py-1.5 ring-1 ring-transparent focus-within:ring-blue/30 transition-all">
            <Search size={11} className="text-overlay0 shrink-0" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск..."
              className="flex-1 text-xs bg-transparent outline-none text-text placeholder-overlay0"
            />
          </div>
          {/* Filter toggles */}
          <div className="flex gap-1">
            {(["all", "available", "unavailable"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`text-[9px] px-2 py-0.5 rounded font-mono transition-colors ${
                  filter === f ? "bg-blue/20 text-blue" : "bg-surface0 text-overlay0 hover:text-text"
                }`}
              >
                {f === "all" ? "Все" : f === "available" ? "● Доступные" : "○ Прочие"}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-1">
          {loading && <p className="px-4 py-3 text-[10px] text-overlay0 italic">Загрузка справочника...</p>}
          {error   && <p className="px-4 py-3 text-[10px] text-red">{error}</p>}
          {!loading && sections.map((section) => {
            const cmds   = grouped[section];
            const isOpen = openSections.has(section);
            return (
              <div key={section}>
                <button
                  onClick={() => toggleSection(section)}
                  className="w-full flex items-center gap-1.5 px-3 py-1.5 hover:bg-surface1/40 transition-colors"
                >
                  {isOpen
                    ? <ChevronDown  size={10} className="text-overlay0 shrink-0" />
                    : <ChevronRight size={10} className="text-overlay0 shrink-0" />}
                  <span className="text-[10px] font-semibold text-overlay0 uppercase tracking-wider flex-1 text-left">
                    {section}
                  </span>
                  <span className="text-[9px] bg-surface0 text-overlay0 px-1.5 py-0.5 rounded font-mono">
                    {cmds.length}
                  </span>
                </button>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="overflow-hidden"
                    >
                      {cmds.map((cmd) => {
                        const isSel = selected?.name === cmd.name;
                        const avail = nodeCommands.size > 0
                          ? nodeCommands.get(cmd.name.toLowerCase()) ?? false
                          : null;
                        return (
                          <button
                            key={cmd.name}
                            onClick={() => setSelected(cmd)}
                            className={`w-full flex items-center gap-2 pl-6 pr-3 py-1.5 text-left text-xs transition-colors ${
                              isSel
                                ? "bg-blue/10 text-blue"
                                : "text-subtext hover:text-text hover:bg-surface1/30"
                            }`}
                          >
                            {avail === true  && <span className="w-1.5 h-1.5 rounded-full bg-green/80 shrink-0" />}
                            {avail === false && <span className="w-1.5 h-1.5 rounded-full bg-overlay0/40 shrink-0" />}
                            {avail === null  && <span className="w-1.5 h-1.5 shrink-0" />}
                            <span className="font-mono truncate">{cmd.name}</span>
                          </button>
                        );
                      })}
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>

        {docs.length > 0 && (
          <div className="border-t border-surface1 px-3 py-2">
            <span className="text-[9px] text-overlay0 font-mono">
              {docs.length} команд задокументировано
            </span>
          </div>
        )}
      </div>

      {/* Right panel */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="border-b border-surface1 px-4 py-2.5 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <Terminal size={13} className="text-overlay0" />
            <span className="text-xs text-subtext">CMD Справочник</span>
            {nodeLabel && (
              <span className="text-[10px] font-mono text-overlay0">
                · {nodeLabel}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {fetchedAt && (
              <span className="text-[10px] text-overlay0 font-mono">
                ● {docCount} команд · {new Date(fetchedAt).toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
            {node && (
              <button
                onClick={handleDiscover}
                disabled={discovering}
                className="flex items-center gap-1 text-[10px] bg-surface1 hover:bg-surface2 text-subtext px-2.5 py-1 rounded-lg transition-colors disabled:opacity-50"
              >
                {discovering
                  ? <Loader2 size={10} className="animate-spin" />
                  : <RefreshCw size={10} />}
                {fetchedAt ? "Обновить" : "Обнаружить"}
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {/* Empty discover state (на ноде не было discover) */}
          {showEmptyDiscover && (
            <div className="flex flex-col items-center gap-3 pt-16 text-center px-4">
              <Terminal size={28} className="text-overlay0" />
              <p className="text-sm text-subtext">CMD-команды не обнаружены для этой ноды</p>
              <p className="text-xs text-overlay0">Нажмите «Обнаружить» чтобы опросить ноду через HELP</p>
              <button
                onClick={handleDiscover}
                disabled={discovering}
                className="mt-2 flex items-center gap-1.5 text-xs bg-blue/90 hover:bg-blue text-mantle font-semibold px-4 py-1.5 rounded-lg transition-colors disabled:opacity-50"
              >
                {discovering ? <Loader2 size={11} className="animate-spin" /> : <Terminal size={11} />}
                Обнаружить команды
              </button>
            </div>
          )}

          {/* Selected command detail */}
          {selected && (
            <div className="p-6 space-y-5 max-w-2xl">
              <div>
                <h2 className="text-base font-semibold text-text font-mono mb-1.5">{selected.name}</h2>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[10px] bg-surface0 text-overlay0 px-2 py-0.5 rounded font-mono">
                    {selected.section}
                  </span>
                  {nodeCommands.size > 0 && (
                    <span className={`text-[10px] px-2 py-0.5 rounded font-mono ${
                      nodeCommands.get(selected.name.toLowerCase()) === true
                        ? "bg-green/10 text-green"
                        : "bg-overlay0/10 text-overlay0"
                    }`}>
                      {nodeCommands.get(selected.name.toLowerCase()) === true
                        ? "● доступна на ноде"
                        : "○ не обнаружена"}
                    </span>
                  )}
                </div>
              </div>

              <div>
                <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">Синтаксис</p>
                <code className="block font-mono bg-surface1 px-4 py-3 rounded-lg text-sm text-peach leading-relaxed whitespace-pre-wrap">
                  {selected.syntax}
                </code>
              </div>

              <div>
                <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">Описание</p>
                {selected.description ? (
                  <p className="text-sm text-subtext leading-relaxed whitespace-pre-wrap">
                    {selected.description}
                  </p>
                ) : (
                  <p className="text-sm text-overlay0 italic">Описание отсутствует</p>
                )}
              </div>
            </div>
          )}

          {!selected && !loading && (
            <p className="text-sm text-overlay0 text-center pt-12">Выберите команду</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const TABS: { key: Tab; label: string; icon: ReactNode }[] = [
  { key: "editor",    label: "Редактор",  icon: <FileText      size={13} /> },
  { key: "diff",      label: "Diff",      icon: <AlertTriangle size={13} /> },
  { key: "history",   label: "История",   icon: <GitBranch     size={13} /> },
  { key: "playbooks", label: "Playbooks", icon: <Play          size={13} /> },
  { key: "profiles",  label: "Профили",   icon: <Layers        size={13} /> },
  { key: "cmdref",    label: "CMD Справ.", icon: <BookOpen      size={13} /> },
];

export function ConfigManagement() {
  const storeCmdUser = useDeployStore((s) => s.cmdUser);
  const storeCmdPass = useDeployStore((s) => s.cmdPassword);

  const [tab,          setTab]          = useState<Tab>("editor");
  const [node,         setNode]         = useState<MonitorNodeInfo | null>(null);
  const [nodes,        setNodes]        = useState<MonitorNodeInfo[]>([]);
  const [nodesLoading, setNodesLoading] = useState(true);
  const [cmdUser,      setCmdUser]      = useState(storeCmdUser || "admin");
  const [cmdPass,      setCmdPass]      = useState(storeCmdPass || "");

  // Загрузка нод из API
  useEffect(() => {
    fetch("/api/monitor/nodes")
      .then((r) => r.json() as Promise<MonitorNodeInfo[]>)
      .then((all) => {
        const ivamail = all.filter(
          (n) => n.node_type === "ivamail_backend" || n.node_type === "ivamail_frontend",
        );
        setNodes(ivamail);
        if (ivamail.length > 0) setNode(ivamail[0]);
      })
      .catch(() => {})
      .finally(() => setNodesLoading(false));
  }, []);

  // При смене ноды — подставить cmd_user из неё
  const handleNodeSelect = (n: MonitorNodeInfo) => {
    setNode(n);
    if (n.cmd_user) setCmdUser(n.cmd_user);
  };

  const creds: CmdCreds = { user: cmdUser, pass: cmdPass };

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      {/* Page header */}
      <div className="px-6 pt-6 pb-4 shrink-0">
        <h1 className="text-xl font-semibold text-text">Config Management</h1>
        <p className="text-xs text-subtext mt-0.5">
          Просмотр, редактирование и применение конфигураций IVA Mail
        </p>
      </div>

      {/* Control bars */}
      <div className="px-6 shrink-0 flex items-center gap-3">
        <NodeBar
          nodes={nodes}
          selected={node}
          loading={nodesLoading}
          onSelect={handleNodeSelect}
        />
        <CredsBar
          user={cmdUser}
          pass={cmdPass}
          node={node}
          onChange={(u, p) => { setCmdUser(u); setCmdPass(p); }}
        />
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-1 px-6 pt-3 pb-0 shrink-0 border-b border-surface1">
        {TABS.map((t) => (
          <TabButton key={t.key} active={tab === t.key} onClick={() => setTab(t.key)}>
            <span className="flex items-center gap-1.5">
              {t.icon}
              {t.label}
            </span>
          </TabButton>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden">
        {nodesLoading ? (
          <div className="flex items-center justify-center h-full gap-2 text-overlay0 text-sm">
            <Loader2 size={16} className="animate-spin" />
            Загрузка нод...
          </div>
        ) : tab === "profiles" ? (
          <AnimatePresence mode="wait">
            <motion.div
              key="profiles"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              className="h-full flex flex-col"
            >
              <ProfilesTab nodes={nodes} creds={creds} onSwitchTab={setTab} />
            </motion.div>
          </AnimatePresence>
        ) : !node ? (
          <div className="flex items-center justify-center h-full text-overlay0 text-sm">
            Нет доступных IVA Mail нод
          </div>
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={tab}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.15 }}
              className="h-full flex flex-col"
            >
              {tab === "editor"    && <EditorTab    node={node} creds={creds} />}
              {tab === "diff"      && <DiffTab      node={node} creds={creds} />}
              {tab === "history"   && <HistoryTab node={node} creds={creds}      />}
              {tab === "playbooks" && <PlaybooksTab node={node} creds={creds} />}
              {tab === "cmdref"    && <CmdReferenceTab node={node}            />}
            </motion.div>
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
