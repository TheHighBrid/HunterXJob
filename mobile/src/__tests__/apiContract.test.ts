/**
 * Every endpoint the client calls must exist in v2's OpenAPI snapshot with
 * the same method. (Type shapes are checked by `npm run api:check` + tsc.)
 */
import { readFileSync } from "fs";
import { join } from "path";

const root = join(__dirname, "..", "..");
const openapi = JSON.parse(readFileSync(join(root, "..", "v2", "openapi.json"), "utf8")) as {
  paths: Record<string, Record<string, unknown>>;
};
const clientSource = readFileSync(join(root, "src", "api", "client.ts"), "utf8");

function normalize(path: string): string {
  return path.split("?")[0].replace(/\$\{[^}]+\}/g, "{param}").replace(/\{[a-z_]+\}/g, "{param}");
}

describe("API contract", () => {
  const known = new Map<string, Set<string>>();
  for (const [path, methods] of Object.entries(openapi.paths)) {
    known.set(normalize(path), new Set(Object.keys(methods).map((m) => m.toUpperCase())));
  }

  const calls: { method: string; path: string }[] = [];
  const pattern = /(request|post)<[^>]+>\(\s*[`"](\/api\/[^`"]*)[`"]/g;
  for (const match of clientSource.matchAll(pattern)) {
    const start = match.index + match[0].length;
    const restOfLine = clientSource.slice(start, clientSource.indexOf("\n", start));
    const explicit = /method: "(\w+)"/.exec(restOfLine);
    const method = match[1] === "post" ? "POST" : explicit ? explicit[1] : "GET";
    calls.push({ method, path: normalize(match[2]) });
  }

  it("finds the client's calls", () => {
    expect(calls.length).toBeGreaterThanOrEqual(18);
  });

  it.each(calls.map((c) => [`${c.method} ${c.path}`, c] as const))("%s exists in v2/openapi.json", (_label, call) => {
    expect(known.get(call.path)?.has(call.method)).toBe(true);
  });

  it("never calls v1-only endpoints", () => {
    expect(clientSource).not.toMatch(/\/api\/applications["`]/);
    expect(clientSource).not.toMatch(/\/api\/reports\/latest/);
    expect(clientSource).not.toMatch(/:8000/);
  });
});
