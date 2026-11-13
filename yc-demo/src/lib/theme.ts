import { useEffect, useState } from "react";

// Light/dark theme. Defaults to the system preference; an explicit choice is remembered.
// index.html applies the class before first paint (no flash); this keeps it in sync afterwards.
export type Theme = "light" | "dark";
export const THEME_STORAGE_KEY = "yc-theme";

const stored = (): Theme | null => {
  try { const value = localStorage.getItem(THEME_STORAGE_KEY); return value === "light" || value === "dark" ? value : null; } catch { return null; }
};
const system = (): Theme => (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
}
export function useTheme() {
  // Start as "light" so server and client render the same markup (no hydration mismatch);
  // the effect below syncs to the class the head script already applied, before the user can see the icon change.
  const [theme, setTheme] = useState<Theme>("light");
  useEffect(() => {
    const initial = stored() ?? system(); applyTheme(initial); setTheme(initial);
    const media = matchMedia("(prefers-color-scheme: dark)");
    const follow = () => { if (!stored()) { const next = system(); applyTheme(next); setTheme(next); } };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, []);
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_STORAGE_KEY, next); } catch { /* Storage may be unavailable. */ }
    applyTheme(next); setTheme(next);
  };
  return { theme, toggle };
}
