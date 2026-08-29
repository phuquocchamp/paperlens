// Theme handling. The CSS in globals.css already supports three states:
//   - `.dark`  class on <html> → dark tokens
//   - `.light` class on <html> → light tokens (and blocks OS dark)
//   - neither  → follow the OS `prefers-color-scheme`
// So switching themes is just toggling that class + persisting the choice in a
// cookie the server reads on first paint (no hydration flash).

export type Theme = "light" | "dark" | "system";

export const THEME_COOKIE = "pl_theme";

export function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

/** Class to place on <html>. "system" => no class (the CSS media query decides). */
export function themeClass(theme: Theme): "" | "light" | "dark" {
  return theme === "system" ? "" : theme;
}

/** Apply the theme to <html> and persist it in a cookie (client only). */
export function applyTheme(theme: Theme): void {
  const el = document.documentElement;
  el.classList.remove("light", "dark");
  const cls = themeClass(theme);
  if (cls) el.classList.add(cls);
  // 1-year cookie so the server renders the correct class with no flash.
  document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax`;
}
