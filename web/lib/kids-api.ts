/**
 * Kids API Client — typed fetch wrappers for the /api/v1/kids/* endpoints.
 *
 * All functions use the shared ``apiFetch`` + ``apiUrl`` helpers so the
 * requests are transparently proxied to the backend.
 */

import { apiFetch, apiUrl } from "@/lib/api";
import type {
  GenerateMapRequest,
  MapProgressItem,
  ProgressResponse,
  QuestMapResponse,
  ThemeSummary,
} from "@/lib/kids-types";

/** Extract a human-readable error message from a failed response. */
async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* ignore */
  }
  return fallback;
}

/**
 * Fetch the list of available public theme packs.
 * GET /api/v1/kids/public-themes
 */
export async function getPublicThemes(): Promise<ThemeSummary[]> {
  const res = await apiFetch(apiUrl("/api/v1/kids/public-themes"));
  if (!res.ok) {
    throw new Error(await readError(res, "Failed to load themes"));
  }
  return (await res.json()) as ThemeSummary[];
}

/**
 * Generate a challenge map from a public theme or personal KB.
 * POST /api/v1/kids/maps
 */
export async function generateMap(
  body: GenerateMapRequest,
): Promise<QuestMapResponse> {
  const res = await apiFetch(apiUrl("/api/v1/kids/maps"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await readError(res, "Failed to generate map"));
  }
  return (await res.json()) as QuestMapResponse;
}

/**
 * List the active profile's maps with progress data.
 * GET /api/v1/kids/maps
 */
export async function getMaps(): Promise<MapProgressItem[]> {
  const res = await apiFetch(apiUrl("/api/v1/kids/maps"));
  if (!res.ok) {
    throw new Error(await readError(res, "Failed to load maps"));
  }
  return (await res.json()) as MapProgressItem[];
}

/**
 * Fetch guardian-facing progress data for a child profile.
 * GET /api/v1/kids/progress/{profileId}?pin=...
 *
 * @param profileId — the child profile ID
 * @param pin       — guardian PIN (query param)
 */
export async function getProgress(
  profileId: string,
  pin: string,
): Promise<ProgressResponse> {
  const params = new URLSearchParams({ pin });
  const res = await apiFetch(
    apiUrl(
      `/api/v1/kids/progress/${encodeURIComponent(profileId)}?${params.toString()}`,
    ),
  );
  if (!res.ok) {
    throw new Error(await readError(res, "Failed to load progress"));
  }
  return (await res.json()) as ProgressResponse;
}
