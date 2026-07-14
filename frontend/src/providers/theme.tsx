import { useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";

import { ThemeContext, type Theme } from "./theme-context";

const THEME_STORAGE_KEY = "achilles.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribeSystem(onChange: () => void): () => void {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", onChange);
  return () => {
    media.removeEventListener("change", onChange);
  };
}

function systemPrefersDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches;
}

/** localStorage throws when the browser blocks site data; theme persistence is
 * a nicety, so fall back to the system preference instead of crashing. */
function storedTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" || stored === "system" ? stored : "light";
  } catch {
    return "light";
  }
}

function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Blocked storage — the choice just won't persist across reloads.
  }
}

/** shadcn token theming: the resolved theme lands as a `dark` class on <html>. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(storedTheme);
  const systemDark = useSyncExternalStore(subscribeSystem, systemPrefersDark);
  const resolvedTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;

  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  }, [resolvedTheme]);

  const value = useMemo(
    () => ({
      theme,
      resolvedTheme,
      setTheme: (next: Theme) => {
        persistTheme(next);
        setThemeState(next);
      },
    }),
    [theme, resolvedTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
