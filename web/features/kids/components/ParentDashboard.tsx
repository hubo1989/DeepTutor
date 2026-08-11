"use client";

import React from "react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WeeklyReport {
  levels_completed: number;
  xp_earned: number;
  avg_accuracy: number;
  active_days: number;
  streak_days: number;
}

export interface WeakTopic {
  level_id: string;
  map_id: string;
  title: string;
  score_pct: number;
}

export interface MapProgressItemData {
  map_id: string;
  title: string;
  icon: string;
  unlocked: boolean;
  completed: boolean;
  total_levels: number;
  cleared_levels: number;
}

export interface ParentDashboardProps {
  /** Child nickname. */
  nickname: string;
  /** Child avatar emoji. */
  avatar: string;
  /** Total XP. */
  totalXp: number;
  /** Current level (1-10). */
  level: number;
  /** Current streak in days. */
  streakDays: number;
  /** Earned badge IDs. */
  badges: string[];
  /** Weekly report data. */
  weeklyReport: WeeklyReport;
  /** Map progress items. */
  maps: MapProgressItemData[];
  /** Identified weak topics. */
  weakTopics: WeakTopic[];
  /** Recommended level IDs to review. */
  recommendations: string[];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function ParentDashboard({
  nickname,
  avatar,
  totalXp,
  level,
  streakDays,
  badges,
  weeklyReport,
  maps,
  weakTopics,
  recommendations,
}: ParentDashboardProps) {
  return (
    <div
      className="w-full max-w-4xl mx-auto space-y-6 p-4 sm:p-6"
      style={{
        backgroundColor: "var(--background)",
        color: "var(--foreground)",
        fontFamily: kidsTheme.fonts.body,
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-3">
        <span className="text-4xl">{avatar}</span>
        <div>
          <h2
            className="text-2xl font-bold"
            style={{ color: kidsTheme.colors.primary }}
          >
            {nickname}
          </h2>
          <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
            Lv.{level} · {totalXp} XP · 🔥 {streakDays} day streak
          </p>
        </div>
      </div>

      {/* Weekly Report Cards */}
      <section>
        <h3
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          7-Day Summary
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatCard
            icon="🏁"
            label="Levels Cleared"
            value={weeklyReport.levels_completed}
            color={kidsTheme.colors.primary}
          />
          <StatCard
            icon="⚡"
            label="XP Earned"
            value={weeklyReport.xp_earned}
            color={kidsTheme.colors.accent}
          />
          <StatCard
            icon="🎯"
            label="Avg Accuracy"
            value={`${Math.round(weeklyReport.avg_accuracy * 100)}%`}
            color={kidsTheme.colors.success || "#22c55e"}
          />
          <StatCard
            icon="📅"
            label="Active Days"
            value={weeklyReport.active_days}
            color={kidsTheme.colors.warning || "#f59e0b"}
          />
        </div>
      </section>

      {/* Map Progress Bars */}
      <section>
        <h3
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          Topic Progress
        </h3>
        <div className="space-y-3">
          {maps.length === 0 && (
            <p
              className="text-sm"
              style={{ color: "var(--muted-foreground)" }}
            >
              No topics started yet.
            </p>
          )}
          {maps.map((mp) => {
            const pct =
              mp.total_levels > 0
                ? (mp.cleared_levels / mp.total_levels) * 100
                : 0;
            return (
              <div key={mp.map_id}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-semibold">
                    {mp.icon} {mp.title}
                  </span>
                  <span
                    className="text-xs"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {mp.cleared_levels}/{mp.total_levels}
                    {mp.completed ? " ✓" : ""}
                  </span>
                </div>
                {/* Progress bar */}
                <div
                  className="w-full h-3 rounded-full overflow-hidden"
                  style={{ backgroundColor: "var(--muted)" }}
                >
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${pct}%`,
                      backgroundColor: mp.completed
                        ? kidsTheme.colors.success || "#22c55e"
                        : kidsTheme.colors.primary,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Accuracy Trend Bar Chart (CSS-based) */}
      <section>
        <h3
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          Accuracy Trend
        </h3>
        <AccuracyTrendChart maps={maps} weeklyReport={weeklyReport} />
      </section>

      {/* Weak Topics */}
      <section>
        <h3
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          Areas to Improve
        </h3>
        {weakTopics.length === 0 ? (
          <p
            className="text-sm"
            style={{ color: "var(--muted-foreground)" }}
          >
            No weak areas detected. Great job! 🎉
          </p>
        ) : (
          <div className="space-y-2">
            {weakTopics.map((wt, idx) => (
              <div
                key={`${wt.map_id}-${wt.level_id}-${idx}`}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{
                  backgroundColor: `${kidsTheme.colors.warning || "#f59e0b"}10`,
                  border: `1px solid ${kidsTheme.colors.warning || "#f59e0b"}30`,
                }}
              >
                <div>
                  <span className="text-sm font-semibold">{wt.title}</span>
                  <span
                    className="ml-2 text-xs"
                    style={{ color: "var(--muted-foreground)" }}
                  >
                    {wt.map_id}
                  </span>
                </div>
                <span
                  className="text-sm font-bold"
                  style={{
                    color:
                      wt.score_pct < 0.4
                        ? "#ef4444"
                        : kidsTheme.colors.warning || "#f59e0b",
                  }}
                >
                  {Math.round(wt.score_pct * 100)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <section>
          <h3
            className="text-lg font-bold mb-3"
            style={{ color: "var(--foreground)" }}
          >
            Recommended Review
          </h3>
          <div className="flex flex-wrap gap-2">
            {recommendations.map((rec, idx) => (
              <span
                key={`${rec}-${idx}`}
                className="px-3 py-1.5 rounded-full text-sm font-medium"
                style={{
                  backgroundColor: `${kidsTheme.colors.primary}15`,
                  color: kidsTheme.colors.primary,
                }}
              >
                🔄 {rec}
              </span>
            ))}
          </div>
        </section>
      )}

      {/* Badges Summary */}
      <section>
        <h3
          className="text-lg font-bold mb-3"
          style={{ color: "var(--foreground)" }}
        >
          Badges ({badges.length})
        </h3>
        <div className="flex flex-wrap gap-2">
          {badges.length === 0 ? (
            <span
              className="text-sm"
              style={{ color: "var(--muted-foreground)" }}
            >
              No badges yet.
            </span>
          ) : (
            badges.map((b, idx) => (
              <span
                key={`${b}-${idx}`}
                className="px-3 py-1 rounded-full text-xs font-semibold"
                style={{
                  backgroundColor: `${kidsTheme.colors.accent}15`,
                  color: kidsTheme.colors.accent,
                }}
              >
                🏅 {b}
              </span>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: string;
  label: string;
  value: string | number;
  color: string;
}) {
  return (
    <div
      className="flex flex-col items-center text-center p-4 rounded-xl"
      style={{
        backgroundColor: `${color}08`,
        border: `1px solid ${color}20`,
      }}
    >
      <span className="text-2xl mb-1">{icon}</span>
      <span
        className="text-xl font-bold"
        style={{ color }}
      >
        {value}
      </span>
      <span
        className="text-xs mt-0.5"
        style={{ color: "var(--muted-foreground)" }}
      >
        {label}
      </span>
    </div>
  );
}

function AccuracyTrendChart({
  maps,
  weeklyReport,
}: {
  maps: MapProgressItemData[];
  weeklyReport: WeeklyReport;
}) {
  // Build a simple bar chart from map-level accuracy proxy.
  // Each bar represents a topic's completion ratio as a visual proxy.
  if (maps.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
        Start a topic to see accuracy trends.
      </p>
    );
  }

  const maxBars = Math.min(maps.length, 8);
  const chartMaps = maps.slice(0, maxBars);

  return (
    <div
      className="flex items-end gap-2 h-32 p-3 rounded-lg"
      style={{ backgroundColor: "var(--muted)" }}
    >
      {chartMaps.map((mp, idx) => {
        const completionPct =
          mp.total_levels > 0
            ? (mp.cleared_levels / mp.total_levels) * 100
            : 0;
        return (
          <div
            key={`${mp.map_id}-${idx}`}
            className="flex-1 flex flex-col items-center justify-end h-full"
          >
            <div
              className="w-full rounded-t-md transition-all duration-500"
              style={{
                height: `${Math.max(completionPct, 4)}%`,
                backgroundColor: mp.completed
                  ? kidsTheme.colors.success || "#22c55e"
                  : kidsTheme.colors.primary,
                minHeight: "4px",
              }}
              title={`${mp.title}: ${Math.round(completionPct)}%`}
            />
            <span
              className="text-[0.5rem] mt-1 truncate w-full text-center"
              style={{ color: "var(--muted-foreground)" }}
            >
              {mp.icon}
            </span>
          </div>
        );
      })}
    </div>
  );
}
