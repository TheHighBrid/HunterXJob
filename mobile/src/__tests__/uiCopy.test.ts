import { readFileSync } from "node:fs";
import { join } from "node:path";

import { JOB_TIMELINE_TITLE, QUESTION_STUDIO_TITLE } from "@/uiCopy";

describe("cosmetic UI branding", () => {
  it("uses HunterXJob on the question studio header", () => {
    expect(QUESTION_STUDIO_TITLE).toBe("Interactive Question Studio (HunterXJob)");
    expect(QUESTION_STUDIO_TITLE).toContain("HunterXJob");
    expect(QUESTION_STUDIO_TITLE).not.toMatch(/JobTomatik/i);
  });

  it("keeps the job detail timeline card label generic", () => {
    expect(JOB_TIMELINE_TITLE).toBe("Timeline");
    expect(JOB_TIMELINE_TITLE).not.toMatch(/JobTomatik/i);
  });

  it("wires those labels into the mobile screens and leaves no JobTomatik UI copy", () => {
    const appDir = join(__dirname, "../../app");
    const reviewSrc = readFileSync(join(appDir, "(tabs)/review/[id].tsx"), "utf8");
    const jobsSrc = readFileSync(join(appDir, "(tabs)/jobs/[id].tsx"), "utf8");

    expect(reviewSrc).toContain("QUESTION_STUDIO_TITLE");
    expect(jobsSrc).toContain("JOB_TIMELINE_TITLE");
    expect(reviewSrc).not.toMatch(/JobTomatik/i);
    expect(jobsSrc).not.toMatch(/JobTomatik/i);
  });
});
