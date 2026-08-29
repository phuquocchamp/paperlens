"use client";

/**
 * React Query hooks for the projects collection.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createProject,
  fetchProjects,
  queryKeys,
  type Project,
} from "@/lib/projects";

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: ({ signal }) => fetchProjects(signal),
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; description?: string }) =>
      createProject(input),
    onSuccess: (project: Project) => {
      // Seed the cache so the new project shows without waiting for a refetch.
      qc.setQueryData<Project[]>(queryKeys.projects, (prev) =>
        prev ? [...prev, project] : [project],
      );
      void qc.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}
