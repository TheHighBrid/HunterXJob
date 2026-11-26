import { humanize } from "@/utils/format";

describe("humanize stage labels", () => {
  it("labels the form_filled stage as Form Planned, not Form Filled", () => {
    expect(humanize("form_filled")).toBe("Form Planned");
    expect(humanize("form_filled")).not.toMatch(/filled|typed/i);
  });

  it("keeps the other mapped stage labels unchanged", () => {
    expect(humanize("validated")).toBe("Form Verified");
    expect(humanize("ready_to_apply")).toBe("Ready to Apply");
    expect(humanize("needs_review")).toBe("Review Required");
    expect(humanize("submitted")).toBe("Submitted ✓");
  });
});
