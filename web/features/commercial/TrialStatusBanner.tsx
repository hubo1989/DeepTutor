"use client";

import { Clock3 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { trialCountdown, type TrialCountdown } from "./access";
import { useCommercialAccess } from "./CommercialAccessContext";

function countdownLabel(
  countdown: TrialCountdown | null,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!countdown) return t("commercial.trial.active");
  if (countdown.expired) return t("commercial.trial.endingNow");
  if (countdown.days > 0) {
    return t("commercial.trial.remainingDaysHours", {
      days: countdown.days,
      hours: countdown.hours,
    });
  }
  if (countdown.hours > 0) {
    return t("commercial.trial.remainingHoursMinutes", {
      hours: countdown.hours,
      minutes: countdown.minutes,
    });
  }
  return t("commercial.trial.remainingMinutes", {
    minutes: Math.max(1, countdown.minutes),
  });
}
export function TrialStatusBanner() {
  const { snapshot } = useCommercialAccess();
  const { t } = useTranslation();
  const [, tick] = useState(0);

  useEffect(() => {
    if (snapshot?.status !== "trialing") return;
    const timer = window.setInterval(() => tick((value) => value + 1), 30_000);
    return () => window.clearInterval(timer);
  }, [snapshot?.status]);

  if (
    !snapshot?.enabled ||
    snapshot.isAdmin ||
    snapshot.status !== "trialing"
  ) {
    return null;
  }

  const label = countdownLabel(trialCountdown(snapshot.validUntil), t);
  return (
    <div
      role="status"
      className="flex shrink-0 items-center justify-center gap-2 border-b border-amber-500/25 bg-amber-500/10 px-4 py-2 text-center text-xs text-amber-900 dark:text-amber-100"
    >
      <Clock3 aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">{t("commercial.trial.label")}</span>
      <span aria-hidden="true">·</span>
      <span>{label}</span>
    </div>
  );
}
