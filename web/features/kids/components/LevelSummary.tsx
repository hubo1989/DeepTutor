"use client";

import React from "react";
import { Star, Zap, Award, ArrowRight, RotateCcw } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LevelSummaryProps {
  /** Stars earned (0-3). */
  stars: number;
  /** XP earned this level. */
  xpEarned: number;
  /** Correct / total ratio. */
  correctCount: number;
  totalCount: number;
  /** New badge IDs earned. */
  newBadges: string[];
  /** Called when the child chooses to continue. */
  onContinue?: () => void;
  /** Called when the child chooses to replay. */
  onRetry?: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function LevelSummary({
  stars,
  xpEarned,
  correctCount,
  totalCount,
  newBadges,
  onContinue,
  onRetry,
}: LevelSummaryProps) {
  const pct = totalCount > 0 ? Math.round((correctCount / totalCount) * 100) : 0;

  return (
    <div
      className="flex flex-col items-center p-8 text-center"
      style={{
        backgroundColor: "var(--card)",
        borderRadius: kidsTheme.borderRadius.card,
        border: "1px solid var(--border)",
      }}
    >
      {/* Title */}
      <h2
        className="text-3xl font-extrabold mb-2"
        style={{ color: kidsTheme.colors.primary }}
      >
        Level Complete!
      </h2>
      <p
        className="text-base mb-6"
        style={{ color: "var(--muted-foreground)" }}
      >
        {pct}% correct - {correctCount}/{totalCount}
      </p>

      {/* Stars */}
      <div className="flex gap-2 mb-6">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="transition-transform"
            style={{
              transform: i < stars ? "scale(1)" : "scale(0.7)",
              opacity: i < stars ? 1 : 0.3,
              transitionDelay: `${i * 150}ms`,
            }}
          >
            <Star
              className="w-14 h-14"
              strokeWidth={2}
              style={{
                color: i < stars ? kidsTheme.colors.star : "var(--muted-foreground)",
                fill: i < stars ? kidsTheme.colors.star : "transparent",
              }}
            />
          </div>
        ))}
      </div>

      {/* XP */}
      <div
        className="flex items-center gap-2 px-6 py-3 mb-6"
        style={{
          backgroundColor: `${kidsTheme.colors.xpBar}15`,
          borderRadius: kidsTheme.borderRadius.button,
        }}
      >
        <Zap
          className="w-6 h-6 fill-current"
          style={{ color: kidsTheme.colors.xpBar }}
        />
        <span
          className="text-2xl font-extrabold"
          style={{ color: kidsTheme.colors.xpBar }}
        >
          +{xpEarned} XP
        </span>
      </div>

      {/* New badges */}
      {newBadges.length > 0 && (
        <div className="mb-6 w-full">
          <div
            className="flex items-center gap-2 justify-center mb-3"
          >
            <Award
              className="w-6 h-6"
              style={{ color: kidsTheme.colors.boss }}
            />
            <span
              className="font-bold text-lg"
              style={{ color: kidsTheme.colors.boss }}
            >
              New Badges!
            </span>
          </div>
          <div className="flex gap-3 justify-center flex-wrap">
            {newBadges.map((badgeId) => (
              <BadgeChip key={badgeId} badgeId={badgeId} />
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-4 w-full">
        {onRetry && (
          <button
            className="flex-1 flex items-center justify-center gap-2 py-4 font-bold transition-opacity hover:opacity-80"
            style={{
              backgroundColor: "var(--secondary)",
              color: "var(--secondary-foreground)",
              borderRadius: kidsTheme.borderRadius.button,
              fontSize: kidsTheme.typography.buttonSize,
              minHeight: kidsTheme.sizing.buttonMinHeight,
            }}
            onClick={onRetry}
          >
            <RotateCcw className="w-5 h-5" />
            Play Again
          </button>
        )}
        {onContinue && (
          <button
            className="flex-1 flex items-center justify-center gap-2 py-4 font-bold text-white transition-opacity hover:opacity-90"
            style={{
              backgroundColor: kidsTheme.colors.primary,
              borderRadius: kidsTheme.borderRadius.button,
              fontSize: kidsTheme.typography.buttonSize,
              minHeight: kidsTheme.sizing.buttonMinHeight,
            }}
            onClick={onContinue}
          >
            Continue
            <ArrowRight className="w-5 h-5" />
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Badge chip sub-component
// ---------------------------------------------------------------------------

function BadgeChip({ badgeId }: { badgeId: string }) {
  const icon = BADGE_ICONS[badgeId] ?? "🏆";
  const label = BADGE_LABELS[badgeId] ?? badgeId;

  return (
    <div
      className="flex flex-col items-center gap-1 px-4 py-3"
      style={{
        backgroundColor: `${kidsTheme.colors.boss}10`,
        borderRadius: kidsTheme.borderRadius.chip,
        border: `2px solid ${kidsTheme.colors.boss}40`,
        minWidth: "6rem",
      }}
    >
      <span className="text-3xl">{icon}</span>
      <span
        className="text-xs font-semibold"
        style={{ color: kidsTheme.colors.boss }}
      >
        {label}
      </span>
    </div>
  );
}

const BADGE_ICONS: Record<string, string> = {
  first_clear: "🌟",
  combo_5: "🔥",
  streak_7: "📅",
  all_stars: "⭐",
  level_5: "🎖️",
  daily_goal: "🎯",
};

const BADGE_LABELS: Record<string, string> = {
  first_clear: "First Steps",
  combo_5: "On Fire!",
  streak_7: "Week Warrior",
  all_stars: "Perfectionist",
  level_5: "Rising Star",
  daily_goal: "Daily Champion",
};
