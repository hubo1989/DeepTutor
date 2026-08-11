"use client";

import React, { useMemo } from "react";
import { Flame } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StreakCalendarProps {
  /** ISO date strings (YYYY-MM-DD) of days the child learned. */
  streakHistory: string[];
  /** Current consecutive day count. */
  streakDays: number;
  /** Number of weeks to show (default 4). */
  weeks?: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a Date to YYYY-MM-DD. */
function toIsoDate(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Get the Monday of the week containing the given date. */
function getWeekStart(d: Date): Date {
  const result = new Date(d);
  const day = result.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday as first day
  result.setDate(result.getDate() + diff);
  return result;
}

interface CalendarDay {
  date: Date;
  isoDate: string;
  isToday: boolean;
  learned: boolean;
  isFuture: boolean;
}

/** Build a flat array of days for the calendar grid. */
function buildCalendarDays(
  streakSet: Set<string>,
  weeks: number,
): CalendarDay[][] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayIso = toIsoDate(today);

  const weekStart = getWeekStart(today);
  // Go back (weeks - 1) weeks from the current week start
  const firstWeekStart = new Date(weekStart);
  firstWeekStart.setDate(firstWeekStart.getDate() - (weeks - 1) * 7);

  const result: CalendarDay[][] = [];
  for (let w = 0; w < weeks; w++) {
    const weekDays: CalendarDay[] = [];
    for (let d = 0; d < 7; d++) {
      const date = new Date(firstWeekStart);
      date.setDate(firstWeekStart.getDate() + w * 7 + d);
      const isoDate = toIsoDate(date);
      weekDays.push({
        date,
        isoDate,
        isToday: isoDate === todayIso,
        learned: streakSet.has(isoDate),
        isFuture: date > today,
      });
    }
    result.push(weekDays);
  }
  return result;
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function StreakCalendar({
  streakHistory,
  streakDays,
  weeks = 4,
}: StreakCalendarProps) {
  const streakSet = useMemo(() => new Set(streakHistory), [streakHistory]);
  const calendarWeeks = useMemo(
    () => buildCalendarDays(streakSet, weeks),
    [streakSet, weeks],
  );

  return (
    <div className="w-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Flame
            className="w-6 h-6 fill-current"
            style={{ color: kidsTheme.colors.streakFlame }}
          />
          <h3
            className="text-lg font-bold"
            style={{ color: "var(--foreground)" }}
          >
            Learning Calendar
          </h3>
        </div>
        <div
          className="flex items-center gap-1.5 px-3 py-1 rounded-full"
          style={{
            backgroundColor: `${kidsTheme.colors.streakFlame}15`,
          }}
        >
          <Flame
            className="w-4 h-4 fill-current"
            style={{ color: kidsTheme.colors.streakFlame }}
          />
          <span
            className="font-bold text-sm"
            style={{ color: kidsTheme.colors.streakFlame }}
          >
            {streakDays} days
          </span>
        </div>
      </div>

      {/* Weekday labels */}
      <div
        className="grid grid-cols-7 gap-1.5 mb-1.5"
      >
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="text-center text-xs font-semibold"
            style={{ color: "var(--muted-foreground)" }}
          >
            {label}
          </div>
        ))}
      </div>

      {/* Calendar grid */}
      <div className="space-y-1.5">
        {calendarWeeks.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7 gap-1.5">
            {week.map((day) => {
              const dayNum = day.date.getDate();
              return (
                <div
                  key={day.isoDate}
                  className="flex items-center justify-center text-sm font-medium transition-all"
                  style={{
                    aspectRatio: "1",
                    borderRadius: kidsTheme.borderRadius.chip,
                    backgroundColor: day.learned
                      ? kidsTheme.colors.streakFlame
                      : day.isToday
                        ? `${kidsTheme.colors.primary}15`
                        : "var(--muted)",
                    color: day.learned
                      ? "white"
                      : day.isFuture
                        ? "var(--muted-foreground)"
                        : "var(--foreground)",
                    border: day.isToday
                      ? `2px solid ${kidsTheme.colors.primary}`
                      : "2px solid transparent",
                    opacity: day.isFuture ? 0.3 : 1,
                  }}
                >
                  {day.learned ? (
                    <Flame className="w-4 h-4 fill-current" />
                  ) : (
                    dayNum
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
