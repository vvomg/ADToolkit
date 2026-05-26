import { useState, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X,
  Terminal,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Search,
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────

export interface EnrichedCommand {
  name: string;
  syntax: string;
  section: string;
  description: string;
  available: boolean;
  documented: boolean;
}

export interface NodeCommandsModalProps {
  open: boolean;
  onClose: () => void;
  nodeId: number;
  nodeLabel: string;
  initialCommands?: EnrichedCommand[] | null;
  fetchedAt?: string | null;
}

// ── Helpers ────────────────────────────────────────────────────────────

function groupBySection(
  commands: EnrichedCommand[],
): Record<string, EnrichedCommand[]> {
  return commands.reduce<Record<string, EnrichedCommand[]>>((acc, cmd) => {
    const s = cmd.section || "Прочие";
    if (!acc[s]) acc[s] = [];
    acc[s].push(cmd);
    return acc;
  }, {});
}

function formatFetchedAt(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString("ru");
  } catch {
    return iso;
  }
}

// ── Component ──────────────────────────────────────────────────────────

export function NodeCommandsModal({
  open,
  onClose,
  nodeId,
  nodeLabel,
  initialCommands,
  fetchedAt: initialFetchedAt,
}: NodeCommandsModalProps) {
  const [commands, setCommands] = useState<EnrichedCommand[]>(
    initialCommands ?? [],
  );
  const [fetchedAt, setFetchedAt] = useState<string | null>(
    initialFetchedAt ?? null,
  );
  const [loading, setLoading] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<EnrichedCommand | null>(null);
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());

  // Load commands when modal opens
  useEffect(() => {
    if (!open) return;

    if (initialCommands && initialCommands.length > 0) {
      setCommands(initialCommands);
      setFetchedAt(initialFetchedAt ?? null);
      return;
    }

    setLoading(true);
    setError(null);
    fetch(`/api/monitor/nodes/${nodeId}/commands`)
      .then(async (resp) => {
        if (resp.status === 204) return null;
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json() as Promise<{
          commands: EnrichedCommand[];
          fetched_at: string;
        }>;
      })
      .then((data) => {
        if (data?.commands) {
          setCommands(data.commands);
          setFetchedAt(data.fetched_at ?? null);
        }
      })
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [open, nodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset when closed
  useEffect(() => {
    if (!open) {
      setSearch("");
      setSelected(null);
      setError(null);
    }
  }, [open]);

  // Auto-expand all sections and select first on load
  useEffect(() => {
    if (commands.length > 0) {
      const sections = [
        ...new Set(commands.map((c) => c.section || "Прочие")),
      ];
      setOpenSections(new Set(sections));
      if (!selected) setSelected(commands[0] ?? null);
    }
  }, [commands]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleDiscover() {
    setDiscovering(true);
    setError(null);
    try {
      const resp = await fetch(
        `/api/monitor/nodes/${nodeId}/discover-commands`,
        { method: "POST" },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as {
        commands: EnrichedCommand[];
        fetched_at: string;
      };
      setCommands(data.commands ?? []);
      setFetchedAt(data.fetched_at ?? null);
      if (data.commands?.length > 0) setSelected(data.commands[0]);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setDiscovering(false);
    }
  }

  function toggleSection(section: string) {
    setOpenSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }

  const filtered = useMemo(() => {
    if (!search.trim()) return commands;
    const q = search.toLowerCase();
    return commands.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.syntax.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q),
    );
  }, [commands, search]);

  const grouped = useMemo(() => groupBySection(filtered), [filtered]);
  const sections = Object.keys(grouped).sort();

  if (!open) return null;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-40 bg-mantle/80 backdrop-blur-sm"
            onClick={onClose}
          />

          {/* Modal */}
          <motion.div
            key="modal"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="fixed inset-4 md:inset-8 xl:inset-16 z-50 flex flex-col bg-base border border-surface0 rounded-2xl shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-surface0 shrink-0 bg-mantle">
              <div className="flex items-center gap-2.5 min-w-0">
                <Terminal size={15} className="text-blue shrink-0" />
                <span className="text-sm font-medium text-text shrink-0">
                  CMD Менеджер
                </span>
                <span className="text-[10px] font-mono text-overlay0 bg-surface0 px-2 py-0.5 rounded truncate">
                  {nodeLabel}
                </span>
                {fetchedAt && (
                  <span className="text-[10px] text-overlay0 shrink-0">
                    · {formatFetchedAt(fetchedAt)}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-3">
                <button
                  onClick={handleDiscover}
                  disabled={discovering || loading}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-surface0 hover:bg-surface1 text-subtext hover:text-text border border-surface1 transition-colors disabled:opacity-50"
                >
                  <RefreshCw
                    size={11}
                    className={discovering ? "animate-spin" : ""}
                  />
                  {discovering ? "Обнаружение..." : "Обновить"}
                </button>
                <button
                  onClick={onClose}
                  className="p-1.5 rounded-lg text-overlay0 hover:text-text hover:bg-surface1 transition-colors"
                >
                  <X size={15} />
                </button>
              </div>
            </div>

            {/* Error banner */}
            {error && (
              <div className="px-5 py-2 bg-red/10 border-b border-red/20 text-xs text-red flex items-center gap-2 shrink-0">
                <span className="shrink-0">⚠</span>
                <span className="font-mono">{error}</span>
              </div>
            )}

            {/* Body */}
            <div className="flex flex-1 min-h-0 overflow-hidden">
              {/* Left panel — section tree */}
              <div className="w-56 shrink-0 border-r border-surface0 flex flex-col bg-mantle/50">
                {/* Search */}
                <div className="p-3 border-b border-surface0">
                  <div className="flex items-center gap-2 bg-surface0 rounded-lg px-2.5 py-1.5 ring-1 ring-transparent focus-within:ring-blue/30 transition-all">
                    <Search size={11} className="text-overlay0 shrink-0" />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Поиск команд..."
                      className="flex-1 text-xs bg-transparent outline-none text-text placeholder-overlay0"
                    />
                  </div>
                </div>

                {/* Tree */}
                <div className="flex-1 overflow-y-auto py-1">
                  {loading && (
                    <p className="text-[10px] text-overlay0 px-4 py-3 italic">
                      Загрузка...
                    </p>
                  )}
                  {!loading && commands.length === 0 && !error && (
                    <div className="px-4 py-4 text-center">
                      <p className="text-[10px] text-overlay0 italic mb-2">
                        Команды не загружены
                      </p>
                      <button
                        onClick={handleDiscover}
                        disabled={discovering}
                        className="text-[10px] text-blue hover:text-blue/80 underline transition-colors disabled:opacity-50"
                      >
                        Обнаружить команды
                      </button>
                    </div>
                  )}
                  {sections.map((section) => {
                    const sectionCmds = grouped[section];
                    const isOpen = openSections.has(section);
                    return (
                      <div key={section}>
                        <button
                          onClick={() => toggleSection(section)}
                          className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left hover:bg-surface0/50 transition-colors"
                        >
                          {isOpen ? (
                            <ChevronDown
                              size={10}
                              className="text-overlay0 shrink-0"
                            />
                          ) : (
                            <ChevronRight
                              size={10}
                              className="text-overlay0 shrink-0"
                            />
                          )}
                          <span className="text-[10px] font-semibold text-overlay0 uppercase tracking-wider flex-1 text-left">
                            {section}
                          </span>
                          <span className="text-[9px] bg-surface0 text-overlay0 px-1.5 py-0.5 rounded font-mono">
                            {sectionCmds.length}
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
                              {sectionCmds.map((cmd) => {
                                const isSelected =
                                  selected?.name === cmd.name;
                                return (
                                  <button
                                    key={cmd.name}
                                    onClick={() => setSelected(cmd)}
                                    className={`w-full flex items-center gap-2 pl-6 pr-3 py-1.5 text-left text-xs transition-colors ${
                                      isSelected
                                        ? "bg-blue/10 text-blue"
                                        : "text-subtext hover:text-text hover:bg-surface0/60"
                                    }`}
                                  >
                                    <span
                                      className={`text-[10px] shrink-0 ${
                                        cmd.available
                                          ? "text-green"
                                          : "text-overlay0"
                                      }`}
                                    >
                                      {cmd.available ? "●" : "○"}
                                    </span>
                                    <span className="font-mono truncate">
                                      {cmd.name}
                                    </span>
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

                {/* Stats footer */}
                {commands.length > 0 && (
                  <div className="border-t border-surface0 px-3 py-2">
                    <span className="text-[9px] text-overlay0 font-mono">
                      {commands.filter((c) => c.available).length}/
                      {commands.length} доступны
                    </span>
                  </div>
                )}
              </div>

              {/* Right panel — command detail */}
              <div className="flex-1 overflow-y-auto p-6">
                {!selected && !loading && commands.length > 0 && (
                  <p className="text-sm text-overlay0 text-center pt-12">
                    Выберите команду
                  </p>
                )}

                {!selected && !loading && commands.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
                    <Terminal size={32} className="text-overlay0" />
                    <div>
                      <p className="text-sm font-medium text-subtext mb-1">
                        Команды не обнаружены
                      </p>
                      <p className="text-xs text-overlay0 mb-4">
                        Нажмите «Обновить» чтобы получить список команд от
                        сервера
                      </p>
                      <button
                        onClick={handleDiscover}
                        disabled={discovering}
                        className="flex items-center gap-2 px-4 py-2 text-sm text-blue bg-blue/10 hover:bg-blue/20 border border-blue/30 rounded-lg transition-colors disabled:opacity-50 mx-auto"
                      >
                        <RefreshCw
                          size={12}
                          className={discovering ? "animate-spin" : ""}
                        />
                        {discovering
                          ? "Обнаружение..."
                          : "Обнаружить команды"}
                      </button>
                    </div>
                  </div>
                )}

                {selected && (
                  <div className="space-y-5 max-w-2xl">
                    {/* Name + badges */}
                    <div>
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <h2 className="text-base font-semibold text-text font-mono">
                          {selected.name}
                        </h2>
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full ring-1 ${
                            selected.available
                              ? "text-green ring-green/30 bg-green/10"
                              : "text-overlay0 ring-overlay0/20 bg-surface1"
                          }`}
                        >
                          {selected.available ? "● Доступна" : "○ Недоступна"}
                        </span>
                        {!selected.documented && (
                          <span className="text-[10px] px-2 py-0.5 rounded-full text-yellow ring-1 ring-yellow/30 bg-yellow/10">
                            не документирована
                          </span>
                        )}
                      </div>
                      <span className="text-[10px] bg-surface0 text-overlay0 px-2 py-0.5 rounded font-mono">
                        {selected.section}
                      </span>
                    </div>

                    {/* Syntax */}
                    <div>
                      <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">
                        Синтаксис
                      </p>
                      <code className="block font-mono bg-surface1 px-4 py-3 rounded-lg text-sm text-peach leading-relaxed whitespace-pre-wrap">
                        {selected.syntax}
                      </code>
                    </div>

                    {/* Description */}
                    <div>
                      <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">
                        Описание
                      </p>
                      {selected.documented && selected.description ? (
                        <p className="text-sm text-subtext leading-relaxed whitespace-pre-wrap">
                          {selected.description}
                        </p>
                      ) : (
                        <p className="text-sm text-overlay0 italic">
                          Описание отсутствует в документации
                        </p>
                      )}
                    </div>

                    {/* Status */}
                    <div className="pt-2 border-t border-surface0">
                      <p className="text-[10px] text-overlay0 uppercase tracking-widest mb-2">
                        Статус
                      </p>
                      <div className="flex items-center gap-4 text-xs flex-wrap">
                        <span
                          className={
                            selected.available ? "text-green" : "text-overlay0"
                          }
                        >
                          {selected.available
                            ? "● Доступна на сервере"
                            : "○ Отсутствует в HELP"}
                        </span>
                        <span
                          className={
                            selected.documented ? "text-blue" : "text-overlay0"
                          }
                        >
                          {selected.documented
                            ? "● Задокументирована"
                            : "○ Нет в справочнике"}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
