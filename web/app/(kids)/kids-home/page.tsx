"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Flame, Zap, TrendingUp, ChevronRight } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";
import { useKidProfile } from "@/features/kids/hooks/useKidProfile";
import { getPublicThemes, getMaps } from "@/lib/kids-api";
import {
  computeLevel,
  progressToNextLevel,
  xpForNextLevel,
  type MapProgressItem,
  type ThemeSummary,
} from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function KidsHomePage() {
  const router = useRouter();
  const { t } = useTranslation();
  const { profile } = useKidProfile();
  const [themes, setThemes] = useState<ThemeSummary[]>([]);
  const [maps, setMaps] = useState<MapProgressItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mock XP data — in production this comes from a lightweight progress fetch
  const [totalXp, setTotalXp] = useState(0);
  const [streakDays, setStreakDays] = useState(0);
  const [dailyXp, setDailyXp] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      setLoading(true);
      setError(null);
      try {
        const [themeData, mapData] = await Promise.all([
          getPublicThemes(),
          getMaps().catch(() => [] as MapProgressItem[]),
        ]);
        if (!cancelled) {
          setThemes(themeData);
          setMaps(mapData);

          // Derive XP from maps progress (rough estimate for display)
          const xp = mapData.reduce(
            (sum, m) => sum + m.cleared_levels * 30,
            0,
          );
          setTotalXp(xp);
          setDailyXp(Math.min(xp, kidsTheme.sessionLimitSeconds));
          setStreakDays(mapData.length > 0 ? 1 : 0);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t("kids.failedToLoad"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadData();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const handleThemeClick = useCallback(
    async (theme: ThemeSummary) => {
      try {
        router.push(`/map/${theme.theme_id}`);
      } catch {
        // navigate error — stay on page
      }
    },
    [router],
  );

  const level = computeLevel(totalXp);
  const progress = progressToNextLevel(totalXp);
  const nextLevelXp = xpForNextLevel(level);

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

  if (error) {
    return (
      <div className="text-center py-12">
        <p style={{ color: kidsTheme.colors.danger }}>{error}</p>
        <button
          className="mt-4 px-6 py-3 font-bold text-white"
          style={{
            backgroundColor: kidsTheme.colors.primary,
            borderRadius: kidsTheme.borderRadius.button,
          }}
          onClick={() => window.location.reload()}
        >
          {t("kids.retry")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Greeting */}
      <div className="flex items-center gap-3">
        <span className="text-4xl">{profile?.avatar ?? "🧒"}</span>
        <div>
          <h1
            className="text-2xl font-extrabold"
            style={{ color: "var(--foreground)" }}
          >
            {profile?.nickname ?? t("kids.friend")}!
          </h1>
          <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
            {t("kids.chooseThemeAdventure")}
          </p>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-3">
        {/* Today XP */}
        <StatCard
          icon={<Zap className="w-5 h-5 fill-current" />}
          iconColor={kidsTheme.colors.xpBar}
          iconBg={`${kidsTheme.colors.xpBar}15`}
          value={dailyXp}
          label={t("kids.todayXp")}
        />
        {/* Streak */}
        <StatCard
          icon={<Flame className="w-5 h-5 fill-current" />}
          iconColor={kidsTheme.colors.streakFlame}
          iconBg={`${kidsTheme.colors.streakFlame}15`}
          value={streakDays}
          label={t("kids.streak")}
          suffix={t("kids.streakSuffix")}
        />
        {/* Level */}
        <StatCard
          icon={<TrendingUp className="w-5 h-5" />}
          iconColor={kidsTheme.colors.primary}
          iconBg={`${kidsTheme.colors.primary}15`}
          value={level}
          label={t("kids.level")}
          prefix={t("kids.levelPrefix")}
        />
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
          {t("kids.levelN", { n: level })}
        </span>
        <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
          {t("kids.xpProgress", { current: totalXp, next: nextLevelXp })}
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

      {/* In-progress maps */}
      {maps.length > 0 && (
        <div>
          <h2
            className="text-lg font-bold mb-3"
            style={{ color: "var(--foreground)" }}
          >
            {t("kids.continueLearning")}
          </h2>
          <div className="space-y-2">
            {maps.slice(0, 3).map((m) => (
              <button
                key={m.map_id}
                className="w-full flex items-center gap-3 p-4 text-left transition-all hover:opacity-80"
                style={{
                  backgroundColor: "var(--card)",
                  borderRadius: kidsTheme.borderRadius.card,
                  border: "1px solid var(--border)",
                }}
                onClick={() => router.push(`/map/${m.map_id.replace("public:", "")}`)}
              >
                <span className="text-3xl">{m.icon}</span>
                <div className="flex-1">
                  <div className="font-bold" style={{ color: "var(--foreground)" }}>
                    {m.title}
                  </div>
                  <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                    {t("kids.levelsProgress", { cleared: m.cleared_levels, total: m.total_levels })}
                  </div>
                </div>
                <ChevronRight className="w-5 h-5" style={{ color: "var(--muted-foreground)" }} />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Theme cards */}
      <div>
        <h2
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          {t("kids.chooseTheme")}
        </h2>
        <div className="grid grid-cols-2 gap-3">
          {themes.map((theme) => (
            <button
              key={theme.theme_id}
              className="flex flex-col items-center p-5 transition-all hover:scale-[1.02]"
              style={{
                backgroundColor: "var(--card)",
                borderRadius: kidsTheme.borderRadius.card,
                border: "2px solid var(--border)",
                minHeight: kidsTheme.sizing.cardMinHeight,
              }}
              onClick={() => handleThemeClick(theme)}
            >
              <span className="text-5xl mb-2">{theme.icon}</span>
              <span
                className="font-bold text-base text-center"
                style={{ color: "var(--foreground)" }}
              >
                {theme.title_zh || theme.title_en}
              </span>
              <span className="text-xs mt-1" style={{ color: "var(--muted-foreground)" }}>
                {t("kids.levelCount", { count: theme.level_count })}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Rewards link */}
      <button
        className="w-full flex items-center justify-center gap-2 py-4 font-bold text-white transition-opacity hover:opacity-90"
        style={{
          backgroundColor: kidsTheme.colors.boss,
          borderRadius: kidsTheme.borderRadius.button,
          fontSize: kidsTheme.typography.buttonSize,
          minHeight: kidsTheme.sizing.buttonMinHeight,
        }}
        onClick={() => router.push("/rewards")}
      >
        <span className="text-xl">🏆</span>
        {t("kids.myAchievements")}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat card sub-component
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  iconColor,
  iconBg,
  value,
  label,
  prefix = "",
  suffix = "",
}: {
  icon: React.ReactNode;
  iconColor: string;
  iconBg: string;
  value: number;
  label: string;
  prefix?: string;
  suffix?: string;
}) {
  return (
    <div
      className="flex flex-col items-center p-3"
      style={{
        backgroundColor: "var(--card)",
        borderRadius: kidsTheme.borderRadius.card,
        border: "1px solid var(--border)",
      }}
    >
      <div
        className="flex items-center justify-center mb-1"
        style={{
          width: "2.5rem",
          height: "2.5rem",
          borderRadius: "50%",
          backgroundColor: iconBg,
        }}
      >
        <span style={{ color: iconColor }}>{icon}</span>
      </div>
      <span
        className="text-xl font-extrabold"
        style={{ color: "var(--foreground)" }}
      >
        {prefix}{value}{suffix ? ` ${suffix}` : ""}
      </span>
      <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
        {label}
      </span>
    </div>
  );
}
