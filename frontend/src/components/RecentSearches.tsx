import { Clock } from "lucide-react";

interface Props {
  items: string[];
  onSelect: (address: string) => void;
  onClear: () => void;
}

const RecentSearches = ({ items, onSelect, onClear }: Props) => {
  if (items.length === 0) return null;
  return (
    <div className="mt-6 animate-fade-in">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Clock className="h-3.5 w-3.5" /> Recent searches
        </div>
        <button
          onClick={onClear}
          className="text-xs text-muted-foreground transition-smooth hover:text-foreground"
        >
          Clear
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {items.map((addr) => (
          <button
            key={addr}
            onClick={() => onSelect(addr)}
            className="group inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-foreground shadow-sm transition-smooth hover:border-primary/40 hover:bg-accent"
          >
            <span className="truncate">{addr}</span>
          </button>
        ))}
      </div>
    </div>
  );
};

export default RecentSearches;
