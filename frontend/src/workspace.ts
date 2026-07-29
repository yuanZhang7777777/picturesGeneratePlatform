export interface AttemptedOutput {
  id: string;
  slot: string;
  attempt: number;
}

export function currentOutputs<T extends AttemptedOutput>(outputs: T[]): T[] {
  const latest = new Map<string, T>();
  outputs.forEach((output) => {
    const current = latest.get(output.slot);
    if (!current || output.attempt > current.attempt) latest.set(output.slot, output);
  });
  return Array.from(latest.values());
}

export function snapshotPollInterval(active: boolean, hidden: boolean): number | false {
  if (!active) return false;
  return hidden ? 15_000 : 3_000;
}

export function projectHasActiveWork(project: { status: string; skus: Array<{ outputs: Array<{ status: string }> }> }) {
  return project.status === "queued" || project.status === "running" || project.skus.some((sku) =>
    sku.outputs.some((output) => output.status === "queued" || output.status === "running"),
  );
}
