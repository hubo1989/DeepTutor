"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, Zap, Flame, TrendingUp } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";
import { useKidProfile } from "@/features/kids/hooks/useKidProfile";
import BadgeGrid from "@/features/kids/components/BadgeGrid";
import StreakCalendar from "@/features/kids/components/StreakCalendar";
import { getMaps } from "@/lib/kids-api";
import {
  computeLevel,
  progressToNextLevel,
  xpForNextLevel,
  type MapProgressItem,
} from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RewardsPage() {
  const router = useRouter();
  const { profile } = useKidProfile();
  const [maps, setMaps] = useState<MapProgressItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        const mapData = await getMaps().catch(() => [] as MapProgressItem[]);
        if (!cancelled) setMaps(mapData);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
  }, []);

  // Derive display data from maps
  const totalXp = maps.reduce((sum, m) => sum + m.cleared_levels * 30, 0);
  const level = computeLevel(totalXp);
  const progress = progressToNextLevel(totalXp);
  const nextLevelXp = xpForNextLevel(level);
  const streakDays = maps.length > 0 ? 1 : 0;
  const badges = maps.some((m) => m.cleared_levels > 0) ? ["first_clear"] : [];

  // Mock streak history — in production from progress API
  const streakHistory: string[] = [];

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <div
          className="inline-block animate-spin rounded-full h-10 w-10 border-4 border-t-transparent"
          style={{
            borderColor: `${kidsTheme.colors.primary} transparent transparent transparent`,
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.push("/home")}
          className="p-2 rounded-full hover:bg-[var(--muted)]"
          aria-label="Back"
        >
          <ChevronLeft className="w-6 h-6" style={{ color: "var(--foreground)" }} />
        </button>
        <h1
          className="text-2xl font-extrabold"
          style={{ color: "var(--foreground)" }}
        >
          My Achievements
        </h1>
      </div>

      {/* Level card */}
      <div
        className="flex items-center gap-4 p-5"
        style={{
          background: `linear-gradient(135deg, ${kidsTheme.colors.primary}, ${kidsTheme.colors.boss})`,
          borderRadius: kidsTheme.borderRadius.card,
          color: "white",
        }}
      >
        <div
          className="flex items-center justify-center text-3xl font-extrabold"
          style={{
            width: "4rem",
            height: "4rem",
            borderRadius: "50%",
            backgroundColor: "rgba(255,255,255,0.2)",
          }}
        >
          {level}
        </div>
        <div className="flex-1">
          <div className="text-sm opacity-90">Level {level}</div>
          <div className="text-2xl font-extrabold">{totalXp} XP</div>
          <div className="text-xs opacity-75">
            {nextLevelXp - totalXp} XP to Level {level + 1}
          </div>
        </div>
      </div>

      {/* Level progress bar */}
      <div
        className="p-4"
        style={{
          backgroundColor: "var(--card)",
          borderRadius: kidsTheme.borderRadius.card,
          border: "1px solid var(--border)",
        }}
      >
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
            Progress to Level {level + 1}
          </span>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            {Math.round(progress * 100)}%
          </span>
        </div>
        <div
          className="w-full h-3 overflow-hidden"
          style={{
            backgroundColor: "var(--muted)",
            borderRadius: "9999px",
          }}
        >
          <div
            style={{
              width: `${Math.round(progress * 100)}%`,
              height: "100%",
              backgroundColor: kidsTheme.colors.xpBar,
              borderRadius: "9999px",
              transition: "width 0.5s ease",
            }}
          />
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-3">
        <div
          className="flex items-center gap-3 p-4"
          style={{
            backgroundColor: "var(--card)",
            borderRadius: kidsTheme.borderRadius.card,
            border: "1px solid var(--border)",
          }}
        >
          <div
            className="flex items-center justify-center"
            style={{
              width: "2.5rem",
              height: "2.5rem",
              borderRadius: "50%",
              backgroundColor: `${kidsTheme.colors.streakFlame}15`,
            }}
          >
            <Flame
              className="w-5 h-5 fill-current"
              style={{ color: kidsTheme.colors.streakFlame }}
            />
          </div>
          <div>
            <div
              className="text-xl font-extrabold"
              style={{ color: "var(--foreground)" }}
            >
              {streakDays}
            </div>
            <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>
              day streak
            </div>
          </div>
        </div>

        <div
          className="flex items-center gap-3 p-4"
          style={{
            backgroundColor: "var(--card)",
            borderRadius: kidsTheme.borderRadius.card,
            border: "1px solid var(--border)",
          }}
        >
          <div
            className="flex items-center justify-center"
            style={{
              width: "2.5rem",
              height: "2.5rem",
              borderRadius: "50%",
              backgroundColor: `${kidsTheme.colors.primary}15`,
            }}
          >
            <TrendingUp
              className="w-5 h-5"
              style={{ color: kidsTheme.colors.primary }}
            />
          </div>
          <div>
            <div
              className="text-xl font-extrabold"
              style={{ color: "var(--foreground)" }}
            >
              {maps.reduce((sum, m) => sum + m.cleared_levels, 0)}
            </div>
            <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>
              levels cleared
            </div>
          </div>
        </div>
      </div>

      {/* Badge wall */}
      <div
        className="p-4"
        style={{
          backgroundColor: "var(--card)",
          borderRadius: kidsTheme.borderRadius.card,
          border: "1px solid var(--border)",
        }}
      >
        <BadgeGrid earnedBadges={badges} />
      </div>

      {/* Streak calendar */}
      <div
        className="p-4"
        style={{
          backgroundColor: "var(--card)",
          borderRadius: kidsTheme.borderRadius.card,
          border: "1px solid var(--border)",
        }}
      >
        <StreakCalendar
          streakHistory={streakHistory}
          streakDays={streakDays}
          weeks={4}
        />
      </div>
    </div>
  );
}
