"use client";

/**
 * "New project" dialog — name + description form that POSTs to
 * /api/projects and, on success, sets the new project active and navigates
 * into its chat.
 *
 * Controlled externally via `open`/`onOpenChange`, or self-contained if a
 * `trigger` is passed.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useCreateProject } from "@/hooks/use-projects";
import { useActiveProject } from "@/components/active-project-provider";

export function NewProjectDialog({
  open,
  onOpenChange,
  trigger,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
}) {
  const router = useRouter();
  const { setActiveProjectId } = useActiveProject();
  const createProject = useCreateProject();

  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : uncontrolledOpen;
  const setOpen = (v: boolean) => {
    if (isControlled) onOpenChange?.(v);
    else setUncontrolledOpen(v);
  };

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  // Reset the form whenever the dialog opens.
  useEffect(() => {
    if (isOpen) {
      setName("");
      setDescription("");
      createProject.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || createProject.isPending) return;
    const project = await createProject.mutateAsync({
      name: trimmed,
      description: description.trim() || undefined,
    });
    setActiveProjectId(project.id);
    setOpen(false);
    // Enter the new project's chat.
    router.push("/chat");
  };

  return (
    <Dialog open={isOpen} onOpenChange={setOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New project</DialogTitle>
          <DialogDescription>
            Group the papers for one survey. Every answer cites only documents in
            this project.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <label
              htmlFor="project-name"
              className="pl-mono-label"
            >
              Name
            </label>
            <Input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Retrieval survey"
              autoFocus
              required
            />
          </div>

          <div className="grid gap-1.5">
            <label
              htmlFor="project-description"
              className="pl-mono-label"
            >
              Description <span className="text-ink-faint">(optional)</span>
            </label>
            <textarea
              id="project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What are you trying to find out?"
              rows={3}
              className="resize-none rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint focus-visible:border-accent-brand/60"
            />
          </div>

          {createProject.isError && (
            <p className="text-sm text-red">
              {(createProject.error as Error).message}
            </p>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              className="border-line"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!name.trim() || createProject.isPending}
              className="bg-primary text-primary-foreground hover:bg-accent-brand-ink"
            >
              {createProject.isPending ? "Creating…" : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
