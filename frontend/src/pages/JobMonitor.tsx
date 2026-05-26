import { useEffect, useRef, useState, useCallback } from "react";
import { Loader2, Download, Upload, CheckCircle2 } from "lucide-react";
import { useJobStore } from "@/stores/jobStore";
import { PhaseTimeline } from "@/components/shared/PhaseTimeline";
import { TerminalOutput } from "@/components/shared/TerminalOutput";
import { NodeStatusBadge } from "@/components/shared/NodeStatusBadge";

// ─── License block ────────────────────────────────────────────────

function LicenseBlock({ deploymentId }: { deploymentId: string }) {
  const uploadRef = useRef<HTMLInputElement>(null)
  const [status, setStatus] = useState<"idle" | "uploading" | "ok" | "err">("idle")
  const [errMsg, setErrMsg] = useState("")

  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setStatus("uploading"); setErrMsg("")
    const fd = new FormData()
    fd.append("license_file", file)
    try {
      const r = await fetch(`/api/deployment/${deploymentId}/upload-license`, {
        method: "POST",
        body: fd,
      })
      if (r.ok) {
        setStatus("ok")
      } else {
        const d = await r.json() as { detail?: string }
        setErrMsg(d.detail ?? "Ошибка загрузки")
        setStatus("err")
      }
    } catch (err) {
      setErrMsg(String(err))
      setStatus("err")
    }
    if (uploadRef.current) uploadRef.current.value = ""
  }, [deploymentId])

  return (
    <div className="bg-yellow/5 border border-yellow/30 rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start gap-3">
        <span className="text-yellow text-xl mt-0.5">⏳</span>
        <div>
          <p className="text-yellow text-sm font-semibold">Требуется лицензия</p>
          <p className="text-subtext text-xs mt-1 leading-relaxed">
            Деплой приостановлен. Скачайте файл запроса лицензии, отправьте его вендору IVA Mail
            и загрузите полученный файл лицензии (.txt) ниже.
          </p>
        </div>
      </div>

      {/* Steps */}
      <div className="space-y-3">
        {/* Step 1: Download request */}
        <div className="flex items-center gap-3 bg-mantle rounded-lg p-3">
          <span className="text-xs font-mono text-overlay0 w-5 text-center flex-shrink-0">1</span>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-subtext">Скачайте файл запроса лицензии</p>
            <p className="text-[10px] text-overlay0 mt-0.5 truncate font-mono">
              license_request_{deploymentId.slice(0, 8)}.txt
            </p>
          </div>
          <a
            href={`/api/deployment/${deploymentId}/license-request/download`}
            download={`license_request_${deploymentId.slice(0, 8)}.txt`}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue/10 text-blue border border-blue/30 rounded-lg hover:bg-blue/20 transition-colors flex-shrink-0"
          >
            <Download size={12} />
            Скачать
          </a>
        </div>

        {/* Step 2: Send to vendor */}
        <div className="flex items-center gap-3 bg-mantle rounded-lg p-3">
          <span className="text-xs font-mono text-overlay0 w-5 text-center flex-shrink-0">2</span>
          <div className="flex-1">
            <p className="text-xs text-subtext">Отправьте файл запроса вендору IVA Mail</p>
            <p className="text-[10px] text-overlay0 mt-0.5">Дождитесь получения файла лицензии (.txt)</p>
          </div>
          <span className="text-overlay0 text-sm">→</span>
        </div>

        {/* Step 3: Upload license */}
        <div className={`flex items-center gap-3 rounded-lg p-3 border transition-colors ${
          status === "ok"
            ? "bg-green/10 border-green/30"
            : status === "err"
            ? "bg-red/10 border-red/30"
            : "bg-mantle border-transparent"
        }`}>
          <span className="text-xs font-mono text-overlay0 w-5 text-center flex-shrink-0">3</span>
          <div className="flex-1 min-w-0">
            {status === "ok" ? (
              <p className="text-xs text-green font-medium">✓ Лицензия принята, деплой продолжается...</p>
            ) : status === "err" ? (
              <>
                <p className="text-xs text-red">✗ Ошибка загрузки</p>
                <p className="text-[10px] text-red/70 mt-0.5 truncate">{errMsg}</p>
              </>
            ) : (
              <>
                <p className="text-xs text-subtext">Загрузите файл лицензии (.txt)</p>
                <p className="text-[10px] text-overlay0 mt-0.5">Файл должен содержать блок BEGIN IVAMAIL LICENSE</p>
              </>
            )}
          </div>
          {status !== "ok" && (
            <>
              <input ref={uploadRef} type="file" accept=".txt" className="hidden" onChange={handleUpload} />
              <button
                onClick={() => uploadRef.current?.click()}
                disabled={status === "uploading"}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-yellow/10 text-yellow border border-yellow/30 rounded-lg hover:bg-yellow/20 transition-colors disabled:opacity-50 flex-shrink-0"
              >
                {status === "uploading" ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Upload size={12} />
                )}
                {status === "uploading" ? "Загрузка..." : "Загрузить"}
              </button>
            </>
          )}
          {status === "ok" && <CheckCircle2 size={18} className="text-green flex-shrink-0" />}
        </div>
      </div>

      {/* Deployment ID hint */}
      <p className="text-[10px] text-overlay0 font-mono">
        Deployment ID: {deploymentId}
      </p>
    </div>
  )
}

// ─── Job Monitor page ─────────────────────────────────────────────

export function JobMonitor() {
  const { activeDeployment, logs, isRestoring, clearJob, restoreDeployment } = useJobStore();

  // При открытии страницы — пытаемся восстановить деплой из localStorage + API
  useEffect(() => {
    if (!activeDeployment && !isRestoring) {
      restoreDeployment();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Пустое состояние — восстановление
  if (isRestoring) {
    return (
      <div className="p-6 flex flex-col items-center justify-center h-64 gap-3">
        <Loader2 size={20} className="text-blue animate-spin" />
        <p className="text-subtext text-sm">Подключение к активному заданию...</p>
      </div>
    );
  }

  // Пустое состояние — нет заданий
  if (!activeDeployment) {
    return (
      <div className="p-6 flex flex-col items-center justify-center h-64 gap-3">
        <p className="text-subtext text-sm">Нет активных заданий.</p>
        <p className="text-overlay0 text-xs">Запустите деплой на странице Deploy.</p>
      </div>
    );
  }

  const badgeStatus =
    activeDeployment.status === "success"         ? "online"   :
    activeDeployment.status === "failed"           ? "offline"  :
    activeDeployment.status === "waiting_license"  ? "degraded" : "online";

  const isFinished = activeDeployment.status === "success" || activeDeployment.status === "failed";

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-text">Job Monitor</h1>
        <div className="flex items-center gap-4">
          <NodeStatusBadge status={badgeStatus} />
          <span
            title={activeDeployment.id}
            className="font-mono text-xs text-overlay0 cursor-default"
          >
            {activeDeployment.id.slice(0, 8)}…
          </span>
          {isFinished ? (
            <button
              onClick={clearJob}
              className="text-xs text-overlay0 hover:text-subtext transition-colors border border-surface1 px-2 py-1 rounded-lg"
            >
              ✕ Закрыть
            </button>
          ) : (
            <button
              onClick={clearJob}
              className="text-xs text-red hover:text-red/70 transition-colors border border-red/30 px-2 py-1 rounded-lg"
            >
              ⏹ Стоп
            </button>
          )}
        </div>
      </div>

      {/* License block */}
      {activeDeployment.status === "waiting_license" && (
        <LicenseBlock deploymentId={activeDeployment.id} />
      )}

      {/* Progress */}
      <div className="bg-surface0 border border-surface1 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-sm text-subtext">
            Фаза:{" "}
            <span className="text-blue font-mono">
              {activeDeployment.currentPhase.replace(/_/g, " ")}
            </span>
          </p>
          <span className="text-text font-mono text-sm font-medium">
            {activeDeployment.progress}%
          </span>
        </div>
        <div className="w-full bg-surface1 rounded-full h-2">
          <div
            className={`h-2 rounded-full transition-all duration-700 ${
              activeDeployment.status === "failed" ? "bg-red" : "bg-blue"
            }`}
            style={{ width: `${activeDeployment.progress}%` }}
          />
        </div>
        <PhaseTimeline currentPhase={activeDeployment.currentPhase} />
      </div>

      {/* Logs */}
      <div className="bg-surface0 border border-surface1 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs text-subtext">Лог выполнения</p>
          <span className="text-[10px] text-overlay0 font-mono">{logs.length} строк</span>
        </div>
        <TerminalOutput lines={logs} maxH="h-96" />
      </div>

      {/* Completion info */}
      {isFinished && (
        <div className={`rounded-xl px-4 py-3 border ${
          activeDeployment.status === "success"
            ? "bg-green/10 border-green/30"
            : "bg-red/10 border-red/30"
        }`}>
          <p className={`text-sm font-medium ${
            activeDeployment.status === "success" ? "text-green" : "text-red"
          }`}>
            {activeDeployment.status === "success"
              ? "✓ Развертывание завершено успешно"
              : "✗ Развертывание завершилось с ошибкой"}
          </p>
          {activeDeployment.status === "success" && (
            <p className="text-subtext text-xs mt-1">
              Отчёт доступен в разделе History
            </p>
          )}
        </div>
      )}
    </div>
  );
}
