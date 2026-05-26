import { useState, useRef, useCallback, useEffect, type ReactNode } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { Plus, X, Rocket, Upload, ChevronDown, ChevronUp, RefreshCw, Download } from "lucide-react"
import { useDeployStore } from "@/stores/deployStore"
import { useJobStore } from "@/stores/jobStore"
import { PhaseTimeline } from "@/components/shared/PhaseTimeline"
import { TerminalOutput } from "@/components/shared/TerminalOutput"
import type {
  MonitoringService,
  StorageNodeConfig,
  MonitoringNodeConfig,
} from "@/stores/deployStore"

// ─── Constants ────────────────────────────────────────────────────

const SIZING = [
  { accounts: 100,   vCPU: 1, ram: 2,  hdd: 20,  iops: 125   },
  { accounts: 500,   vCPU: 2, ram: 4,  hdd: 40,  iops: 625   },
  { accounts: 1000,  vCPU: 2, ram: 4,  hdd: 40,  iops: 1250  },
  { accounts: 3000,  vCPU: 4, ram: 6,  hdd: 60,  iops: 3750  },
  { accounts: 5000,  vCPU: 4, ram: 8,  hdd: 60,  iops: 6250  },
  { accounts: 10000, vCPU: 6, ram: 10, hdd: 100, iops: 12500 },
]

function getSizing(accounts: number) {
  return SIZING.find((s) => s.accounts >= accounts) ?? SIZING[SIZING.length - 1]
}

const SERVICE_LABELS: Record<MonitoringService, string> = {
  prometheus:    "Prometheus",
  grafana:       "Grafana",
  loki:          "Loki",
  graylog:       "Graylog",
  alertmanager:  "Alertmanager",
  node_exporter: "Node Exporter",
}

const ALL_SERVICES: MonitoringService[] = [
  "prometheus", "grafana", "loki", "graylog", "alertmanager", "node_exporter",
]

const PROFILES = ["dev-cluster", "prod-cluster"]

// ─── Shared input class ───────────────────────────────────────────

const inputCls =
  "font-mono text-xs bg-mantle border border-surface1 rounded-lg px-3 py-1.5 text-text w-full focus:border-blue outline-none transition-colors"

// ─── Accordion Section ────────────────────────────────────────────

function Section({
  title,
  children,
  defaultOpen = true,
}: {
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-left hover:bg-surface1/40 transition-colors"
      >
        <span className="font-medium text-text text-sm">{title}</span>
        <span className="text-overlay0 text-xs">{open ? "▲" : "▼"}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-5 pb-5 pt-2 border-t border-surface1 space-y-4">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ─── ModeToggle ───────────────────────────────────────────────────

interface ModeOption<T extends string> {
  label: string
  value: T
}

function ModeToggle<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ModeOption<T>[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-lg overflow-hidden border border-surface1">
      {options.map((opt, i) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={[
            "px-3 py-1 text-xs font-medium transition-colors",
            i > 0 ? "border-l border-surface1" : "",
            value === opt.value
              ? "bg-blue text-mantle"
              : "bg-surface1 text-subtext hover:text-text",
          ].join(" ")}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// ─── IpField ──────────────────────────────────────────────────────

function IpField({
  value,
  onChange,
  onRemove,
  placeholder = "10.3.6.x",
}: {
  value: string
  onChange: (v: string) => void
  onRemove?: () => void
  placeholder?: string
}) {
  return (
    <div className="flex gap-2 items-center">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={inputCls}
      />
      {onRemove && (
        <button
          onClick={onRemove}
          className="text-overlay0 hover:text-red transition-colors flex-shrink-0"
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

// ─── IpList ───────────────────────────────────────────────────────

function IpList({
  label,
  ips,
  onChange,
  placeholder,
}: {
  label?: string
  ips: string[]
  onChange: (ips: string[]) => void
  placeholder?: string
}) {
  return (
    <div>
      {label && <label className="text-xs text-subtext mb-2 block">{label}</label>}
      <div className="space-y-2">
        {ips.map((ip, i) => (
          <IpField
            key={i}
            value={ip}
            placeholder={placeholder}
            onChange={(v) => {
              const next = [...ips]
              next[i] = v
              onChange(next)
            }}
            onRemove={ips.length > 1 ? () => onChange(ips.filter((_, j) => j !== i)) : undefined}
          />
        ))}
        <button
          onClick={() => onChange([...ips, ""])}
          className="flex items-center gap-1.5 text-xs text-blue hover:text-text transition-colors"
        >
          <Plus size={12} /> Добавить
        </button>
      </div>
    </div>
  )
}

// ─── PerNodeSshRow ────────────────────────────────────────────────

function PerNodeSshRow({ ip }: { ip: string }) {
  const s = useDeployStore()
  const [open, setOpen] = useState(false)
  const overrides = s.perNodeSsh[ip] ?? {}
  const merged = s.getNodeSsh(ip)

  return (
    <div className="border border-surface1 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-mantle hover:bg-surface1/40 transition-colors"
      >
        <span className="font-mono text-xs text-text">{ip}</span>
        <span className="text-overlay0">
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-3 pt-2 border-t border-surface1 space-y-3">
              <ModeToggle
                options={[
                  { label: "Пароль", value: "password" as const },
                  { label: "SSH Ключ", value: "key" as const },
                ]}
                value={overrides.authMode ?? merged.authMode}
                onChange={(v) => s.setPerNodeSsh(ip, { authMode: v })}
              />
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-subtext mb-1 block">User</label>
                  <input
                    value={overrides.user ?? ""}
                    onChange={(e) => s.setPerNodeSsh(ip, { user: e.target.value })}
                    placeholder={merged.user}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">Port</label>
                  <input
                    type="number"
                    value={overrides.port ?? ""}
                    onChange={(e) =>
                      s.setPerNodeSsh(ip, { port: Number(e.target.value) })
                    }
                    placeholder={String(merged.port)}
                    className={inputCls}
                  />
                </div>
                {(overrides.authMode ?? merged.authMode) === "password" ? (
                  <div className="col-span-2">
                    <label className="text-xs text-subtext mb-1 block">Password</label>
                    <input
                      type="password"
                      value={overrides.password ?? ""}
                      onChange={(e) => s.setPerNodeSsh(ip, { password: e.target.value })}
                      placeholder="••••••••"
                      className={inputCls}
                    />
                  </div>
                ) : (
                  <div className="col-span-2">
                    <label className="text-xs text-subtext mb-1 block">Key Path</label>
                    <input
                      value={overrides.keyPath ?? ""}
                      onChange={(e) => s.setPerNodeSsh(ip, { keyPath: e.target.value })}
                      placeholder={merged.keyPath || "/path/to/key"}
                      className={inputCls}
                    />
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ─── Package section ──────────────────────────────────────────────

interface PkgFile {
  filename: string
  path: string
  size_bytes: number
}

function PackageSection() {
  const s = useDeployStore()
  const [files, setFiles]         = useState<PkgFile[]>([])
  const [loading, setLoading]     = useState(false)
  const [urlInput, setUrlInput]   = useState("")
  const [downloading, setDown]    = useState(false)
  const [dlStatus, setDlStatus]   = useState<string | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch("/api/package/list")
      if (r.ok) {
        const d = await r.json() as { packages: PkgFile[] }
        setFiles(d.packages)
        // Если ещё не выбран файл — выбираем первый
        if (d.packages.length > 0 && s.packageType === "controller_file" && !s.packageValue) {
          s.setPackageValue(d.packages[0].path)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [s])

  useEffect(() => { void refresh() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDownloadUrl = async () => {
    if (!urlInput.trim()) return
    setDown(true); setDlStatus(null)
    try {
      const r = await fetch("/api/package/download-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urlInput.trim() }),
      })
      if (r.ok) {
        const d = await r.json() as PkgFile & { source_url: string }
        setDlStatus(`✓ Скачано: ${d.filename}`)
        setUrlInput("")
        await refresh()
        s.setPackageType("controller_file")
        s.setPackageValue(d.path)
      } else {
        const e = await r.json() as { detail?: string }
        setDlStatus(`✗ ${e.detail ?? "Ошибка загрузки"}`)
      }
    } catch (e) {
      setDlStatus(`✗ ${String(e)}`)
    } finally {
      setDown(false)
    }
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const fd = new FormData(); fd.append("file", file)
    setDlStatus(null)
    try {
      const r = await fetch("/api/package/upload", { method: "POST", body: fd })
      if (r.ok) {
        const d = await r.json() as PkgFile
        setDlStatus(`✓ Загружено: ${d.filename}`)
        await refresh()
        s.setPackageType("controller_file")
        s.setPackageValue(d.path)
      } else {
        const er = await r.json() as { detail?: string }
        setDlStatus(`✗ ${er.detail ?? "Ошибка"}`)
      }
    } catch (err) {
      setDlStatus(`✗ ${String(err)}`)
    }
    if (uploadRef.current) uploadRef.current.value = ""
  }

  const fmtSize = (b: number) =>
    b > 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(0)} MB` : `${(b / 1024).toFixed(0)} KB`

  return (
    <div className="space-y-4">
      {/* Dropdown из папки контроллера */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs text-subtext font-medium">Из папки контроллера</label>
          <button
            onClick={() => void refresh()}
            className="flex items-center gap-1 text-[10px] text-overlay0 hover:text-subtext transition-colors"
          >
            <RefreshCw size={10} className={loading ? "animate-spin" : ""} />
            Обновить
          </button>
        </div>
        {files.length === 0 ? (
          <p className="text-xs text-overlay0 italic">
            {loading ? "Загрузка..." : "Папка пуста. Скачайте или загрузите пакет ниже."}
          </p>
        ) : (
          <select
            value={s.packageType === "controller_file" ? s.packageValue : ""}
            onChange={(e) => {
              s.setPackageType("controller_file")
              s.setPackageValue(e.target.value)
            }}
            className="text-xs bg-mantle border border-surface1 rounded-lg px-3 py-1.5 text-text w-full focus:border-blue outline-none transition-colors"
          >
            <option value="">— выберите файл —</option>
            {files.map((f) => (
              <option key={f.path} value={f.path}>
                {f.filename} ({fmtSize(f.size_bytes)})
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Скачать по URL */}
      <div className="space-y-1.5">
        <label className="text-xs text-subtext font-medium">Скачать по URL на контроллер</label>
        <div className="flex gap-2">
          <input
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="http://distrohost.example/ivamail.deb"
            className={inputCls}
            onKeyDown={(e) => e.key === "Enter" && void handleDownloadUrl()}
          />
          <button
            onClick={() => void handleDownloadUrl()}
            disabled={downloading || !urlInput.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue/10 text-blue border border-blue/30 rounded-lg hover:bg-blue/20 transition-colors disabled:opacity-40 flex-shrink-0"
          >
            {downloading ? (
              <RefreshCw size={12} className="animate-spin" />
            ) : (
              <Download size={12} />
            )}
            Скачать
          </button>
        </div>
      </div>

      {/* Загрузить файл */}
      <div className="flex items-center gap-3">
        <input ref={uploadRef} type="file" accept=".deb,.rpm" className="hidden" onChange={handleFileUpload} />
        <button
          onClick={() => uploadRef.current?.click()}
          className="flex items-center gap-1.5 text-xs text-subtext border border-surface1 px-3 py-1.5 rounded-lg hover:bg-surface1/40 transition-colors"
        >
          <Upload size={12} /> Загрузить с компьютера
        </button>
        <span className="text-[10px] text-overlay0">.deb / .rpm</span>
      </div>

      {/* Статус */}
      {dlStatus && (
        <p className={`text-xs font-mono ${dlStatus.startsWith("✓") ? "text-green" : "text-red"}`}>
          {dlStatus}
        </p>
      )}

      {/* Разделитель — путь на сервере */}
      <div className="flex items-center gap-3">
        <div className="flex-1 border-t border-surface1/60" />
        <span className="text-[10px] text-overlay0">или</span>
        <div className="flex-1 border-t border-surface1/60" />
      </div>

      <div>
        <label className="text-xs text-subtext mb-1.5 block font-medium">Путь на целевых серверах</label>
        <input
          value={s.packageType === "server_path" ? s.packageValue : ""}
          onChange={(e) => {
            s.setPackageType("server_path")
            s.setPackageValue(e.target.value)
          }}
          placeholder="/opt/packages/ivamail.deb"
          className={`${inputCls} ${s.packageType === "server_path" ? "border-blue" : ""}`}
        />
        <p className="text-[10px] text-overlay0 mt-0.5">Пакет уже присутствует на каждом узле по этому пути</p>
      </div>

      {/* Текущий источник */}
      {s.packageValue && (
        <div className="bg-mantle border border-surface1 rounded-lg px-3 py-2 flex items-center justify-between gap-2">
          <span className="text-[10px] text-overlay0">Источник:</span>
          <span className="text-xs font-mono text-text truncate flex-1 text-right">
            {s.packageType === "controller_file"
              ? `📦 ${s.packageValue.split(/[/\\]/).pop()}`
              : s.packageType === "server_path"
              ? `🖥 ${s.packageValue}`
              : s.packageValue}
          </span>
        </div>
      )}
    </div>
  )
}

// ─── Deploy page ──────────────────────────────────────────────────

export function Deploy() {
  const s = useDeployStore()
  const { activeDeployment, logs, isLaunching, launchDeploy } = useJobStore()
  const sizing = getSizing(s.licensedAccounts)
  const licenseInputRef = useRef<HTMLInputElement>(null)

  // Deduplicated list of all known IPs for per-node SSH
  const allIps = Array.from(
    new Set([
      ...s.getEffectiveBackends(),
      ...s.getEffectiveFrontends(),
      ...s.storageNodes.map((n) => n.ip),
      ...s.haproxyNodes.map((n) => n.ip),
      ...s.monitoringNodes.map((n) => n.ip),
    ])
  ).filter(Boolean)

  const handleLicenseUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file || !activeDeployment) return
      const fd = new FormData()
      fd.append("license", file)
      await fetch(`/api/deployment/${activeDeployment.id}/upload-license`, {
        method: "POST",
        body: fd,
      })
    },
    [activeDeployment]
  )

  return (
    <div className="p-6 space-y-4 max-w-3xl">
      <h1 className="text-xl font-semibold text-text">Deploy</h1>

      {/* ── 1 · Topology ─────────────────────────────────────────── */}
      <Section title="1 · Topology">
        <div className="grid grid-cols-2 gap-6">
          {/* Backends */}
          <div className="space-y-2">
            <label className="text-xs text-subtext block">Backends</label>
            <ModeToggle
              options={[
                { label: "Отдельные IP", value: "individual" as const },
                { label: "IP Range",     value: "range"      as const },
              ]}
              value={s.backendInputMode}
              onChange={s.setBackendInputMode}
            />
            {s.backendInputMode === "individual" ? (
              <IpList
                ips={s.backends}
                onChange={s.setBackends}
                placeholder="10.3.6.x"
              />
            ) : (
              <div className="space-y-1">
                <input
                  value={s.backendRange}
                  onChange={(e) => s.setBackendRange(e.target.value)}
                  placeholder="10.3.6.206-207"
                  className={inputCls}
                />
                <p className="text-[10px] text-overlay0">
                  Формат: 10.3.6.100-103 или 10.3.6.100-10.3.6.103
                </p>
              </div>
            )}
            <p className="text-xs text-overlay0">
              → {s.getEffectiveBackends().length} нод
            </p>
          </div>

          {/* Frontends */}
          <div className="space-y-2">
            <label className="text-xs text-subtext block">Frontends</label>
            <ModeToggle
              options={[
                { label: "Отдельные IP", value: "individual" as const },
                { label: "IP Range",     value: "range"      as const },
              ]}
              value={s.frontendInputMode}
              onChange={s.setFrontendInputMode}
            />
            {s.frontendInputMode === "individual" ? (
              <IpList
                ips={s.frontends}
                onChange={s.setFrontends}
                placeholder="10.3.6.x"
              />
            ) : (
              <div className="space-y-1">
                <input
                  value={s.frontendRange}
                  onChange={(e) => s.setFrontendRange(e.target.value)}
                  placeholder="10.3.6.102-103"
                  className={inputCls}
                />
                <p className="text-[10px] text-overlay0">
                  Формат: 10.3.6.100-103 или 10.3.6.100-10.3.6.103
                </p>
              </div>
            )}
            <p className="text-xs text-overlay0">
              → {s.getEffectiveFrontends().length} нод
            </p>
          </div>
        </div>
      </Section>

      {/* ── 2 · Infrastructure ───────────────────────────────────── */}
      <Section title="2 · Infrastructure">
        {/* 2.1 Storage */}
        <div>
          <label className="text-xs text-subtext mb-2 block font-medium">Хранилище (СХД)</label>
          <div className="space-y-2">
            {s.storageNodes.map((node, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input
                  value={node.ip}
                  onChange={(e) => {
                    const next: StorageNodeConfig[] = [...s.storageNodes]
                    next[i] = { ...next[i], ip: e.target.value }
                    s.setStorageNodes(next)
                  }}
                  placeholder="10.3.6.x"
                  className={inputCls}
                />
                {node.isPrimary ? (
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-md bg-green/10 text-green flex-shrink-0">
                    Primary
                  </span>
                ) : (
                  <>
                    <span className="text-[10px] font-medium px-2 py-0.5 rounded-md bg-yellow/10 text-yellow flex-shrink-0">
                      Backup
                    </span>
                    <button
                      onClick={() =>
                        s.setStorageNodes(s.storageNodes.filter((_, j) => j !== i))
                      }
                      className="text-overlay0 hover:text-red transition-colors flex-shrink-0"
                    >
                      <X size={14} />
                    </button>
                  </>
                )}
              </div>
            ))}
            <button
              onClick={() =>
                s.setStorageNodes([
                  ...s.storageNodes,
                  { ip: "", isPrimary: false },
                ])
              }
              className="flex items-center gap-1.5 text-xs text-blue hover:text-text transition-colors"
            >
              <Plus size={12} /> Резервная СХД
            </button>
          </div>
        </div>

        <div className="border-t border-surface1/60" />

        {/* 2.2 HAProxy */}
        <div>
          <label className="text-xs text-subtext mb-2 block font-medium">Балансировщики (HAProxy)</label>
          <div className="space-y-2">
            {s.haproxyNodes.map((node, i) => (
              <div key={i} className="flex gap-2 items-center">
                <input
                  value={node.ip}
                  onChange={(e) => {
                    const next = [...s.haproxyNodes]
                    next[i] = { ip: e.target.value }
                    s.setHaproxyNodes(next)
                  }}
                  placeholder="10.3.6.x"
                  className={inputCls}
                />
                {s.haproxyNodes.length > 1 && (
                  <button
                    onClick={() =>
                      s.setHaproxyNodes(s.haproxyNodes.filter((_, j) => j !== i))
                    }
                    className="text-overlay0 hover:text-red transition-colors flex-shrink-0"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            ))}
            <button
              onClick={() => s.setHaproxyNodes([...s.haproxyNodes, { ip: "" }])}
              className="flex items-center gap-1.5 text-xs text-blue hover:text-text transition-colors"
            >
              <Plus size={12} /> Балансировщик
            </button>
          </div>
        </div>

        <div className="border-t border-surface1/60" />

        {/* 2.3 Мониторинг */}
        <div>
          <label className="text-xs text-subtext mb-2 block font-medium">Мониторинг</label>
          <div className="space-y-3">
            {s.monitoringNodes.map((node, i) => (
              <div
                key={i}
                className="bg-mantle border border-surface1 rounded-lg p-3 space-y-2"
              >
                <div className="flex gap-2 items-center">
                  <input
                    value={node.ip}
                    onChange={(e) => {
                      const next: MonitoringNodeConfig[] = s.monitoringNodes.map(
                        (n, j) => (j === i ? { ...n, ip: e.target.value } : n)
                      )
                      s.setMonitoringNodes(next)
                    }}
                    placeholder="10.3.6.x"
                    className={inputCls}
                  />
                  {s.monitoringNodes.length > 1 && (
                    <button
                      onClick={() =>
                        s.setMonitoringNodes(
                          s.monitoringNodes.filter((_, j) => j !== i)
                        )
                      }
                      className="text-overlay0 hover:text-red transition-colors flex-shrink-0"
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  {ALL_SERVICES.map((svc) => {
                    const checked = node.services.includes(svc)
                    return (
                      <label
                        key={svc}
                        className="flex items-center gap-1.5 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            const next: MonitoringNodeConfig[] =
                              s.monitoringNodes.map((n, j) => {
                                if (j !== i) return n
                                const services = checked
                                  ? n.services.filter((x) => x !== svc)
                                  : [...n.services, svc]
                                return { ...n, services }
                              })
                            s.setMonitoringNodes(next)
                          }}
                          className="accent-blue"
                        />
                        <span className="text-xs text-subtext">
                          {SERVICE_LABELS[svc]}
                        </span>
                      </label>
                    )
                  })}
                </div>
              </div>
            ))}
            <button
              onClick={() =>
                s.setMonitoringNodes([
                  ...s.monitoringNodes,
                  { ip: "", services: [] },
                ])
              }
              className="flex items-center gap-1.5 text-xs text-blue hover:text-text transition-colors"
            >
              <Plus size={12} /> Узел мониторинга
            </button>
          </div>
        </div>
      </Section>

      {/* ── 3 · Пакет IVA Mail ───────────────────────────────────── */}
      <Section title="3 · Пакет IVA Mail">
        <PackageSection />
      </Section>

      {/* ── 4 · License Config ───────────────────────────────────── */}
      <Section title="4 · License Config">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-xs text-subtext mb-1 block">Licensed accounts</label>
            <input
              type="number"
              value={s.licensedAccounts}
              onChange={(e) => s.setLicensedAccounts(Number(e.target.value))}
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-subtext mb-1 block">
              Resource accounts <span className="text-overlay0">(авто)</span>
            </label>
            <input
              type="number"
              value={s.resourceAccounts}
              onChange={(e) =>
                s.setLicenseField("resourceAccounts", Number(e.target.value))
              }
              className={inputCls}
            />
            <p className="text-[10px] text-overlay0 mt-0.5">общие ящики, переговорки</p>
          </div>
          <div>
            <label className="text-xs text-subtext mb-1 block">
              Backends в кластере <span className="text-overlay0">(авто)</span>
            </label>
            <input
              type="number"
              value={s.licensedBackends}
              onChange={(e) =>
                s.setLicenseField("licensedBackends", Number(e.target.value))
              }
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-subtext mb-1 block">
              Frontends в кластере <span className="text-overlay0">(авто)</span>
            </label>
            <input
              type="number"
              value={s.licensedFrontends}
              onChange={(e) =>
                s.setLicenseField("licensedFrontends", Number(e.target.value))
              }
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-subtext mb-1 block">Licensee (RU)</label>
            <input
              value={s.licenseeRu}
              onChange={(e) => s.setLicenseField("licenseeRu", e.target.value)}
              placeholder="ООО Пример"
              className={inputCls}
            />
          </div>
          <div>
            <label className="text-xs text-subtext mb-1 block">Licensee (EN)</label>
            <input
              value={s.licenseeEn}
              onChange={(e) => s.setLicenseField("licenseeEn", e.target.value)}
              placeholder="OOO Primer"
              className={inputCls}
            />
          </div>
        </div>

        <button
          onClick={s.recalcLicense}
          className="text-xs text-blue hover:text-text transition-colors flex items-center gap-1"
        >
          ↺ Пересчитать
        </button>

        {/* Hardware card */}
        <div className="bg-mantle border border-surface1 rounded-lg p-4">
          <p className="text-xs text-subtext mb-3 font-medium">
            Требования к железу (на 1 узел бэкенда)
          </p>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-overlay0 text-left">
                <th className="pb-2 font-normal w-16" />
                <th className="pb-2 font-normal text-text">Рекомендуемые</th>
                <th className="pb-2 font-normal text-subtext">Минимум</th>
              </tr>
            </thead>
            <tbody>
              {[
                { label: "vCPU", rec: String(sizing.vCPU),             min: "4"    },
                { label: "RAM",  rec: `${sizing.ram} ГБ`,              min: "8 ГБ" },
                { label: "HDD",  rec: `${sizing.hdd} ГБ`,              min: `${sizing.hdd} ГБ` },
                { label: "IOPS", rec: sizing.iops.toLocaleString("ru"), min: sizing.iops.toLocaleString("ru") },
              ].map((row) => (
                <tr key={row.label}>
                  <td className="text-overlay0 py-0.5 pr-4">{row.label}</td>
                  <td className="text-text py-0.5 pr-4">{row.rec}</td>
                  <td className="text-subtext py-0.5">{row.min}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-overlay0 text-[10px] mt-2">
            {Math.ceil(s.licensedAccounts / 5000)} узел/узла · 5 000 аккаунтов ·{" "}
            {s.licensedBackends} бэкенда · {s.licensedFrontends} фронтенда
          </p>
        </div>
      </Section>

      {/* ── 5 · SSH & Credentials ────────────────────────────────── */}
      <Section title="5 · SSH & Credentials">
        {/* Profile selector */}
        <div className="flex items-center gap-3">
          <label className="text-xs text-subtext flex-shrink-0">Профиль</label>
          <select
            value={s.selectedProfile}
            onChange={(e) => s.selectProfile(e.target.value)}
            className="text-xs bg-mantle border border-surface1 rounded-lg px-3 py-1.5 text-text w-48 focus:border-blue outline-none"
          >
            {PROFILES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        {/* Global / Per-node toggle */}
        <ModeToggle
          options={[
            { label: "Общие",    value: "global"   as const },
            { label: "Per-node", value: "per-node" as const },
          ]}
          value={s.usePerNodeSsh ? "per-node" : "global"}
          onChange={(v) => s.setUsePerNodeSsh(v === "per-node")}
        />

        {!s.usePerNodeSsh ? (
          /* Global SSH */
          <div className="space-y-3">
            <ModeToggle
              options={[
                { label: "Пароль",   value: "password" as const },
                { label: "SSH Ключ", value: "key"      as const },
              ]}
              value={s.globalSsh.authMode}
              onChange={(v) => s.setGlobalSsh({ authMode: v })}
            />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-subtext mb-1 block">SSH User</label>
                <input
                  value={s.globalSsh.user}
                  onChange={(e) => s.setGlobalSsh({ user: e.target.value })}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="text-xs text-subtext mb-1 block">Port</label>
                <input
                  type="number"
                  value={s.globalSsh.port}
                  onChange={(e) => s.setGlobalSsh({ port: Number(e.target.value) })}
                  className={inputCls}
                />
              </div>
              {s.globalSsh.authMode === "password" ? (
                <div className="col-span-2">
                  <label className="text-xs text-subtext mb-1 block">SSH Password</label>
                  <input
                    type="password"
                    value={s.globalSsh.password}
                    onChange={(e) => s.setGlobalSsh({ password: e.target.value })}
                    className={inputCls}
                  />
                </div>
              ) : (
                <div className="col-span-2">
                  <label className="text-xs text-subtext mb-1 block">SSH Key Path</label>
                  <input
                    value={s.globalSsh.keyPath}
                    onChange={(e) => s.setGlobalSsh({ keyPath: e.target.value })}
                    placeholder="/path/to/id_rsa"
                    className={inputCls}
                  />
                </div>
              )}
            </div>

            <div className="border-t border-surface1/60 pt-3">
              <p className="text-xs text-subtext mb-2 font-medium">CMD / PG</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-subtext mb-1 block">CMD User</label>
                  <input
                    value={s.cmdUser}
                    onChange={(e) => useDeployStore.setState({ cmdUser: e.target.value })}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">CMD Password</label>
                  <input
                    type="password"
                    value={s.cmdPassword}
                    onChange={(e) =>
                      useDeployStore.setState({ cmdPassword: e.target.value })
                    }
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">PG User</label>
                  <input
                    value={s.pgUser}
                    onChange={(e) => useDeployStore.setState({ pgUser: e.target.value })}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">PG Password</label>
                  <input
                    type="password"
                    value={s.pgPassword}
                    onChange={(e) =>
                      useDeployStore.setState({ pgPassword: e.target.value })
                    }
                    className={inputCls}
                  />
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* Per-node SSH */
          <div className="space-y-3">
            {allIps.length === 0 ? (
              <p className="text-xs text-overlay0">
                Добавьте IP-адреса в секциях Topology / Infrastructure.
              </p>
            ) : (
              <div className="space-y-2">
                {allIps.map((ip) => (
                  <PerNodeSshRow key={ip} ip={ip} />
                ))}
              </div>
            )}

            {/* Global CMD/PG defaults */}
            <div className="border-t border-surface1/60 pt-3">
              <p className="text-xs text-subtext mb-2 font-medium">CMD / PG (глобально)</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-subtext mb-1 block">CMD User</label>
                  <input
                    value={s.cmdUser}
                    onChange={(e) => useDeployStore.setState({ cmdUser: e.target.value })}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">CMD Password</label>
                  <input
                    type="password"
                    value={s.cmdPassword}
                    onChange={(e) =>
                      useDeployStore.setState({ cmdPassword: e.target.value })
                    }
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">PG User</label>
                  <input
                    value={s.pgUser}
                    onChange={(e) => useDeployStore.setState({ pgUser: e.target.value })}
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="text-xs text-subtext mb-1 block">PG Password</label>
                  <input
                    type="password"
                    value={s.pgPassword}
                    onChange={(e) =>
                      useDeployStore.setState({ pgPassword: e.target.value })
                    }
                    className={inputCls}
                  />
                </div>
              </div>
            </div>
          </div>
        )}
      </Section>

      {/* ── Launch button ─────────────────────────────────────────── */}
      <button
        onClick={launchDeploy}
        disabled={isLaunching}
        className="w-full flex items-center justify-center gap-2 bg-blue hover:bg-blue/80 disabled:opacity-50 text-mantle font-semibold py-3 rounded-xl transition-colors text-sm"
      >
        {isLaunching ? (
          <>
            <span className="animate-spin inline-block">⟳</span> Запускаем...
          </>
        ) : (
          <>
            <Rocket size={16} /> Запустить деплой
          </>
        )}
      </button>

      {/* ── Inline progress ───────────────────────────────────────── */}
      <AnimatePresence>
        {activeDeployment && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="bg-surface0 border border-blue/20 rounded-xl p-5 space-y-4"
          >
            <div className="flex items-center justify-between">
              <span className="text-blue font-medium text-sm">⟳ Деплой запущен</span>
              <span className="font-mono text-xs text-overlay0">
                ID: {activeDeployment.id}
              </span>
            </div>

            {/* Progress bar */}
            <div>
              <div className="flex justify-between text-xs text-subtext mb-1">
                <span className="font-mono">
                  {activeDeployment.currentPhase.replace(/_/g, " ")}
                </span>
                <span>{activeDeployment.progress}%</span>
              </div>
              <div className="w-full bg-surface1 rounded-full h-1.5">
                <div
                  className="bg-blue h-1.5 rounded-full transition-all duration-500"
                  style={{ width: `${activeDeployment.progress}%` }}
                />
              </div>
            </div>

            {/* Waiting license banner */}
            {activeDeployment.currentPhase === "waiting_license" && (
              <div className="flex items-center justify-between bg-yellow/10 border border-yellow/30 rounded-lg px-4 py-3">
                <span className="text-yellow text-sm font-medium">
                  Ожидание лицензии
                </span>
                <button
                  onClick={() => licenseInputRef.current?.click()}
                  className="flex items-center gap-1.5 text-xs bg-yellow text-mantle font-semibold px-3 py-1.5 rounded-lg hover:bg-yellow/80 transition-colors"
                >
                  <Upload size={12} /> Загрузить license.txt
                </button>
                <input
                  ref={licenseInputRef}
                  type="file"
                  accept=".txt,.lic"
                  className="hidden"
                  onChange={handleLicenseUpload}
                />
              </div>
            )}

            <PhaseTimeline currentPhase={activeDeployment.currentPhase} compact />
            <TerminalOutput lines={logs} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
