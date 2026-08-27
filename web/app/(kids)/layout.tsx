"use client";

import React, { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { kidsCssVars, kidsTheme } from "@/features/kids/theme/kidsTheme";
import { KidProfileProvider, useKidProfile } from "@/features/kids/hooks/useKidProfile";
import { KidsStoreProvider } from "@/features/kids/store/kidsStore";

// ---------------------------------------------------------------------------
// Inner layout (consumes context)
// ---------------------------------------------------------------------------

function KidsShell({ children }: { children: React.ReactNode }) {
  const { loading } = useKidProfile();
  const { t } = useTranslation();
  const [showReminder, setShowReminder] = useState(false);
  const [sessionStart] = useState(() => Date.now());

  // Session limit check — remind 5 min before the 20-minute limit
  useEffect(() => {
    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - sessionStart) / 1000);
      const remaining = kidsTheme.sessionLimitSeconds - elapsed;
      if (remaining <= kidsTheme.sessionReminderSeconds && remaining > 0) {
        setShowReminder(true);
      }
    }, 30000); // check every 30s

    return () => clearInterval(interval);
  }, [sessionStart]);

  // Inject kids CSS variables on the root wrapper
  const cssVars = useMemo(() => ({ ...kidsCssVars } as React.CSSProperties), []);

  if (loading) {
    return (
      <div
        className="flex items-center justify-center min-h-screen"
        style={{ backgroundColor: "var(--background)" }}
      >
        <div className="text-center">
          <div
            className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-t-transparent mb-4"
            style={{ borderColor: `${kidsTheme.colors.primary} transparent transparent transparent` }}
          />
          <p style={{ color: "var(--muted-foreground)" }}>{t("kids.loading")}</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="min-h-screen"
      style={{
        ...cssVars,
        backgroundColor: "var(--background)",
        // Kids-specific overrides: larger base font, rounded everything
        fontFamily: "inherit",
      }}
    >
      {/* Session reminder banner */}
      {showReminder && (
        <div
          className="fixed top-0 left-0 right-0 z-50 px-4 py-3 text-center font-semibold"
          style={{
            backgroundColor: kidsTheme.colors.warning,
            color: "white",
            borderRadius: "0 0 1rem 1rem",
          }}
        >
          {t("kids.fiveMinLeft")}
        </div>
      )}

      {/* Restricted features are hidden: no payment, share, or settings nav.
          The kids layout intentionally omits sidebar, header menus, etc. */}
      <main className="max-w-3xl mx-auto px-4 py-6 pb-24">{children}</main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Layout wrapper
// ---------------------------------------------------------------------------

export default function KidsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <KidProfileProvider>
      <KidsStoreProvider>
        <KidsShell>{children}</KidsShell>
      </KidsStoreProvider>
    </KidProfileProvider>
  );
}
