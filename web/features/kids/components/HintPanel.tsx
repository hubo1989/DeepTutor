"use client";

import React from "react";
import { Lightbulb, ChevronRight, X } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HintPanelProps {
  /** Whether the panel is visible. */
  visible: boolean;
  /** Current hint stage (1 = encourage, 2 = guide, 3 = explain). */
  stage: number;
  /** Hint texts indexed by stage (0-based: [encourage, guide, explain]). */
  hints: string[];
  /** Called when the child requests the next hint stage. */
  onNextHint?: () => void;
  /** Called when the panel is dismissed. */
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Stage metadata
// ---------------------------------------------------------------------------

const STAGE_META: Array<{ label: string; color: string; bgColor: string }> = [
  {
    label: "Encourage",
    color: kidsTheme.colors.warning,
    bgColor: kidsTheme.colors.warningBg,
  },
  {
    label: "Hint",
    color: kidsTheme.colors.primary,
    bgColor: `${kidsTheme.colors.primary}10`,
  },
  {
    label: "Explain",
    color: kidsTheme.colors.success,
    bgColor: kidsTheme.colors.successBg,
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function HintPanel({
  visible,
  stage,
  hints,
  onNextHint,
  onClose,
}: HintPanelProps) {
  if (!visible) return null;

  // Show hints up to the current stage (1-based → 0-based indexing)
  const visibleHints = hints.slice(0, Math.min(stage, hints.length, 3));
  const canAdvance = stage < 3 && stage < hints.length;

  return (
    <div
      className="mt-4 p-4 transition-all"
      style={{
        backgroundColor: "var(--card)",
        borderRadius: kidsTheme.borderRadius.card,
        border: `2px solid ${kidsTheme.colors.warning}40`,
      }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Lightbulb
            className="w-5 h-5"
            style={{ color: kidsTheme.colors.warning }}
          />
          <span
            className="font-bold text-base"
            style={{ color: kidsTheme.colors.warning }}
          >
            Hints
          </span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1 rounded-full hover:bg-[var(--muted)]"
            aria-label="Close hints"
          >
            <X className="w-4 h-4" style={{ color: "var(--muted-foreground)" }} />
          </button>
        )}
      </div>

      {/* Hint stages */}
      <div className="space-y-3">
        {visibleHints.map((hint, idx) => {
          const meta = STAGE_META[idx] ?? STAGE_META[0];
          return (
            <div
              key={idx}
              className="p-3"
              style={{
                backgroundColor: meta.bgColor,
                borderRadius: kidsTheme.borderRadius.chip,
                borderLeft: `4px solid ${meta.color}`,
              }}
            >
              <div className="flex items-center gap-2 mb-1">
                <span
                  className="flex items-center justify-center text-xs font-bold"
                  style={{
                    width: "1.5rem",
                    height: "1.5rem",
                    borderRadius: "50%",
                    backgroundColor: meta.color,
                    color: "white",
                  }}
                >
                  {idx + 1}
                </span>
                <span
                  className="text-xs font-semibold uppercase"
                  style={{ color: meta.color }}
                >
                  {meta.label}
                </span>
              </div>
              <p
                className="text-sm"
                style={{ color: "var(--foreground)", lineHeight: 1.5 }}
              >
                {hint}
              </p>
            </div>
          );
        })}
      </div>

      {/* Advance button */}
      {canAdvance && onNextHint && (
        <button
          className="w-full mt-3 flex items-center justify-center gap-2 py-3 font-semibold transition-opacity hover:opacity-80"
          style={{
            backgroundColor: kidsTheme.colors.warning,
            color: "white",
            borderRadius: kidsTheme.borderRadius.button,
            fontSize: kidsTheme.typography.buttonSize,
          }}
          onClick={onNextHint}
        >
          Show Next Hint
          <ChevronRight className="w-5 h-5" />
        </button>
      )}

      {/* Stage indicator */}
      <div className="flex gap-1 mt-3 justify-center">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              width: i < stage ? "1.5rem" : "0.5rem",
              height: "0.25rem",
              borderRadius: "0.125rem",
              backgroundColor:
                i < stage ? kidsTheme.colors.warning : "var(--muted)",
              transition: "width 0.3s",
            }}
          />
        ))}
      </div>
    </div>
  );
}
