"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { ChevronLeft, Lightbulb } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";
import { useKidsStore } from "@/features/kids/store/kidsStore";
import { useKidQuest } from "@/features/kids/hooks/useKidQuest";
import { useKidProfile } from "@/features/kids/hooks/useKidProfile";
import QuestionCard from "@/features/kids/components/QuestionCard";
import FeedbackOverlay, { type FeedbackType } from "@/features/kids/components/FeedbackOverlay";
import HintPanel from "@/features/kids/components/HintPanel";
import LevelSummary from "@/features/kids/components/LevelSummary";
import type { Question } from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Demo questions (used when WS is not connected — for UI preview/testing)
// ---------------------------------------------------------------------------

const DEMO_QUESTIONS: Question[] = [
  {
    question_id: "demo_q1",
    text: "Which dinosaur had three horns on its head?",
    question_type: "single_choice",
    options: ["T-Rex", "Triceratops", "Stegosaurus", "Brachiosaurus"],
    correct_answer: "Triceratops",
    explanation: "Triceratops means 'three-horned face' — it had three horns!",
    points: 10,
    hints: [
      "Think about its name — it has a number in it!",
      "The name starts with 'Tri' which means three.",
      "Triceratops had three horns: two large ones above the eyes and one small one on the nose.",
    ],
    acceptable_answers: [],
    correct_answer_ids: [],
    matching_pairs: [],
    ordering_sequence: [],
    sub_questions: [],
  },
  {
    question_id: "demo_q2",
    text: "True or False: All dinosaurs were meat-eaters.",
    question_type: "true_false",
    options: [],
    correct_answer: "false",
    explanation: "Some dinosaurs ate plants (herbivores) and some ate meat (carnivores).",
    points: 10,
    hints: [
      "Remember the reading about dinosaur diets!",
      "Some dinosaurs only ate plants.",
    ],
    acceptable_answers: [],
    correct_answer_ids: [],
    matching_pairs: [],
    ordering_sequence: [],
    sub_questions: [],
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function PlayPage() {
  const router = useRouter();
  const { t } = useTranslation();
  const params = useParams<{ levelId: string }>();
  const levelId = params.levelId;
  const { profile } = useKidProfile();

  const store = useKidsStore();
  const [feedback, setFeedback] = useState<FeedbackType>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [summaryData, setSummaryData] = useState({
    stars: 0,
    xpEarned: 0,
    newBadges: [] as string[],
  });

  // For demo mode, seed questions immediately
  useEffect(() => {
    if (store.phase === "idle" && DEMO_QUESTIONS.length > 0) {
      store.startLevel(
        {
          level_id: levelId,
          title: "Level",
          order: 0,
          is_boss: false,
        },
        DEMO_QUESTIONS,
      );
    }
  }, [store, levelId]);

  // WS config — null for now (demo mode); connect when WS endpoint is ready
  const wsConfig = useMemo(() => {
    if (!profile) return null;
    return {
      wsUrl: `/api/v1/quest/ws`,
      profileId: profile.profileId,
      levelId,
      mapKey: `public:${levelId}`,
      ageBand: profile.ageBand,
    };
  }, [profile, levelId]);

  const { wsStatus, sendAnswer, requestHint } = useKidQuest(wsConfig);

  // Current question
  const currentQuestion = store.questions[store.currentIndex] ?? null;
  const isLastQuestion = store.currentIndex >= store.questions.length - 1;

  // Handle answer submission
  const handleAnswer = useCallback(
    (answer: string) => {
      if (!currentQuestion) return;

      // In WS mode, send to backend
      if (wsStatus === "connected") {
        sendAnswer(answer);
        return;
      }

      // Demo mode: grade locally
      store.submitAnswer(answer);
      const isCorrect = checkAnswer(currentQuestion, answer);

      if (isCorrect) {
        store.markCorrect();
        const newCombo = store.combo + 1;
        if (newCombo >= 3) {
          setFeedback("combo");
        } else {
          setFeedback("correct");
        }
      } else {
        store.markWrong();
        setFeedback("wrong");
      }
    },
    [currentQuestion, store, wsStatus, sendAnswer],
  );

  // Handle feedback animation complete
  const handleFeedbackComplete = useCallback(() => {
    setFeedback(null);

    if (store.phase === "level_complete") {
      setShowSummary(true);
      return;
    }

    // Move to next question or complete level
    if (isLastQuestion) {
      const correctPct =
        store.questions.length > 0
          ? store.correctCount / store.questions.length
          : 0;
      const stars = correctPct >= 1 ? 3 : correctPct >= 0.8 ? 2 : correctPct >= 0.6 ? 1 : 0;
      const xp = store.correctCount * 10;

      setSummaryData({ stars, xpEarned: xp, newBadges: [] });
      store.completeLevel(xp);
      // Immediately show summary after completing the last question
      setShowSummary(true);
    } else {
      store.nextQuestion();
    }
  }, [store, isLastQuestion]);

  const handleContinue = useCallback(() => {
    router.push("/kids-home");
  }, [router]);

  const handleRetry = useCallback(() => {
    store.reset();
    setShowSummary(false);
    store.startLevel(
      {
        level_id: levelId,
        title: "Level",
        order: 0,
        is_boss: false,
      },
      DEMO_QUESTIONS,
    );
  }, [store, levelId]);

  // Show level summary
  if (showSummary) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="w-full max-w-md">
          <LevelSummary
            stars={summaryData.stars}
            xpEarned={summaryData.xpEarned}
            correctCount={store.correctCount}
            totalCount={store.questions.length}
            newBadges={summaryData.newBadges}
            onContinue={handleContinue}
            onRetry={handleRetry}
          />
        </div>
      </div>
    );
  }

  // Loading
  if (!currentQuestion) {
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

  const submitted = store.phase === "judged_correct" || store.phase === "judged_wrong";
  const isCorrect = store.phase === "judged_correct";

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
        <span className="text-sm font-semibold" style={{ color: "var(--muted-foreground)" }}>
          Q{store.currentIndex + 1} / {store.questions.length}
        </span>
      </div>

      {/* Progress bar */}
      <div
        className="w-full h-2 overflow-hidden"
        style={{
          backgroundColor: "var(--muted)",
          borderRadius: "9999px",
        }}
      >
        <div
          style={{
            width: `${((store.currentIndex + (submitted ? 1 : 0)) / store.questions.length) * 100}%`,
            height: "100%",
            backgroundColor: kidsTheme.colors.primary,
            borderRadius: "9999px",
            transition: "width 0.3s ease",
          }}
        />
      </div>

      {/* Question card */}
      <QuestionCard
        question={currentQuestion}
        questionNumber={store.currentIndex + 1}
        totalQuestions={store.questions.length}
        onAnswer={handleAnswer}
        submitted={submitted}
        isCorrect={isCorrect}
        selectedAnswer={store.selectedAnswer}
        combo={store.combo}
      />

      {/* Hint button */}
      {!submitted && currentQuestion.hints.length > 0 && !store.hintVisible && (
        <button
          className="w-full flex items-center justify-center gap-2 py-3 font-semibold transition-opacity hover:opacity-80"
          style={{
            backgroundColor: `${kidsTheme.colors.warning}15`,
            color: kidsTheme.colors.warning,
            borderRadius: kidsTheme.borderRadius.button,
            border: `2px solid ${kidsTheme.colors.warning}40`,
          }}
          onClick={() => store.showHint()}
        >
          <Lightbulb className="w-5 h-5" />
          {t("kids.showHint")}
        </button>
      )}

      {/* Hint panel */}
      <HintPanel
        visible={store.hintVisible}
        stage={store.hintStage}
        hints={currentQuestion.hints}
        onNextHint={requestHint}
        onClose={() => store.hideHint()}
      />

      {/* Next button (after submit) or See Results (level complete) */}
      {(submitted || store.phase === "level_complete") && (
        <button
          className="w-full flex items-center justify-center gap-2 py-4 font-bold text-white transition-opacity hover:opacity-90"
          style={{
            backgroundColor: isCorrect
              ? kidsTheme.colors.success
              : kidsTheme.colors.primary,
            borderRadius: kidsTheme.borderRadius.button,
            fontSize: kidsTheme.typography.buttonSize,
            minHeight: kidsTheme.sizing.buttonMinHeight,
          }}
          disabled={feedback !== null}
          aria-disabled={feedback !== null}
          onClick={() => {
            if (feedback !== null) return;
            handleFeedbackComplete();
          }}
        >
          {isLastQuestion ? t("kids.seeResults") : t("kids.nextQuestion")}
        </button>
      )}

      {/* Feedback overlay */}
      <FeedbackOverlay
        type={feedback}
        comboCount={store.combo}
        onComplete={handleFeedbackComplete}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local grading (demo mode)
// ---------------------------------------------------------------------------

function checkAnswer(question: Question, answer: string): boolean {
  const ans = answer.trim().toLowerCase();

  switch (question.question_type) {
    case "single_choice":
    case "image_choice":
    case "error_correction":
      return ans === question.correct_answer.trim().toLowerCase();

    case "true_false":
      return ans === question.correct_answer.trim().toLowerCase();

    case "fill_blank":
      if (ans === question.correct_answer.trim().toLowerCase()) return true;
      return question.acceptable_answers.some(
        (a) => a.trim().toLowerCase() === ans,
      );

    case "matching":
      // Answer is "left|right;;left|right;;..." — compare normalized
      return answer.trim() === question.correct_answer.trim();

    case "ordering":
      // Answer is "item;;item;;..." — compare normalized
      return answer.trim() === question.correct_answer.trim();

    default:
      return false;
  }
}
