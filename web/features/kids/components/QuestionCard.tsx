"use client";

import React, { useEffect, useState } from "react";
import { Check, X } from "lucide-react";
import { kidsTheme } from "@/features/kids/theme/kidsTheme";
import type { Question } from "@/lib/kids-types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface QuestionCardProps {
  /** The question to render. */
  question: Question;
  /** 1-based question number (for display). */
  questionNumber: number;
  /** Total questions in the level. */
  totalQuestions: number;
  /** Called when the child selects an answer. */
  onAnswer: (answer: string) => void;
  /** Whether the answer has been submitted (locks interaction). */
  submitted: boolean;
  /** Whether the submitted answer was correct. */
  isCorrect: boolean;
  /** The selected answer value. */
  selectedAnswer: string;
  /** Current combo count. */
  combo: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function QuestionCard({
  question,
  questionNumber,
  totalQuestions,
  onAnswer,
  submitted,
  isCorrect,
  selectedAnswer,
  combo,
}: QuestionCardProps) {
  const [localAnswer, setLocalAnswer] = useState("");

  const handleSelect = (value: string) => {
    if (submitted) return;
    setLocalAnswer(value);
    onAnswer(value);
  };

  const handleTextInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (submitted) return;
    setLocalAnswer(e.target.value);
  };

  const handleSubmitText = () => {
    if (submitted || !localAnswer.trim()) return;
    onAnswer(localAnswer.trim());
  };

  return (
    <div
      className="w-full p-6"
      style={{
        backgroundColor: "var(--card)",
        borderRadius: kidsTheme.borderRadius.card,
        border: "1px solid var(--border)",
      }}
    >
      {/* Header: question number + combo */}
      <div className="flex items-center justify-between mb-4">
        <span
          className="text-sm font-semibold px-3 py-1 rounded-full"
          style={{
            backgroundColor: "var(--muted)",
            color: "var(--muted-foreground)",
          }}
        >
          Q{questionNumber} / {totalQuestions}
        </span>
        {combo >= 2 && (
          <span
            className="text-sm font-bold px-3 py-1 rounded-full"
            style={{
              backgroundColor: `${kidsTheme.colors.warning}30`,
              color: kidsTheme.colors.warning,
            }}
          >
            Combo x{combo}
          </span>
        )}
      </div>

      {/* Question text */}
      <h3
        className="text-xl font-bold mb-6"
        style={{ color: "var(--foreground)", lineHeight: 1.4 }}
      >
        {question.text}
      </h3>

      {/* Render by type */}
      {renderQuestionBody(
        question,
        localAnswer || selectedAnswer,
        handleSelect,
        handleTextInput,
        handleSubmitText,
        submitted,
        isCorrect,
      )}

      {/* Explanation (shown after submit) */}
      {submitted && question.explanation && (
        <div
          className="mt-4 p-4 rounded-xl"
          style={{
            backgroundColor: isCorrect
              ? kidsTheme.colors.successBg
              : kidsTheme.colors.dangerBg,
            borderLeft: `4px solid ${
              isCorrect ? kidsTheme.colors.success : kidsTheme.colors.danger
            }`,
          }}
        >
          <p
            className="text-sm"
            style={{ color: "var(--foreground)" }}
          >
            {question.explanation}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Question body renderer (dispatches by type)
// ---------------------------------------------------------------------------

function renderQuestionBody(
  question: Question,
  selectedValue: string,
  onSelect: (value: string) => void,
  onTextInput: (e: React.ChangeEvent<HTMLInputElement>) => void,
  onSubmitText: () => void,
  submitted: boolean,
  isCorrect: boolean,
): React.ReactNode {
  switch (question.question_type) {
    case "single_choice":
    case "image_choice":
      return (
        <ChoiceOptions
          options={question.options}
          selectedValue={selectedValue}
          correctAnswer={question.correct_answer}
          onSelect={onSelect}
          submitted={submitted}
          isCorrect={isCorrect}
        />
      );

    case "true_false":
      return (
        <ChoiceOptions
          options={["true", "false"]}
          selectedValue={selectedValue}
          correctAnswer={question.correct_answer}
          onSelect={onSelect}
          submitted={submitted}
          isCorrect={isCorrect}
        />
      );

    case "fill_blank":
      return (
        <FillBlankInput
          value={selectedValue}
          onChange={onTextInput}
          onSubmit={onSubmitText}
          submitted={submitted}
          correctAnswer={question.correct_answer}
        />
      );

    case "matching":
      return (
        <MatchingInteraction
          pairs={question.matching_pairs}
          selectedValue={selectedValue}
          onSelect={onSelect}
          submitted={submitted}
        />
      );

    case "ordering":
      return (
        <OrderingInteraction
          sequence={question.ordering_sequence}
          selectedValue={selectedValue}
          onSelect={onSelect}
          submitted={submitted}
        />
      );

    case "error_correction":
      return (
        <ChoiceOptions
          options={question.options}
          selectedValue={selectedValue}
          correctAnswer={question.correct_answer}
          onSelect={onSelect}
          submitted={submitted}
          isCorrect={isCorrect}
        />
      );

    case "boss_comprehensive":
      return (
        <div className="space-y-3">
          <p
            className="text-sm font-semibold"
            style={{ color: kidsTheme.colors.boss }}
          >
            Boss Challenge - Answer all parts!
          </p>
          <ChoiceOptions
            options={question.options}
            selectedValue={selectedValue}
            correctAnswer={question.correct_answer}
            onSelect={onSelect}
            submitted={submitted}
            isCorrect={isCorrect}
          />
        </div>
      );

    default:
      return (
        <p className="text-sm" style={{ color: "var(--muted-foreground)" }}>
          Unsupported question type.
        </p>
      );
  }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ChoiceOptions({
  options,
  selectedValue,
  correctAnswer,
  onSelect,
  submitted,
  isCorrect,
}: {
  options: string[];
  selectedValue: string;
  correctAnswer: string;
  onSelect: (value: string) => void;
  submitted: boolean;
  isCorrect: boolean;
}) {
  return (
    <div className="grid gap-3">
      {options.map((opt, idx) => {
        const isSelected = opt === selectedValue;
        const isCorrectOpt = opt === correctAnswer;

        let bg = "var(--secondary)";
        let border = "var(--border)";

        if (submitted) {
          if (isCorrectOpt) {
            bg = kidsTheme.colors.successBg;
            border = kidsTheme.colors.success;
          } else if (isSelected) {
            bg = kidsTheme.colors.dangerBg;
            border = kidsTheme.colors.danger;
          }
        } else if (isSelected) {
          bg = `${kidsTheme.colors.primary}15`;
          border = kidsTheme.colors.primary;
        }

        return (
          <button
            key={idx}
            className="flex items-center gap-3 p-4 text-left transition-all hover:opacity-80"
            style={{
              backgroundColor: bg,
              border: `2px solid ${border}`,
              borderRadius: kidsTheme.borderRadius.button,
              minHeight: kidsTheme.sizing.buttonMinHeight,
              cursor: submitted ? "default" : "pointer",
            }}
            onClick={() => onSelect(opt)}
            disabled={submitted}
          >
            <span
              className="flex items-center justify-center font-bold text-sm"
              style={{
                width: "2rem",
                height: "2rem",
                borderRadius: "50%",
                backgroundColor: "var(--muted)",
                flexShrink: 0,
              }}
            >
              {String.fromCharCode(65 + idx)}
            </span>
            <span
              className="flex-1 text-base font-medium"
              style={{ color: "var(--foreground)" }}
            >
              {opt}
            </span>
            {submitted && isCorrectOpt && (
              <Check className="w-5 h-5" style={{ color: kidsTheme.colors.success }} />
            )}
            {submitted && isSelected && !isCorrectOpt && (
              <X className="w-5 h-5" style={{ color: kidsTheme.colors.danger }} />
            )}
          </button>
        );
      })}
    </div>
  );
}

function FillBlankInput({
  value,
  onChange,
  onSubmit,
  submitted,
  correctAnswer,
}: {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onSubmit: () => void;
  submitted: boolean;
  correctAnswer: string;
}) {
  return (
    <div className="flex gap-3">
      <input
        type="text"
        value={value}
        onChange={onChange}
        disabled={submitted}
        placeholder="Type your answer..."
        className="flex-1 px-4 py-3 text-base"
        style={{
          backgroundColor: "var(--background)",
          border: `2px solid ${submitted ? (value.toLowerCase() === correctAnswer.toLowerCase() ? kidsTheme.colors.success : kidsTheme.colors.danger) : "var(--input)"}`,
          borderRadius: kidsTheme.borderRadius.button,
          color: "var(--foreground)",
          outline: "none",
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSubmit();
        }}
      />
      {!submitted && (
        <button
          className="px-6 py-3 font-bold text-white transition-opacity hover:opacity-90"
          style={{
            backgroundColor: kidsTheme.colors.primary,
            borderRadius: kidsTheme.borderRadius.button,
            fontSize: kidsTheme.typography.buttonSize,
          }}
          onClick={onSubmit}
        >
          OK
        </button>
      )}
    </div>
  );
}

function MatchingInteraction({
  pairs,
  selectedValue,
  onSelect,
  submitted,
}: {
  pairs: [string, string][];
  selectedValue: string;
  onSelect: (value: string) => void;
  submitted: boolean;
}) {
  // Build a shuffled list of right-side items for the child to pick from.
  // The correct answer is encoded as "leftItem|rightItem" pairs joined by ";;".
  const correctAnswer = pairs
    .map((p) => `${p[0]}|${p[1]}`)
    .join(";;");

  // If the child hasn't submitted yet, they tap pairs in order.
  // For simplicity in the kids UI, we present each left item with a dropdown
  // of right items to choose from.
  const [selections, setSelections] = useState<Record<string, string>>(() => {
    if (!selectedValue || !selectedValue.includes(";;")) return {};
    const parsed: Record<string, string> = {};
    for (const part of selectedValue.split(";;")) {
      const [l, r] = part.split("|");
      if (l && r) parsed[l] = r;
    }
    return parsed;
  });
  const rightItems = pairs.map((p) => p[1]);

  const handleChange = (left: string, right: string) => {
    if (submitted) return;
    const newSel = { ...selections, [left]: right };
    setSelections(newSel);
    // Emit answer only when all pairs are matched
    if (pairs.every((p) => newSel[p[0]])) {
      const answer = pairs.map((p) => `${p[0]}|${newSel[p[0]]}`).join(";;");
      onSelect(answer);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium mb-2" style={{ color: "var(--muted-foreground)" }}>
        Match each item on the left with the correct answer!
      </p>
      {pairs.map((pair, idx) => {
        const leftItem = pair[0];
        const selectedRight = selections[leftItem] || "";
        const isCorrectMatch = submitted && selectedRight === pair[1];

        return (
          <div
            key={idx}
            className="flex items-center gap-3 p-3"
            style={{
              backgroundColor: submitted
                ? isCorrectMatch
                  ? kidsTheme.colors.successBg
                  : kidsTheme.colors.dangerBg
                : "var(--muted)",
              borderRadius: kidsTheme.borderRadius.chip,
            }}
          >
            <span className="font-semibold flex-1" style={{ color: "var(--foreground)" }}>
              {leftItem}
            </span>
            <select
              className="px-3 py-2 text-sm font-medium"
              style={{
                backgroundColor: "var(--background)",
                border: "1px solid var(--border)",
                borderRadius: kidsTheme.borderRadius.chip,
                color: "var(--foreground)",
                cursor: submitted ? "default" : "pointer",
              }}
              value={selectedRight}
              onChange={(e) => handleChange(leftItem, e.target.value)}
              disabled={submitted}
            >
              <option value="">Choose...</option>
              {rightItems.map((r, ri) => (
                <option key={ri} value={r}>{r}</option>
              ))}
            </select>
            {submitted && isCorrectMatch && (
              <Check className="w-5 h-5" style={{ color: kidsTheme.colors.success }} />
            )}
            {submitted && !isCorrectMatch && selectedRight && (
              <X className="w-5 h-5" style={{ color: kidsTheme.colors.danger }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function OrderingInteraction({
  sequence,
  selectedValue,
  onSelect,
  submitted,
}: {
  sequence: string[];
  selectedValue: string;
  onSelect: (value: string) => void;
  submitted: boolean;
}) {
  // The child taps items in the order they think is correct.
  // The correct answer is the original sequence joined by ";;".
  // A pre-answered question restores the submitted order; otherwise the
  // items are shuffled once on first render. Both effects of setting this
  // state synchronously inside useEffect bodies are avoided by initializing
  // lazily here.
  const [userOrder, setUserOrder] = useState<string[]>(() =>
    selectedValue && selectedValue.includes(";;") ? selectedValue.split(";;") : []
  );
  const [available, setAvailable] = useState<string[]>(() =>
    selectedValue && selectedValue.includes(";;")
      ? []
      : [...sequence].sort(() => Math.random() - 0.5)
  );

  const correctAnswer = sequence.join(";;");

  const handlePick = (item: string) => {
    if (submitted) return;
    const newOrder = [...userOrder, item];
    const newAvailable = available.filter((a) => a !== item);
    setUserOrder(newOrder);
    setAvailable(newAvailable);
    // Emit answer when all items are placed
    if (newOrder.length === sequence.length) {
      onSelect(newOrder.join(";;"));
    }
  };

  const handleRemove = (item: string) => {
    if (submitted) return;
    const newOrder = userOrder.filter((a) => a !== item);
    const newAvailable = [...available, item];
    setUserOrder(newOrder);
    setAvailable(newAvailable);
  };

  const isCorrectOrder = submitted && userOrder.join(";;") === correctAnswer;

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium mb-2" style={{ color: "var(--muted-foreground)" }}>
        Tap the items in the correct order!
      </p>

      {/* User's ordered list */}
      <div className="space-y-2">
        {userOrder.map((item, idx) => {
          const isCorrectPos = submitted && sequence[idx] === item;
          return (
            <button
              key={idx}
              className="flex items-center gap-3 p-3 w-full text-left"
              style={{
                backgroundColor: submitted
                  ? isCorrectPos
                    ? kidsTheme.colors.successBg
                    : kidsTheme.colors.dangerBg
                  : `${kidsTheme.colors.primary}15`,
                borderRadius: kidsTheme.borderRadius.chip,
                cursor: submitted ? "default" : "pointer",
              }}
              onClick={() => handleRemove(item)}
              disabled={submitted}
            >
              <span
                className="flex items-center justify-center font-bold text-sm"
                style={{
                  width: "1.75rem",
                  height: "1.75rem",
                  borderRadius: "50%",
                  backgroundColor: submitted
                    ? isCorrectPos
                      ? kidsTheme.colors.success
                      : kidsTheme.colors.danger
                    : kidsTheme.colors.primary,
                  color: "white",
                }}
              >
                {idx + 1}
              </span>
              <span style={{ color: "var(--foreground)" }}>{item}</span>
              {submitted && isCorrectPos && (
                <Check className="w-4 h-4 ml-auto" style={{ color: kidsTheme.colors.success }} />
              )}
            </button>
          );
        })}
      </div>

      {/* Available items to pick */}
      {available.length > 0 && (
        <div
          className="flex flex-wrap gap-2 p-3"
          style={{
            backgroundColor: "var(--muted)",
            borderRadius: kidsTheme.borderRadius.chip,
          }}
        >
          {available.map((item, idx) => (
            <button
              key={idx}
              className="px-4 py-2 font-medium transition-opacity hover:opacity-80"
              style={{
                backgroundColor: kidsTheme.colors.primary,
                color: "white",
                borderRadius: kidsTheme.borderRadius.button,
              }}
              onClick={() => handlePick(item)}
            >
              {item}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
