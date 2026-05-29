import { useState, useEffect, useCallback } from "react";
import {
  RefreshCw, AlertTriangle, Play, Server, Database, Mail, Settings2,
  ChevronDown, Download, ExternalLink,
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────────

interface InventoryConfig {
  host: string;
  port: number;
  user: string;
  password: string;
  all_domains: boolean;
  domains: string[];
  accounts: string[];
  include_imap_stats: boolean;
  imap_host: string;
  imap_port: number | null;
  imap_ssl: boolean;
  imap_user: string;
  imap_password: string;
  cmd_workers: number;
  imap_workers: number;
  timeout: number;
  page_size: number;
  include_acl: boolean;
  include_non_mail_objects: boolean;
  recalculate_storage: boolean;
  mailbox_class: string;
  report_title: string;
  max_domains: number | null;
  max_accounts_per_domain: number | null;
  max_mailboxes_per_account: number | null;
}

interface ScanListItem {
  scan_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  domains_count: number | null;
  accounts_count: number | null;
  folders_count: number | null;
  config_snapshot: Record<string, unknown> | null;
}

interface ScanDetail extends ScanListItem {
  log_output: string | null;
  error_message: string | null;
}

// ── Defaults ───────────────────────────────────────────────────────────────────

const DEFAULT_CONFIG: InventoryConfig = {
  host: "", port: 106, user: "", password: "",
  all_domains: true, domains: [], accounts: [],
  include_imap_stats: true,
  imap_host: "", imap_port: null, imap_ssl: false, imap_user: "", imap_password: "",
  cmd_workers: 20, imap_workers: 20, timeout: 60, page_size: 1000,
  include_acl: true, include_non_mail_objects: true, recalculate_storage: true,
  mailbox_class: "mail", report_title: "IVA Mail Inventory",
  max_domains: null, max_accounts_per_domain: null, max_mailboxes_per_account: null,
};

// ── Helpers ────────────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, string> = {
  success: "text-green bg-green/10",
  failed:  "text-red   bg-red/10",
  running: "text-blue  bg-blue/10 animate-pulse",
};

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString("ru"); } catch { return iso; }
}

function formatDuration(started: string, finished: string | null): string {
  if (!finished) return "—";
  const secs = Math.round((new Date(finished).getTime() - new Date(started).getTime()) / 1000);
  const m = Math.floor(secs / 60), s = secs % 60;
  return m > 0 ? `${m}м ${s}с` : `${s}с`;
}

// ── Toggle component ───────────────────────────────────────────────────────────

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer select-none">
      <div
        className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${checked ? "bg-blue" : "bg-surface2"}`}
        onClick={onChange}
      >
        <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </div>
      <span className="text-sm text-subtext">{label}</span>
    </label>
  );
}

// ── Input component ────────────────────────────────────────────────────────────

function Field({
  label, children,
}: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs text-subtext">{label}</label>
      {children}
    </div>
  );
}

const inputCls = "w-full bg-surface1 border border-surface2 rounded-lg px-3 py-2 text-sm text-text placeholder-overlay0 focus:outline-none focus:border-blue transition-colors";

// ── Section component ──────────────────────────────────────────────────────────

function Section({
  open, onToggle, icon: Icon, iconColor, title, children,
}: {
  open: boolean;
  onToggle: () => void;
  icon: React.ElementType;
  iconColor: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-3 hover:bg-surface1/30 transition-colors"
      >
        <span className="text-sm font-medium text-text flex items-center gap-2">
          <Icon size={14} className={iconColor} />
          {title}
        </span>
        <ChevronDown
          size={14}
          className={`text-overlay0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="px-5 pb-5 pt-4 border-t border-surface1 space-y-4">
          {children}
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function Search() {
  const [cfg, setCfg] = useState<InventoryConfig>(DEFAULT_CONFIG);
  const [scans, setScans] = useState<ScanListItem[]>([]);
  const [activeScan, setActiveScan] = useState<ScanDetail | null>(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Section visibility
  const [openConn, setOpenConn] = useState(true);
  const [openScope, setOpenScope] = useState(true);
  const [openImap, setOpenImap] = useState(false);
  const [openParams, setOpenParams] = useState(false);
  const [showLimits, setShowLimits] = useState(false);

  // Textarea helpers for multi-line domains/accounts
  const [domainsText, setDomainsText] = useState("");
  const [accountsText, setAccountsText] = useState("");

  const update = <K extends keyof InventoryConfig>(key: K, val: InventoryConfig[K]) =>
    setCfg(prev => ({ ...prev, [key]: val }));

  // ── Fetch scans list ──

  const fetchScans = useCallback(async (spinner = false) => {
    if (spinner) setRefreshing(true);
    try {
      const r = await fetch("/api/inventory/");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: ScanListItem[] = await r.json();
      setScans(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const pollActiveScan = useCallback(async (id: string) => {
    try {
      const r = await fetch(`/api/inventory/${id}`);
      if (!r.ok) return;
      const data: ScanDetail = await r.json();
      setActiveScan(data);
      if (data.status !== "running") {
        setScanning(false);
        fetchScans();
      }
    } catch { /* ignore network errors */ }
  }, [fetchScans]);

  useEffect(() => { fetchScans(); }, [fetchScans]);

  // Poll running scan every 3s
  useEffect(() => {
    if (!activeScan || activeScan.status !== "running") return;
    const t = setInterval(() => pollActiveScan(activeScan.scan_id), 3_000);
    return () => clearInterval(t);
  }, [activeScan, pollActiveScan]);

  // ── Start scan ──

  const handleStart = async () => {
    if (!cfg.host || !cfg.user) return;
    setScanning(true);
    setError(null);

    const payload = {
      ...cfg,
      domains: domainsText.split("\n").map(s => s.trim()).filter(Boolean),
      accounts: accountsText.split("\n").map(s => s.trim()).filter(Boolean),
      imap_host: cfg.imap_host || null,
      imap_port: cfg.imap_port || null,
      imap_user: cfg.imap_user || null,
      imap_password: cfg.imap_password || null,
      max_domains: cfg.max_domains || null,
      max_accounts_per_domain: cfg.max_accounts_per_domain || null,
      max_mailboxes_per_account: cfg.max_mailboxes_per_account || null,
    };

    try {
      const r = await fetch("/api/inventory/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: `HTTP ${r.status}` }));
        throw new Error((err as { detail?: string }).detail ?? `HTTP ${r.status}`);
      }
      const { scan_id } = await r.json() as { scan_id: string };
      setActiveScan({
        scan_id,
        started_at: new Date().toISOString(),
        finished_at: null,
        status: "running",
        domains_count: null,
        accounts_count: null,
        folders_count: null,
        config_snapshot: null,
        log_output: null,
        error_message: null,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setScanning(false);
    }
  };

  // Row click: restore config from snapshot
  const restoreConfig = (snap: Record<string, unknown> | null) => {
    if (!snap) return;
    const partial = snap as Partial<InventoryConfig>;
    setCfg(prev => ({ ...prev, ...partial }));
    if (Array.isArray(partial.domains)) setDomainsText((partial.domains as string[]).join("\n"));
    if (Array.isArray(partial.accounts)) setAccountsText((partial.accounts as string[]).join("\n"));
  };

  // ── Render ──

  return (
    <div className="p-6 space-y-5">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text">Search / Инвентаризация</h1>
          <p className="text-xs text-overlay0 mt-0.5">Инвентаризация почтовых ящиков кластера IVA Mail</p>
        </div>
        <button
          onClick={() => fetchScans(true)}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 bg-surface0 hover:bg-surface1 text-subtext text-sm rounded-lg transition-colors disabled:opacity-50"
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

      {/* ── Section 1: Connection ── */}
      <Section open={openConn} onToggle={() => setOpenConn(v => !v)}
        icon={Server} iconColor="text-blue" title="Подключение">
        <div className="grid grid-cols-2 gap-4">
          <Field label="CMD Хост">
            <input type="text" value={cfg.host} onChange={e => update("host", e.target.value)}
              placeholder="10.3.6.126" className={inputCls} />
          </Field>
          <Field label="Порт">
            <input type="number" value={cfg.port} onChange={e => update("port", Number(e.target.value))}
              className={inputCls} />
          </Field>
          <Field label="Пользователь">
            <input type="text" value={cfg.user} onChange={e => update("user", e.target.value)}
              placeholder="admin" className={inputCls} />
          </Field>
          <Field label="Пароль">
            <input type="password" value={cfg.password} onChange={e => update("password", e.target.value)}
              placeholder="••••••••" className={inputCls} />
          </Field>
        </div>
      </Section>

      {/* ── Section 2: Scope ── */}
      <Section open={openScope} onToggle={() => setOpenScope(v => !v)}
        icon={Database} iconColor="text-mauve" title="Область сканирования">
        <Toggle
          checked={cfg.all_domains}
          onChange={() => update("all_domains", !cfg.all_domains)}
          label="Все домены"
        />
        {!cfg.all_domains && (
          <div className="grid grid-cols-2 gap-4">
            <Field label="Домены (по одному на строку)">
              <textarea
                value={domainsText}
                onChange={e => setDomainsText(e.target.value)}
                rows={4}
                placeholder={"example.com\ntest.ru"}
                className={`${inputCls} font-mono resize-none`}
              />
            </Field>
            <Field label="Аккаунты (по одному на строку)">
              <textarea
                value={accountsText}
                onChange={e => setAccountsText(e.target.value)}
                rows={4}
                placeholder={"user@example.com\nadmin@test.ru"}
                className={`${inputCls} font-mono resize-none`}
              />
            </Field>
          </div>
        )}
        {/* Limits collapse */}
        <button
          onClick={() => setShowLimits(v => !v)}
          className="text-xs text-overlay0 hover:text-subtext flex items-center gap-1 transition-colors"
        >
          <ChevronDown size={11} className={`transition-transform ${showLimits ? "rotate-180" : ""}`} />
          Лимиты (для тестирования)
        </button>
        {showLimits && (
          <div className="grid grid-cols-3 gap-4">
            {(["max_domains", "max_accounts_per_domain", "max_mailboxes_per_account"] as const).map(key => (
              <Field key={key} label={
                key === "max_domains" ? "Макс. доменов" :
                key === "max_accounts_per_domain" ? "Макс. аккаунтов/домен" :
                "Макс. ящиков/аккаунт"
              }>
                <input
                  type="number" min={1}
                  value={cfg[key] ?? ""}
                  onChange={e => update(key, e.target.value ? Number(e.target.value) : null)}
                  placeholder="—"
                  className={inputCls}
                />
              </Field>
            ))}
          </div>
        )}
      </Section>

      {/* ── Section 3: IMAP ── */}
      <Section open={openImap} onToggle={() => setOpenImap(v => !v)}
        icon={Mail} iconColor="text-teal" title="IMAP статистика">
        <Toggle
          checked={cfg.include_imap_stats}
          onChange={() => update("include_imap_stats", !cfg.include_imap_stats)}
          label="Включить IMAP статистику"
        />
        {cfg.include_imap_stats && (
          <div className="grid grid-cols-2 gap-4">
            <Field label="IMAP хост (пусто = CMD хост)">
              <input type="text" value={cfg.imap_host}
                onChange={e => update("imap_host", e.target.value)}
                placeholder={cfg.host || "10.3.6.126"} className={inputCls} />
            </Field>
            <Field label="IMAP порт">
              <input type="number" value={cfg.imap_port ?? ""}
                onChange={e => update("imap_port", e.target.value ? Number(e.target.value) : null)}
                placeholder="143" className={inputCls} />
            </Field>
            <Field label="IMAP пользователь">
              <input type="text" value={cfg.imap_user}
                onChange={e => update("imap_user", e.target.value)}
                placeholder="admin" className={inputCls} />
            </Field>
            <Field label="IMAP пароль">
              <input type="password" value={cfg.imap_password}
                onChange={e => update("imap_password", e.target.value)}
                placeholder="••••••••" className={inputCls} />
            </Field>
            <div className="col-span-2">
              <Toggle
                checked={cfg.imap_ssl}
                onChange={() => update("imap_ssl", !cfg.imap_ssl)}
                label="SSL/TLS (порт 993)"
              />
            </div>
          </div>
        )}
      </Section>

      {/* ── Section 4: Options ── */}
      <Section open={openParams} onToggle={() => setOpenParams(v => !v)}
        icon={Settings2} iconColor="text-yellow" title="Параметры">
        <div className="grid grid-cols-4 gap-4">
          {([
            ["cmd_workers",  "CMD потоки"],
            ["imap_workers", "IMAP потоки"],
            ["timeout",      "Timeout (сек)"],
            ["page_size",    "Размер страницы"],
          ] as [keyof InventoryConfig, string][]).map(([key, label]) => (
            <Field key={key} label={label}>
              <input type="number" min={1}
                value={cfg[key] as number}
                onChange={e => update(key, Number(e.target.value))}
                className={inputCls} />
            </Field>
          ))}
        </div>
        <Field label="Заголовок отчёта">
          <input type="text" value={cfg.report_title}
            onChange={e => update("report_title", e.target.value)}
            className={inputCls} />
        </Field>
        <div className="grid grid-cols-3 gap-4">
          <Toggle checked={cfg.include_acl}
            onChange={() => update("include_acl", !cfg.include_acl)}
            label="Включить ACL" />
          <Toggle checked={cfg.include_non_mail_objects}
            onChange={() => update("include_non_mail_objects", !cfg.include_non_mail_objects)}
            label="Все типы объектов" />
          <Toggle checked={cfg.recalculate_storage}
            onChange={() => update("recalculate_storage", !cfg.recalculate_storage)}
            label="Пересчёт хранилища" />
        </div>
      </Section>

      {/* ── Launch button ── */}
      <button
        onClick={handleStart}
        disabled={scanning || !cfg.host || !cfg.user}
        className="flex items-center gap-2 px-5 py-2.5 bg-blue hover:bg-blue/80 text-base text-sm font-medium rounded-xl transition-colors disabled:opacity-50"
      >
        {scanning
          ? <RefreshCw size={14} className="animate-spin" />
          : <Play size={14} />
        }
        {scanning ? "Сканирование..." : "▶ Запустить инвентаризацию"}
      </button>

      {/* ── Active scan status ── */}
      {activeScan && activeScan.status === "running" && (
        <div className="bg-surface0 border border-surface1 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-3">
            <RefreshCw size={14} className="text-blue animate-spin" />
            <span className="text-sm text-text font-medium">Сканирование в процессе...</span>
            <span className="text-xs text-overlay0 font-mono">{activeScan.scan_id.slice(0, 8)}</span>
          </div>
          {activeScan.log_output && (
            <pre className="bg-mantle rounded-lg p-3 text-xs text-subtext font-mono overflow-auto max-h-40 whitespace-pre-wrap">
              {activeScan.log_output.split("\n").slice(-20).join("\n")}
            </pre>
          )}
        </div>
      )}

      {/* ── History table ── */}
      <div className="space-y-3">
        <h2 className="text-sm font-medium text-subtext">История сканирований</h2>

        {loading && (
          <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
            <p className="text-subtext text-sm">Загрузка...</p>
          </div>
        )}

        {!loading && scans.length === 0 && (
          <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
            <p className="text-subtext text-sm">Нет сканирований</p>
            <p className="text-overlay0 text-xs mt-1">Заполните форму выше и нажмите «Запустить инвентаризацию»</p>
          </div>
        )}

        {scans.length > 0 && (
          <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-surface1 text-subtext text-xs">
                  {["ID", "Запущен", "Статус", "Домены", "Аккаунты", "Папки", "Длительность", ""].map(h => (
                    <th key={h} className="text-left px-4 py-3 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {scans.map((s, i) => (
                  <tr
                    key={s.scan_id}
                    onClick={() => restoreConfig(s.config_snapshot)}
                    title="Клик — загрузить параметры в форму"
                    className={[
                      "transition-colors cursor-pointer hover:bg-surface1/40",
                      i < scans.length - 1 ? "border-b border-surface1/50" : "",
                    ].join(" ")}
                  >
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs text-overlay0" title={s.scan_id}>
                        {s.scan_id.slice(0, 8)}…
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-subtext whitespace-nowrap">
                      {formatDate(s.started_at)}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-[11px] font-mono px-2 py-0.5 rounded-md ${STATUS_STYLE[s.status] ?? "text-subtext bg-surface1"}`}>
                        {s.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-subtext">{s.domains_count ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-subtext">{s.accounts_count ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-subtext">{s.folders_count ?? "—"}</td>
                    <td className="px-4 py-3 text-xs text-subtext">
                      {formatDuration(s.started_at, s.finished_at)}
                    </td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      {s.status === "success" && (
                        <div className="flex items-center gap-3">
                          <a
                            href={`/api/inventory/${s.scan_id}/download/json`}
                            download
                            className="flex items-center gap-1 text-xs text-overlay0 hover:text-blue transition-colors"
                          >
                            <Download size={11} />JSON
                          </a>
                          <button
                            onClick={() => window.open(`/api/inventory/${s.scan_id}/download/html`, "_blank")}
                            className="flex items-center gap-1 text-xs text-overlay0 hover:text-blue transition-colors"
                          >
                            <ExternalLink size={11} />HTML
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
