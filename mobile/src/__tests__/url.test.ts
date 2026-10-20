import { isPlainHttpPublic, normalizeServerUrl, SERVER_URL_PLACEHOLDER, validateApiKey } from "@/lib/url";

describe("normalizeServerUrl", () => {
  it.each([
    ["hunterx.tail1234.ts.net:8011", "http://hunterx.tail1234.ts.net:8011"],
    ["  http://100.101.102.103:8011/  ", "http://100.101.102.103:8011"],
    ["https://hunterx.example.com", "https://hunterx.example.com"],
    ["https://hunterx.example.com/base/", "https://hunterx.example.com/base"],
  ])("accepts %s", (input, expected) => {
    expect(normalizeServerUrl(input)).toEqual({ ok: true, url: expected });
  });

  it.each(["", "ftp://host", "http://host:99999", SERVER_URL_PLACEHOLDER, "http://exa mple.com"])("rejects %s", (input) => {
    expect(normalizeServerUrl(input).ok).toBe(false);
  });

  it("has no hard-coded server, only a placeholder", () => {
    expect(SERVER_URL_PLACEHOLDER).toContain("<");
    expect(SERVER_URL_PLACEHOLDER).toContain(":8011");
  });
});

describe("isPlainHttpPublic", () => {
  it("flags unencrypted public hosts only", () => {
    expect(isPlainHttpPublic("http://203.0.113.10:8011")).toBe(true);
    expect(isPlainHttpPublic("http://hunterx.example.com:8011")).toBe(true);
    expect(isPlainHttpPublic("https://hunterx.example.com")).toBe(false);
    expect(isPlainHttpPublic("http://hunterx.tail1234.ts.net:8011")).toBe(false);
    expect(isPlainHttpPublic("http://100.64.1.2:8011")).toBe(false);
    expect(isPlainHttpPublic("http://192.168.1.20:8011")).toBe(false);
  });
});

describe("validateApiKey", () => {
  it("requires at least 32 characters", () => {
    expect(validateApiKey("")).toMatch(/Enter/);
    expect(validateApiKey("short")).toMatch(/32/);
    expect(validateApiKey("k".repeat(32))).toBeNull();
  });
});
