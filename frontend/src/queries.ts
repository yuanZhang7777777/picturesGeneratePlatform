import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { loadProject, loadWorkspace } from "./api";
import { projectHasActiveWork, snapshotPollInterval } from "./workspace";

function usePageHidden() {
  const [hidden, setHidden] = useState(document.hidden);
  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return hidden;
}

export function useWorkspaceSnapshot() {
  const hidden = usePageHidden();
  return useQuery({
    queryKey: ["workspace"],
    queryFn: loadWorkspace,
    refetchInterval: (query) => snapshotPollInterval(
      query.state.data?.projects.some(projectHasActiveWork) ?? false,
      hidden,
    ),
  });
}

export function useProjectSnapshot(projectId: string | undefined) {
  const hidden = usePageHidden();
  return useQuery({
    queryKey: ["project", projectId],
    queryFn: () => loadProject(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: (query) => snapshotPollInterval(
      query.state.data ? projectHasActiveWork(query.state.data) : false,
      hidden,
    ),
  });
}
