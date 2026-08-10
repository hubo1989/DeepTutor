/**
 * Kids Theme Tokens
 *
 * Design tokens for the kid-friendly UI: large tap targets, rounded
 * corners, high-saturation colors, and playful spacing. These complement
 * the existing CSS custom properties (``--background``, ``--foreground``,
 * etc.) by adding kid-specific overrides applied via inline styles or
 * a scoped class on the kids layout root.
 */

export const kidsTheme = {
  /** Extra-large radius for cards and buttons. */
  borderRadius: {
    card: "1.5rem",
    button: "1rem",
    chip: "0.75rem",
    overlay: "1.25rem",
  },
  /** Saturated, playful palette. */
  colors: {
    primary: "#6C5CE7",
    primaryHover: "#5A4BD1",
    accent: "#0984E3",
    success: "#00B894",
    successBg: "#E6FFF9",
    danger: "#FF7675",
    dangerBg: "#FFF0F0",
    warning: "#FDCB6E",
    warningBg: "#FFF8E7",
    star: "#FDCB6E",
    locked: "#A0AEC0",
    boss: "#E84393",
    xpBar: "#6C5CE7",
    streakFlame: "#FF7675",
  },
  /** Font families for kids UI. */
  fonts: {
    body: "Geist, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    heading: "Geist, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  },
  /** Tap-target minimums — large for small hands. */
  sizing: {
    buttonMinHeight: "3.5rem",
    buttonMinWidth: "3.5rem",
    cardMinHeight: "7rem",
    iconButton: "3rem",
  },
  /** Generous spacing for breathing room. */
  spacing: {
    cardPadding: "1.5rem",
    buttonPadding: "1rem 1.75rem",
    sectionGap: "1.5rem",
  },
  /** Bigger, bolder typography. */
  typography: {
    titleSize: "2rem",
    subtitleSize: "1.5rem",
    bodySize: "1.125rem",
    captionSize: "0.875rem",
    buttonSize: "1.125rem",
    titleWeight: 800,
    bodyWeight: 600,
  },
  /** Animations for feedback and transitions. */
  animation: {
    feedbackDuration: "0.5s",
    overlayDuration: "0.3s",
    bounceScale: 1.08,
  },
  /** Session limit for kids (20 minutes, in seconds). */
  sessionLimitSeconds: 1200,
  /** Reminder threshold (5 minutes before limit). */
  sessionReminderSeconds: 300,
} as const;

/** CSS custom property names for kids theme injection. */
export const kidsCssVars: Record<string, string> = {
  "--kids-primary": kidsTheme.colors.primary,
  "--kids-primary-hover": kidsTheme.colors.primaryHover,
  "--kids-success": kidsTheme.colors.success,
  "--kids-success-bg": kidsTheme.colors.successBg,
  "--kids-danger": kidsTheme.colors.danger,
  "--kids-danger-bg": kidsTheme.colors.dangerBg,
  "--kids-warning": kidsTheme.colors.warning,
  "--kids-warning-bg": kidsTheme.colors.warningBg,
  "--kids-star": kidsTheme.colors.star,
  "--kids-locked": kidsTheme.colors.locked,
  "--kids-boss": kidsTheme.colors.boss,
  "--kids-radius-card": kidsTheme.borderRadius.card,
  "--kids-radius-button": kidsTheme.borderRadius.button,
  "--kids-radius-chip": kidsTheme.borderRadius.chip,
};
