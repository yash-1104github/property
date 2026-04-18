import { useCallback, useEffect, useState } from "react";

const KEY = "pdl.recentSearches";
const MAX = 5;

export function useRecentSearches() {
  const [items, setItems] = useState<string[]>([]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setItems(JSON.parse(raw));
    } catch {
      /* ignore */
    }
  }, []);

  const persist = (next: string[]) => {
    setItems(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };

  const add = useCallback((address: string) => {
    setItems((prev) => {
      const next = [address, ...prev.filter((a) => a.toLowerCase() !== address.toLowerCase())].slice(0, MAX);
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const clear = useCallback(() => persist([]), []);

  return { items, add, clear };
}
