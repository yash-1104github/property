import { Copy, Check } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

interface Field {
  label: string;
  value?: string | number | null;
  copyable?: boolean;
}

interface Props {
  title: string;
  icon: React.ReactNode;
  fields: Field[];
}

const PropertyCard = ({ title, icon, fields }: Props) => {
  const visible = fields.filter((f) => f.value !== undefined && f.value !== null && f.value !== "");
  if (visible.length === 0) return null;

  return (
    <div className="group rounded-2xl border border-border bg-card p-6 shadow-soft transition-smooth hover:-translate-y-0.5 hover:shadow-elevated animate-fade-in-up">
      <div className="mb-5 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-accent-foreground">
          {icon}
        </div>
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
      </div>
      <dl className="space-y-4">
        {visible.map((f) => (
          <Row key={f.label} field={f} />
        ))}
      </dl>
    </div>
  );
};

const Row = ({ field }: { field: Field }) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(String(field.value));
      setCopied(true);
      toast.success(`${field.label} copied`);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy");
    }
  };

  return (
    <div className="flex items-start justify-between gap-4 border-b border-border/60 pb-3 last:border-none last:pb-0">
      <dt className="text-sm text-muted-foreground">{field.label}</dt>
      <dd className="flex items-center gap-2 text-right">
        <span className="text-sm font-medium text-foreground">{String(field.value)}</span>
        {field.copyable && (
          <button
            type="button"
            onClick={handleCopy}
            aria-label={`Copy ${field.label}`}
            className="rounded-md p-1 text-muted-foreground transition-smooth hover:bg-accent hover:text-accent-foreground"
          >
            {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        )}
      </dd>
    </div>
  );
};

export default PropertyCard;
