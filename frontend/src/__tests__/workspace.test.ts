import { expect, test } from "vitest";
import { currentOutputs, snapshotPollInterval } from "../workspace";

test("counts only the highest attempt for each output slot while retaining version history", () => {
  const result = currentOutputs([
    { id: "old-main", slot: "主图", attempt: 1 },
    { id: "new-main", slot: "主图", attempt: 2 },
    { id: "detail", slot: "细节图", attempt: 1 },
  ]);

  expect(result.map((output) => output.id)).toEqual(["new-main", "detail"]);
});

test("polls active snapshots every 3 seconds in foreground and 15 seconds in background", () => {
  expect(snapshotPollInterval(true, false)).toBe(3000);
  expect(snapshotPollInterval(true, true)).toBe(15000);
  expect(snapshotPollInterval(false, false)).toBe(false);
});
