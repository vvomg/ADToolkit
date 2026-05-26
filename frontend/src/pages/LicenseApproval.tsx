import { useState } from "react";
import { Download, Upload, CheckCircle } from "lucide-react";

export function LicenseApproval() {
  const [file, setFile]       = useState<File | null>(null);
  const [approved, setApproved] = useState(false);

  return (
    <div className="p-6 max-w-xl space-y-6">
      <h1 className="text-xl font-semibold text-text">License Approval</h1>

      <div className="bg-yellow/5 border border-yellow/20 rounded-xl p-5 space-y-5">
        <p className="text-yellow text-sm flex items-center gap-2">
          ⏸ Деплой приостановлен. Загрузите файл лицензии от вендора.
        </p>

        <div className="space-y-2">
          <p className="text-subtext text-xs font-medium">1. Скачайте файл запроса</p>
          <button className="flex items-center gap-2 text-xs text-blue hover:text-text transition-colors bg-surface0 hover:bg-surface1 px-3 py-2 rounded-lg">
            <Download size={14} /> Скачать license-request.txt
          </button>
          <p className="text-overlay0 text-[11px]">Файл содержит запрос лицензии для передачи вендору IVA Mail.</p>
        </div>

        <div className="space-y-2">
          <p className="text-subtext text-xs font-medium">2. Загрузите полученный license.txt</p>
          <label className="flex items-center gap-2 text-xs text-subtext hover:text-text cursor-pointer bg-surface0 hover:bg-surface1 px-3 py-2 rounded-lg w-fit transition-colors">
            <Upload size={14} />
            {file ? file.name : "Выбрать файл..."}
            <input
              type="file"
              accept=".txt"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </div>

        <button
          disabled={!file || approved}
          onClick={() => setApproved(true)}
          className="flex items-center gap-2 bg-green hover:bg-green/80 disabled:opacity-40 disabled:cursor-not-allowed text-mantle text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          <CheckCircle size={14} /> Подтвердить и продолжить
        </button>

        {approved && (
          <p className="text-green text-xs font-mono">✓ Лицензия принята, деплой продолжается...</p>
        )}
      </div>
    </div>
  );
}
