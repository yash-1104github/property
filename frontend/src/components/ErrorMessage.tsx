import { AlertCircle, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

const ErrorMessage = ({
  title = "No property record found",
  message = "We couldn't find data for that address. Try another one.",
  onRetry,
}: Props) => (
  <div className="rounded-2xl border border-border bg-card p-10 text-center shadow-soft animate-fade-in">
    <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
      <AlertCircle className="h-6 w-6 text-destructive" />
    </div>
    <h3 className="text-lg font-semibold text-foreground">{title}</h3>
    <p className="mt-2 text-muted-foreground">{message}</p>
    {onRetry && (
      <Button onClick={onRetry} variant="outline" className="mt-6 rounded-full">
        <RotateCw className="mr-2 h-4 w-4" /> Try again
      </Button>
    )}
  </div>
);

export default ErrorMessage;
