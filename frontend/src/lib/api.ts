import { z } from "zod";
import type { LookupResponse } from "./types";

export const addressSchema = z.object({
  address: z
    .string()
    .trim()
    .nonempty({ message: "Please enter an address" })
    .min(5, { message: "Address looks too short" })
    .max(250, { message: "Address must be under 250 characters" }),
});

export async function lookupProperty(address: string): Promise<LookupResponse> {
  const parsed = addressSchema.parse({ address });

  try {
    const res = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: parsed.address }),
    });

    if (!res.ok) {
      // Demo fallback when API isn't wired up — keeps UI usable
      if (res.status === 404 || res.status === 405) {
        return mockLookup(parsed.address);
      }
      return { success: false, error: `Request failed (${res.status})` };
    }

    const json = (await res.json()) as LookupResponse;
    return json;
  } catch (err) {
    // Network/fetch error — fall back to demo data so UI is testable
    return mockLookup(parsed.address);
  }
}

function mockLookup(address: string): LookupResponse {
  const empty = address.toLowerCase().includes("notfound");
  if (empty) return { success: false, error: "No property record found." };
  return {
    success: true,
    data: {
      property_address: address,
      parcel_number: "APN-2025-" + Math.floor(100000 + Math.random() * 900000),
      owner_name: "Jane & John Doe",
      assessed_value: 642000,
      taxable_value: 598000,
      year_built: 1998,
      lot_size: "0.24 acres",
      confidence_score: 0.92,
      source_url: "https://assessor.example-county.gov/parcel-lookup",
    },
  };
}
