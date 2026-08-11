/**
 * Kids Gamified Learning — Frontend Type Definitions
 *
 * These types mirror the backend dataclasses in
 * ``deeptutor/services/gamification/models.py`` and the API response
 * models in ``deeptutor/api/routers/kids.py``.
 */

// ---------------------------------------------------------------------------
// Core domain types (aligned with models.py)
// ---------------------------------------------------------------------------

/** Question types supported by the forging pipeline. */
export type QuestionType =
  | "single_choice"
  | "image_choice"
  | "true_false"
  | "fill_blank"
  | "matching"
  | "ordering"
  | "error_correction"
  | "boss_comprehensive";

/** A single question within a level. */
export interface Question {
  question_id: string;
  text: string;
  question_type: QuestionType;
  options: string[];
  correct_answer: string;
  explanation: string;
  points: number;
  hints: string[];
  acceptable_answers: string[];
  correct_answer_ids: string[];
  matching_pairs: [string, string][];
  ordering_sequence: string[];
  sub_questions: Record<string, unknown>[];
}

/** A level within a quest map. */
export interface Level {
  level_id: string;
  title: string;
  description: string;
  order: number;
  is_boss: boolean;
  questions: Question[];
  unlock_dependency: string;
}

/** A themed collection of levels forming a learning path. */
export interface QuestMap {
  map_id: string;
  title: string;
  description: string;
  theme: string;
  icon: string;
  order: number;
  levels: Level[];
  unlock_dependency: string;
}

// ---------------------------------------------------------------------------
// Progress types (aligned with store.py / models.py)
// ---------------------------------------------------------------------------

/** Tracks a child's attempt state on a single level. */
export interface LevelProgress {
  level_id: string;
  stars: number;
  best_correct_pct: number;
  attempts: number;
  cleared: boolean;
  last_played_at: string;
}

/** Tracks a child's progress on a single quest map. */
export interface MapProgress {
  map_id: string;
  levels: Record<string, LevelProgress>;
  unlocked: boolean;
  completed: boolean;
}

/** The complete gamification state for one child profile. */
export interface ProgressState {
  profile_id: string;
  total_xp: number;
  level: number;
  daily_xp: number;
  daily_xp_date: string;
  streak_days: number;
  streak_history: string[];
  maps: Record<string, MapProgress>;
  badges: string[];
  max_combo: number;
  daily_goal_xp: number;
  daily_goal_met_dates: string[];
  created_at: string;
  updated_at: string;
  schema_version: number;
}

// ---------------------------------------------------------------------------
// API response types (aligned with kids.py Pydantic models)
// ---------------------------------------------------------------------------

/** Public theme summary for card display. */
export interface ThemeSummary {
  theme_id: string;
  title_zh: string;
  title_en: string;
  description_zh: string;
  description_en: string;
  icon: string;
  age_band: string;
  level_count: number;
}

/** Request body for map generation. */
export interface GenerateMapRequest {
  source: "public" | "personal";
  theme_id?: string;
  kb_name?: string;
  age_band: string;
}

/** A level's summary within a generated map. */
export interface LevelSummaryModel {
  level_id: string;
  title: string;
  order: number;
  is_boss: boolean;
}

/** Response for a generated QuestMap. */
export interface QuestMapResponse {
  map_id: string;
  title: string;
  description: string;
  theme: string;
  icon: string;
  levels: LevelSummaryModel[];
}

/** One map's progress entry for the maps list. */
export interface MapProgressItem {
  map_id: string;
  title: string;
  icon: string;
  unlocked: boolean;
  completed: boolean;
  total_levels: number;
  cleared_levels: number;
}

/** Guardian-facing progress panel payload. */
export interface ProgressResponse {
  profile_id: string;
  nickname: string;
  avatar: string;
  age_band: string;
  total_xp: number;
  level: number;
  daily_xp: number;
  daily_goal_xp: number;
  streak_days: number;
  max_combo: number;
  badges: string[];
  maps: MapProgressItem[];
}

// ---------------------------------------------------------------------------
// Badge types (aligned with badges.py)
// ---------------------------------------------------------------------------

export interface BadgeRule {
  badge_id: string;
  name: string;
  description: string;
  icon: string;
}

// ---------------------------------------------------------------------------
// Engine constants (aligned with engine.py)
// ---------------------------------------------------------------------------

export const LEVEL_THRESHOLDS: number[] = [
  0, 50, 120, 220, 350, 520, 730, 990, 1300, 1680,
];
export const MAX_LEVEL: number = 10;
export const DEFAULT_DAILY_XP_CAP: number = 200;

/** Compute the child's level (1-10) from total XP. */
export function computeLevel(totalXp: number): number {
  if (totalXp < 0) return 1;
  for (let i = LEVEL_THRESHOLDS.length - 1; i >= 0; i--) {
    if (totalXp >= LEVEL_THRESHOLDS[i]) {
      return Math.min(i + 1, MAX_LEVEL);
    }
  }
  return 1;
}

/** Return progress ratio (0.0-1.0) toward the next level. */
export function progressToNextLevel(totalXp: number): number {
  const level = computeLevel(totalXp);
  if (level >= MAX_LEVEL) return 1.0;
  const currentThreshold = LEVEL_THRESHOLDS[level - 1];
  const nextThreshold = LEVEL_THRESHOLDS[level];
  if (nextThreshold === currentThreshold) return 1.0;
  return (totalXp - currentThreshold) / (nextThreshold - currentThreshold);
}

/** Return the XP threshold needed to reach the next level. */
export function xpForNextLevel(currentLevel: number): number {
  if (currentLevel >= MAX_LEVEL) return LEVEL_THRESHOLDS[LEVEL_THRESHOLDS.length - 1];
  return LEVEL_THRESHOLDS[currentLevel];
}
