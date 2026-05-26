import { useState, useEffect, useRef, useCallback, useMemo, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronRight, ChevronDown, Server, Globe, Users,
  Download, Upload, GitBranch, Play, RotateCcw,
  CheckCircle2, AlertTriangle, Plus, Minus,
  Save, RefreshCw, Clock, FileText, Loader2,
  KeyRound, Eye, EyeOff, AlertCircle, BookOpen, Search, Terminal,
} from "lucide-react";
import { useDeployStore } from "@/stores/deployStore";
import { configApi, type CmdCreds, type ConfigData, type DiffEntry, type GitCommit } from "@/api/configApi";

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

type Tab = "editor" | "diff" | "history" | "playbooks" | "cmdref";

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
            onChange={(e) => setLocalU(e.target.value)}
            placeholder="user"
            className="text-xs font-mono bg-surface1 rounded px-2 py-0.5 w-24 outline-none focus:ring-1 focus:ring-blue/50 text-text"
          />
          <div className="relative flex items-center gap-1">
            <div className="relative">
              <input
                type={showPw ? "text" : "password"}
                value={localP}
                onChange={(e) => setLocalP(e.target.value)}
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
          const displayVal = Array.isArray(val) ? (val as string[]).join(", ") : String(val ?? "");
          return (
            <tr
              key={key}
              className={`border-b border-surface1/40 hover:bg-surface1/20 transition-colors ${
                isEdited ? "bg-yellow/5" : ""
              }`}
            >
              <td className="py-1.5 pr-4 text-mauve">{key}</td>
              <td className="py-1.5">
                {onEdit ? (
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

// ── Tab: Diff ─────────────────────────────────────────────────────────────────

const diffKindMeta = {
  changed: { icon: <AlertTriangle size={10} />, cls: "text-yellow", bg: "bg-yellow/5" },
  added:   { icon: <Plus  size={10} />,         cls: "text-green",  bg: "bg-green/5"  },
  removed: { icon: <Minus size={10} />,         cls: "text-red",    bg: "bg-red/5"    },
} as const;

// Статический список для DiffTab (fallback если live-модули недоступны)
const FALLBACK_MODULES = ["Cluster", "CMD", "SMTP", "IMAP", "POP3", "WebAccess", "AntiSpam", "Antivirus"];

function DiffTab({ node, creds }: { node: MonitorNodeInfo; creds: CmdCreds }) {
  const [module,   setModule]   = useState("Cluster");
  const [entries,  setEntries]  = useState<DiffEntry[]>([]);
  const [loading,  setLoading]  = useState(false);
  const [compared, setCompared] = useState(false);
  const [error,    setError]    = useState<string | null>(null);
  const [isIdentical, setIsIdentical] = useState(false);
  const [availableModules, setAvailableModules] = useState<string[]>(FALLBACK_MODULES);

  // Apply stream state
  const [applyRunning, setApplyRunning] = useState(false);
  const [applyLines,   setApplyLines]   = useState<string[]>([]);
  const [applyExitOk,  setApplyExitOk]  = useState<boolean | null>(null);
  const applyTermRef                    = useRef<HTMLPreElement>(null);
  const applyAbortRef                   = useRef<AbortController | null>(null);

  // Загрузить модули из config-store для текущей ноды
  useEffect(() => {
    configApi.stored.listModules(node.ip)
      .then((mods) => { if (mods.length > 0) setAvailableModules(mods); })
      .catch(() => {});
  }, [node.ip]);

  useEffect(() => {
    if (applyTermRef.current) applyTermRef.current.scrollTop = applyTermRef.current.scrollHeight;
  }, [applyLines]);

  useEffect(() => () => { applyAbortRef.current?.abort(); }, []);

  const compare = async () => {
    if (!creds.pass.trim()) { setError("Введите CMD пароль для сравнения с live"); return; }
    setLoading(true);
    setError(null);
    setCompared(false);
    try {
      const result = await configApi.diff.module(node.ip, module, creds);
      setEntries(result);
      setIsIdentical(result.length === 0);
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
    applyAbortRef.current = configApi.ansible.streamApplyV2(
      [node.ip],
      "full",
      false,
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

  const lineColor = (line: string | undefined): string => {
    if (!line) return "text-overlay1";
    if (line.includes("PLAY RECAP"))   return "text-mauve font-semibold";
    if (line.includes("PLAY ["))       return "text-blue";
    if (line.includes("TASK ["))       return "text-subtext";
    if (line.startsWith("ok:"))        return "text-green";
    if (line.startsWith("changed:"))   return "text-yellow";
    if (line.startsWith("failed:"))    return "text-red";
    if (line.startsWith("fatal:"))     return "text-red";
    if (line.includes("[EXIT 0]"))     return "text-green font-semibold";
    if (line.includes("[EXIT"))        return "text-red font-semibold";
    return "text-overlay1";
  };

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div>
          <label className="text-xs text-subtext block mb-1">Модуль</label>
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

      {compared && entries.length > 0 && (
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
          <div className="grid grid-cols-3 text-[10px] text-overlay0 font-semibold uppercase tracking-wider px-4 py-2 border-b border-surface1 bg-surface1/30">
            <span>Ключ</span>
            <span>Сохранённое</span>
            <span>Live ({node.ip})</span>
          </div>
          <div className="divide-y divide-surface1/30">
            {entries.map((e) => {
              const meta = diffKindMeta[e.kind];
              return (
                <div key={e.key} className={`grid grid-cols-3 gap-2 px-4 py-2 text-xs font-mono ${meta.bg}`}>
                  <div className={`flex items-center gap-1.5 ${meta.cls}`}>
                    {meta.icon}
                    <span className="text-text">{e.key}</span>
                  </div>
                  <span className={e.kind === "removed" ? "text-red line-through opacity-60" : "text-subtext"}>
                    {e.stored ?? <span className="text-overlay0 italic">—</span>}
                  </span>
                  <span className={
                    e.kind === "added"   ? "text-green"  :
                    e.kind === "changed" ? "text-yellow" : "text-subtext"
                  }>
                    {e.live ?? <span className="text-overlay0 italic">—</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

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
              <div key={i} className={lineColor(line)}>{line || " "}</div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Tab: История ──────────────────────────────────────────────────────────────

function HistoryTab({ node }: { node: MonitorNodeInfo }) {
  const [commits,        setCommits]        = useState<GitCommit[]>([]);
  const [selectedCommit, setSelectedCommit] = useState<GitCommit | null>(null);
  const [diff,           setDiff]           = useState<string | null>(null);
  const [loadingLog,     setLoadingLog]     = useState(true);
  const [loadingDiff,    setLoadingDiff]    = useState(false);
  const [rollingBack,    setRollingBack]    = useState(false);
  const [rolledBack,     setRolledBack]     = useState<string | null>(null);
  const [error,          setError]          = useState<string | null>(null);

  // Tags
  const [tags,        setTags]        = useState<string[]>([]);
  const [loadingTags, setLoadingTags] = useState(true);

  // Tag rollback stream
  const [tagRbRunning,  setTagRbRunning]  = useState<string | null>(null); // current tag being rolled back
  const [tagRbLines,    setTagRbLines]    = useState<string[]>([]);
  const [tagRbExitOk,   setTagRbExitOk]  = useState<boolean | null>(null);
  const tagRbTermRef                      = useRef<HTMLPreElement>(null);
  const tagRbAbortRef                     = useRef<AbortController | null>(null);

  useEffect(() => {
    setLoadingLog(true);
    configApi.git.log(50)
      .then(setCommits)
      .catch((e) => setError(String(e)))
      .finally(() => setLoadingLog(false));

    setLoadingTags(true);
    configApi.git.tags()
      .then(setTags)
      .catch(() => {})
      .finally(() => setLoadingTags(false));
  }, []);

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
    setTagRbExitOk(null);
    const modeLabel = mode === "yaml_only" ? "YAML only" : "YAML + Apply";
    setTagRbLines([`[${new Date().toLocaleTimeString("ru")}] Откат к тегу ${tag} (${modeLabel}) → ${node.ip}...`, ""]);
    tagRbAbortRef.current = configApi.ansible.streamRollback(
      [node.ip],
      tag,
      mode,
      (line) => setTagRbLines((prev) => [...prev, line]),
      (ok)   => { setTagRbRunning(null); setTagRbExitOk(ok); },
    );
  };

  const lineColor = (line: string | undefined): string => {
    if (!line) return "text-overlay1";
    if (line.includes("PLAY RECAP"))   return "text-mauve font-semibold";
    if (line.includes("PLAY ["))       return "text-blue";
    if (line.includes("TASK ["))       return "text-subtext";
    if (line.startsWith("ok:"))        return "text-green";
    if (line.startsWith("changed:"))   return "text-yellow";
    if (line.startsWith("failed:"))    return "text-red";
    if (line.startsWith("fatal:"))     return "text-red";
    if (line.includes("[EXIT 0]"))     return "text-green font-semibold";
    if (line.includes("[EXIT"))        return "text-red font-semibold";
    return "text-overlay1";
  };

  return (
    <div className="p-5 space-y-4">
      {error && <ErrMsg msg={error} />}

      {/* Git Tags — Config Snapshots */}
      <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
        <div className="px-4 py-2.5 border-b border-surface1 flex items-center gap-2">
          <RotateCcw size={13} className="text-overlay0" />
          <span className="text-xs font-medium text-subtext">Снимки конфигурации (git tags)</span>
          {loadingTags && <Loader2 size={11} className="animate-spin text-overlay0 ml-auto" />}
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
                {tagRbRunning ? `Откат ${tagRbRunning}...` :
                 tagRbExitOk === true ? "Откат завершён успешно" :
                 tagRbExitOk === false ? "Откат завершён с ошибкой" : "Вывод"}
              </span>
            </div>
            <pre ref={tagRbTermRef} className="font-mono text-[11px] h-44 overflow-y-auto p-4 leading-relaxed bg-mantle/50">
              {tagRbLines.map((line, i) => (
                <div key={i} className={lineColor(line)}>{line || " "}</div>
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

          <div className="divide-y divide-surface1/30">
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
              <pre className="text-[11px] font-mono p-4 leading-relaxed">
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

function PlaybooksTab({ node }: { node: MonitorNodeInfo }) {
  const [running,        setRunning]        = useState<PlaybookKey | null>(null);
  const [lines,          setLines]          = useState<string[]>([]);
  const [exitOk,         setExitOk]         = useState<boolean | null>(null);
  const [includeObjects, setIncludeObjects] = useState(false);
  const termRef                             = useRef<HTMLPreElement>(null);
  const abortRef                            = useRef<AbortController | null>(null);

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
    };

    const hosts = [node.ip];
    if (key === "dump") {
      abortRef.current = configApi.ansible.streamDumpV2(hosts, includeObjects, undefined, onLine, onDone);
    } else {
      abortRef.current = configApi.ansible.streamApply(hosts, onLine, onDone);
    }
  };

  const lineColor = (line: string | undefined): string => {
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
              <div key={i} className={lineColor(line)}>{line || " "}</div>
            ))}
          </pre>
        </div>
      )}
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
    <div className="flex flex-col h-screen">
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
              {tab === "history"   && <HistoryTab node={node}                   />}
              {tab === "playbooks" && <PlaybooksTab node={node}               />}
              {tab === "cmdref"    && <CmdReferenceTab node={node}            />}
            </motion.div>
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
