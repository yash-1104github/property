import { useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { addressSchema } from "@/lib/api";
import { toast } from "sonner";

interface Props {
  initialValue?: string;
  loading?: boolean;
  onSearch: (address: string) => void;
  size?: "lg" | "md";
}

const AddressSearchBar = ({ initialValue = "", loading, onSearch, size = "lg" }: Props) => {
  const [value, setValue] = useState(initialValue);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const result = addressSchema.safeParse({ address: value });
    if (!result.success) {
      toast.error(result.error.issues[0]?.message ?? "Invalid address");
      return;
    }
    onSearch(result.data.address);
  };

  const isLg = size === "lg";

  return (
    <form
      onSubmit={handleSubmit}
      className={`group flex w-full items-center gap-2 rounded-2xl border border-border bg-card p-2 shadow-elevated transition-smooth focus-within:border-primary/40 focus-within:shadow-glow ${
        isLg ? "sm:p-2.5" : ""
      }`}
    >
      <div className="flex flex-1 items-center gap-3 pl-3">
        <Search className="h-5 w-5 shrink-0 text-muted-foreground" />
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. 1600 Pennsylvania Ave NW, Washington, DC"
          maxLength={250}
          className={`w-full bg-transparent outline-none placeholder:text-muted-foreground/70 ${
            isLg ? "py-3 text-base sm:text-lg" : "py-2 text-sm"
          }`}
          aria-label="Property address"
        />
      </div>
      <Button
        type="submit"
        disabled={loading}
        size={isLg ? "lg" : "default"}
        className="rounded-xl bg-gradient-primary px-5 font-semibold text-primary-foreground shadow-soft hover:opacity-95 sm:px-6"
      >
        {loading ? (
          <>
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Searching
          </>
        ) : (
          <>Search Property</>
        )}
      </Button>
    </form>
  );
};

export default AddressSearchBar;
