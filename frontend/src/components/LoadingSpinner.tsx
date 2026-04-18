import { Loader2 } from "lucide-react";

interface Props {
  label?: string;
}

const LoadingSpinner = ({ label = "Fetching data…" }: Props) => (
  <div className="flex flex-col items-center justify-center gap-4 py-16 animate-fade-in">
    <Loader2 className="h-10 w-10 text-primary animate-spin" />
    <p className="text-muted-foreground font-medium">{label}</p>
  </div>
);

export default LoadingSpinner;
