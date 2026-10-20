import type { ProfileFact } from "@/api/types";
import { applyEdits, editableFields, factSubtitle, factTitle, groupFacts, readinessCopy } from "@/profile";

function fact(category: string, data: Record<string, unknown>, verified = false, key = `${category}:x`): ProfileFact {
  return { id: key, key, category, data, verified, source: "file", provenance: "profile.yaml#x", verified_at: null, updated_at: null };
}

describe("profile helpers", () => {
  it("labels facts by category", () => {
    const job = fact("employment", { employer: "Example Co", title: "Analyst", start: "2020-01", end: null, current: true, location: "Ottawa, ON" });
    expect(factTitle(job)).toBe("Analyst — Example Co");
    expect(factSubtitle(job)).toBe("2020-01 – present · Ottawa, ON");
    expect(factTitle(fact("work_authorization", { country: "US", authorized: false, requires_sponsorship: true }))).toBe(
      "US: Not authorized, needs sponsorship",
    );
    expect(factTitle(fact("contact", { field: "first_name", value: "Sam" }))).toBe("first name: Sam");
    expect(factSubtitle(fact("achievement", { text: "Cut losses", metrics: ["18%"], skills: ["SQL"] }))).toBe("Metrics: 18% · SQL");
    expect(factTitle(fact("skill", { name: "SQL", aliases: [] }))).toBe("SQL");
  });

  it("groups facts in a stable order and counts unverified ones", () => {
    const groups = groupFacts([
      fact("skill", { name: "SQL" }, true, "skill:sql"),
      fact("contact", { field: "email", value: "a@example.com" }, false, "contact:email"),
      fact("skill", { name: "Excel" }, false, "skill:excel"),
    ]);
    expect(groups.map((group) => group.category)).toEqual(["contact", "skill"]);
    expect(groups[1]?.unverified).toBe(1);
  });

  it("round-trips edits with lists, booleans, numbers and nulls", () => {
    const data = { name: "SQL", aliases: ["PostgreSQL"], years: 4, level: "" };
    const fields = editableFields(data);
    expect(fields.find((item) => item.name === "aliases")?.value).toBe("PostgreSQL");
    const edits = new Map([
      ["aliases", "PostgreSQL, T-SQL"],
      ["years", ""],
    ]);
    expect(applyEdits(data, edits)).toEqual({ name: "SQL", aliases: ["PostgreSQL", "T-SQL"], years: null, level: "" });
    const auth = { country: "CA", authorized: false, requires_sponsorship: null };
    expect(applyEdits(auth, new Map([["authorized", "true"]]))).toEqual({ country: "CA", authorized: true, requires_sponsorship: null });
  });

  it("explains readiness", () => {
    expect(readinessCopy(true, [])).toMatch(/^Ready/);
    expect(readinessCopy(false, ["verified email"])).toContain("verified email");
  });
});
