import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Building2, FileText, Receipt, ExternalLink, MapPin } from "lucide-react";
import AddressSearchBar from "@/components/AddressSearchBar";
import PropertyCard from "@/components/PropertyCard";
import LoadingSpinner from "@/components/LoadingSpinner";
import ErrorMessage from "@/components/ErrorMessage";
import ConfidenceBadge from "@/components/ConfidenceBadge";
import { lookupProperty } from "@/lib/api";
import type { PropertyData } from "@/lib/types";
import { useRecentSearches } from "@/hooks/useRecentSearches";

const formatMoney = (v?: number | string) => {
  if (v === undefined || v === null || v === "") return undefined;
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
};

const Results = () => {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const address = params.get("address") ?? "";

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<PropertyData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { add } = useRecentSearches();

  const run = async (addr: string) => {
    setLoading(true);
    setError(null);
    setData(null);
    const res = await lookupProperty(addr);
    if (res.success && res.data) {
      setData(res.data);
      add(addr);
    } else {
      setError(res.error ?? "No property record found.");
    }
    setLoading(false);
  };

  useEffect(() => {
    if (!address) {
      navigate("/");
      return;
    }
    run(address);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address]);

  const handleSearch = (addr: string) => {
    navigate(`/results?address=${encodeURIComponent(addr)}`);
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/60 backdrop-blur-sm">
        <div className="container mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-2 text-sm font-semibold text-foreground transition-smooth hover:text-primary">
            <ArrowLeft className="h-4 w-4" /> Back
          </Link>
          <span className="text-sm font-semibold text-gradient">Property Data Lookup</span>
        </div>
      </header>

      <main className="container mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
        <div className="mb-8">
          <AddressSearchBar initialValue={address} loading={loading} onSearch={handleSearch} size="md" />
        </div>

        {loading && <LoadingSpinner />}

        {!loading && error && (
          <ErrorMessage
            title="No property record found"
            message="Try a more specific address, including city and state."
            onRetry={() => run(address)}
          />
        )}

        {!loading && data && (
          <div className="space-y-6">
            <div className="rounded-2xl border border-border bg-card p-6 shadow-soft animate-fade-in">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-primary text-primary-foreground shadow-soft">
                    <MapPin className="h-5 w-5" />
                  </div>
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Result</p>
                    <h1 className="mt-1 text-xl font-bold text-foreground sm:text-2xl">
                      {data.property_address ?? address}
                    </h1>
                  </div>
                </div>
                <ConfidenceBadge score={data.confidence_score} />
              </div>

              {data.source_url && (
                <a
                  href={data.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-primary transition-smooth hover:underline"
                >
                  Source: {new URL(data.source_url).hostname}
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>

            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              <PropertyCard
                title="Owner Information"
                icon={<Building2 className="h-5 w-5" />}
                fields={[{ label: "Owner name", value: data.owner_name, copyable: true }]}
              />
              <PropertyCard
                title="Property Details"
                icon={<FileText className="h-5 w-5" />}
                fields={[
                  { label: "Parcel number", value: data.parcel_number, copyable: true },
                  { label: "Year built", value: data.year_built },
                  { label: "Lot size", value: data.lot_size },
                ]}
              />
              <PropertyCard
                title="Tax Information"
                icon={<Receipt className="h-5 w-5" />}
                fields={[
                  { label: "Assessed value", value: formatMoney(data.assessed_value) },
                  { label: "Taxable value", value: formatMoney(data.taxable_value) },
                ]}
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default Results;
