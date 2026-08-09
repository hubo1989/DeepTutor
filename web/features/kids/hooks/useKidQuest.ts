/**
 * useKidQuest — WebSocket event-driven state machine for the quest play loop.
 *
 * Connects to the unified WS endpoint, sends answer events, and translates
 * incoming quest events (item_presented, item_judged, level_cleared,
 * badge_earned) into store mutations.
 *
 * The hook is intentionally framework-light: it does not assume a
 * particular WS library and uses the native WebSocket API.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useKidsStore } from "@/features/kids/store/kidsStore";
import type { Question } from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// WS event types (aligned with questing.py event metadata.sub_type)
// ---------------------------------------------------------------------------

export interface WsQuestEvent {
  type: string;
  source?: string;
  stage?: string;
  text?: string;
  metadata?: {
    sub_type?: string;
    question?: Record<string, unknown>;
    result?: {
      stars?: number;
      correct_count?: number;
      total?: number;
      xp_earned?: number;
    };
    badge_id?: string;
    [key: string]: unknown;
  };
}

export interface KidQuestConfig {
  wsUrl: string;
  profileId: string;
  levelId: string;
  mapKey: string;
  ageBand: string;
}

export type WsStatus = "disconnected" | "connecting" | "connected" | "error";

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useKidQuest(config: KidQuestConfig | null) {
  const store = useKidsStore();
  const wsRef = useRef<WebSocket | null>(null);
  const [wsStatus, setWsStatus] = useState<WsStatus>("disconnected");
  const [newBadges, setNewBadges] = useState<string[]>([]);

  const connect = useCallback(() => {
    if (!config) return;

    setWsStatus("connecting");
    const ws = new WebSocket(config.wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setWsStatus("connected");
      ws.send(
        JSON.stringify({
          type: "quest_start",
          profile_id: config.profileId,
          level_id: config.levelId,
          map_key: config.mapKey,
          age_band: config.ageBand,
        }),
      );
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data as string) as WsQuestEvent;
        handleQuestEvent(data, store, setNewBadges);
      } catch {
        // Ignore non-JSON messages
      }
    };

    ws.onerror = () => {
      setWsStatus("error");
      store.setError("Connection error");
    };

    ws.onclose = () => {
      setWsStatus("disconnected");
    };
  }, [config, store]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setWsStatus("disconnected");
  }, []);

  const sendAnswer = useCallback(
    (answer: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: "quest_answer",
          answer,
          question_index: store.currentIndex,
        }),
      );
      store.submitAnswer(answer);
    },
    [store],
  );

  const requestHint = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // In local mode, still advance the hint
      store.advanceHint();
      return;
    }
    ws.send(
      JSON.stringify({
        type: "quest_hint",
        question_index: store.currentIndex,
        stage: store.hintStage + 1,
      }),
    );
    store.advanceHint();
  }, [store]);

  // Auto-connect when config is provided
  useEffect(() => {
    if (config) {
      connect();
    }
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.wsUrl]);

  return {
    wsStatus,
    newBadges,
    connect,
    disconnect,
    sendAnswer,
    requestHint,
  };
}

// ---------------------------------------------------------------------------
// Event handler (pure, testable)
// ---------------------------------------------------------------------------

function handleQuestEvent(
  event: WsQuestEvent,
  store: ReturnType<typeof useKidsStore>,
  setNewBadges: (fn: (prev: string[]) => string[]) => void,
): void {
  const subType = event.metadata?.sub_type ?? "";

  switch (subType) {
    case "item_presented": {
      const q = event.metadata?.question as Record<string, unknown> | undefined;
      if (q) {
        const question = parseQuestion(q);
        if (store.questions.length === 0 && store.currentLevel) {
          store.startLevel(store.currentLevel, [question]);
        }
      }
      break;
    }

    case "item_judged": {
      const result = event.metadata?.result;
      if (result) {
        const isCorrect = (result.correct_count ?? 0) > 0;
        if (isCorrect) {
          store.markCorrect();
        } else {
          store.markWrong();
        }
      }
      break;
    }

    case "level_cleared":
    case "quest_summary": {
      const result = event.metadata?.result;
      if (result) {
        store.completeLevel(result.xp_earned ?? 0);
      }
      break;
    }

    case "badge_earned": {
      const badgeId = event.metadata?.badge_id;
      if (badgeId) {
        setNewBadges((prev) => [...prev, badgeId]);
      }
      break;
    }

    default:
      break;
  }
}

/** Parse a raw question dict from a WS event into a Question object. */
function parseQuestion(raw: Record<string, unknown>): Question {
  return {
    question_id: String(raw.question_id ?? ""),
    text: String(raw.text ?? raw.question_text ?? ""),
    question_type: String(
      raw.question_type ?? "single_choice",
    ) as Question["question_type"],
    options: Array.isArray(raw.options) ? (raw.options as string[]) : [],
    correct_answer: String(raw.correct_answer ?? ""),
    explanation: String(raw.explanation ?? ""),
    points: Number(raw.points ?? 10),
    hints: Array.isArray(raw.hints) ? (raw.hints as string[]) : [],
    acceptable_answers: Array.isArray(raw.acceptable_answers)
      ? (raw.acceptable_answers as string[])
      : [],
    correct_answer_ids: Array.isArray(raw.correct_answer_ids)
      ? (raw.correct_answer_ids as string[])
      : [],
    matching_pairs: Array.isArray(raw.matching_pairs)
      ? (raw.matching_pairs as [string, string][])
      : [],
    ordering_sequence: Array.isArray(raw.ordering_sequence)
      ? (raw.ordering_sequence as string[])
      : [],
    sub_questions: Array.isArray(raw.sub_questions)
      ? (raw.sub_questions as Record<string, unknown>[])
      : [],
  };
}
