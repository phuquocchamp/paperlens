"use client";

/**
 * App shell: the SidebarProvider + AppSidebar + main content region.
 *
 * The sidebar is 264px expanded / 64px icon-only (CONTRACT §6) — overriding the
 * shadcn defaults via CSS vars on the provider. `defaultOpen` comes from the
 * server-read `sidebar_state` cookie so there is no hydration flash.
 */

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";

export function AppShell({
  children,
  defaultSidebarOpen,
}: {
  children: React.ReactNode;
  defaultSidebarOpen: boolean;
}) {
  return (
    <SidebarProvider
      defaultOpen={defaultSidebarOpen}
      style={
        {
          "--sidebar-width": "264px",
          "--sidebar-width-icon": "64px",
        } as React.CSSProperties
      }
    >
      <AppSidebar />
      <SidebarInset>{children}</SidebarInset>
    </SidebarProvider>
  );
}
