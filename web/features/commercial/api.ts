import { apiFetch, apiUrl } from "@/lib/api";

import { parseCommercialAccessSnapshot } from "./access";
import type { CommercialAccessSnapshot } from "./types";

export async function fetchCommercialAccess(): Promise<CommercialAccessSnapshot> {
  const response = await apiFetch(apiUrl("/api/v1/commercial/me"), {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Commercial access probe failed (${response.status})`);
  }

  const parsed = parseCommercialAccessSnapshot(await response.json());
  if (!parsed) throw new Error("Commercial access probe returned invalid data");
  return parsed;
}
