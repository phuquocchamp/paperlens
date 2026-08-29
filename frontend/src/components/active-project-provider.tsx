"use client";

/**
 * Active-project context.
 *
 * The selected project id is persisted in the `pl_project` cookie so a Server
 * Component (`layout.tsx`) can read it and seed this provider on first paint —
 * no flash of the wrong project. Switching writes the cookie client-side (so the
 * next server render agrees) and updates local state immediately.
 *
 * URL segments were intentionally NOT used: the chat routes (`/chat`,
 * `/chat/[chatId]`) are owned by the streaming wiring and are not re-nested
 * under a project segment.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

export const ACTIVE_PROJECT_COOKIE = "pl_project";

interface ActiveProjectContextValue {
  activeProjectId: string | null;
  setActiveProjectId: (id: string | null) => void;
}

const ActiveProjectContext = createContext<ActiveProjectContextValue | null>(
  null,
);

/** Write the cookie so the next server render reads the same active project. */
function writeCookie(id: string | null) {
  if (typeof document === "undefined") return;
  const maxAge = 60 * 60 * 24 * 365; // 1 year
  if (id) {
    document.cookie = `${ACTIVE_PROJECT_COOKIE}=${encodeURIComponent(
      id,
    )}; path=/; max-age=${maxAge}; samesite=lax`;
  } else {
    document.cookie = `${ACTIVE_PROJECT_COOKIE}=; path=/; max-age=0; samesite=lax`;
  }
}

export function ActiveProjectProvider({
  initialProjectId,
  children,
}: {
  initialProjectId: string | null;
  children: React.ReactNode;
}) {
  const [activeProjectId, setActiveProjectIdState] = useState<string | null>(
    initialProjectId,
  );

  const setActiveProjectId = useCallback((id: string | null) => {
    writeCookie(id);
    setActiveProjectIdState(id);
  }, []);

  const value = useMemo(
    () => ({ activeProjectId, setActiveProjectId }),
    [activeProjectId, setActiveProjectId],
  );

  return (
    <ActiveProjectContext.Provider value={value}>
      {children}
    </ActiveProjectContext.Provider>
  );
}

export function useActiveProject(): ActiveProjectContextValue {
  const ctx = useContext(ActiveProjectContext);
  if (!ctx) {
    throw new Error(
      "useActiveProject must be used within an ActiveProjectProvider",
    );
  }
  return ctx;
}
