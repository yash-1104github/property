import { cn } from "@/lib/utils";
import { ShieldCheck } from "lucide-react";

interface Props {
  score?: number; // 0-1 or 0-100
}

const ConfidenceBadge = ({ score }: Props) => {
  if (score === undefined || score === null) return null;
  const pct = score <= 1 ? Math.round(score * 100) : Math.round(score);

  const level = pct >= 80 ? "High" : pct >= 50 ? "Medium" : "Low";
  const styles =
    level === "High"
      ? "bg-success/10 text-success border-success/20"
      : level === "Medium"
        ? "bg-warning/10 text-warning border-warning/20"
        : "bg-destructive/10 text-destructive border-destructive/20";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold",
        styles
      )}
    >
      <ShieldCheck className="h-3.5 w-3.5" />
      {level} confidence · {pct}%
    </span>
  );
};

export default ConfidenceBadge;
