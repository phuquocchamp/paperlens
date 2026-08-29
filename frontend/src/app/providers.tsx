"use client";

/**
 * Client-side context providers shared by the whole app:
 *   - TanStack Query for projects/documents lists (RSC prefetch → hydration in
 *     later phases; the client here owns polling and mutations).
 *   - shadcn TooltipProvider (sidebar icon-mode tooltips need it at the root).
 *
 * Chat message state is intentionally NOT in React Query — it lives in `useChat`
 * (CONTRACT §6 state map).
 */

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
    </QueryClientProvider>
  );
}
