"use client";

/**
 * AppSidebar — project-scoped navigation (CONTRACT §6, artboards 1 & 2).
 *
 * Layout (top → bottom): PaperLens brand · PROJECT switcher (name + subtitle +
 * chevron) · green "New chat" · CHATS list · "DOCUMENTS · N" with a per-file
 * status dot (green = ready, amber = processing, red = rejected/failed) ·
 * Settings. Section labels are IBM Plex Mono, uppercase, faint.
 *
 * Real data: projects via GET /api/projects, documents via the polling
 * `useDocuments` hook, conversations via `useConversations` (M7). The active
 * project comes from the `pl_project` cookie (server-seeded), falling back to
 * the first project. Switching projects updates the cookie and enters that
 * project's chat.
 */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronsUpDown,
  FileText,
  FolderOpen,
  MessageSquare,
  Plus,
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useProjects } from "@/hooks/use-projects";
import { useDocuments } from "@/hooks/use-documents";
import { useConversations } from "@/hooks/use-conversations";
import { useActiveProject } from "@/components/active-project-provider";
import { NewProjectDialog } from "@/components/projects/new-project-dialog";
import { SettingsMenu } from "@/components/settings-menu";
import type { DocumentRow } from "@/lib/projects";

type DotStatus = "ready" | "processing" | "error";

/** Map a backend DocumentStatus to a sidebar dot colour. */
function dotStatus(status: string): DotStatus {
  if (status === "text_ready" || status === "full_ready") return "ready";
  if (status === "rejected" || status === "failed") return "error";
  return "processing"; // uploaded | queued | processing
}

function StatusDot({ status }: { status: DotStatus }) {
  return (
    <span
      aria-label={`Status: ${status}`}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        status === "ready" && "bg-accent-brand",
        status === "processing" && "animate-pulse bg-amber",
        status === "error" && "bg-red",
      )}
    />
  );
}

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { activeProjectId, setActiveProjectId } = useActiveProject();
  const { data: projects } = useProjects();
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  // Resolve the active project: the cookie's id if it still exists, else the
  // first project. Persist the fallback so the rest of the app agrees.
  const activeProject = useMemo(() => {
    if (!projects || projects.length === 0) return null;
    return projects.find((p) => p.id === activeProjectId) ?? projects[0];
  }, [projects, activeProjectId]);

  useEffect(() => {
    if (activeProject && activeProject.id !== activeProjectId) {
      setActiveProjectId(activeProject.id);
    }
  }, [activeProject, activeProjectId, setActiveProjectId]);

  const { data: documents } = useDocuments(activeProject?.id ?? null);
  const { data: conversations } = useConversations(activeProject?.id ?? null);

  const visibleDocs = useMemo(
    () => (documents ?? []).filter((d) => d.status !== "deleted"),
    [documents],
  );

  const processingCount = useMemo(
    () =>
      visibleDocs.filter((d) =>
        ["uploaded", "queued", "processing"].includes(d.status),
      ).length,
    [visibleDocs],
  );

  const subtitle = activeProject
    ? `${visibleDocs.length} paper${visibleDocs.length === 1 ? "" : "s"}${
        processingCount ? ` · ${processingCount} processing` : ""
      }`
    : "No project selected";

  const switchProject = (id: string) => {
    setActiveProjectId(id);
    router.push("/chat");
  };

  return (
    <Sidebar collapsible="icon" className="border-line">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <Link
              href="/projects"
              className="flex items-center gap-2 px-1.5 py-1"
            >
              <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
                <span className="size-2.5 rounded-full border-2 border-current" />
              </span>
              <span className="font-heading text-base font-bold text-ink group-data-[collapsible=icon]:hidden">
                PaperLens
              </span>
            </Link>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        {/* PROJECT */}
        <SidebarGroup>
          <SidebarGroupLabel className="pl-mono-label group-data-[collapsible=icon]:hidden">
            Project
          </SidebarGroupLabel>
          <SidebarMenu>
            <SidebarMenuItem>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <SidebarMenuButton
                    size="lg"
                    tooltip={activeProject?.name ?? "Projects"}
                    className="border border-line bg-surface data-[slot=sidebar-menu-button]:!p-2"
                  >
                    <StatusDot
                      status={processingCount > 0 ? "processing" : "ready"}
                    />
                    <div className="grid flex-1 text-left leading-tight">
                      <span className="truncate text-sm font-medium text-ink">
                        {activeProject?.name ?? "Select a project"}
                      </span>
                      <span className="truncate text-xs text-ink-faint">
                        {subtitle}
                      </span>
                    </div>
                    <ChevronsUpDown className="ml-auto size-4 text-ink-faint" />
                  </SidebarMenuButton>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="w-[248px]"
                  side="bottom"
                >
                  <DropdownMenuLabel className="pl-mono-label">
                    Switch project
                  </DropdownMenuLabel>
                  {(projects ?? []).map((p) => (
                    <DropdownMenuItem
                      key={p.id}
                      onSelect={() => switchProject(p.id)}
                      className="gap-2"
                    >
                      <span className="truncate">{p.name}</span>
                      {p.id === activeProject?.id && (
                        <Check className="ml-auto size-4 text-accent-brand" />
                      )}
                    </DropdownMenuItem>
                  ))}
                  {projects && projects.length === 0 && (
                    <div className="px-2 py-1.5 text-xs text-ink-faint">
                      No projects yet
                    </div>
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setNewProjectOpen(true)}>
                    <Plus className="size-4" />
                    New project
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                    <Link href="/projects">
                      <FolderOpen className="size-4" />
                      All projects
                    </Link>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </SidebarMenuItem>

            {/* New chat — green primary; starts a fresh conversation at /chat. */}
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                tooltip="New chat"
                isActive={pathname === "/chat"}
                className="mt-1 bg-primary font-medium text-primary-foreground hover:bg-accent-brand-ink hover:text-primary-foreground data-[active=true]:bg-primary data-[active=true]:text-primary-foreground"
              >
                <Link
                  href="/chat"
                  onClick={(e) => {
                    // Force a fresh chat session even when already on /chat.
                    // A plain same-URL nav does NOT remount ChatView, so its
                    // in-memory messages + conversation id would persist and a
                    // "new" chat would silently append to the previous
                    // conversation. A changing `?new` token makes ChatView reset
                    // (see its reset effect).
                    e.preventDefault();
                    router.push(`/chat?new=${Date.now()}`);
                  }}
                >
                  <Plus />
                  <span>New chat</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>

        {/* CHATS — saved conversations for the active project (newest first). */}
        <SidebarGroup className="group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel className="pl-mono-label">Chats</SidebarGroupLabel>
          <SidebarMenu>
            {(conversations ?? []).length === 0 && (
              <SidebarMenuItem>
                <div className="px-2 py-1.5 text-xs text-ink-faint">
                  No saved chats yet
                </div>
              </SidebarMenuItem>
            )}
            {(conversations ?? []).map((c) => {
              const href = `/chat/${c.id}`;
              return (
                <SidebarMenuItem key={c.id}>
                  <SidebarMenuButton
                    asChild
                    tooltip={c.title ?? "Untitled chat"}
                    isActive={pathname === href}
                    className="h-auto py-1.5"
                  >
                    <Link href={href}>
                      <MessageSquare className="size-3.5 shrink-0 text-ink-faint" />
                      <span className="truncate text-xs text-ink-soft">
                        {c.title ?? "Untitled chat"}
                      </span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            })}
          </SidebarMenu>
        </SidebarGroup>

        {/* DOCUMENTS · N */}
        <SidebarGroup className="group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel className="pl-mono-label">
            Documents · {visibleDocs.length}
          </SidebarGroupLabel>
          <SidebarMenu>
            {visibleDocs.length === 0 && (
              <SidebarMenuItem>
                <SidebarMenuButton asChild className="h-auto py-1.5">
                  <Link href="/documents">
                    <FileText className="size-3.5 text-ink-faint" />
                    <span className="truncate text-xs text-ink-faint">
                      Upload documents
                    </span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            )}
            {visibleDocs.map((doc: DocumentRow) => (
              <SidebarMenuItem key={doc.id}>
                <SidebarMenuButton asChild className="h-auto py-1.5">
                  <Link href="/documents">
                    <StatusDot status={dotStatus(doc.status)} />
                    <span className="truncate text-xs text-ink-soft">
                      {doc.filename}
                    </span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>

        {/* Documents nav entry visible in collapsed icon mode. */}
        <SidebarGroup className="hidden group-data-[collapsible=icon]:block">
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                tooltip="Documents"
                isActive={pathname.startsWith("/documents")}
              >
                <Link href="/documents">
                  <FileText />
                  <span>Documents</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SettingsMenu />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>

      {/* New-project dialog, controlled from the switcher menu. */}
      <NewProjectDialog open={newProjectOpen} onOpenChange={setNewProjectOpen} />
    </Sidebar>
  );
}
