"use client";

import React, { useEffect, useState } from "react";
import { Check, X, Zap } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FeedbackType = "correct" | "wrong" | "combo" | null;

export interface FeedbackOverlayProps {
  /** The type of feedback to show. ``null`` hides the overlay. */
  type: FeedbackType;
  /** Optional combo count (shown when type === "combo"). */
  comboCount?: number;
  /** Called when the animation completes. */
  onComplete?: () => void;
  /** Auto-dismiss duration in ms. Default 1200. */
  duration?: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FeedbackOverlay({
  type,
  comboCount = 0,
  onComplete,
  duration = 1200,
}: FeedbackOverlayProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (type === null) {
      setVisible(false);
      return;
    }

    setVisible(true);
    const timer = setTimeout(() => {
      setVisible(false);
      onComplete?.();
    }, duration);

    return () => clearTimeout(timer);
  }, [type, duration, onComplete]);

  if (type === null) return null;

  const config = getFeedbackConfig(type, comboCount);

  return (
    <div
      className="fixed inset-0 flex items-center justify-center pointer-events-none z-50"
      style={{
        opacity: visible ? 1 : 0,
        transition: `opacity ${kidsTheme.animation.overlayDuration}`,
      }}
    >
      <div
        className="flex flex-col items-center justify-center"
        style={{
          padding: "2rem 3rem",
          borderRadius: kidsTheme.borderRadius.overlay,
          backgroundColor: config.bgColor,
          boxShadow: `0 0 3rem ${config.glowColor}`,
          transform: visible ? `scale(${kidsTheme.animation.bounceScale})` : "scale(0.8)",
          transition: `transform ${kidsTheme.animation.feedbackDuration} cubic-bezier(0.34, 1.56, 0.64, 1)`,
        }}
      >
        {/* Icon */}
        <div style={{ marginBottom: "0.5rem" }}>
          {config.icon}
        </div>

        {/* Text */}
        <span
          className="text-2xl font-extrabold"
          style={{ color: config.textColor }}
        >
          {config.text}
        </span>

        {/* Combo indicator */}
        {type === "combo" && comboCount > 0 && (
          <div className="flex gap-1 mt-2">
            {Array.from({ length: Math.min(comboCount, 5) }).map((_, i) => (
              <Zap
                key={i}
                className="w-5 h-5 fill-current"
                style={{ color: kidsTheme.colors.warning }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Config helper
// ---------------------------------------------------------------------------

function getFeedbackConfig(
  type: FeedbackType,
  comboCount: number,
): {
  bgColor: string;
  textColor: string;
  glowColor: string;
  text: string;
  icon: React.ReactNode;
} {
  switch (type) {
    case "correct":
      return {
        bgColor: kidsTheme.colors.successBg,
        textColor: kidsTheme.colors.success,
        glowColor: `${kidsTheme.colors.success}40`,
        text: "Correct!",
        icon: (
          <Check
            className="w-16 h-16"
            strokeWidth={3}
            style={{ color: kidsTheme.colors.success }}
          />
        ),
      };

    case "wrong":
      return {
        bgColor: kidsTheme.colors.dangerBg,
        textColor: kidsTheme.colors.danger,
        glowColor: `${kidsTheme.colors.danger}40`,
        text: "Try Again",
        icon: (
          <X
            className="w-16 h-16"
            strokeWidth={3}
            style={{ color: kidsTheme.colors.danger }}
          />
        ),
      };

    case "combo":
      return {
        bgColor: kidsTheme.colors.warningBg,
        textColor: kidsTheme.colors.warning,
        glowColor: `${kidsTheme.colors.warning}40`,
        text: `Combo x${comboCount}!`,
        icon: (
          <Zap
            className="w-16 h-16 fill-current"
            style={{ color: kidsTheme.colors.warning }}
          />
        ),
      };

    default:
      return {
        bgColor: "transparent",
        textColor: "transparent",
        glowColor: "transparent",
        text: "",
        icon: null,
      };
  }
}
