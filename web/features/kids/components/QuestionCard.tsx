"use client";

import React, { useState } from "react";
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
        <MatchingDisplay pairs={question.matching_pairs} />
      );

    case "ordering":
      return (
        <OrderingDisplay sequence={question.ordering_sequence} />
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

function MatchingDisplay({ pairs }: { pairs: [string, string][] }) {
  return (
    <div className="space-y-2">
      {pairs.map((pair, idx) => (
        <div
          key={idx}
          className="flex items-center gap-3 p-3"
          style={{
            backgroundColor: "var(--muted)",
            borderRadius: kidsTheme.borderRadius.chip,
          }}
        >
          <span className="font-semibold" style={{ color: "var(--foreground)" }}>
            {pair[0]}
          </span>
          <span style={{ color: kidsTheme.colors.primary }}>{"<->"}</span>
          <span className="font-semibold" style={{ color: "var(--foreground)" }}>
            {pair[1]}
          </span>
        </div>
      ))}
    </div>
  );
}

function OrderingDisplay({ sequence }: { sequence: string[] }) {
  return (
    <div className="flex flex-col gap-2">
      {sequence.map((item, idx) => (
        <div
          key={idx}
          className="flex items-center gap-3 p-3"
          style={{
            backgroundColor: "var(--muted)",
            borderRadius: kidsTheme.borderRadius.chip,
          }}
        >
          <span
            className="flex items-center justify-center font-bold text-sm"
            style={{
              width: "1.75rem",
              height: "1.75rem",
              borderRadius: "50%",
              backgroundColor: kidsTheme.colors.primary,
              color: "white",
            }}
          >
            {idx + 1}
          </span>
          <span style={{ color: "var(--foreground)" }}>{item}</span>
        </div>
      ))}
    </div>
  );
}
