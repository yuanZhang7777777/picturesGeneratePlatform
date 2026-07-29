export interface AttemptedOutput {
  id: string;
  slot: string;
  slotId: string;
  slotOrder: number;
  attempt: number;
}

export function currentOutputs<T extends AttemptedOutput>(outputs: T[]): T[] {
  const latest = new Map<string, T>();
  outputs.forEach((output) => {
    const current = latest.get(output.slotId);
    if (!current || output.attempt > current.attempt) latest.set(output.slotId, output);
  });
  return Array.from(latest.values()).sort((left, right) => left.slotOrder - right.slotOrder);
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
