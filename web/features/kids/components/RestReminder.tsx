"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RestReminderProps {
  /** Session duration in minutes before the reminder shows (default: 20). */
  durationMinutes?: number;
  /** Callback when the child dismisses the reminder. */
  onDismiss?: () => void;
  /** Callback when the child takes a break (closes the session). */
  onTakeBreak?: () => void;
  /** Whether the timer should be running. */
  isActive?: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_DURATION_MINUTES = 20;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function RestReminder({
  durationMinutes = DEFAULT_DURATION_MINUTES,
  onDismiss,
  onTakeBreak,
  isActive = true,
}: RestReminderProps) {
  const totalSeconds = durationMinutes * 60;
  const [secondsLeft, setSecondsLeft] = useState<number>(totalSeconds);
  const [showPopup, setShowPopup] = useState<boolean>(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Reset the timer when the duration changes. Adjusting state during render
  // (compared against the previous value) is the sanctioned React pattern;
  // doing it from an effect body would cascade renders.
  const [prevDuration, setPrevDuration] = useState(durationMinutes);
  if (prevDuration !== durationMinutes) {
    setPrevDuration(durationMinutes);
    setSecondsLeft(durationMinutes * 60);
    setShowPopup(false);
  }

  // Countdown timer
  useEffect(() => {
    if (!isActive || showPopup) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    intervalRef.current = setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev <= 1) {
          // Time's up — show the popup
          setShowPopup(true);
          if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isActive, showPopup]);

  const handleDismiss = useCallback(() => {
    setShowPopup(false);
    // Reset timer for another cycle
    setSecondsLeft(durationMinutes * 60);
    onDismiss?.();
  }, [durationMinutes, onDismiss]);

  const handleTakeBreak = useCallback(() => {
    setShowPopup(false);
    onTakeBreak?.();
  }, [onTakeBreak]);

  // Format remaining time as MM:SS
  const formatTime = (totalSec: number): string => {
    const minutes = Math.floor(totalSec / 60);
    const seconds = totalSec % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  };

  // Progress ratio (0 to 1)
  const progressRatio = totalSeconds > 0 ? secondsLeft / totalSeconds : 0;

  return (
    <>
      {/* Compact countdown indicator (always visible when active) */}
      {!showPopup && isActive && (
        <div
          className="fixed bottom-4 right-4 flex items-center gap-2 px-3 py-2 rounded-full shadow-lg z-40"
          style={{
            backgroundColor: "var(--background)",
            border: `1px solid var(--border)`,
          }}
        >
          <span
            className="text-xs font-mono font-bold"
            style={{
              color:
                progressRatio < 0.2
                  ? kidsTheme.colors.warning || "#f59e0b"
                  : "var(--muted-foreground)",
            }}
          >
            ⏱ {formatTime(secondsLeft)}
          </span>
        </div>
      )}

      {/* Rest reminder popup */}
      {showPopup && (
        <div
          className="fixed inset-0 flex items-center justify-center z-50"
          style={{ backgroundColor: "rgba(0, 0, 0, 0.4)" }}
        >
          <div
            className="flex flex-col items-center text-center p-8 rounded-3xl max-w-sm mx-4"
            style={{
              backgroundColor: "var(--background)",
              boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
            }}
          >
            {/* Animated emoji */}
            <div
              className="text-6xl mb-4"
              style={{
                animation: "bounce 1s ease-in-out infinite",
              }}
            >
              🧘
            </div>

            {/* Title */}
            <h2
              className="text-2xl font-bold mb-2"
              style={{ color: kidsTheme.colors.primary }}
            >
              Time for a Break!
            </h2>

            {/* Message */}
            <p
              className="text-base mb-6 leading-relaxed"
              style={{ color: "var(--foreground)" }}
            >
              You&apos;ve been learning for {durationMinutes} minutes. Rest your
              eyes, stretch, and come back refreshed! 🌟
            </p>

            {/* Buttons */}
            <div className="flex flex-col gap-3 w-full">
              <button
                onClick={handleTakeBreak}
                className="w-full py-3 rounded-2xl text-white font-bold text-base transition-all hover:scale-[1.02] active:scale-[0.98]"
                style={{
                  backgroundColor: kidsTheme.colors.primary,
                }}
              >
                Take a Break 😊
              </button>
              <button
                onClick={handleDismiss}
                className="w-full py-3 rounded-2xl font-semibold text-sm transition-all hover:scale-[1.02] active:scale-[0.98]"
                style={{
                  backgroundColor: "var(--muted)",
                  color: "var(--muted-foreground)",
                }}
              >
                Keep Going (5 more minutes)
              </button>
            </div>
          </div>

          {/* Inline keyframes for bounce animation */}
          <style>{`
            @keyframes bounce {
              0%, 100% { transform: translateY(0); }
              50% { transform: translateY(-10px); }
            }
          `}</style>
        </div>
      )}
    </>
  );
}
