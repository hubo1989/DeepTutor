/**
 * Kids Store — React Context-based state for the gamified learning UI.
 *
 * Uses React Context + useReducer instead of an external state library,
 * since the project does not include Zustand. The store manages the
 * ephemeral play-session state: current level, question queue, answer
 * feedback, combo counter, and temporary level progress.
 *
 * Persistent progress (XP, stars, badges) lives on the backend and is
 * fetched via the API; this store only tracks the in-flight session.
 */

"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type ReactNode,
} from "react";
import type {
  LevelSummaryModel,
  Question,
  QuestMapResponse,
} from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PlayPhase =
  | "idle"
  | "loading"
  | "presenting"
  | "judged_correct"
  | "judged_wrong"
  | "level_complete"
  | "error";

export interface KidsStoreState {
  activeMap: QuestMapResponse | null;
  currentLevel: LevelSummaryModel | null;
  questions: Question[];
  currentIndex: number;
  phase: PlayPhase;
  combo: number;
  correctCount: number;
  attemptedCount: number;
  hintVisible: boolean;
  hintStage: number;
  sessionXp: number;
  errorMessage: string;
  selectedAnswer: string;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

type KidsAction =
  | { type: "SET_ACTIVE_MAP"; map: QuestMapResponse | null }
  | {
      type: "START_LEVEL";
      level: LevelSummaryModel;
      questions: Question[];
    }
  | { type: "SUBMIT_ANSWER"; answer: string }
  | { type: "MARK_CORRECT" }
  | { type: "MARK_WRONG" }
  | { type: "NEXT_QUESTION" }
  | { type: "COMPLETE_LEVEL"; xp: number }
  | { type: "SHOW_HINT" }
  | { type: "ADVANCE_HINT" }
  | { type: "HIDE_HINT" }
  | { type: "RESET" }
  | { type: "SET_ERROR"; message: string };

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

const initialState: KidsStoreState = {
  activeMap: null,
  currentLevel: null,
  questions: [],
  currentIndex: 0,
  phase: "idle",
  combo: 0,
  correctCount: 0,
  attemptedCount: 0,
  hintVisible: false,
  hintStage: 0,
  sessionXp: 0,
  errorMessage: "",
  selectedAnswer: "",
};

function kidsReducer(state: KidsStoreState, action: KidsAction): KidsStoreState {
  switch (action.type) {
    case "SET_ACTIVE_MAP":
      return { ...state, activeMap: action.map };

    case "START_LEVEL":
      return {
        ...state,
        currentLevel: action.level,
        questions: action.questions,
        currentIndex: 0,
        phase: "presenting",
        combo: 0,
        correctCount: 0,
        attemptedCount: 0,
        hintVisible: false,
        hintStage: 0,
        selectedAnswer: "",
        errorMessage: "",
      };

    case "SUBMIT_ANSWER":
      return { ...state, selectedAnswer: action.answer };

    case "MARK_CORRECT":
      return {
        ...state,
        phase: "judged_correct",
        combo: state.combo + 1,
        correctCount: state.correctCount + 1,
        attemptedCount: state.attemptedCount + 1,
      };

    case "MARK_WRONG":
      return {
        ...state,
        phase: "judged_wrong",
        combo: 0,
        attemptedCount: state.attemptedCount + 1,
      };

    case "NEXT_QUESTION": {
      const nextIdx = state.currentIndex + 1;
      if (nextIdx >= state.questions.length) {
        return { ...state, phase: "level_complete" };
      }
      return {
        ...state,
        currentIndex: nextIdx,
        phase: "presenting",
        selectedAnswer: "",
        hintVisible: false,
        hintStage: 0,
      };
    }

    case "COMPLETE_LEVEL":
      return {
        ...state,
        phase: "level_complete",
        sessionXp: state.sessionXp + action.xp,
      };

    case "SHOW_HINT":
      return { ...state, hintVisible: true, hintStage: 1 };

    case "ADVANCE_HINT":
      return { ...state, hintStage: Math.min(state.hintStage + 1, 3) };

    case "HIDE_HINT":
      return { ...state, hintVisible: false, hintStage: 0 };

    case "RESET":
      return { ...initialState };

    case "SET_ERROR":
      return { ...state, phase: "error", errorMessage: action.message };

    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

interface KidsStoreContextValue {
  state: KidsStoreState;
  // Convenience accessors
  activeMap: QuestMapResponse | null;
  currentLevel: LevelSummaryModel | null;
  questions: Question[];
  currentIndex: number;
  phase: PlayPhase;
  combo: number;
  correctCount: number;
  attemptedCount: number;
  hintVisible: boolean;
  hintStage: number;
  sessionXp: number;
  errorMessage: string;
  selectedAnswer: string;
  // Actions
  setActiveMap: (map: QuestMapResponse | null) => void;
  startLevel: (level: LevelSummaryModel, questions: Question[]) => void;
  submitAnswer: (answer: string) => void;
  markCorrect: () => void;
  markWrong: () => void;
  nextQuestion: () => void;
  completeLevel: (xp: number) => void;
  showHint: () => void;
  advanceHint: () => void;
  hideHint: () => void;
  reset: () => void;
  setError: (msg: string) => void;
}

const KidsStoreContext = createContext<KidsStoreContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function KidsStoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(kidsReducer, initialState);

  const value = useMemo<KidsStoreContextValue>(
    () => ({
      state,
      // Convenience accessors
      activeMap: state.activeMap,
      currentLevel: state.currentLevel,
      questions: state.questions,
      currentIndex: state.currentIndex,
      phase: state.phase,
      combo: state.combo,
      correctCount: state.correctCount,
      attemptedCount: state.attemptedCount,
      hintVisible: state.hintVisible,
      hintStage: state.hintStage,
      sessionXp: state.sessionXp,
      errorMessage: state.errorMessage,
      selectedAnswer: state.selectedAnswer,
      // Actions
      setActiveMap: (map) => dispatch({ type: "SET_ACTIVE_MAP", map }),
      startLevel: (level, questions) =>
        dispatch({ type: "START_LEVEL", level, questions }),
      submitAnswer: (answer) => dispatch({ type: "SUBMIT_ANSWER", answer }),
      markCorrect: () => dispatch({ type: "MARK_CORRECT" }),
      markWrong: () => dispatch({ type: "MARK_WRONG" }),
      nextQuestion: () => dispatch({ type: "NEXT_QUESTION" }),
      completeLevel: (xp) => dispatch({ type: "COMPLETE_LEVEL", xp }),
      showHint: () => dispatch({ type: "SHOW_HINT" }),
      advanceHint: () => dispatch({ type: "ADVANCE_HINT" }),
      hideHint: () => dispatch({ type: "HIDE_HINT" }),
      reset: () => dispatch({ type: "RESET" }),
      setError: (message) => dispatch({ type: "SET_ERROR", message }),
    }),
    [state],
  );

  return (
    <KidsStoreContext.Provider value={value}>
      {children}
    </KidsStoreContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useKidsStore(): KidsStoreContextValue {
  const ctx = useContext(KidsStoreContext);
  if (!ctx) {
    throw new Error("useKidsStore must be used within a KidsStoreProvider");
  }
  return ctx;
}
