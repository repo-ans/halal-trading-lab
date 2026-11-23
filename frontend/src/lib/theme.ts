import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const STORAGE_KEY = "halal-trading-lab-theme";

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "light" || stored === "dark") return stored;
    } catch {
      // ignore -- private browsing / storage disabled, fall through to default
    }
    return "dark";
  });

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // ignore
    }
  }, [theme]);

  function toggle() {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }

  return [theme, toggle];
}

// For canvas-rendered things (lightweight-charts) that can't pick up CSS
// variables -- watches <html data-theme> directly rather than its own
// localStorage-backed state, so it reacts to the SAME toggle the app shell
// uses (a second independent useTheme() call would have its own un-synced
// state and miss updates from elsewhere in the tree).
export function useThemeAttr(): Theme {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.getAttribute("data-theme") as Theme | null) ?? "dark",
  );

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme((document.documentElement.getAttribute("data-theme") as Theme | null) ?? "dark");
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}
