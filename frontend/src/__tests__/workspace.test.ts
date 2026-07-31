import { expect, test } from "vitest";
import { currentOutputs, projectHasActiveWork, snapshotPollInterval } from "../workspace";

test("counts only the highest attempt for each output slot while retaining version history", () => {
  const result = currentOutputs([
    { id: "old-main", slot: "主图", slotId: "main", slotOrder: 1, attempt: 1 },
    { id: "new-main", slot: "主图", slotId: "main", slotOrder: 1, attempt: 2 },
    { id: "detail", slot: "细节图", slotId: "detail", slotOrder: 2, attempt: 1 },
  ]);

  expect(result.map((output) => output.id)).toEqual(["new-main", "detail"]);
});

test("keeps different slots with the same display name and orders them by server slot order", () => {
  const result = currentOutputs([
    { id: "wide-v1", slot: "详情图", slotId: "wide", slotOrder: 20, attempt: 1 },
    { id: "close-v1", slot: "详情图", slotId: "close", slotOrder: 10, attempt: 1 },
    { id: "wide-v2", slot: "详情图", slotId: "wide", slotOrder: 20, attempt: 2 },
  ]);

  expect(result.map((output) => output.id)).toEqual(["close-v1", "wide-v2"]);
});

test("polls active snapshots every 3 seconds in foreground and 15 seconds in background", () => {
  expect(snapshotPollInterval(true, false)).toBe(3000);
  expect(snapshotPollInterval(true, true)).toBe(15000);
  expect(snapshotPollInterval(false, false)).toBe(false);
});

test("continues polling while an imported product is awaiting preparation", () => {
  expect(projectHasActiveWork({ status: "draft", skus: [{ preparationStatus: "preparing", outputs: [] }] })).toBe(true);
  expect(projectHasActiveWork({ status: "organizing", skus: [{ preparationStatus: "pending", outputs: [] }] })).toBe(true);
  expect(projectHasActiveWork({ status: "completed", skus: [{ preparationStatus: "ready", outputs: [] }] })).toBe(false);
});
