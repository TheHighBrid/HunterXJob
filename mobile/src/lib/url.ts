/**
 * Server URL helpers. There is deliberately no built-in server address: the
 * owner types their own (typically a Tailscale name or 100.x address on port
 * 8011). SERVER_URL_PLACEHOLDER is only an input hint.
 */
export const DEFAULT_PORT = 8011;
export const SERVER_URL_PLACEHOLDER = `http://<your-vm>.<tailnet>.ts.net:${DEFAULT_PORT}`;
export const MIN_API_KEY_LENGTH = 32;

export type UrlCheck = { ok: true; url: string } | { ok: false; error: string };

const URL_PATTERN = /^(https?):\/\/([^/?#:\s]+|\[[0-9a-f:]+\])(?::(\d{1,5}))?(\/[^?#\s]*)?$/i;

function withScheme(value: string): string {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `http://${value}`;
}

function precheck(value: string): string | null {
  if (!value) return "Enter the server URL.";
  if (value.includes("<") || value.includes(">")) return "Replace the <placeholder> parts with your server's name or IP.";
  return null;
}

/** Trim, add http:// when no scheme is given, drop trailing slashes, validate. */
export function normalizeServerUrl(input: string): UrlCheck {
  const trimmed = input.trim();
  const problem = precheck(trimmed);
  if (problem) return { ok: false, error: problem };
  const value = withScheme(trimmed).replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(value)) return { ok: false, error: "Use an http:// or https:// URL." };
  const match = URL_PATTERN.exec(value);
  if (!match) return { ok: false, error: "That doesn't look like a valid URL." };
  const port = match[3] ? Number(match[3]) : 1;
  if (port < 1 || port > 65535) return { ok: false, error: "The port must be 1-65535." };
  return { ok: true, url: value };
}

/** True when traffic to this URL is unencrypted and not obviously private. */
export function isPlainHttpPublic(url: string): boolean {
  const match = /^http:\/\/([^/:]+)/i.exec(url);
  if (!match) return false;
  const host = match[1].toLowerCase();
  const isPrivate =
    host === "localhost" ||
    host.endsWith(".ts.net") ||
    host.endsWith(".local") ||
    /^127\./.test(host) ||
    /^10\./.test(host) ||
    /^192\.168\./.test(host) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
    /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(host); // Tailscale CGNAT range
  return !isPrivate;
}

export function validateApiKey(key: string): string | null {
  const value = key.trim();
  if (!value) return "Enter the API key from the server's .env (API_KEY).";
  if (value.length < MIN_API_KEY_LENGTH) return `The API key must be at least ${MIN_API_KEY_LENGTH} characters.`;
  return null;
}
