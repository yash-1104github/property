import { useNavigate } from "react-router-dom";
import { Building2, ShieldCheck, Zap } from "lucide-react";
import AddressSearchBar from "@/components/AddressSearchBar";
import RecentSearches from "@/components/RecentSearches";
import { useRecentSearches } from "@/hooks/useRecentSearches";

const features = [
  { icon: Zap, title: "Instant lookup", desc: "Owner, parcel, and tax data in seconds." },
  { icon: ShieldCheck, title: "Sourced & scored", desc: "Every result ships with a confidence score." },
  { icon: Building2, title: "Nationwide coverage", desc: "Public records across counties, unified." },
];

const Landing = () => {
  const navigate = useNavigate();
  const { items, clear } = useRecentSearches();

  const handleSearch = (address: string) => {
    navigate(`/results?address=${encodeURIComponent(address)}`);
  };

  return (
    <div className="min-h-screen bg-hero">
      <header className="container mx-auto flex max-w-6xl items-center justify-between px-4 py-6 sm:px-6 lg:px-8">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-primary text-primary-foreground shadow-soft">
            <Building2 className="h-4 w-4" />
          </div>
          <span className="text-sm font-bold tracking-tight text-foreground">PropLookup</span>
        </div>
        <a
          href="#features"
          className="text-sm font-medium text-muted-foreground transition-smooth hover:text-foreground"
        >
          How it works
        </a>
      </header>

      <main>
        <section className="container mx-auto max-w-3xl px-4 pt-12 pb-16 text-center sm:px-6 sm:pt-20 lg:px-8">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-border bg-card/70 px-3 py-1 text-xs font-medium text-muted-foreground shadow-sm backdrop-blur-sm animate-fade-in">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            Live data · trusted public records
          </div>

          <h1 className="text-4xl font-extrabold leading-[1.1] tracking-tight text-foreground sm:text-6xl animate-fade-in-up">
            <span className="text-gradient">Property Data Lookup</span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-base text-muted-foreground sm:text-lg animate-fade-in-up">
            Enter any address to get ownership, tax, and parcel data instantly.
          </p>

          <div className="mx-auto mt-10 max-w-2xl animate-scale-in">
            <AddressSearchBar onSearch={handleSearch} />
            <RecentSearches items={items} onSelect={handleSearch} onClear={clear} />
          </div>
        </section>

        <section id="features" className="container mx-auto max-w-5xl px-4 pb-24 sm:px-6 lg:px-8">
          <div className="grid gap-5 sm:grid-cols-3">
            {features.map(({ icon: Icon, title, desc }) => (
              <div
                key={title}
                className="rounded-2xl border border-border bg-card p-6 shadow-soft transition-smooth hover:-translate-y-0.5 hover:shadow-elevated"
              >
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-accent-foreground">
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="text-base font-semibold text-foreground">{title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{desc}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-border bg-card/40 mt-30">
        <div className="container mx-auto max-w-6xl px-4 py-6 text-center text-xs text-muted-foreground sm:px-6 lg:px-8">
          © {new Date().getFullYear()} PropLookup · Built for fast property research
        </div>
      </footer>
    </div>
  );
};

export default Landing;
