"use client";

import React from "react";
import { Lock } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BadgeGridProps {
  /** Badge IDs the child has earned. */
  earnedBadges: string[];
}

// ---------------------------------------------------------------------------
// All badge definitions (mirrors badges.py BADGE_RULES)
// ---------------------------------------------------------------------------

interface BadgeDef {
  badge_id: string;
  name: string;
  description: string;
  icon: string;
}

const ALL_BADGES: BadgeDef[] = [
  {
    badge_id: "first_clear",
    name: "First Steps",
    description: "Clear your very first level!",
    icon: "🌟",
  },
  {
    badge_id: "combo_5",
    name: "On Fire!",
    description: "Get 5 correct answers in a row.",
    icon: "🔥",
  },
  {
    badge_id: "streak_7",
    name: "Week Warrior",
    description: "Learn 7 days in a row.",
    icon: "📅",
  },
  {
    badge_id: "all_stars",
    name: "Perfectionist",
    description: "Get 3 stars on every level in a map.",
    icon: "⭐",
  },
  {
    badge_id: "level_5",
    name: "Rising Star",
    description: "Reach Level 5.",
    icon: "🎖️",
  },
  {
    badge_id: "daily_goal",
    name: "Daily Champion",
    description: "Meet your daily XP goal.",
    icon: "🎯",
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function BadgeGrid({ earnedBadges }: BadgeGridProps) {
  const earnedSet = new Set(earnedBadges);
  const earnedCount = ALL_BADGES.filter((b) => earnedSet.has(b.badge_id)).length;

  return (
    <div className="w-full">
      {/* Summary */}
      <div className="flex items-center justify-between mb-4">
        <h3
          className="text-lg font-bold"
          style={{ color: "var(--foreground)" }}
        >
          Badges
        </h3>
        <span
          className="text-sm font-semibold px-3 py-1 rounded-full"
          style={{
            backgroundColor: `${kidsTheme.colors.primary}15`,
            color: kidsTheme.colors.primary,
          }}
        >
          {earnedCount} / {ALL_BADGES.length}
        </span>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-6">
        {ALL_BADGES.map((badge) => {
          const earned = earnedSet.has(badge.badge_id);
          return (
            <div
              key={badge.badge_id}
              className="flex flex-col items-center text-center p-3 transition-all"
              style={{
                backgroundColor: earned
                  ? `${kidsTheme.colors.primary}10`
                  : "var(--muted)",
                borderRadius: kidsTheme.borderRadius.card,
                border: `2px solid ${
                  earned
                    ? `${kidsTheme.colors.primary}40`
                    : "var(--border)"
                }`,
                opacity: earned ? 1 : 0.5,
              }}
            >
              {/* Icon */}
              <div className="text-3xl mb-1">
                {earned ? badge.icon : <Lock className="w-7 h-7" style={{ color: "var(--muted-foreground)" }} />}
              </div>

              {/* Name */}
              <span
                className="text-xs font-bold mb-0.5"
                style={{
                  color: earned ? kidsTheme.colors.primary : "var(--muted-foreground)",
                }}
              >
                {badge.name}
              </span>

              {/* Description */}
              <span
                className="text-[0.625rem] leading-tight"
                style={{ color: "var(--muted-foreground)" }}
              >
                {badge.description}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
