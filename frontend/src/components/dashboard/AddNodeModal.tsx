import { useState, useEffect } from "react";
import { X, Wifi, Shield, Database, BarChart2, Loader2 } from "lucide-react";

// ── Types (shared with Dashboard) ─────────────────────────────────

export interface MonitorNodePublic {
  id: number;
  ip: string;
  hostname: string | null;
  display_name: string | null;
  node_type: "ivamail_backend" | "ivamail_frontend" | "nfs" | "haproxy" | "monitoring";
  ssh_user: string;
  ssh_auth_mode: "password" | "key";
  ssh_key_path: string | null;
  ssh_port: number;
  has_ssh_password: boolean;
  cmd_user: string;
  has_cmd_password: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string | null;
}

// ── Props ──────────────────────────────────────────────────────────

export interface AddNodeModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: (node: MonitorNodePublic) => void;
  editNode?: MonitorNodePublic | null;
  defaultNodeType?: MonitorNodePublic["node_type"];
}

// ── Constants ──────────────────────────────────────────────────────

const NODE_TYPE_OPTIONS: {
  value: MonitorNodePublic["node_type"];
  label: string;
  icon: React.ReactNode;
}[] = [
  { value: "ivamail_backend",  label: "IVA Backend",  icon: <Wifi size={13} /> },
  { value: "ivamail_frontend", label: "IVA Frontend", icon: <Wifi size={13} /> },
  { value: "haproxy",          label: "HAProxy",       icon: <Shield size={13} /> },
  { value: "nfs",              label: "NFS/БД",        icon: <Database size={13} /> },
  { value: "monitoring",       label: "Мониторинг",    icon: <BarChart2 size={13} /> },
];

const isIvaMail = (t: MonitorNodePublic["node_type"]) =>
  t === "ivamail_backend" || t === "ivamail_frontend";

// ── Probe result type ──────────────────────────────────────────────

interface ProbeResult {
  ssh_ok: boolean;
  cmd_ok: boolean | null;
  os_pretty: string | null;
  version: string | null;
  error: string | null;
}

// ── Component ──────────────────────────────────────────────────────

export function AddNodeModal({
  open,
  onClose,
  onSaved,
  editNode = null,
  defaultNodeType = "ivamail_backend",
}: AddNodeModalProps) {
  const isEdit = editNode !== null;

  // Form state
  const [nodeType, setNodeType] = useState<MonitorNodePublic["node_type"]>(
    editNode?.node_type ?? defaultNodeType
  );
  const [ip, setIp] = useState(editNode?.ip ?? "");
  const [displayName, setDisplayName] = useState(editNode?.display_name ?? "");
  const [sshUser, setSshUser] = useState(editNode?.ssh_user ?? "user");
  const [authMode, setAuthMode] = useState<"password" | "key">(
    editNode?.ssh_auth_mode ?? "password"
  );
  const [sshPassword, setSshPassword] = useState("");
  const [sshKeyPath, setSshKeyPath] = useState(editNode?.ssh_key_path ?? "");
  const [sshPort, setSshPort] = useState(editNode?.ssh_port ?? 22);
  const [cmdUser, setCmdUser] = useState(editNode?.cmd_user ?? "admin");
  const [cmdPassword, setCmdPassword] = useState("");

  // UI state
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  // Re-initialise form when editNode changes or modal opens
  useEffect(() => {
    if (!open) return;
    setNodeType(editNode?.node_type ?? defaultNodeType);
    setIp(editNode?.ip ?? "");
    setDisplayName(editNode?.display_name ?? "");
    setSshUser(editNode?.ssh_user ?? "user");
    setAuthMode(editNode?.ssh_auth_mode ?? "password");
    setSshPassword("");
    setSshKeyPath(editNode?.ssh_key_path ?? "");
    setSshPort(editNode?.ssh_port ?? 22);
    setCmdUser(editNode?.cmd_user ?? "admin");
    setCmdPassword("");
    setProbeResult(null);
    setApiError(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editNode?.id]);

  if (!open) return null;

  // ── Handlers ────────────────────────────────────────────────────

  async function handleProbe() {
    setProbing(true);
    setProbeResult(null);
    setApiError(null);
    try {
      const body: Record<string, unknown> = {
        node_type: nodeType,
        ip,
        ssh_user: sshUser,
        ssh_auth_mode: authMode,
        ssh_port: sshPort,
        cmd_user: cmdUser,
      };
      if (authMode === "password" && sshPassword) body.ssh_password = sshPassword;
      if (authMode === "key" && sshKeyPath) body.ssh_key_path = sshKeyPath;
      if (isIvaMail(nodeType) && cmdPassword) body.cmd_password = cmdPassword;

      const resp = await fetch("/api/monitor/nodes/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        setApiError(err.detail ?? `HTTP ${resp.status}`);
      } else {
        const result: ProbeResult = await resp.json();
        setProbeResult(result);
      }
    } catch (e) {
      setApiError(e instanceof Error ? e.message : String(e));
    } finally {
      setProbing(false);
    }
  }

  async function handleSave() {
    if (!ip.trim()) return;
    setSaving(true);
    setApiError(null);
    try {
      const body: Record<string, unknown> = {
        node_type: nodeType,
        ip: ip.trim(),
        display_name: displayName.trim() || null,
        ssh_user: sshUser,
        ssh_auth_mode: authMode,
        ssh_port: Number(sshPort),
        cmd_user: cmdUser,
      };
      if (authMode === "password") {
        if (sshPassword) body.ssh_password = sshPassword;
      } else {
        body.ssh_key_path = sshKeyPath || null;
      }
      if (isIvaMail(nodeType) && cmdPassword) {
        body.cmd_password = cmdPassword;
      }

      const url = isEdit
        ? `/api/monitor/nodes/${editNode!.id}`
        : "/api/monitor/nodes";
      const method = isEdit ? "PUT" : "POST";

      const resp = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        if (resp.status === 409) {
          setApiError("Нода с таким IP уже существует");
        } else {
          setApiError(err.detail ?? `HTTP ${resp.status}`);
        }
        return;
      }

      const node: MonitorNodePublic = await resp.json();
      onSaved(node);
      onClose();
    } catch (e) {
      setApiError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backdropFilter: "blur(4px)", background: "rgba(17,17,27,0.7)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-surface0 border border-surface1 rounded-2xl w-full max-w-[480px] shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-surface1">
          <h2 className="text-sm font-semibold text-text">
            {isEdit ? "Редактировать ноду" : "Добавить ноду"}
          </h2>
          <button
            onClick={onClose}
            className="text-overlay0 hover:text-text transition-colors rounded-md p-0.5"
          >
            <X size={16} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-5">

          {/* Node type segmented control */}
          <div className="space-y-1.5">
            <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
              Тип ноды
            </label>
            <div className="flex flex-wrap gap-1.5">
              {NODE_TYPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setNodeType(opt.value)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    nodeType === opt.value
                      ? "bg-blue/20 text-blue border border-blue/40"
                      : "bg-surface1 text-subtext border border-transparent hover:border-surface1"
                  }`}
                >
                  {opt.icon}
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* IP + display name */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                IP адрес <span className="text-red">*</span>
              </label>
              <input
                type="text"
                value={ip}
                onChange={(e) => setIp(e.target.value)}
                placeholder="10.3.6.___"
                className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors font-mono"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                Имя (опц.)
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="авто (be-10-3-6-x)"
                className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors"
              />
            </div>
          </div>

          {/* SSH section */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-surface1" />
              <span className="text-[10px] text-overlay0 uppercase tracking-wider font-medium">SSH</span>
              <div className="h-px flex-1 bg-surface1" />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                  Пользователь
                </label>
                <input
                  type="text"
                  value={sshUser}
                  onChange={(e) => setSshUser(e.target.value)}
                  className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text outline-none transition-colors"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                  SSH порт
                </label>
                <input
                  type="number"
                  value={sshPort}
                  onChange={(e) => setSshPort(Number(e.target.value))}
                  className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text outline-none transition-colors font-mono"
                />
              </div>
            </div>

            {/* Auth mode toggle */}
            <div className="space-y-1.5">
              <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                Авторизация
              </label>
              <div className="flex gap-4">
                {(["password", "key"] as const).map((mode) => (
                  <label key={mode} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      checked={authMode === mode}
                      onChange={() => setAuthMode(mode)}
                      className="accent-blue"
                    />
                    <span className="text-sm text-subtext">
                      {mode === "password" ? "Пароль" : "SSH-ключ"}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {authMode === "password" ? (
              <div className="space-y-1.5">
                <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                  Пароль
                </label>
                <input
                  type="password"
                  value={sshPassword}
                  onChange={(e) => setSshPassword(e.target.value)}
                  placeholder={isEdit ? "не изменён — оставьте пустым" : ""}
                  className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors"
                />
              </div>
            ) : (
              <div className="space-y-1.5">
                <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                  Путь к ключу
                </label>
                <input
                  type="text"
                  value={sshKeyPath}
                  onChange={(e) => setSshKeyPath(e.target.value)}
                  placeholder="/home/user/.ssh/id_rsa"
                  className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors font-mono"
                />
              </div>
            )}
          </div>

          {/* CMD section — only for IVA Mail nodes */}
          {isIvaMail(nodeType) && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="h-px flex-1 bg-surface1" />
                <span className="text-[10px] text-overlay0 uppercase tracking-wider font-medium">
                  CMD (только для IVA Mail нод)
                </span>
                <div className="h-px flex-1 bg-surface1" />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                    CMD user
                  </label>
                  <input
                    type="text"
                    value={cmdUser}
                    onChange={(e) => setCmdUser(e.target.value)}
                    className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text outline-none transition-colors"
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[11px] text-overlay0 uppercase tracking-wider font-medium">
                    CMD password
                  </label>
                  <input
                    type="password"
                    value={cmdPassword}
                    onChange={(e) => setCmdPassword(e.target.value)}
                    placeholder={isEdit ? "не изменён — оставьте пустым" : ""}
                    className="w-full bg-surface1 border border-surface1 focus:border-blue/50 rounded-lg px-3 py-2 text-sm text-text placeholder:text-overlay0 outline-none transition-colors"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Probe section */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-surface1" />
              <span className="text-[10px] text-overlay0 uppercase tracking-wider font-medium">Проверка</span>
              <div className="h-px flex-1 bg-surface1" />
            </div>

            <button
              onClick={handleProbe}
              disabled={probing || !ip.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-surface1 hover:bg-surface1/80 border border-surface1 hover:border-blue/30 text-subtext text-sm rounded-lg transition-colors disabled:opacity-50"
            >
              {probing && <Loader2 size={13} className="animate-spin" />}
              Проверить соединение
            </button>

            {probeResult && (
              <div className="rounded-lg border bg-surface1/50 px-3 py-2.5 space-y-1">
                {probeResult.ssh_ok ? (
                  <p className="text-xs text-green">
                    SSH ✓{probeResult.os_pretty ? ` · ${probeResult.os_pretty}` : ""}
                  </p>
                ) : (
                  <p className="text-xs text-red">
                    SSH ✗{probeResult.error ? ` · ${probeResult.error}` : ""}
                  </p>
                )}
                {isIvaMail(nodeType) && probeResult.cmd_ok !== null && (
                  probeResult.cmd_ok ? (
                    <p className="text-xs text-green">
                      CMD ✓{probeResult.version ? ` · IVA Mail ${probeResult.version}` : ""}
                    </p>
                  ) : (
                    <p className="text-xs text-red">CMD ✗</p>
                  )
                )}
              </div>
            )}
          </div>

          {/* API error */}
          {apiError && (
            <div className="rounded-lg border border-red/20 bg-red/10 px-3 py-2.5">
              <p className="text-xs text-red">{apiError}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-surface1">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-subtext hover:text-text bg-surface1 hover:bg-surface1/70 rounded-lg transition-colors"
          >
            Отмена
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !ip.trim()}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-base bg-blue/20 hover:bg-blue/30 text-blue border border-blue/30 rounded-lg transition-colors disabled:opacity-50"
          >
            {saving && <Loader2 size={13} className="animate-spin" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
