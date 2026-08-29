"use client";

/**
 * Projects dashboard — the landing page (`/` redirects here).
 *
 * Lists every project (GET /api/projects) as cards; a "New project" button opens
 * the create dialog. Clicking a card sets it active (cookie) and enters its
 * chat. Empty state prompts the first project.
 *
 * Per-card document counts are intentionally omitted — they would cost one
 * request per project. The active project's real counts show in the sidebar.
 */

import { useRouter } from "next/navigation";
import { FolderPlus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjects } from "@/hooks/use-projects";
import { useActiveProject } from "@/components/active-project-provider";
import { NewProjectDialog } from "@/components/projects/new-project-dialog";
import type { Project } from "@/lib/projects";

/** Deterministic date format (fixed locale + UTC) — no hydration mismatch. */
function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(d);
}

function ProjectCard({
  project,
  onOpen,
}: {
  project: Project;
  onOpen: (p: Project) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(project)}
      className="group flex flex-col gap-3 rounded-xl border border-line bg-surface p-5 text-left transition-colors hover:border-accent-brand/50 hover:bg-line-soft/30"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent-brand-soft text-accent-brand-ink">
          <span className="size-3 rounded-full border-2 border-current" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-heading text-base font-semibold text-ink">
            {project.name}
          </h3>
          <p className="mt-1 line-clamp-2 text-sm text-ink-soft">
            {project.description || "No description"}
          </p>
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between">
        <span className="font-mono text-xs text-ink-faint">
          Created {formatDate(project.created_at)}
        </span>
        <span className="text-xs font-medium text-accent-brand opacity-0 transition-opacity group-hover:opacity-100">
          Open →
        </span>
      </div>
    </button>
  );
}

export default function ProjectsDashboard() {
  const router = useRouter();
  const { setActiveProjectId } = useActiveProject();
  const { data: projects, isLoading, isError, error } = useProjects();

  const openProject = (p: Project) => {
    setActiveProjectId(p.id);
    router.push("/chat");
  };

  return (
    <div className="flex h-svh min-h-0 w-full flex-col overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-10">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-heading text-2xl font-bold text-ink">Projects</h1>
            <p className="mt-1 text-sm text-ink-soft">
              Each project is a corpus of papers you ask questions across.
            </p>
          </div>
          <NewProjectDialog
            trigger={
              <Button className="bg-primary text-primary-foreground hover:bg-accent-brand-ink">
                <Plus className="size-4" />
                New project
              </Button>
            }
          />
        </header>

        <div className="mt-8">
          {isLoading && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-36 rounded-xl" />
              ))}
            </div>
          )}

          {isError && (
            <div className="rounded-xl border border-red/30 bg-red-soft/50 p-6 text-sm text-red">
              Failed to load projects: {(error as Error)?.message}
            </div>
          )}

          {!isLoading && !isError && projects && projects.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-4 rounded-2xl border border-dashed border-line bg-surface px-6 py-16 text-center">
              <span className="flex size-12 items-center justify-center rounded-full bg-accent-brand-soft text-accent-brand-ink">
                <FolderPlus className="size-6" />
              </span>
              <div>
                <p className="font-heading text-lg font-semibold text-ink">
                  Create your first project
                </p>
                <p className="mt-1 max-w-sm text-sm text-ink-faint">
                  Upload 10–30 papers on a technique, then ask questions with
                  page-verifiable citations.
                </p>
              </div>
              <NewProjectDialog
                trigger={
                  <Button className="bg-primary text-primary-foreground hover:bg-accent-brand-ink">
                    <Plus className="size-4" />
                    New project
                  </Button>
                }
              />
            </div>
          )}

          {!isLoading && !isError && projects && projects.length > 0 && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {projects.map((p) => (
                <ProjectCard key={p.id} project={p} onOpen={openProject} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
