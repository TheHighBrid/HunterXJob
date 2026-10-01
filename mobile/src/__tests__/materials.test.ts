import type { Material } from "@/api/types";
import { byKind, materialsSummary, shortHash, statusTone } from "@/materials";

function material(kind: Material["kind"], version: number, status: Material["status"]): Material {
  return {
    id: `${kind}-${version}`,
    application_id: "app",
    job_id: "job",
    kind,
    version,
    status,
    generator: "template",
    content_sha256: "a".repeat(64),
    profile_sha256: "b".repeat(64),
    has_pdf: true,
    has_docx: true,
    llm: "disabled",
    decision_note: "",
    created_at: null,
    decided_at: null,
    pdf_sha256: null,
    docx_sha256: null,
  };
}

describe("materials helpers", () => {
  it("picks the newest version per kind", () => {
    const items = [material("resume", 1, "superseded"), material("resume", 2, "draft"), material("cover_letter", 1, "approved")];
    const { latest, older } = byKind(items, "resume");
    expect(latest?.version).toBe(2);
    expect(older.map((item) => item.version)).toEqual([1]);
    expect(byKind([], "cover_letter").latest).toBeUndefined();
  });

  it("summarises the approval state without implying anything was attached", () => {
    expect(materialsSummary([])).toMatch(/No materials yet/);
    expect(materialsSummary([material("resume", 1, "draft")])).toMatch(/1 draft waiting/);
    expect(materialsSummary([material("resume", 1, "approved")])).toMatch(/approved résumé/);
    expect(materialsSummary([material("resume", 1, "rejected")])).toMatch(/No approved résumé/);
  });

  it("formats hashes and status tones", () => {
    expect(shortHash("0123456789abcdef")).toBe("0123456789ab");
    expect(shortHash(null)).toBe("—");
    expect(statusTone("approved")).toBe("success");
    expect(statusTone("draft")).toBe("warning");
    expect(statusTone("rejected")).toBe("danger");
    expect(statusTone("superseded")).toBe("muted");
  });
});
