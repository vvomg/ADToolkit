import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, AlertTriangle, ExternalLink, ArrowRight } from "lucide-react";
import { useJobStore } from "@/stores/jobStore";

// ── Types ─────────────────────────────────────────────────────────

interface DeploymentItem {
  deployment_id: string;
  status: string;
  created_at: string;
  updated_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  licensed_accounts: number | null;
}

// ── Helpers ───────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, string> = {
  success:          "text-green  bg-green/10",
  failed:           "text-red    bg-red/10",
  running:          "text-blue   bg-blue/10 animate-pulse",
  waiting_license:  "text-yellow bg-yellow/10",
  configuration:    "text-blue   bg-blue/10",
  preflight:        "text-blue   bg-blue/10",
  infra_setup:      "text-blue   bg-blue/10",
  node_startup:     "text-blue   bg-blue/10",
  cluster_config:   "text-blue   bg-blue/10",
  license_request:  "text-blue   bg-blue/10",
  license_install:  "text-blue   bg-blue/10",
  remaining_nodes:  "text-blue   bg-blue/10",
  health_checks:    "text-blue   bg-blue/10",
  reporting:        "text-blue   bg-blue/10",
};

function statusLabel(status: string): string {
  const LABELS: Record<string, string> = {
    success: "success", failed: "failed",
    waiting_license: "waiting license",
    configuration: "running", preflight: "running",
    infra_setup: "running", node_startup: "running",
    cluster_config: "running", license_request: "running",
    license_install: "running", remaining_nodes: "running",
    health_checks: "running", reporting: "running",
  };
  return LABELS[status] ?? status;
}

function isActiveStatus(status: string): boolean {
  return !["success", "failed"].includes(status);
}

function formatDate(iso: string): string {
  try { return new Date(iso).toLocaleString("ru"); } catch { return iso; }
}

function formatDuration(secs: number | null): string {
  if (secs === null) return "—";
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  if (m === 0) return `${s}с`;
  return `${m}м ${s}с`;
}

function shortId(id: string): string {
  return id.slice(0, 8) + "…";
}

// ── History ───────────────────────────────────────────────────────

export function History() {
  const navigate = useNavigate();
  const activateDeployment = useJobStore((s) => s.activateDeployment);
  const [deployments, setDeployments] = useState<DeploymentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [activating, setActivating] = useState<string | null>(null);

  const handleRowClick = async (d: DeploymentItem) => {
    setActivating(d.deployment_id);
    await activateDeployment(d.deployment_id);
    setActivating(null);
    navigate("/monitor");
  };

  const fetchDeployments = async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const resp = await fetch("/api/deployment/");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: DeploymentItem[] = await resp.json();
      setDeployments(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { fetchDeployments(); }, []);

  // Auto-refresh каждые 15с если есть активные деплои
  useEffect(() => {
    const hasActive = deployments.some((d) => isActiveStatus(d.status));
    if (!hasActive) return;
    const interval = setInterval(() => fetchDeployments(), 15_000);
    return () => clearInterval(interval);
  }, [deployments]);

  const openReport = (id: string) => {
    window.open(`/api/deployment/${id}/report`, "_blank");
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text">History</h1>
        <button
          onClick={() => fetchDeployments(true)}
          disabled={refreshing}
          className="flex items-center gap-2 px-3 py-1.5 bg-surface0 hover:bg-surface1 text-subtext text-sm rounded-lg transition-colors disabled:opacity-50"
        >
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 bg-red/10 border border-red/20 rounded-xl px-4 py-3">
          <AlertTriangle size={14} className="text-red shrink-0" />
          <span className="text-red text-sm">{error}</span>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
          <p className="text-subtext text-sm">Загрузка...</p>
        </div>
      )}

      {/* Empty */}
      {!loading && deployments.length === 0 && !error && (
        <div className="bg-surface0 border border-surface1 rounded-xl p-8 text-center">
          <p className="text-subtext text-sm">История деплоев пуста</p>
          <p className="text-overlay0 text-xs mt-1">Запустите первый деплой на странице Deploy</p>
        </div>
      )}

      {/* Table */}
      {deployments.length > 0 && (
        <div className="bg-surface0 border border-surface1 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-surface1 text-subtext text-xs">
                {["ID", "Запущен", "Статус", "Аккаунтов", "Длительность", ""].map((h) => (
                  <th key={h} className="text-left px-5 py-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {deployments.map((d, i) => {
                const isActive = isActiveStatus(d.status);
                const isLoading = activating === d.deployment_id;
                return (
                  <tr
                    key={d.deployment_id}
                    onClick={() => handleRowClick(d)}
                    className={[
                      "transition-colors cursor-pointer group",
                      i < deployments.length - 1 ? "border-b border-surface1/50" : "",
                      isLoading ? "opacity-60" : "hover:bg-surface1/40",
                    ].join(" ")}
                  >
                    <td className="px-5 py-3">
                      <span
                        title={d.deployment_id}
                        className="font-mono text-xs text-overlay0"
                      >
                        {shortId(d.deployment_id)}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-xs text-subtext whitespace-nowrap">
                      {formatDate(d.created_at)}
                    </td>
                    <td className="px-5 py-3">
                      <span className={`text-[11px] font-mono px-2 py-0.5 rounded-md ${STATUS_STYLE[d.status] ?? "text-subtext bg-surface1"}`}>
                        {statusLabel(d.status)}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-subtext">
                      {d.licensed_accounts ? d.licensed_accounts.toLocaleString("ru") : "—"}
                    </td>
                    <td className="px-5 py-3 text-xs text-subtext">
                      {isActive
                        ? <span className="text-blue animate-pulse">в процессе</span>
                        : formatDuration(d.duration_seconds)
                      }
                    </td>
                    <td className="px-5 py-3" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-2">
                        {d.status === "success" && (
                          <button
                            onClick={() => openReport(d.deployment_id)}
                            className="flex items-center gap-1 text-xs text-overlay0 hover:text-blue transition-colors"
                          >
                            <ExternalLink size={11} />
                            Отчёт
                          </button>
                        )}
                        <button
                          onClick={(e) => { e.stopPropagation(); void handleRowClick(d); }}
                          disabled={isLoading}
                          className={[
                            "flex items-center gap-1 text-xs px-2 py-0.5 rounded-md transition-colors",
                            d.status === "waiting_license"
                              ? "text-yellow bg-yellow/10 hover:bg-yellow/20"
                              : isActive
                              ? "text-blue bg-blue/10 hover:bg-blue/20"
                              : "text-overlay0 opacity-0 group-hover:opacity-100 hover:text-subtext",
                          ].join(" ")}
                        >
                          {isLoading ? (
                            <RefreshCw size={11} className="animate-spin" />
                          ) : (
                            <ArrowRight size={11} />
                          )}
                          {d.status === "waiting_license"
                            ? "Лицензия"
                            : isActive
                            ? "Monitor"
                            : "Открыть"
                          }
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
