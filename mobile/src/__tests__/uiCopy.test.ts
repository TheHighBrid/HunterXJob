import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DRY_RUN_VERIFIED_TITLE,
  dryRunVerifiedBody,
  JOB_TIMELINE_TITLE,
  QUESTION_STUDIO_TITLE,
} from "@/uiCopy";

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

describe("dry-run success copy", () => {
  it("describes a dry run as rendered and verified, with values planned and nothing typed", () => {
    expect(DRY_RUN_VERIFIED_TITLE).toBe("Dry-Run Verified ✓");
    const body = dryRunVerifiedBody("Acme");
    expect(body).toBe(
      "All fields on Acme were rendered and verified. Values were planned; nothing was typed or submitted.",
    );
    expect(body).toContain("rendered and verified");
    expect(body).toContain("planned");
    expect(body).toMatch(/nothing was typed or submitted/);
    expect(body).not.toMatch(/filled/i);
  });

  it("wires the dry-run toast through uiCopy and does not claim fields were filled", () => {
    const jobsSrc = readFileSync(join(__dirname, "../../app/(tabs)/jobs/[id].tsx"), "utf8");
    expect(jobsSrc).toContain("DRY_RUN_VERIFIED_TITLE");
    expect(jobsSrc).toContain("dryRunVerifiedBody");
    expect(jobsSrc).not.toMatch(/filled and verified/i);
  });
});
