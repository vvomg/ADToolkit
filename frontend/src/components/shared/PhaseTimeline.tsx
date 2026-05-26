import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { ALL_PHASES } from "@/mock/deployments";

interface Props {
  currentPhase: string;
  compact?: boolean;
}

export function PhaseTimeline({ currentPhase, compact }: Props) {
  const currentIdx = ALL_PHASES.indexOf(currentPhase as never);
  const phases = compact ? ALL_PHASES.slice(0, 8) : ALL_PHASES;

  return (
    <div className="flex flex-wrap gap-1.5">
      {phases.map((phase, i) => {
        const done    = i < currentIdx;
        const current = i === currentIdx;
        return (
          <span
            key={phase}
            title={phase}
            className={`flex items-center gap-1 text-xs font-mono px-2 py-1 rounded-md ${
              done    ? "bg-green/10 text-green" :
              current ? "bg-blue/10 text-blue"   :
                        "bg-surface1 text-overlay0"
            }`}
          >
            {done    ? <CheckCircle2 size={10} /> :
             current ? <Loader2 size={10} className="animate-spin" /> :
                       <Circle size={10} />}
            {phase.replace(/_/g, " ")}
          </span>
        );
      })}
    </div>
  );
}
