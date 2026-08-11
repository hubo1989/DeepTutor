/**
 * useTTS — Text-to-Speech hook for the gamified learning UI.
 *
 * Strategy:
 *   1. Try the backend TTS API (/api/v1/kids/tts).
 *   2. If the API returns ``fallback: true``, use the browser-native
 *      ``speechSynthesis`` API.
 *   3. If neither is available, report an error.
 *
 * For 7-9 age band, TTS is enabled by default. The parent or child can
 * toggle it off via the ``enabled`` state.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseTTSConfig {
  /** Age band of the active child profile. */
  ageBand: string;
  /** Profile ID for metrics. */
  profileId?: string;
  /** Override default enable/disable. If undefined, auto-detects from ageBand. */
  enabled?: boolean;
  /** API base URL (default: current origin). */
  apiBaseUrl?: string;
}

export interface UseTTSReturn {
  /** Whether TTS is currently enabled. */
  enabled: boolean;
  /** Toggle TTS on/off. */
  toggleEnabled: () => void;
  /** Set enabled explicitly. */
  setEnabled: (value: boolean) => void;
  /** Speak the given text. Returns a promise that resolves when speech ends. */
  speak: (text: string) => Promise<void>;
  /** Stop any ongoing speech. */
  stop: () => void;
  /** Whether speech is currently in progress. */
  isSpeaking: boolean;
  /** Error message if TTS failed (empty string when no error). */
  error: string;
}

// Age bands where TTS is enabled by default.
const TTS_DEFAULT_AGE_BANDS = ["7-9"];

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTTS(config: UseTTSConfig): UseTTSReturn {
  const { ageBand, profileId, apiBaseUrl } = config;

  // Determine default enabled state
  const defaultEnabled =
    config.enabled !== undefined
      ? config.enabled
      : TTS_DEFAULT_AGE_BANDS.includes(ageBand);

  const [enabled, setEnabledState] = useState<boolean>(defaultEnabled);
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const [error, setError] = useState<string>("");

  const currentUtteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const apiBase = apiBaseUrl || (typeof window !== "undefined" ? window.location.origin : "");

  // Persist enabled state to localStorage for this profile
  useEffect(() => {
    const storageKey = `deeptutor:tts:${profileId || "default"}`;
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) {
        setEnabledState(stored === "true");
      }
    } catch {
      // localStorage may be unavailable
    }
  }, [profileId]);

  const setEnabled = useCallback(
    (value: boolean) => {
      setEnabledState(value);
      const storageKey = `deeptutor:tts:${profileId || "default"}`;
      try {
        localStorage.setItem(storageKey, String(value));
      } catch {
        // Ignore storage errors
      }
    },
    [profileId],
  );

  const toggleEnabled = useCallback(() => {
    setEnabled(!enabled);
  }, [enabled, setEnabled]);

  /**
   * Browser-native speechSynthesis fallback.
   */
  const speakWithBrowser = useCallback(
    (text: string): Promise<void> => {
      return new Promise((resolve, reject) => {
        if (typeof window === "undefined" || !window.speechSynthesis) {
          reject(new Error("Browser speechSynthesis not available."));
          return;
        }

        // Cancel any ongoing speech
        window.speechSynthesis.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.9; // slightly slower for children
        utterance.pitch = 1.0;
        utterance.volume = 1.0;

        // Try to select a Chinese voice if available (for zh content)
        const voices = window.speechSynthesis.getVoices();
        const zhVoice = voices.find(
          (v) => v.lang.startsWith("zh") || v.lang.startsWith("cmn"),
        );
        if (zhVoice) {
          utterance.voice = zhVoice;
        }

        utterance.onstart = () => setIsSpeaking(true);
        utterance.onend = () => {
          setIsSpeaking(false);
          currentUtteranceRef.current = null;
          resolve();
        };
        utterance.onerror = (event: SpeechSynthesisErrorEvent) => {
          setIsSpeaking(false);
          currentUtteranceRef.current = null;
          reject(new Error(`Speech error: ${event.error}`));
        };

        currentUtteranceRef.current = utterance;
        window.speechSynthesis.speak(utterance);
      });
    },
    [],
  );

  /**
   * Speak text using backend API or browser fallback.
   */
  const speak = useCallback(
    async (text: string): Promise<void> => {
      if (!enabled) return;
      if (!text.trim()) return;

      setError("");

      // Try backend API first
      try {
        const response = await fetch(`${apiBase}/api/v1/kids/tts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text,
            age_band: ageBand,
            profile_id: profileId || "",
          }),
        });

        if (response.ok) {
          const data = await response.json();
          if (data.fallback === true) {
            // Backend says use browser fallback
            await speakWithBrowser(text);
            return;
          }
          if (data.audio_base64) {
            // Play the base64 audio
            await playBase64Audio(data.audio_base64, data.content_type || "audio/mpeg");
            return;
          }
          // No audio data, fall through to browser
          await speakWithBrowser(text);
          return;
        }
        // API error — fall back to browser
        await speakWithBrowser(text);
      } catch {
        // Network or API error — try browser fallback
        try {
          await speakWithBrowser(text);
        } catch (browserErr) {
          const msg = browserErr instanceof Error ? browserErr.message : "TTS failed";
          setError(msg);
        }
      }
    },
    [enabled, ageBand, profileId, apiBase, speakWithBrowser],
  );

  const stop = useCallback(() => {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    setIsSpeaking(false);
    currentUtteranceRef.current = null;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  return {
    enabled,
    toggleEnabled,
    setEnabled,
    speak,
    stop,
    isSpeaking,
    error,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Play base64-encoded audio data.
 */
function playBase64Audio(base64Data: string, contentType: string): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      const byteCharacters = atob(base64Data);
      const byteArrays: Uint8Array[] = [];
      const chunkSize = 8192;
      for (let offset = 0; offset < byteCharacters.length; offset += chunkSize) {
        const slice = byteCharacters.slice(offset, offset + chunkSize);
        const byteNumbers = new Array(slice.length);
        for (let i = 0; i < slice.length; i++) {
          byteNumbers[i] = slice.charCodeAt(i);
        }
        byteArrays.push(new Uint8Array(byteNumbers));
      }
      const blob = new Blob(byteArrays as BlobPart[], { type: contentType });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);

      audio.onended = () => {
        URL.revokeObjectURL(url);
        resolve();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("Audio playback failed."));
      };

      audio.play().catch((err) => {
        URL.revokeObjectURL(url);
        reject(err);
      });
    } catch (err) {
      reject(err);
    }
  });
}
