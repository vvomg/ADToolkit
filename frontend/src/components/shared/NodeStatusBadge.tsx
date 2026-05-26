import type { NodeStatus } from "@/mock/cluster";

const cfg: Record<NodeStatus, { dot: string; text: string; pulse: boolean }> = {
  online:   { dot: "bg-green",    text: "text-green",    pulse: true  },
  degraded: { dot: "bg-yellow",   text: "text-yellow",   pulse: false },
  offline:  { dot: "bg-red",      text: "text-red",      pulse: false },
  unknown:  { dot: "bg-overlay0", text: "text-overlay0", pulse: false },
};

export function NodeStatusBadge({ status }: { status: NodeStatus }) {
  const c = cfg[status];
  return (
    <span className="flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${c.dot} ${c.pulse ? "animate-pulse" : ""}`} />
      <span className={`text-xs font-mono ${c.text}`}>{status}</span>
    </span>
  );
}
