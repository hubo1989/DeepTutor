"use client";

import React from "react";
import { Lock, Star, Crown, Play } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MapNodeStatus = "locked" | "available" | "in_progress" | "cleared";

export interface MapNodeProps {
  /** Display title of the level. */
  title: string;
  /** 0-based order within the map (controls vertical / path position). */
  order: number;
  /** Current status of the level. */
  status: MapNodeStatus;
  /** Stars earned (0-3). Only meaningful when status is "cleared". */
  stars: number;
  /** Whether this is a boss level. */
  isBoss: boolean;
  /** Click handler (called only when status !== "locked"). */
  onClick?: () => void;
  /** Horizontal offset for the zigzag path layout, in pixels. */
  offsetX?: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MapNode({
  title,
  order,
  status,
  stars,
  isBoss,
  onClick,
  offsetX = 0,
}: MapNodeProps) {
  const isLocked = status === "locked";
  const isCleared = status === "cleared";

  const handleClick = () => {
    if (!isLocked && onClick) {
      onClick();
    }
  };

  // Color scheme based on status
  const colorMap: Record<MapNodeStatus, { bg: string; border: string; text: string }> = {
    locked: {
      bg: "var(--muted)",
      border: kidsTheme.colors.locked,
      text: kidsTheme.colors.locked,
    },
    available: {
      bg: kidsTheme.colors.successBg,
      border: kidsTheme.colors.success,
      text: kidsTheme.colors.success,
    },
    in_progress: {
      bg: kidsTheme.colors.warningBg,
      border: kidsTheme.colors.warning,
      text: kidsTheme.colors.warning,
    },
    cleared: {
      bg: kidsTheme.colors.successBg,
      border: kidsTheme.colors.success,
      text: kidsTheme.colors.success,
    },
  };

  const colors = colorMap[status];
  const bossColor = isBoss ? kidsTheme.colors.boss : colors.border;

  return (
    <div
      className="relative flex flex-col items-center cursor-pointer transition-transform hover:scale-105"
      style={{
        marginLeft: `${offsetX}px`,
        opacity: isLocked ? 0.6 : 1,
      }}
      onClick={handleClick}
      role="button"
      aria-label={`${title}${isLocked ? " (locked)" : ""}`}
    >
      {/* Node circle */}
      <div
        className="flex items-center justify-center"
        style={{
          width: "4.5rem",
          height: "4.5rem",
          borderRadius: "50%",
          backgroundColor: colors.bg,
          border: `4px solid ${bossColor}`,
          boxShadow: isBoss
            ? `0 0 1rem ${kidsTheme.colors.boss}40`
            : `0 0.25rem 0.5rem rgba(0,0,0,0.1)`,
        }}
      >
        {isLocked ? (
          <Lock className="w-7 h-7" style={{ color: kidsTheme.colors.locked }} />
        ) : isBoss ? (
          <Crown className="w-7 h-7" style={{ color: kidsTheme.colors.boss }} />
        ) : isCleared ? (
          <Star className="w-7 h-7 fill-current" style={{ color: kidsTheme.colors.star }} />
        ) : (
          <Play className="w-7 h-7" style={{ color: colors.text }} />
        )}
      </div>

      {/* Stars row */}
      {isCleared && (
        <div className="flex gap-0.5 mt-1">
          {[0, 1, 2].map((i) => (
            <Star
              key={i}
              className="w-4 h-4"
              style={{
                color: i < stars ? kidsTheme.colors.star : "var(--muted-foreground)",
                fill: i < stars ? kidsTheme.colors.star : "transparent",
              }}
            />
          ))}
        </div>
      )}

      {/* Boss label */}
      {isBoss && (
        <span
          className="text-xs font-bold mt-1 px-2 py-0.5 rounded-full"
          style={{
            backgroundColor: `${kidsTheme.colors.boss}20`,
            color: kidsTheme.colors.boss,
          }}
        >
          BOSS
        </span>
      )}

      {/* Title */}
      <span
        className="text-sm font-semibold text-center mt-1 max-w-[7rem]"
        style={{ color: "var(--foreground)" }}
      >
        {title}
      </span>
    </div>
  );
}
