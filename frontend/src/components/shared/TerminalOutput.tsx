import { useEffect, useRef } from "react";

function lineColor(line: string): string {
  if (line.includes("[ERROR]")) return "text-red";
  if (line.includes("[WARN]"))  return "text-yellow";
  if (line.includes("[CMD]"))   return "text-mauve";
  return "text-teal";
}

interface Props {
  lines: string[];
  maxH?: string;
}

export function TerminalOutput({ lines, maxH = "h-64" }: Props) {
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);

  return (
    <pre
      ref={ref}
      className={`font-mono text-xs ${maxH} overflow-y-auto bg-mantle rounded-lg p-3`}
    >
      {lines.map((line, i) => (
        <div key={i} className={lineColor(line)}>{line}</div>
      ))}
    </pre>
  );
}
