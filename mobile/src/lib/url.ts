/**
 * Server URL helpers. There is deliberately no built-in server address: the
 * owner types their own (typically a Tailscale name or 100.x address on port
 * 8011). SERVER_URL_PLACEHOLDER is only an input hint.
 */
export const DEFAULT_PORT = 8011;
export const SERVER_URL_PLACEHOLDER = `http://<your-vm>.<tailnet>.ts.net:${DEFAULT_PORT}`;
export const MIN_API_KEY_LENGTH = 32;

export type UrlCheck = { ok: true; url: string } | { ok: false; error: string };

const SCHEME = /^https?:\/\//i;
const HOST_NAME = /^[^/?#:\s[\]]+$/;
const IPV6_HOST = /^\[[0-9a-f:]+\]$/i;
const PORT = /^\d{1,5}$/;
const PATH_CHARS = /^[^?#\s]*$/;

function withScheme(value: string): string {
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `http://${value}`;
}

function precheck(value: string): string | null {
  if (!value) return "Enter the server URL.";
  if (value.includes("<") || value.includes(">")) return "Replace the <placeholder> parts with your server's name or IP.";
  return null;
}

/** Split "host[:port]" (host may be a bracketed IPv6 literal). */
function splitHostPort(authority: string): { host: string; port: string | null } {
  const close = authority.startsWith("[") ? authority.indexOf("]") : -1;
  const colon = authority.indexOf(":", close + 1);
  if (colon === -1) return { host: authority, port: null };
  return { host: authority.slice(0, colon), port: authority.slice(colon + 1) };
}

/** Error message for an http(s) URL with the scheme already checked, or null when valid. */
function authorityError(rest: string): string | null {
  const slash = rest.indexOf("/");
  const authority = slash === -1 ? rest : rest.slice(0, slash);
  const path = slash === -1 ? "" : rest.slice(slash);
  const { host, port } = splitHostPort(authority);
  const hostOk = HOST_NAME.test(host) || IPV6_HOST.test(host);
  if (!hostOk || !PATH_CHARS.test(path) || (port !== null && !PORT.test(port))) return "That doesn't look like a valid URL.";
  const portNumber = port === null ? 1 : Number(port);
  if (portNumber < 1 || portNumber > 65535) return "The port must be 1-65535.";
  return null;
}

/** Trim, add http:// when no scheme is given, drop trailing slashes, validate. */
export function normalizeServerUrl(input: string): UrlCheck {
  const trimmed = input.trim();
  const problem = precheck(trimmed);
  if (problem) return { ok: false, error: problem };
  const value = withScheme(trimmed).replace(/\/+$/, "");
  const scheme = SCHEME.exec(value);
  if (!scheme) return { ok: false, error: "Use an http:// or https:// URL." };
  const error = authorityError(value.slice(scheme[0].length));
  return error ? { ok: false, error } : { ok: true, url: value };
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
