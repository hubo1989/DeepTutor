"use client";

import React, { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { ChevronLeft } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";
import MapNode, { type MapNodeStatus } from "@/features/kids/components/MapNode";
import { generateMap, getMaps } from "@/lib/kids-api";
import type {
  LevelSummaryModel,
  MapProgressItem,
  QuestMapResponse,
} from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MapPage() {
  const router = useRouter();
  const { t } = useTranslation();
  const params = useParams<{ mapId: string }>();
  const mapId = params.mapId;

  const [mapData, setMapData] = useState<QuestMapResponse | null>(null);
  const [progress, setProgress] = useState<MapProgressItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadMap() {
      setLoading(true);
      setError(null);
      try {
        // Generate the map from the theme ID
        const map = await generateMap({
          source: "public",
          theme_id: mapId,
          age_band: "7-9",
        });
        if (!cancelled) {
          setMapData(map);

          // Check if there's existing progress for this map
          try {
            const maps = await getMaps();
            const existing = maps.find(
              (m) => m.map_id === `public:${mapId}`,
            );
            if (existing) setProgress(existing);
          } catch {
            // No progress yet — that's fine
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t("kids.failedToLoadMap"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    if (mapId) loadMap();
    return () => {
      cancelled = true;
    };
  }, [mapId, t]);

  const handleLevelClick = useCallback(
    (level: LevelSummaryModel) => {
      router.push(`/play/${level.level_id}`);
    },
    [router],
  );

  const getLevelStatus = useCallback(
    (level: LevelSummaryModel, index: number): MapNodeStatus => {
      // First level is always available
      if (index === 0) return "available";

      // If progress exists, check cleared levels
      if (progress) {
        // Simple heuristic: if previous levels are cleared, this is available
        // In full implementation, this reads from LevelProgress
        const clearedCount = progress.cleared_levels;
        if (index < clearedCount) return "cleared";
        if (index === clearedCount) return "available";
      }

      return "locked";
    },
    [progress],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <div
          className="inline-block animate-spin rounded-full h-10 w-10 border-4 border-t-transparent"
          style={{
            borderColor: `${kidsTheme.colors.primary} transparent transparent transparent`,
          }}
        />
      </div>
    );
  }

  if (error || !mapData) {
    return (
      <div className="text-center py-12">
        <p style={{ color: kidsTheme.colors.danger }}>
          {error ?? t("kids.mapNotFound")}
        </p>
        <button
          className="mt-4 px-6 py-3 font-bold text-white"
          style={{
            backgroundColor: kidsTheme.colors.primary,
            borderRadius: kidsTheme.borderRadius.button,
          }}
          onClick={() => router.push("/kids-home")}
        >
          {t("kids.backToHome")}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.push("/kids-home")}
          className="p-2 rounded-full hover:bg-[var(--muted)]"
          aria-label={t("kids.back")}
        >
          <ChevronLeft className="w-6 h-6" style={{ color: "var(--foreground)" }} />
        </button>
        <span className="text-4xl">{mapData.icon}</span>
        <div>
          <h1
            className="text-xl font-extrabold"
            style={{ color: "var(--foreground)" }}
          >
            {mapData.title}
          </h1>
          {mapData.description && (
            <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
              {mapData.description}
            </p>
          )}
        </div>
      </div>

      {/* Map nodes — vertical zigzag path */}
      <div className="flex flex-col items-center gap-6 py-8">
        {mapData.levels.map((level, idx) => {
          const status = getLevelStatus(level, idx);
          // Zigzag offset: alternate left/right
          const offsetX = idx % 2 === 0 ? -40 : 40;

          return (
            <div key={level.level_id} className="relative">
              {/* SVG path connector */}
              {idx > 0 && (
                <svg
                  className="absolute -top-6 left-1/2 -translate-x-1/2 pointer-events-none"
                  width="4"
                  height="24"
                  style={{ overflow: "visible" }}
                >
                  <path
                    d={`M 2 0 Q ${idx % 2 === 0 ? 20 : -20} 12 2 24`}
                    stroke={status !== "locked" ? kidsTheme.colors.primary : "var(--muted)"}
                    strokeWidth="3"
                    fill="none"
                    strokeDasharray={status === "cleared" ? "none" : "6 4"}
                    strokeLinecap="round"
                  />
                </svg>
              )}
              <MapNode
                title={level.title}
                order={idx}
                status={status}
                stars={status === "cleared" ? 3 : 0}
                isBoss={level.is_boss}
                onClick={() => handleLevelClick(level)}
                offsetX={offsetX}
              />
            </div>
          );
        })}
      </div>

      {/* Progress summary */}
      {progress && (
        <div
          className="flex items-center justify-between p-4"
          style={{
            backgroundColor: "var(--card)",
            borderRadius: kidsTheme.borderRadius.card,
            border: "1px solid var(--border)",
          }}
        >
          <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
            {t("kids.progress")}
          </span>
          <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>
            {t("kids.levelsClearedFull", { cleared: progress.cleared_levels, total: progress.total_levels })}
          </span>
        </div>
      )}
    </div>
  );
}
