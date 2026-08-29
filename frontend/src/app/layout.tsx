import type { Metadata } from "next";
import { Bricolage_Grotesque, Source_Sans_3, IBM_Plex_Mono } from "next/font/google";
import { cookies } from "next/headers";
import "./globals.css";

import { Providers } from "./providers";
import { AppShell } from "@/components/app-shell";
import { ActiveProjectProvider } from "@/components/active-project-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { isTheme, themeClass, type Theme } from "@/lib/theme";

// Headings — "Bricolage Grotesque" (variable family, 600/700 used in the UI).
const fontHeading = Bricolage_Grotesque({
  variable: "--font-heading",
  subsets: ["latin"],
  display: "swap",
});

// Body — "Source Sans 3" (variable family; 400/600/700 used).
const fontSans = Source_Sans_3({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

// Labels, chips, and monospace bits — "IBM Plex Mono" (static: weights required).
const fontMono = IBM_Plex_Mono({
  variable: "--font-mono",
  weight: ["400", "500"],
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "PaperLens",
  description:
    "Project-scoped, page-verifiable Q&A over your corpus of scientific papers.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Read the sidebar collapse state on the server so the first paint matches —
  // no localStorage hydration flash (CONTRACT §6). shadcn's SidebarProvider
  // writes this same `sidebar_state` cookie on every toggle.
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultSidebarOpen = sidebarState !== "false";

  // Active project, read server-side so the sidebar/chat/documents paint the
  // right project on first render (no flash). Written client-side on switch.
  const activeProjectId = cookieStore.get("pl_project")?.value ?? null;

  // Theme, read server-side so the first paint uses the chosen theme with no
  // flash. "system" (or unset) => no class, and the CSS media query decides.
  const themeCookie = cookieStore.get("pl_theme")?.value;
  const initialTheme: Theme = isTheme(themeCookie) ? themeCookie : "system";

  return (
    <html lang="en" className={themeClass(initialTheme)} suppressHydrationWarning>
      <body
        className={`${fontHeading.variable} ${fontSans.variable} ${fontMono.variable} antialiased`}
      >
        <ThemeProvider initialTheme={initialTheme}>
          <Providers>
            <ActiveProjectProvider initialProjectId={activeProjectId}>
              <AppShell defaultSidebarOpen={defaultSidebarOpen}>
                {children}
              </AppShell>
            </ActiveProjectProvider>
          </Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
