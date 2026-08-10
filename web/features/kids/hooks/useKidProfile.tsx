/**
 * useKidProfile — React context hook for the active kid profile.
 *
 * Provides the current profile ID, nickname, avatar, age band, and role
 * to all components within the kids layout. The profile data is sourced
 * from the backend profiles API and stored in React context.
 */

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiFetch, apiUrl } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface KidProfileInfo {
  profileId: string;
  nickname: string;
  avatar: string;
  ageBand: string;
  role: "kid" | "guardian";
}

interface KidProfileContextValue {
  profile: KidProfileInfo | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const KidProfileContext = createContext<KidProfileContextValue>({
  profile: null,
  loading: true,
  error: null,
  refresh: () => {},
});

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function KidProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<KidProfileInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;

    async function loadProfile() {
      setLoading(true);
      setError(null);
      try {
        // Check the current session/profile state
        const res = await apiFetch(apiUrl("/api/v1/profiles"), {
          cache: "no-store",
        });
        if (!res.ok) {
          if (!cancelled) {
            setError("Failed to load profile");
            setLoading(false);
          }
          return;
        }
        const data = (await res.json()) as Array<{
          profile_id: string;
          nickname: string;
          avatar: string;
          age_band: string;
          role: string;
        }>;
        if (!cancelled) {
          // Use the first profile as the active one (UI can add a switcher)
          if (data.length > 0) {
            const p = data[0];
            setProfile({
              profileId: p.profile_id,
              nickname: p.nickname,
              avatar: p.avatar || "🧒",
              ageBand: p.age_band || "7-9",
              role: "kid",
            });
          } else {
            setProfile(null);
          }
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load profile");
          setLoading(false);
        }
      }
    }

    loadProfile();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const value = useMemo<KidProfileContextValue>(
    () => ({ profile, loading, error, refresh }),
    [profile, loading, error, refresh],
  );

  return (
    <KidProfileContext.Provider value={value}>
      {children}
    </KidProfileContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useKidProfile(): KidProfileContextValue {
  return useContext(KidProfileContext);
}
