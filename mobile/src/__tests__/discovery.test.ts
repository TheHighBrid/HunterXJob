import { linkedLabel, livenessBadgeStatus, livenessInfo, livenessLine, matchMethodLabel, sourceLabel } from "@/discovery";

describe("sourceLabel", () => {
  it("names the supported ATS sources", () => {
    expect(sourceLabel("greenhouse")).toBe("Greenhouse");
    expect(sourceLabel("lever")).toBe("Lever");
    expect(sourceLabel("ashby")).toBe("Ashby");
    expect(sourceLabel("feed")).toBe("Feed");
    expect(sourceLabel(null)).toBe("Unknown source");
  });
});

describe("livenessInfo", () => {
  it("never shows a suspect posting as closed", () => {
    expect(livenessInfo("suspect")?.label).toBe("May be closed");
    expect(livenessInfo("suspect")?.tone).toBe("warning");
    expect(livenessInfo("unknown")?.tone).toBe("muted");
    expect(livenessInfo("closed")?.tone).toBe("danger");
    expect(livenessInfo("live")?.tone).toBe("success");
  });

  it("shows nothing for never-checked postings", () => {
    expect(livenessInfo(null)).toBeNull();
    expect(livenessInfo("weird")).toBeNull();
  });
});

describe("linkedLabel", () => {
  it("labels duplicates and primaries", () => {
    expect(linkedLabel({ duplicate_of_id: "j1", linked_count: 1, stage: "applying" })).toBe("Duplicate");
    expect(linkedLabel({ duplicate_of_id: "j1", linked_count: 1, stage: "duplicate" })).toBeNull();
    expect(linkedLabel({ duplicate_of_id: null, linked_count: 2, stage: "scored" })).toBe("2 linked postings");
    expect(linkedLabel({ duplicate_of_id: null, linked_count: 1, stage: "scored" })).toBe("1 linked posting");
    expect(linkedLabel({ duplicate_of_id: null, linked_count: 0, stage: "scored" })).toBeNull();
  });

  it("doesn't repeat the closed stage as a liveness badge", () => {
    expect(livenessBadgeStatus({ liveness: "closed", stage: "closed" })).toBeNull();
    expect(livenessBadgeStatus({ liveness: "suspect", stage: "ready_to_apply" })).toBe("suspect");
    expect(livenessBadgeStatus({ liveness: null, stage: "scored" })).toBeNull();
  });
});

describe("liveness history", () => {
  it("formats a check line", () => {
    const line = livenessLine({
      checked_at: "2026-10-01T12:00:00Z",
      trigger: "cycle",
      outcome: "gone",
      signal: "http_404",
      http_status: 404,
      action: "marked_suspect",
      detail: "",
    });
    expect(line).toBe("gone (HTTP 404): http 404 → marked suspect");
    expect(matchMethodLabel("fuzzy")).toBe("near-identical title and description");
    expect(matchMethodLabel(undefined)).toBe("linked");
  });
});
