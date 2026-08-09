const API_BASE = import.meta.env.VITE_API_URL ?? "/api";

function cookie(name: string): string {
  const prefix = `${name}=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function rawFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = cookie("jobs_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
}

async function errorMessageFor(response: Response): Promise<string> {
  let message = "Something went wrong. Please try again.";
  try {
    const body = (await response.json()) as { detail?: string | Array<{ msg: string }> };
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) message = body.detail[0]?.msg ?? message;
  } catch {
    // Keep the safe generic message for non-JSON proxy/server failures.
  }
  return message;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await rawFetch(path, options);
  if (!response.ok) {
    throw new ApiError(await errorMessageFor(response), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** For endpoints returning a binary body (PDF/ZIP), not JSON — e.g. resume
 * tailoring. Returns the blob plus the server-suggested filename (from
 * Content-Disposition) so the caller can trigger a real file download. */
export async function apiBlob(
  path: string,
  options: RequestInit = {},
): Promise<{ blob: Blob; filename: string }> {
  const response = await rawFetch(path, options);
  if (!response.ok) {
    throw new ApiError(await errorMessageFor(response), response.status);
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] ?? "download" };
}
